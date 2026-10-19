"""D8 统计报表域：区间聚合、三类口径报表、自定义报表与 CSV 导出（设计03 D8 / 设计06 M-12）。

口径约定（页面与导出共用下面这批函数，否则导出的数字和屏幕上对不上）：
  · 时间轴按"业务发生时刻"落进区间：订单 created_at、飞行计划 submitted_at
    （没申报过的计划退回 created_at，不然它永远不出现在任何区间里）、
    违规 violation_time、结算 created_at、应急 created_at；
  · 区间是闭区间 [start 00:00, end+1day)：用"小于次日零点"而不是 "<= 23:59:59"，
    带小数秒的记录会掉在边界外；
  · 金额一律取实付 pay_amount，且只算"已支付且未作废"的订单（NET_PAID）：退款只多一条
    direction=2 的流水、orders.paid 仍留着 1，光看 paid=1 会把退掉的钱一直算进营收；
  · 平台抽成按生成结算单同一份费率（pricing_service.PLATFORM_RATE）在订单上计提，
    所以"已支付但还没确认收货（还没出结算单）"的抽成也看得见，overview 与
    /stats/merchant、/aux/report 三处的钱是同一个数；商家收入 = 实付 − 抽成；
  · 商家员工（user_type=3）只看本商家，平台/空管/运维看全量——收窄统一走
    scope.merchant_filter，和订单/结算/监控各蓝图同一个闸门。

输出形状统一成 {meta, kpi, kpiLabels, kpiKinds, sections:[{key,title,columns,rows}]}：
KPI 的中文名和展示类型跟着数据一起下去，页面卡片、图表轴、CSV 表头读同一份元数据，
不会出现"页面上叫实付金额、导出里叫 pay_amount"这种两张皮；列同理带 kind（text/int/money/num），
前端不用再按字段名后缀猜哪一列是钱。

桶键不用 DATE_FORMAT：它的格式串带 %，而 SQLAlchemy 在 pyformat 驱动下对字面 % 的处理
取决于版本；CAST(x AS DATE) 与 CONCAT(YEAR, LPAD(MONTH)) 给出同样的桶键，没这层不确定性。

契约 D8 写的"结果缓存 Redis（5 分钟刷新）"这版没做：Redis 只在依赖清单里，运行链路一个键
都没用（见差距分析非功能项）。这里的聚合是几条走索引的 GROUP BY，演示规模下单次 200ms 内；
真要加缓存会在本模块入口包一层 TTL，不改下面的 SQL 口径。
"""
import csv
import io
import json
import re
from datetime import date, datetime, timedelta

from app.common import scope
from app.common.errors import ApiError, BizCode
from app.extensions import db
from app.models import Airspace
from app.models.airspace import AIRSPACE_TYPE_NAME, AUDIT_STATUS_NAME
from app.models.compliance import VIOLATION_TYPE
from app.services import geo, pricing_service

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_SPAN_DAYS = 30
MAX_SPAN_DAYS = 366           # 一次最多看一年：日粒度也就 366 个桶，再多是图上的一片糊
ZONE_SCAN_MAX_PLANS = 500     # 空域穿行统计最多扫这么多条计划（几何判定在 Python 侧算）
CUSTOM_LIMIT = 500            # 自定义报表一次最多返回多少组

ORDER_STATUS_NAME = {0: "待支付", 1: "待接单", 2: "待调度", 3: "待审批", 4: "待起飞",
                     5: "飞行中", 6: "已送达", 7: "已完成", 8: "已取消"}

# 钱的口径只认"已支付且未作废"：退款不会把 orders.paid 改回 0（它是流水 direction=2 的账），
# 所以光判断 paid=1 会把退掉的钱永久算在营收里。作废的三条路径最后都落 status=8。
NET_PAID = "o.paid = 1 AND o.status <> 8"
# 抽成写在订单上，不写在结算单上：确认收货之前没有结算行，只读 settlement 的话
# "在途已支付"的抽成永远统计不到，商家报表里的收入就会虚高。
# 费率是代码常量（结算生成用的是同一个），拼进 SQL 不涉及请求参数。
FEE_EXPR = (f"COALESCE(SUM(CASE WHEN {NET_PAID}"
            f" THEN ROUND(o.pay_amount * {pricing_service.PLATFORM_RATE}, 2) END), 0)")
PAY_EXPR = f"COALESCE(SUM(CASE WHEN {NET_PAID} THEN o.pay_amount END), 0)"
DISCOUNT_EXPR = f"COALESCE(SUM(CASE WHEN {NET_PAID} THEN o.discount_amount END), 0)"
AVG_EXPR = f"COALESCE(AVG(CASE WHEN {NET_PAID} THEN o.total_amount END), 0)"

# 指标的内部聚合类型只有 int / money（两位小数）两种，但"两位小数"不等于"是钱"：
# 货量、审批时长都是小数，展示时套上 ¥ 就成了假数。这里把非货币的小数指标单独标出来，
# 列定义下发给页面时按这张表换成展示类型，页面不必再按字段名猜。
NON_CURRENCY = {"weightKg": "num", "avgApproveMin": "num"}

PLAN_STATUS_NAME = {0: "待申报", 1: "待审批", 2: "已批准待执行", 3: "执行中", 4: "已完成",
                    5: "已驳回", 6: "已取消"}
SETTLE_STATUS_NAME = {1: "待结算", 2: "已结算", 3: "对账中", 4: "有差异"}
PENALTY_NAME = {0: "未处罚", 1: "警告", 2: "罚款", 3: "暂停服务", 4: "永久封禁"}


# ------------------------------------------------------------------ 基础工具

def _rows(sql: str, **params) -> list[dict]:
    return [dict(r) for r in db.session.execute(db.text(sql), params).mappings().all()]


def _one(sql: str, **params) -> dict:
    rs = _rows(sql, **params)
    return rs[0] if rs else {}


def _i(v) -> int:
    """SUM(布尔) 在 MySQL 回来是 Decimal，图表和 CSV 要的是整数。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _m(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _rate(part, total, digits: int = 1) -> float:
    n = _i(total)
    return round(_i(part) / n * 100, digits) if n else 0.0


def _table(key: str, title: str, columns: list[tuple], rows: list[dict]) -> dict:
    """列定义：(键, 中文名[, 展示类型])。

    展示类型（text|int|money|num）跟着列一起下去，页面才知道哪一列该带 ¥、
    哪一列是该取整的计数。前端再按字段名后缀猜一次，就是第二套口径的开端。
    """
    return {"key": key, "title": title,
            "columns": [{"key": c[0], "label": c[1], "kind": c[2] if len(c) > 2 else "text"}
                        for c in columns],
            "rows": rows}


def _parse_date(raw: str, label: str) -> date:
    if not DATE_RE.match(raw):
        raise ApiError(BizCode.PARAM_ERROR, f"{label}格式应为 YYYY-MM-DD")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(BizCode.PARAM_ERROR, f"{label}不是合法日期：{raw}")


def resolve_range(args) -> tuple[date, date, str]:
    """归一化 start/end/grain；缺省最近 30 天、按天分桶。"""
    grain = "month" if (args.get("grain") or "").strip() == "month" else "day"
    raw_end = (args.get("end") or "").strip()
    raw_start = (args.get("start") or "").strip()
    end = _parse_date(raw_end, "结束日期") if raw_end else date.today()
    start = (_parse_date(raw_start, "开始日期") if raw_start
             else end - timedelta(days=DEFAULT_SPAN_DAYS - 1))
    if start > end:
        raise ApiError(BizCode.PARAM_ERROR, "开始日期不能晚于结束日期")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ApiError(BizCode.PARAM_ERROR, f"统计区间最长 {MAX_SPAN_DAYS} 天，请分段查询")
    return start, end, grain


def _cond(col: str, start: date, end: date) -> tuple[str, dict]:
    """时间区间片段。col 只允许代码里写死的列表达式，值一律走绑定参数。"""
    return (f" AND {col} >= :__ts AND {col} < DATE_ADD(:__te, INTERVAL 1 DAY)",
            {"__ts": f"{start} 00:00:00", "__te": f"{end} 00:00:00"})


def _bucket_expr(col: str, grain: str) -> str:
    return (f"CONCAT(YEAR({col}), '-', LPAD(MONTH({col}), 2, '0'))"
            if grain == "month" else f"CAST({col} AS DATE)")


def _buckets(start: date, end: date, grain: str) -> list[str]:
    """补齐空桶：图表上"没有数据"要画成 0，不能画成断线——断线看着像链路故障。"""
    if grain == "month":
        out, y, m = [], start.year, start.month
        while (y, m) <= (end.year, end.month):
            out.append(f"{y}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out
    return [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)]


def _result(meta: dict, kpi: dict, spec: list[tuple[str, str, str]], sections: list[dict]) -> dict:
    """把数值规整一遍再出去：JSON 不认 Decimal，日期桶也要成字符串。

    spec 是 (键, 中文名, 展示类型) 三元组清单，同一份清单既出 kpiLabels 又出 kpiKinds，
    页面卡片和 CSV 表头因此不可能分家。
    """
    return {"meta": meta, "kpi": kpi,
            "kpiLabels": {k: label for k, label, _ in spec},
            "kpiKinds": {k: kind for k, _, kind in spec},
            "sections": sections}


def _meta(start, end, grain, user) -> dict:
    return {"start": start.isoformat(), "end": end.isoformat(), "grain": grain,
            "scope": "merchant" if scope.merchant_scope(user) is not None else "platform"}


def _group(key: str, title: str, *, dim_expr: str,
           measures: list[tuple[str, str, str, str]],
           base_from: str, base_where: str, time_col: str, scope_col: str,
           start: date, end: date, user, dim_name: str = "维度",
           dim_label: dict | None = None, limit: int = 30) -> dict:
    """按单一维度分组数数：状态分布、品类分布、违规类型分布共用这一个函数。

    measures 每项 (键, 中文名, SQL 表达式, 'int'|'money')。表达式全部写死在本模块常量里，
    页面传进来的只有维度名和指标名，都要先过白名单；类型显式声明，
    免得"订单量"这种计数字段带着小数点进到图表和 CSV 里。
    """
    c_sql, c_par = _cond(time_col, start, end)
    s_sql, s_par = scope.merchant_filter(user, scope_col)
    select = [f"{dim_expr} dim"] + [f"{expr} `{k}`" for k, _, expr, _ in measures]
    rows = _rows(f"SELECT {', '.join(select)} {base_from}"
                 f" WHERE {base_where}{c_sql}{s_sql}"
                 # 度量相同就按维度值排，保证同一份数据两次导出行序一致
                 f" GROUP BY dim ORDER BY `{measures[0][0]}` DESC, dim LIMIT {limit}",
                 **c_par, **s_par)
    out = []
    for r in rows:
        raw = r["dim"]
        row = {"dim": (dim_label.get(_i(raw), f"未知类型({_i(raw)})")
                       if dim_label is not None
                       else (str(raw) if raw is not None else "未填写"))}
        for k, _, _, kind in measures:
            row[k] = _m(r[k]) if kind == "money" else _i(r[k])
        out.append(row)
    return _table(key, title,
                  [("dim", dim_name, "text"),
                   *[(k, label, NON_CURRENCY.get(k, kind)) for k, label, _, kind in measures]],
                  out)


# ------------------------------------------------------------------ 1. 综合口径

KPI_SPEC: list[tuple[str, str, str]] = [
    ("orderCount", "订单量", "int"), ("doneCount", "完成量", "int"),
    ("doneRate", "完成率%", "num"), ("cancelCount", "取消量", "int"),
    ("cancelRate", "取消率%", "num"), ("urgentCount", "加急单", "int"),
    ("customerCount", "下单客户", "int"), ("payAmount", "实付金额", "money"),
    ("discountAmount", "券抵扣额", "money"), ("avgOrderAmount", "单均金额", "money"),
    ("weightKg", "货量kg", "num"),
    ("planCount", "计划申报", "int"), ("approvedCount", "审批通过", "int"),
    ("approvalRate", "通过率%", "num"), ("flightCount", "实飞架次", "int"),
    ("violationCount", "违规条数", "int"), ("punishRate", "处罚率%", "num"),
    ("penaltyAmount", "罚款额", "money"),
    ("settlementCount", "结算单", "int"), ("platformFee", "平台抽成", "money"),
    ("merchantIncome", "商家收入", "money"),
    ("diffCount", "对账差异单", "int"),
    ("emergencyCount", "应急事件", "int"), ("emergencyOpen", "未解除应急", "int"),
]


def overview(user, args) -> dict:
    start, end, grain = resolve_range(args)

    t_sql, t_par = _cond("o.created_at", start, end)
    m_sql, m_par = scope.merchant_filter(user, "o.merchant_id")
    k = _one(
        "SELECT COUNT(*) order_count, SUM(o.status IN (6,7)) done_count,"
        " SUM(o.status = 8) cancel_count, SUM(o.paid = 1) paid_count,"
        " SUM(o.delivery_type = 2) urgent_count, COUNT(DISTINCT o.customer_id) customer_count,"
        f" {PAY_EXPR} pay_amount, {DISCOUNT_EXPR} discount_amount,"
        f" {AVG_EXPR} avg_order_amount, {FEE_EXPR} platform_fee,"
        " COALESCE(SUM(o.weight_kg), 0) weight_kg"
        f" FROM orders o WHERE o.deleted = 0{t_sql}{m_sql}", **t_par, **m_par)

    pt_sql, pt_par = _cond("COALESCE(fp.submitted_at, fp.created_at)", start, end)
    pm_sql, pm_par = scope.merchant_filter(user, "fp.merchant_id")
    p = _one(
        "SELECT COUNT(*) plan_count, SUM(fp.approve_result = 1) approved_count,"
        " SUM(fp.approve_result = 2) rejected_count,"
        " SUM(fp.actual_takeoff_at IS NOT NULL) flight_count"
        f" FROM flight_plan fp WHERE fp.deleted = 0{pt_sql}{pm_sql}", **pt_par, **pm_par)

    vt_sql, vt_par = _cond("vr.violation_time", start, end)
    vm_sql, vm_par = scope.merchant_filter(user, "vr.merchant_id")
    v = _one(
        "SELECT COUNT(*) violation_count, SUM(vr.penalty_type IS NOT NULL) punished_count,"
        " COALESCE(SUM(vr.penalty_amount), 0) penalty_amount"
        f" FROM violation_record vr WHERE vr.deleted = 0{vt_sql}{vm_sql}",
        **vt_par, **vm_par)

    st_sql, st_par = _cond("s.created_at", start, end)
    sm_sql, sm_par = scope.merchant_filter(user, "s.merchant_id")
    s = _one(
        "SELECT COUNT(*) settlement_count, SUM(s.status = 4) diff_count"
        f" FROM settlement s WHERE s.deleted = 0{st_sql}{sm_sql}", **st_par, **sm_par)

    # 应急事件表没有 merchant_id，只能借 plan_id 关联到商家；
    # 关联不上的（无计划应急）在商家视角自然看不到——平台口径才是全量。
    e_sql, e_par = _cond("e.created_at", start, end)
    mid = scope.merchant_scope(user)
    em, em_par = (" AND fp2.merchant_id = :__emid", {"__emid": mid}) if mid is not None else ("", {})
    e = _one(
        "SELECT COUNT(*) emergency_count, SUM(e.status != 3) emergency_open"
        f" FROM emergency_event e LEFT JOIN flight_plan fp2 ON fp2.id = e.plan_id"
        f" WHERE e.deleted = 0{e_sql}{em}", **e_par, **em_par)

    vio_count = _i(v["violation_count"])
    kpi = {
        "orderCount": _i(k["order_count"]),
        "doneCount": _i(k["done_count"]),
        "doneRate": _rate(k["done_count"], k["order_count"]),
        "cancelCount": _i(k["cancel_count"]),
        "cancelRate": _rate(k["cancel_count"], k["order_count"]),
        "urgentCount": _i(k["urgent_count"]),
        "customerCount": _i(k["customer_count"]),
        "payAmount": _m(k["pay_amount"]),
        "discountAmount": _m(k["discount_amount"]),
        "avgOrderAmount": _m(k["avg_order_amount"]),
        "weightKg": _m(k["weight_kg"]),
        "planCount": _i(p["plan_count"]),
        "approvedCount": _i(p["approved_count"]),
        "approvalRate": _rate(p["approved_count"], p["plan_count"]),
        "flightCount": _i(p["flight_count"]),
        "violationCount": vio_count,
        "punishRate": _rate(v["punished_count"], vio_count),
        "penaltyAmount": _m(v["penalty_amount"]),
        "settlementCount": _i(s["settlement_count"]),
        "platformFee": _m(k["platform_fee"]),
        # 抽成按订单计提（见 FEE_EXPR），收入 = 实付 − 抽成；已退款的单子两头都不算
        "merchantIncome": round(_m(k["pay_amount"]) - _m(k["platform_fee"]), 2),
        "diffCount": _i(s["diff_count"]),
        "emergencyCount": _i(e["emergency_count"]),
        "emergencyOpen": _i(e["emergency_open"]),
    }

    return _result(_meta(start, end, grain, user), kpi, KPI_SPEC, [
        _trend(start, end, grain, user),
        _group("statusDist", "订单状态分布", dim_expr="o.status", dim_name="订单状态",
               measures=[("orderCount", "订单量", "COUNT(*)", "int"),
                         ("payAmount", "实付金额", PAY_EXPR, "money")],
               base_from="FROM orders o", base_where="o.deleted = 0",
               time_col="o.created_at", scope_col="o.merchant_id",
               start=start, end=end, user=user, dim_label=ORDER_STATUS_NAME),
        _group("goodsDist", "货物类型分布", dim_expr="o.goods_type", dim_name="货物类型",
               measures=[("orderCount", "订单量", "COUNT(*)", "int"),
                         ("weightKg", "货量kg", "COALESCE(SUM(o.weight_kg), 0)", "money"),
                         ("payAmount", "实付金额", PAY_EXPR, "money")],
               base_from="FROM orders o", base_where="o.deleted = 0",
               time_col="o.created_at", scope_col="o.merchant_id",
               start=start, end=end, user=user),
        _group("violationDist", "违规类型分布", dim_expr="vr.violation_type",
               dim_name="违规类型",
               measures=[("violationCount", "违规条数", "COUNT(*)", "int"),
                         ("punishedCount", "已处罚", "SUM(vr.penalty_type IS NOT NULL)", "int"),
                         ("penaltyAmount", "罚款额",
                          "COALESCE(SUM(vr.penalty_amount), 0)", "money")],
               base_from="FROM violation_record vr", base_where="vr.deleted = 0",
               time_col="vr.violation_time", scope_col="vr.merchant_id",
               start=start, end=end, user=user, dim_label=VIOLATION_TYPE),
    ])


def _trend(start, end, grain, user) -> dict:
    """订单/计划/违规各查各的 GROUP BY，再在 Python 侧按桶键合并。

    不写成一条三表 LEFT JOIN 的聚合 SQL：那样行数会做笛卡尔膨胀，SUM 直接翻倍，
    导出表里的金额就是个错的数字。分开聚合，每个数都能单独拿 SQL 核对。
    """
    buckets = _buckets(start, end, grain)
    blank = {"orderCount": 0, "payAmount": 0.0, "doneCount": 0, "cancelCount": 0,
             "planCount": 0, "violationCount": 0}
    series = {b: {"bucket": b, **blank} for b in buckets}

    def merge(sql, params, fields):
        for r in _rows(sql, **params):
            row = series.get(str(r["bucket"]))
            if row is None:
                continue        # 桶键对不上（历史补录到区间外）直接丢，不硬塞进图表
            for out_key, (src_key, money) in fields.items():
                row[out_key] = _m(r[src_key]) if money else _i(r[src_key])

    c_sql, c_par = _cond("o.created_at", start, end)
    m_sql, m_par = scope.merchant_filter(user, "o.merchant_id")
    merge(f"SELECT {_bucket_expr('o.created_at', grain)} bucket, COUNT(*) order_count,"
          f" {PAY_EXPR} pay_amount,"
          " SUM(o.status IN (6,7)) done_count, SUM(o.status = 8) cancel_count"
          f" FROM orders o WHERE o.deleted = 0{c_sql}{m_sql}"
          " GROUP BY bucket", {**c_par, **m_par},
          {"orderCount": ("order_count", False), "payAmount": ("pay_amount", True),
           "doneCount": ("done_count", False), "cancelCount": ("cancel_count", False)})

    p_sql, p_par = _cond("COALESCE(fp.submitted_at, fp.created_at)", start, end)
    pm_sql, pm_par = scope.merchant_filter(user, "fp.merchant_id")
    merge(f"SELECT {_bucket_expr('COALESCE(fp.submitted_at, fp.created_at)', grain)} bucket,"
          " COUNT(*) plan_count"
          f" FROM flight_plan fp WHERE fp.deleted = 0{p_sql}{pm_sql} GROUP BY bucket",
          {**p_par, **pm_par}, {"planCount": ("plan_count", False)})

    v_sql, v_par = _cond("vr.violation_time", start, end)
    vm_sql, vm_par = scope.merchant_filter(user, "vr.merchant_id")
    merge(f"SELECT {_bucket_expr('vr.violation_time', grain)} bucket, COUNT(*) violation_count"
          f" FROM violation_record vr WHERE vr.deleted = 0{v_sql}{vm_sql} GROUP BY bucket",
          {**v_par, **vm_par}, {"violationCount": ("violation_count", False)})

    return _table("trend", f"趋势（{'按月' if grain == 'month' else '按日'}）",
                  [("bucket", "日期", "text"), ("orderCount", "订单量", "int"),
                   ("payAmount", "实付金额", "money"), ("doneCount", "完成量", "int"),
                   ("cancelCount", "取消量", "int"), ("planCount", "计划申报", "int"),
                   ("violationCount", "违规数", "int")],
                  [series[b] for b in buckets])


# ------------------------------------------------------------------ 2. 商家口径

def merchant_report(user, args) -> dict:
    start, end, grain = resolve_range(args)
    t_sql, t_par = _cond("o.created_at", start, end)
    m_sql, m_par = scope.merchant_filter(user, "o.merchant_id")
    orders = _rows(
        "SELECT o.merchant_id,"
        " COALESCE(mt.merchant_name, '未分派商家') merchant_name,"
        " COUNT(*) order_count, SUM(o.status IN (6,7)) done_count,"
        " SUM(o.status = 8) cancel_count, SUM(o.delivery_type = 2) urgent_count,"
        f" {PAY_EXPR} pay_amount, {DISCOUNT_EXPR} discount_amount,"
        f" {FEE_EXPR} platform_fee,"
        " COALESCE(SUM(o.weight_kg), 0) weight_kg"
        f" FROM orders o LEFT JOIN merchants mt ON mt.id = o.merchant_id"
        f" WHERE o.deleted = 0{t_sql}{m_sql}"
        " GROUP BY o.merchant_id, merchant_name"
        # 金额相同的商家必须有个固定次序：否则同一份数据两次导出行序不一样，
        # "导出可复核"就无从谈起，商家 id 升序就是这个平手的兜底键。
        " ORDER BY pay_amount DESC, o.merchant_id LIMIT 200",
        **t_par, **m_par)

    st_sql, st_par = _cond("s.created_at", start, end)
    sm_sql, sm_par = scope.merchant_filter(user, "s.merchant_id")
    settle = {r["merchant_id"]: r for r in _rows(
        "SELECT s.merchant_id, COUNT(*) settle_count, SUM(s.status = 4) diff_count"
        f" FROM settlement s WHERE s.deleted = 0{st_sql}{sm_sql} GROUP BY s.merchant_id",
        **st_par, **sm_par)}

    vt_sql, vt_par = _cond("vr.violation_time", start, end)
    vm_sql, vm_par = scope.merchant_filter(user, "vr.merchant_id")
    vio = {r["merchant_id"]: r for r in _rows(
        "SELECT vr.merchant_id, COUNT(*) violation_count,"
        " SUM(vr.penalty_type IS NOT NULL) punished_count,"
        " COALESCE(SUM(vr.penalty_amount), 0) penalty_amount"
        f" FROM violation_record vr WHERE vr.deleted = 0{vt_sql}{vm_sql}"
        " GROUP BY vr.merchant_id", **vt_par, **vm_par)}

    money_cols = ("payAmount", "discountAmount", "weightKg",
                  "platformFee", "merchantIncome", "penaltyAmount")
    int_cols = ("orderCount", "doneCount", "cancelCount", "urgentCount", "settleCount",
                "diffCount", "violationCount", "punishedCount")
    rows = []
    for r in orders:
        sv, vv = settle.get(r["merchant_id"], {}), vio.get(r["merchant_id"], {})
        pay = _m(r["pay_amount"])
        fee = _m(r["platform_fee"])
        row = {
            "merchantName": r["merchant_name"],
            "orderCount": _i(r["order_count"]), "doneCount": _i(r["done_count"]),
            "cancelCount": _i(r["cancel_count"]), "urgentCount": _i(r["urgent_count"]),
            "doneRate": _rate(r["done_count"], r["order_count"]),
            "payAmount": pay, "discountAmount": _m(r["discount_amount"]),
            "weightKg": _m(r["weight_kg"]),
            "settleCount": _i(sv.get("settle_count")), "diffCount": _i(sv.get("diff_count")),
            "platformFee": fee, "merchantIncome": round(pay - fee, 2),
            "violationCount": _i(vv.get("violation_count")),
            "punishedCount": _i(vv.get("punished_count")),
            "penaltyAmount": _m(vv.get("penalty_amount")),
        }
        rows.append(row)

    # 合计行单独加总，而不是拿各行四舍五入后的值再相加（那样会漂几分钱）
    total = {"merchantName": f"合计（{len(rows)} 家）"}
    for col in money_cols:
        total[col] = round(sum(_m(x[col]) for x in rows), 2)
    for col in int_cols:
        total[col] = sum(_i(x[col]) for x in rows)
    total["doneRate"] = _rate(total["doneCount"], total["orderCount"])

    kpi = {
        "merchantCount": len(rows),
        "orderCount": total["orderCount"],
        "doneCount": total["doneCount"],
        "doneRate": total["doneRate"],
        "payAmount": total["payAmount"],
        "discountAmount": total["discountAmount"],
        "platformFee": total["platformFee"],
        "merchantIncome": total["merchantIncome"],
        "settleCount": total["settleCount"],
        "diffCount": total["diffCount"],
        "violationCount": total["violationCount"],
        "penaltyAmount": total["penaltyAmount"],
    }
    kpi_spec = [
        ("merchantCount", "有单商家数", "int"), ("orderCount", "订单量", "int"),
        ("doneCount", "完成量", "int"), ("doneRate", "完成率%", "num"),
        ("payAmount", "实付金额", "money"), ("discountAmount", "券抵扣额", "money"),
        ("platformFee", "平台抽成", "money"), ("merchantIncome", "商家收入", "money"),
        ("settleCount", "结算单", "int"), ("diffCount", "对账差异单", "int"),
        ("violationCount", "违规条数", "int"), ("penaltyAmount", "罚款额", "money"),
    ]
    return _result(_meta(start, end, grain, user), kpi, kpi_spec, [
        _table("merchantDetail", "商家经营明细",
               [("merchantName", "商家", "text"), ("orderCount", "订单量", "int"),
                ("doneCount", "完成量", "int"), ("doneRate", "完成率%", "num"),
                ("cancelCount", "取消量", "int"), ("urgentCount", "加急量", "int"),
                ("payAmount", "实付金额", "money"), ("discountAmount", "券抵扣", "money"),
                ("weightKg", "货量kg", "num"), ("settleCount", "结算单", "int"),
                ("diffCount", "差异单", "int"), ("platformFee", "平台抽成", "money"),
                ("merchantIncome", "商家收入", "money"), ("violationCount", "违规条数", "int"),
                ("punishedCount", "已处罚", "int"), ("penaltyAmount", "罚款额", "money")],
               rows + [total]),
    ])


# ------------------------------------------------------------------ 3. 空管口径

def airspace_report(user, args) -> dict:
    start, end, grain = resolve_range(args)
    t_sql, t_par = _cond("COALESCE(fp.submitted_at, fp.created_at)", start, end)
    m_sql, m_par = scope.merchant_filter(user, "fp.merchant_id")
    a = _one(
        "SELECT COUNT(*) submit_count, SUM(fp.approve_result = 1) approved_count,"
        " SUM(fp.approve_result = 2) rejected_count,"
        " SUM(fp.status = 4) plan_done_count,"
        " COALESCE(AVG(CASE WHEN fp.approved_at IS NOT NULL AND fp.submitted_at IS NOT NULL"
        "                  THEN TIMESTAMPDIFF(MINUTE, fp.submitted_at, fp.approved_at) END), 0)"
        " avg_approve_min"
        " FROM flight_plan fp WHERE fp.deleted = 0 AND fp.submitted_at IS NOT NULL"
        f"{t_sql}{m_sql}", **t_par, **m_par)

    fly_sql, fly_par = _cond("fp.actual_takeoff_at", start, end)
    flight_count = _one(
        f"SELECT COUNT(*) c FROM flight_plan fp WHERE fp.deleted = 0"
        f" AND fp.actual_takeoff_at IS NOT NULL{fly_sql}{m_sql}", **fly_par, **m_par)["c"]

    vt_sql, vt_par = _cond("vr.violation_time", start, end)
    vm_sql, vm_par = scope.merchant_filter(user, "vr.merchant_id")
    vio_by_plan = _rows(
        f"SELECT {_bucket_expr('vr.violation_time', grain)} bucket, COUNT(*) violation_count,"
        " COUNT(DISTINCT vr.plan_id) plan_count, SUM(vr.violation_type = 1) no_fly_count"
        f" FROM violation_record vr WHERE vr.deleted = 0{vt_sql}{vm_sql} GROUP BY bucket",
        **vt_par, **vm_par)

    # 申报数按同一个桶再聚合一次，然后在 Python 侧对齐：
    # 违规有、申报空的话，图表上会被读成"漏数"，两个序列必须共用同一组桶键。
    submit_by_bucket = {str(r["bucket"]): _i(r["submit_count"]) for r in _rows(
        f"SELECT {_bucket_expr('COALESCE(fp.submitted_at, fp.created_at)', grain)} bucket,"
        " COUNT(*) submit_count FROM flight_plan fp"
        f" WHERE fp.deleted = 0 AND fp.submitted_at IS NOT NULL{t_sql}{m_sql} GROUP BY bucket",
        **t_par, **m_par)}

    vio_map = {str(r["bucket"]): r for r in vio_by_plan}
    trend_rows = [{"bucket": b,
                   "submitCount": submit_by_bucket.get(b, 0),
                   "violationCount": _i((vio_map.get(b) or {}).get("violation_count")),
                   "conflictPlans": _i((vio_map.get(b) or {}).get("plan_count")),
                   "noFlyCount": _i((vio_map.get(b) or {}).get("no_fly_count"))}
                  for b in _buckets(start, end, grain)]
    conflict_plans = sum(_i(r["plan_count"]) for r in vio_by_plan)

    zone_table, zone_total = _zone_usage(start, end, user)
    kpi = {
        "submitCount": _i(a["submit_count"]),
        "approvedCount": _i(a["approved_count"]),
        "rejectedCount": _i(a["rejected_count"]),
        "approvalRate": _rate(a["approved_count"], a["submit_count"]),
        "avgApproveMin": round(float(a["avg_approve_min"] or 0), 1),
        "flightCount": _i(flight_count),
        "violationCount": sum(_i(r["violation_count"]) for r in vio_by_plan),
        # 冲突率分母用实飞架次而不是违规条数：一次任务刷出 5 条违规不该算 5 次冲突
        "conflictRate": _rate(conflict_plans, flight_count),
        "zoneTotal": zone_total,
        "zoneActiveCount": sum(1 for r in zone_table["rows"] if _i(r["planCount"])),
    }
    kpi_spec = [
        ("submitCount", "计划申报", "int"), ("approvedCount", "审批通过", "int"),
        ("rejectedCount", "审批驳回", "int"), ("approvalRate", "通过率%", "num"),
        ("avgApproveMin", "平均审批时长(分)", "num"), ("flightCount", "实飞架次", "int"),
        ("violationCount", "违规条数", "int"), ("conflictRate", "冲突率%", "num"),
        ("zoneTotal", "空域总数", "int"), ("zoneActiveCount", "被穿行空域数", "int"),
    ]
    return _result(_meta(start, end, grain, user), kpi, kpi_spec, [
        _table("trend", f"空管趋势（{'按月' if grain == 'month' else '按日'}）",
               [("bucket", "日期", "text"), ("submitCount", "申报数", "int"),
                ("violationCount", "违规数", "int"), ("conflictPlans", "涉及架次", "int"),
                ("noFlyCount", "闯禁飞", "int")], trend_rows),
        _group("violationDist", "违规类型分布", dim_expr="vr.violation_type",
               dim_name="违规类型",
               measures=[("violationCount", "违规条数", "COUNT(*)", "int"),
                         ("planCount", "涉及架次", "COUNT(DISTINCT vr.plan_id)", "int"),
                         ("punishedCount", "已处罚", "SUM(vr.penalty_type IS NOT NULL)", "int"),
                         ("penaltyAmount", "罚款额", "COALESCE(SUM(vr.penalty_amount), 0)",
                          "money")],
               base_from="FROM violation_record vr", base_where="vr.deleted = 0",
               time_col="vr.violation_time", scope_col="vr.merchant_id",
               start=start, end=end, user=user, dim_label=VIOLATION_TYPE),
        zone_table,
    ])


def _zone_usage(start, end, user) -> tuple[dict, int]:
    """每个空域被多少条航线穿行、区内落了多少条违规点。

    穿行统计量的是"航线经过"，不是"合规违规"，所以直接用 geo 的原始判据
    （point_in_polygon / polygon_rings，和飞行中逐帧判定同一份实现），
    不复用 check_route_against_airspaces——那个会按空域类型把可飞区滤掉，
    用在合规上是对的，用在"这个区到底有没有人飞"上会把可飞区的量整个吞掉。
    """
    zones = db.session.query(Airspace).filter(Airspace.deleted == 0).all()
    rings = {z.id: geo.polygon_rings(z.geometry_json or {}) for z in zones}
    # 只有"生效"的空域参与穿行统计：停用的区已经退出合规判定，报成有人飞是假信号
    active = [(z.id, r) for z in zones if z.status == 1 for r in [rings.get(z.id)] if r]

    t_sql, t_par = _cond("COALESCE(fp.submitted_at, fp.created_at)", start, end)
    m_sql, m_par = scope.merchant_filter(user, "fp.merchant_id")
    plans = _rows(
        "SELECT fp.id, fp.route_json FROM flight_plan fp WHERE fp.deleted = 0"
        f"{t_sql}{m_sql} ORDER BY fp.id DESC LIMIT {ZONE_SCAN_MAX_PLANS}", **t_par, **m_par)

    cross: dict[int, int] = {z.id: 0 for z in zones}
    for r in plans:
        pts = _route_points(r["route_json"])
        if not pts:
            continue
        for zid, zone_rings in active:
            # any() 天然去重：一条计划的多个航点落在同一个区里也只加一次
            if any(geo.point_in_polygon(lng, lat, ring)
                   for ring in zone_rings for lng, lat in pts):
                cross[zid] += 1

    vt_sql, vt_par = _cond("vr.violation_time", start, end)
    vm_sql, vm_par = scope.merchant_filter(user, "vr.merchant_id")
    vio_pts = _rows(
        "SELECT vr.lng, vr.lat FROM violation_record vr"
        f" WHERE vr.deleted = 0 AND vr.lng IS NOT NULL{vt_sql}{vm_sql}", **vt_par, **vm_par)

    inside: dict[int, int] = {z.id: 0 for z in zones}
    for r in vio_pts:
        lng, lat = float(r["lng"]), float(r["lat"])
        for zid, zone_rings in rings.items():
            if any(geo.point_in_polygon(lng, lat, ring) for ring in zone_rings):
                inside[zid] += 1
                break             # 一个违规点只归进最靠外命中的第一个区，避免重复计数

    rows = [{"zoneName": z.airspace_name,
             "type": AIRSPACE_TYPE_NAME.get(z.airspace_type, f"其他({z.airspace_type})"),
             "status": ("生效" if z.status == 1 else "停用")
                       + "/" + AUDIT_STATUS_NAME.get(z.audit_status, "未知"),
             "planCount": cross.get(z.id, 0),
             "violationCount": inside.get(z.id, 0),
             "altitudeMax": z.altitude_max} for z in zones if rings.get(z.id)]
    rows.sort(key=lambda x: (-x["planCount"], -x["violationCount"], x["zoneName"]))
    if scope.merchant_scope(user) is not None:
        rows = [r for r in rows if r["violationCount"] or r["planCount"]]
    table = _table("zoneUsage", "空域使用与穿行",
                   [("zoneName", "空域", "text"), ("type", "类型", "text"),
                    ("status", "台账状态", "text"), ("planCount", "穿行计划数", "int"),
                    ("violationCount", "区内违规", "int"), ("altitudeMax", "高度上限m", "num")],
                   rows[:50])
    return table, len(zones)


def _route_points(route_json) -> list[tuple[float, float]]:
    """flight_plan.route_json 兼容 str / 已解析两种形态（raw SQL 回来的是字符串）。"""
    data = route_json
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return []
    out = []
    for p in (data or []):
        if isinstance(p, dict) and p.get("lng") is not None and p.get("lat") is not None:
            out.append((float(p["lng"]), float(p["lat"])))
    return out


# ------------------------------------------------------------------ 4. 自定义报表

# 数据集 = 一张事实表 + 它能提供的维度与指标。页面右侧的字段池由这里下发，
# 前端不另存一份清单，免得出现"页面上选得了、后端说不认识"的两张皮。
# dim/measure 的键是白名单，SQL 表达式全部写死在本文件里：请求参数进不了 SQL。
DATASETS: dict[str, dict] = {
    "order": {
        "label": "订单",
        "from": "FROM orders o LEFT JOIN merchants mt ON mt.id = o.merchant_id",
        "where": "o.deleted = 0",
        "time": "o.created_at",
        "scope": "o.merchant_id",
        "dims": {
            "merchant": ("商家", "COALESCE(mt.merchant_name, '未分派商家')", None),
            "goodsType": ("货物类型", "o.goods_type", None),
            "deliveryType": ("时效类型", "o.delivery_type", {1: "普通", 2: "加急"}),
            "status": ("订单状态", "o.status", ORDER_STATUS_NAME),
        },
        "measures": {
            "orderCount": ("订单量", "COUNT(*)", "int"),
            "doneCount": ("完成量", "SUM(o.status IN (6,7))", "int"),
            "cancelCount": ("取消量", "SUM(o.status = 8)", "int"),
            "payAmount": ("实付金额", PAY_EXPR, "money"),
            "discountAmount": ("券抵扣", DISCOUNT_EXPR, "money"),
            "weightKg": ("货量kg", "COALESCE(SUM(o.weight_kg), 0)", "money"),
            "avgAmount": ("单均金额", AVG_EXPR, "money"),
            "platformFee": ("平台抽成", FEE_EXPR, "money"),
        },
    },
    "plan": {
        "label": "飞行计划",
        "from": ("FROM flight_plan fp LEFT JOIN merchants mt ON mt.id = fp.merchant_id"
                 " LEFT JOIN vertiport v ON v.id = fp.takeoff_vertiport_id"),
        "where": "fp.deleted = 0",
        "time": "COALESCE(fp.submitted_at, fp.created_at)",
        "scope": "fp.merchant_id",
        "dims": {
            "merchant": ("商家", "COALESCE(mt.merchant_name, '未分派商家')", None),
            "vertiport": ("起飞场站", "COALESCE(v.station_name, '未指定')", None),
            "status": ("计划状态", "fp.status", PLAN_STATUS_NAME),
        },
        "measures": {
            "planCount": ("计划数", "COUNT(*)", "int"),
            "approvedCount": ("通过数", "SUM(fp.approve_result = 1)", "int"),
            "rejectedCount": ("驳回数", "SUM(fp.approve_result = 2)", "int"),
            "flightCount": ("实飞架次", "SUM(fp.actual_takeoff_at IS NOT NULL)", "int"),
            "avgApproveMin": ("平均审批时长(分)",
                              "COALESCE(AVG(TIMESTAMPDIFF(MINUTE, fp.submitted_at,"
                              " fp.approved_at)), 0)", "money"),
        },
    },
    "violation": {
        "label": "违规台账",
        "from": "FROM violation_record vr LEFT JOIN merchants mt ON mt.id = vr.merchant_id",
        "where": "vr.deleted = 0",
        "time": "vr.violation_time",
        "scope": "vr.merchant_id",
        "dims": {
            "merchant": ("商家", "COALESCE(mt.merchant_name, '未知商家')", None),
            "violationType": ("违规类型", "vr.violation_type", VIOLATION_TYPE),
            "status": ("处置状态", "vr.status", {1: "未处理", 2: "处理中", 3: "已归档"}),
            "penaltyType": ("处罚类型", "COALESCE(vr.penalty_type, 0)", PENALTY_NAME),
        },
        "measures": {
            "violationCount": ("违规条数", "COUNT(*)", "int"),
            "planCount": ("涉及架次", "COUNT(DISTINCT vr.plan_id)", "int"),
            "punishedCount": ("已处罚", "SUM(vr.penalty_type IS NOT NULL)", "int"),
            "penaltyAmount": ("罚款额", "COALESCE(SUM(vr.penalty_amount), 0)", "money"),
        },
    },
    "settlement": {
        "label": "结算账单",
        "from": "FROM settlement s LEFT JOIN merchants mt ON mt.id = s.merchant_id",
        "where": "s.deleted = 0",
        "time": "s.created_at",
        "scope": "s.merchant_id",
        "dims": {
            "merchant": ("商家", "COALESCE(mt.merchant_name, '未知商家')", None),
            "status": ("账单状态", "s.status", SETTLE_STATUS_NAME),
        },
        "measures": {
            "settleCount": ("账单数", "COUNT(*)", "int"),
            # 结算数据集只覆盖"已经出账"的钱，指标名要带"已结"：
            # 否则它和订单数据集里的同名指标口径不一样，两张表看着像互相矛盾。
            "payAmount": ("已结应结金额", "COALESCE(SUM(s.pay_amount), 0)", "money"),
            "platformFee": ("已结平台抽成", "COALESCE(SUM(s.platform_fee), 0)", "money"),
            "merchantIncome": ("已结商家收入",
                               "COALESCE(SUM(s.pay_amount - s.platform_fee), 0)", "money"),
            "diffCount": ("差异单", "SUM(s.status = 4)", "int"),
        },
    },
}
TIME_DIMS = {"day": "按日", "month": "按月"}


def field_pool() -> dict:
    """给页面的字段池：数据集 → 可用维度 / 可用指标。"""
    return {
        "datasets": [{"key": k, "label": d["label"],
                      "dims": [{"key": dk, "label": dv[0]} for dk, dv in d["dims"].items()],
                      "measures": [{"key": mk, "label": mv[0]}
                                   for mk, mv in d["measures"].items()]}
                     for k, d in DATASETS.items()],
        "timeDims": [{"key": k, "label": v} for k, v in TIME_DIMS.items()],
    }


def _pick(names, allowed: dict, what: str, keep_all_if_empty: bool = True) -> list[str]:
    raw = (names or "").strip()
    keys = [x.strip() for x in re.split(r"[,，]", raw) if x.strip()]
    if not keys:
        return list(allowed.keys()) if keep_all_if_empty else []
    bad = [k for k in keys if k not in allowed]
    if bad:
        raise ApiError(BizCode.PARAM_ERROR,
                       f"不支持的{what}：{'、'.join(bad)}"
                       f"（可选：{'、'.join(allowed.keys())}）")
    # 去重保序：同一个键写两遍会在 SQL 里造出两个同名列，取值时后者覆盖前者
    seen: set[str] = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def custom_report(user, args) -> dict:
    """字段池拖拽报表：数据集 + 维度（含时间粒度）+ 指标 → 一张交叉表。"""
    ds_key = (args.get("dataset") or "order").strip()
    ds = DATASETS.get(ds_key)
    if ds is None:
        raise ApiError(BizCode.PARAM_ERROR,
                       f"不认识的数据集：{ds_key}（可选：{'、'.join(DATASETS.keys())}）")
    start, end, _grain = resolve_range(args)

    pool = {**{"day": None, "month": None}, **ds["dims"]}
    dim_keys = _pick(args.get("dims"), pool, "维度", keep_all_if_empty=False)
    if not dim_keys:
        raise ApiError(BizCode.PARAM_ERROR, "至少选一个维度，比如「按日」或「商家」")
    # 时间分桶由选中的维度本身决定（day 就按日、month 就按月），
    # 不跟着 grain 走：否则会出现"列头写着按日、数却按月聚"的自相矛盾。
    dims: list[tuple[str, str, str]] = []
    for key in dim_keys:
        if key in TIME_DIMS:
            dims.append((key, TIME_DIMS[key], _bucket_expr(ds["time"], key)))
        else:
            label, expr, _ = ds["dims"][key]
            dims.append((key, label, expr))
    dims = dims[:4]      # 维度封顶 4 个：交叉分组一旦指数爆炸，这张表就没法读了

    measures = _pick(args.get("metrics"), ds["measures"], "指标")
    label_maps = {k: v[2] for k, v in ds["dims"].items() if v[2]}

    c_sql, c_par = _cond(ds["time"], start, end)
    s_sql, s_par = scope.merchant_filter(user, ds["scope"])
    select = [f"{expr} `{key}`" for key, _, expr in dims]
    select += [f"{ds['measures'][m][1]} `{m}`" for m in measures]
    group_by = ", ".join(str(i + 1) for i in range(len(dims)))
    order_by = f"`{measures[0]}` DESC, `{dims[0][0]}` ASC"
    rows = _rows(f"SELECT {', '.join(select)} {ds['from']}"
                 f" WHERE {ds['where']}{c_sql}{s_sql}"
                 f" GROUP BY {group_by} ORDER BY {order_by} LIMIT {CUSTOM_LIMIT}",
                 **c_par, **s_par)

    out = []
    for r in rows:
        row = {}
        for key, _label, _expr in dims:
            raw = r[key]
            lm = label_maps.get(key)
            row[key] = (lm.get(_i(raw), f"未知({_i(raw)})") if lm
                        else (str(raw) if raw is not None else "未填写"))
        for m in measures:
            row[m] = _m(r[m]) if ds["measures"][m][2] == "money" else _i(r[m])
        out.append(row)

    columns = ([(k, l, "text") for k, l, _ in dims]
               + [(m, ds["measures"][m][0], NON_CURRENCY.get(m, ds["measures"][m][2]))
                  for m in measures])
    time_used = next((k for k, _l, _e in dims if k in TIME_DIMS), "none")
    return {"meta": {"start": start.isoformat(), "end": end.isoformat(), "grain": time_used,
                     "dataset": ds_key, "datasetLabel": ds["label"],
                     "truncated": len(out) >= CUSTOM_LIMIT,
                     "scope": "merchant" if scope.merchant_scope(user) is not None
                              else "platform"},
            "table": _table("custom", f"{ds['label']} · 自定义报表", columns, out)}


# ------------------------------------------------------------------ 5. CSV 导出

def data_to_csv(payload: dict, title: str) -> str:
    """把 {meta,kpi,kpiLabels,sections} 或 {meta,table} 摊成一块块带标题的 CSV。

    页面看什么就导出什么：CSV 的行列直接取自 sections 的 columns，
    不再维护第二套「导出专用表头」——两套表头迟早会漂开。
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    meta = payload.get("meta") or {}
    grain_name = {"month": "按月", "day": "按日"}.get(meta.get("grain"), "不按时间分桶")
    w.writerow([title])
    w.writerow(["统计区间", f"{meta.get('start', '')} ~ {meta.get('end', '')}",
                "分桶", grain_name,
                "数据范围", "本商家" if meta.get("scope") == "merchant" else "全平台"])
    w.writerow([])

    kpi, labels = payload.get("kpi") or {}, payload.get("kpiLabels") or {}
    if kpi:
        w.writerow(["核心指标"])
        w.writerow(["指标", "数值"])
        for key, value in kpi.items():
            w.writerow([labels.get(key, key), value])
        w.writerow([])

    tables = payload.get("sections")
    if tables is None:
        tables = [payload.get("table")]
    for t in tables:
        if not t:
            continue
        names = [c["label"] for c in t["columns"]]
        keys = [c["key"] for c in t["columns"]]
        w.writerow([t.get("title", "")])
        w.writerow(names)
        for row in t["rows"]:
            w.writerow(["" if row.get(k) is None else row.get(k) for k in keys])
        if not t["rows"]:
            w.writerow(["（区间内没有数据）"] + [""] * (len(names) - 1))
        w.writerow([])
    return buf.getvalue()


def report_csv(user, args) -> tuple[str, str]:
    """按 report 参数选一条链路出 CSV。返回 (csv 文本, 报表名)。"""
    which = (args.get("report") or "overview").strip()
    if which == "custom":
        data = custom_report(user, args)
        return data_to_csv(data, "自定义报表"), "自定义报表"
    builders = {"overview": (overview, "综合运营报表"),
                "merchant": (merchant_report, "商家经营报表"),
                "airspace": (airspace_report, "空域合规监管报表")}
    if which not in builders:
        raise ApiError(BizCode.PARAM_ERROR,
                       f"不认识的报表：{which}（可选：{'、'.join(builders)}、custom）")
    fn, title = builders[which]
    return data_to_csv(fn(user, args), title), title
