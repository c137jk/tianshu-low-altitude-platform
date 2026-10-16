"""B6-1 统计报表中心（契约 D8 / 设计06 M-12）：区间口径、数据范围、页面与导出同一个数。

差距分析里这一块的原始缺口是"统计只有一个当前快照，没有区间、没有维度、没有导出"，
而快照式统计最容易犯的不是算错一次，是**页面一套口径、导出另一套口径**。
所以用例钉六件事：
  1. 门禁分两扇门：report:view 管"看一眼"，report:export 管"把整份经营数据带走"；
  2. 区间是闭区间且右边界补到次日零点，缺省最近 30 天并把空桶补 0（图表不许画断线）；
  3. 时间轴按表固定：订单 created_at、计划 submitted_at（空则退回 created_at）、
     违规 violation_time、结算 created_at、应急 created_at —— 挪走一张表的时间只该影响那张表；
  4. 商家员工看到的每一个数字都只含本商家，连 zoneUsage 这种 Python 侧算的表也一样；
  5. CSV 里的每个数字都来自页面同一个 service 入口，逐指标比对；
  6. 维度/指标/数据集白名单：请求参数进不了 SQL，也不许出现"页面选得了、后端说不认识"。

跑法：TEST_DB_RESET=0 .venv/Scripts/python.exe -m pytest tests/test_stats_report.py -q
"""
import csv
import io
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.extensions import db
from app.models import User
from app.services import compliance_monitor, order_service, stats_service

NO_FLY_POINT = (120.8550, 27.9150)      # 龙湾机场净空禁飞区（种子 id=1）内部
READ_URLS = ("/api/stats/overview", "/api/stats/merchant", "/api/stats/airspace",
             "/api/reports/fields", "/api/reports/custom?dims=day")
TODAY = date.today().isoformat()


def _data(resp) -> dict:
    body = resp.get_json()
    assert body["code"] == 0, body
    return body["data"]


def _kpi(api, who="admin", **qs) -> dict:
    return _data(api.as_(who).get("/api/stats/overview", query_string=qs))["kpi"]


def _section(payload, key) -> dict:
    for s in payload["sections"]:
        if s["key"] == key:
            return s
    raise AssertionError(f"缺 section {key}：{[s['key'] for s in payload['sections']]}")


def _check_shape(payload) -> dict:
    """报表输出统一形状：每个 kpi 有中文名和展示类型、每张表的列定义和数据列一一对应。

    前端卡片、图表轴、CSV 表头都读这份元数据，对不上就会导出成"页面叫实付金额、
    文件里叫 pay_amount"的两张皮；kind 缺失时前端只能按字段名后缀猜哪列是钱，
    货量 kg 会被印成 ¥12.50。
    """
    if "kpi" in payload:
        assert set(payload["kpiLabels"]) == set(payload["kpi"]), "KPI 缺中文名"
        assert set(payload["kpiKinds"]) == set(payload["kpi"]), "KPI 缺展示类型"
        assert all(v is not None for v in payload["kpiLabels"].values())
    tables = payload.get("sections") or [payload.get("table")]
    for s in tables:
        keys = [c["key"] for c in s["columns"]]
        assert len(keys) == len(set(keys)), f"{s['key']} 列键重复"
        assert all(c["label"] for c in s["columns"])
        assert all(c["kind"] in ("text", "int", "money", "num") for c in s["columns"]), \
            f"{s['key']} 列缺展示类型"
        for r in s["rows"]:
            assert set(r) == set(keys), f"{s['key']} 的列定义和数据对不上：{set(r) ^ set(keys)}"
    return payload


def _csv_rows(resp) -> list[list[str]]:
    """下载响应 → 原始行（空行保留：它是分块边界，滤掉就把 KPI 块和明细块粘一起了）。"""
    assert resp.data.startswith(b"\xef\xbb\xbf"), "CSV 缺 BOM，Excel 双击会是乱码"
    return list(csv.reader(io.StringIO(resp.data.decode("utf-8-sig"))))


def _csv_kpi(rows) -> dict:
    for i, r in enumerate(rows):
        if r[:1] == ["指标"]:
            out = {}
            for line in rows[i + 1:]:
                if not line or not line[0].strip():
                    break
                out[line[0]] = line[1]
            return out
    raise AssertionError("CSV 里没有「核心指标」块")


def _csv_block(rows, first_header: str) -> tuple[list[str], list[list[str]]]:
    for i, r in enumerate(rows):
        if r[:1] == [first_header]:
            body = []
            for line in rows[i + 1:]:
                if not line or not line[0].strip():
                    break
                body.append(line)
            return r, body
    raise AssertionError(f"CSV 里没有以 {first_header} 开头的表头")


def _confirm_receipt(order, user_ids):
    """送达后确认收货：这是唯一会同时产出订单/计划/结算三张表的链路终点。"""
    order_service.confirm_receipt(order, db.session.get(User, user_ids["customer"]))
    return order


# ---------------------------------------------------------------- 违规/结算造数

@pytest.fixture()
def full_chain(make_flight, user_ids):
    """一条"下单→支付→申报→审批→执行→落地→确认收货"的链路 + 一条已处罚的违规。

    数字全部走服务层状态机（不手搓 INSERT），报表算错时能确定是报表的问题而不是数据的问题。
    """
    order, plan = make_flight("landed")
    _confirm_receipt(order, user_ids)
    rec = compliance_monitor.record_violation(plan, {"type": 1, "desc": "报表用例闯入禁飞"},
                                              *NO_FLY_POINT)
    assert rec is not None
    db.session.execute(text("UPDATE violation_record SET penalty_type=2, penalty_amount=500.00 "
                            "WHERE id=:i"), {"i": rec.id})
    db.session.commit()
    return order, plan, rec


# ---------------------------------------------------------------- 1. 形状与空窗口

def test_empty_window_is_zero_filled_not_empty(api):
    """一条数据都没有时要给 30 个全 0 的桶，不能给空数组。

    空数组在前端画出来是断线/空白图，运维第一反应是"链路坏了"而不是"今天没单"。
    """
    d = _check_shape(_data(api.as_("admin").get("/api/stats/overview")))
    assert d["meta"]["grain"] == "day" and d["meta"]["scope"] == "platform"
    start = date.fromisoformat(d["meta"]["start"])
    assert d["meta"]["end"] == TODAY
    trend = _section(d, "trend")
    assert [r["bucket"] for r in trend["rows"]] == [
        (start + timedelta(days=n)).isoformat() for n in range(30)]
    assert all(v == 0 for v in d["kpi"].values()), d["kpi"]
    # 零分母不能把报表炸成 500：完成率/通过率/冲突率都是 0.0
    assert d["kpi"]["doneRate"] == 0.0 and d["kpi"]["approvalRate"] == 0.0


def test_empty_merchant_and_airspace_reports_survive_zero(api):
    m = _check_shape(_data(api.as_("admin").get("/api/stats/merchant")))
    assert m["kpi"]["merchantCount"] == 0
    assert _section(m, "merchantDetail")["rows"][-1]["merchantName"].startswith("合计（0 家）")
    a = _check_shape(_data(api.as_("admin").get("/api/stats/airspace")))
    assert a["kpi"]["conflictRate"] == 0.0 and a["kpi"]["avgApproveMin"] == 0.0


# ---------------------------------------------------------------- 2. 口径：谁贡献了哪个数字

def test_completed_chain_lands_in_every_kpi(api, full_chain):
    order, _plan, _rec = full_chain
    d = _check_shape(_data(api.as_("admin").get("/api/stats/overview")))
    k = d["kpi"]
    assert (k["orderCount"], k["doneCount"], k["cancelCount"], k["planCount"]) == (1, 1, 0, 1)
    assert k["doneRate"] == 100.0 and k["approvalRate"] == 100.0
    assert k["flightCount"] == 1 and k["customerCount"] == 1
    assert k["payAmount"] == pytest.approx(float(order.pay_amount))
    assert k["violationCount"] == 1 and k["punishRate"] == 100.0 and k["penaltyAmount"] == 500.0
    assert k["settlementCount"] == 1
    # 账单口径：商家收入 = 应结 − 平台抽成，两个数必须能加回实付
    assert k["platformFee"] + k["merchantIncome"] == pytest.approx(k["payAmount"])
    assert [(r["dim"], r["orderCount"]) for r in _section(d, "statusDist")["rows"]] == [("已完成", 1)]
    assert _section(d, "goodsDist")["rows"][0]["dim"] == "文件"
    assert _section(d, "violationDist")["rows"][0]["dim"] == "闯禁飞区"


def test_unpaid_orders_do_not_move_money(api, make_flight):
    """草稿单进订单量，不进金额：报表里的钱只统计已支付（paid=1）。"""
    draft, _ = make_flight("draft")
    paid, _ = make_flight("paid")
    k = _kpi(api)
    assert k["orderCount"] == 2 and k["doneCount"] == 0
    assert k["payAmount"] == pytest.approx(float(paid.pay_amount))
    assert k["discountAmount"] == pytest.approx(float(paid.discount_amount))
    assert k["avgOrderAmount"] == pytest.approx(float(paid.total_amount))
    assert k["weightKg"] == pytest.approx(float(draft.weight_kg) + float(paid.weight_kg))


def test_refunded_orders_leave_the_money_kpis(api, make_flight, user_ids):
    """付过款又被作废的单：订单量留着、钱必须走。

    orders.paid 退款后仍是 1（退款只是多一条 direction=2 的流水），只看 paid=1
    会把退掉的钱永久算在营收里 —— 这是报表里最容易长期错下去、又最不容易被发现的一个数。
    """
    keep, _ = make_flight("paid")
    gone, _ = make_flight("paid")
    order_service.cancel_order(gone, db.session.get(User, user_ids["customer"]), "不想要了")
    k = _kpi(api)
    assert (k["orderCount"], k["cancelCount"], k["cancelRate"]) == (2, 1, 50.0)
    assert k["payAmount"] == pytest.approx(float(keep.pay_amount))
    assert k["platformFee"] == pytest.approx(float(keep.pay_amount) * 0.10, abs=0.01)
    row = _data(api.as_("admin").get("/api/stats/merchant"))
    assert row["kpi"]["payAmount"] == pytest.approx(float(keep.pay_amount))
    assert row["kpi"]["merchantIncome"] == pytest.approx(
        float(keep.pay_amount) * 0.90, abs=0.01)
    # 已取消的单在状态分布里仍然看得见，但它那一行的实付是 0
    dist = {r["dim"]: r for r in _section(_data(api.as_("admin").get("/api/stats/overview")),
                                          "statusDist")["rows"]}
    assert (dist["已取消"]["orderCount"], dist["已取消"]["payAmount"]) == (1, 0.0)
    assert dist["待接单"]["payAmount"] == pytest.approx(float(keep.pay_amount))


def test_time_axis_is_per_table(api, full_chain, user_ids):
    """把订单时间挪到 45 天前：只有订单口径掉出窗口，计划/违规/结算都还在。

    这条钉的是"四张表各按自己的业务时刻落桶"。写成统一一个 created_at 的话，
    跨月补录的订单会把整个区间的违规数一起带偏，而且从图上看不出来。
    """
    old = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    db.session.execute(text("UPDATE orders SET created_at=:t WHERE id=:i"),
                       {"t": f"{old} 10:00:00", "i": full_chain[0].id})
    db.session.commit()
    default = _kpi(api)                                   # 默认最近 30 天
    assert (default["orderCount"], default["payAmount"]) == (0, 0.0)
    assert (default["planCount"], default["violationCount"], default["settlementCount"]) == (1, 1, 1)
    back = _kpi(api, start=old)                           # 窗口拉长就回来了
    assert back["orderCount"] == 1 and back["payAmount"] > 0


def test_unsubmitted_plan_falls_back_to_created_at(api, make_flight):
    """没申报过的计划也得有落点：submitted_at 为空退回 created_at，否则它永远不出现在任何区间。
    但空管口径只数真申报过的，两边不能混。"""
    _order, plan = make_flight("planned")
    assert plan.submitted_at is None
    assert _kpi(api)["planCount"] == 1
    assert _data(api.as_("admin").get("/api/stats/airspace"))["kpi"]["submitCount"] == 0


def test_range_is_closed_and_partitions_without_gap(api, make_flight):
    """昨天一条、今天一条：两个单日窗口相加必须等于两天窗口，边界不重不漏。"""
    o1, _ = make_flight("draft")
    o2, _ = make_flight("draft")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.session.execute(text("UPDATE orders SET created_at=:x WHERE id=:i"),
                       {"x": f"{yesterday} 23:30:00", "i": o1.id})
    db.session.commit()

    def n(start, end):
        return _kpi(api, start=start, end=end)["orderCount"]

    assert (n(yesterday, yesterday), n(TODAY, TODAY), n(yesterday, TODAY)) == (1, 1, 2)
    trend = _section(_data(api.as_("admin").get(
        "/api/stats/overview", query_string={"start": yesterday, "end": TODAY})), "trend")
    assert [r["bucket"] for r in trend["rows"]] == [yesterday, TODAY]
    assert sum(r["orderCount"] for r in trend["rows"]) == 2


def test_month_grain_buckets_across_year(api, make_flight):
    make_flight("paid")
    d = _data(api.as_("admin").get("/api/stats/overview", query_string={
        "grain": "month", "start": "2025-11-01", "end": "2026-02-15"}))
    assert [r["bucket"] for r in _section(d, "trend")["rows"]] == [
        "2025-11", "2025-12", "2026-01", "2026-02"]
    recent = _data(api.as_("admin").get("/api/stats/overview", query_string={"grain": "month"}))
    rows = _section(recent, "trend")["rows"]
    assert rows[-1]["bucket"] == TODAY[:7] and rows[-1]["orderCount"] == 1
    assert all(len(b) == 7 for b in [r["bucket"] for r in rows])


def test_emergency_kpi_opens_and_closes(api, make_flight):
    """应急两个数是"存量"口径：未解除的条数要能随降落归零，否则大屏会一直亮着。"""
    _order, plan = make_flight("flying")
    assert compliance_monitor.raise_emergency(plan, 2, 2, "闯入禁飞区") is not None
    assert compliance_monitor.raise_emergency(plan, 3, 1, "低电量") is not None
    k = _kpi(api)
    assert (k["emergencyCount"], k["emergencyOpen"]) == (2, 2)
    compliance_monitor.resolve_emergencies(plan, measure="安全降落")
    db.session.commit()          # 解除是调用方提交的（降落链路里由 D3 提交）
    after = _kpi(api)
    assert (after["emergencyCount"], after["emergencyOpen"]) == (2, 0)


# ---------------------------------------------------------------- 3. 参数校验

@pytest.mark.parametrize("qs,keyword", [
    ({"start": "2026-1-2"}, "日期"),
    ({"start": "2026-13-01"}, "日期"),          # 过了正则但不是合法日期
    ({"start": "2026-09-10", "end": "2026-09-01"}, "不能晚于"),
    ({"start": "2025-01-01", "end": "2026-09-01"}, "366"),
])
def test_bad_ranges_are_400_on_every_report(api, qs, keyword):
    for url in ("/api/stats/overview", "/api/stats/merchant",
                "/api/stats/airspace", "/api/reports/custom"):
        r = api.as_("admin").get(url, query_string=qs).get_json()
        assert r["code"] == 40000 and keyword in r["message"], (url, r)
    bad = api.as_("admin").get("/api/reports/export", query_string=qs).get_json()
    assert bad["code"] == 40000


def test_field_pool_is_the_backend_whitelist(api):
    """页面字段池和后端白名单必须是同一份：前端选得出的组合后端不能说"不认识"。"""
    pool = _data(api.as_("admin").get("/api/reports/fields"))
    assert [d["key"] for d in pool["datasets"]] == list(stats_service.DATASETS)
    assert [t["key"] for t in pool["timeDims"]] == ["day", "month"]
    for ds in pool["datasets"]:
        assert ds["dims"] and ds["measures"], ds["key"]
        r = api.as_("admin").get("/api/reports/custom", query_string={
            "dataset": ds["key"], "dims": ds["dims"][0]["key"],
            "metrics": ds["measures"][0]["key"]}).get_json()
        assert r["code"] == 0, (ds["key"], r)


@pytest.mark.parametrize("qs,keyword", [
    ({"dataset": "order", "dims": "sender_phone"}, "不支持的维度"),
    ({"dataset": "order", "dims": "day,phone_cipher"}, "不支持的维度"),
    ({"dataset": "order", "dims": "day", "metrics": "salary"}, "不支持的指标"),
    ({"dataset": "salary", "dims": "day"}, "不认识的数据集"),
    ({"dataset": "order", "dims": ""}, "至少选一个维度"),
])
def test_custom_report_rejects_unknown_fields(api, qs, keyword):
    """维度/指标/数据集三层白名单：列名（含手机号密文列）不能从请求参数进 SQL。"""
    r = api.as_("admin").get("/api/reports/custom", query_string=qs).get_json()
    assert r["code"] == 40000 and keyword in r["message"], r


def test_custom_report_bucket_follows_the_chosen_dim(api, make_flight):
    make_flight("paid")
    day = _data(api.as_("admin").get("/api/reports/custom",
                                     query_string={"dims": "day", "metrics": "orderCount"}))
    assert day["meta"]["grain"] == "day"
    assert [c["label"] for c in day["table"]["columns"]] == ["按日", "订单量"]
    assert day["table"]["rows"][0]["day"] == TODAY

    month = _data(api.as_("admin").get("/api/reports/custom",
                                       query_string={"dims": "month", "metrics": "payAmount"}))
    assert month["meta"]["grain"] == "month"
    assert [c["label"] for c in month["table"]["columns"]] == ["按月", "实付金额"]
    assert month["table"]["rows"][0]["month"] == TODAY[:7]

    plain = _data(api.as_("admin").get("/api/reports/custom", query_string={"dims": "goodsType"}))
    assert plain["meta"]["grain"] == "none"          # 没选时间维度就别声称按了什么分桶
    # 不写 metrics 就是全量指标：默认值由字段池给，前端不用自己维护一份清单
    whole = _data(api.as_("admin").get("/api/reports/custom", query_string={"dims": "day"}))
    assert [c["key"] for c in whole["table"]["columns"]] == \
        ["day", *stats_service.DATASETS["order"]["measures"].keys()]


def test_custom_report_dedupes_and_caps_dims(api, make_flight):
    make_flight("paid")
    dup = _data(api.as_("admin").get("/api/reports/custom",
                                     query_string={"dims": "day,day", "metrics": "orderCount"}))
    assert [c["key"] for c in dup["table"]["columns"]] == ["day", "orderCount"]
    cn = _data(api.as_("admin").get("/api/reports/custom",
                                    query_string={"dims": "goodsType，merchant",
                                                  "metrics": "orderCount"}))
    assert [c["key"] for c in cn["table"]["columns"]][:2] == ["goodsType", "merchant"]
    many = _data(api.as_("admin").get("/api/reports/custom", query_string={
        "dims": "day,merchant,goodsType,deliveryType,status", "metrics": "orderCount"}))
    dim_cols = [c for c in many["table"]["columns"] if c["key"] != "orderCount"]
    assert len(dim_cols) == 4, "维度封顶 4 个：再交叉下去这张表没法读"


# ---------------------------------------------------------------- 4. 数据范围

def test_merchant_identity_sees_only_its_own_numbers(api, make_flight, second_merchant):
    """同一条 SQL 换个身份跑：商家员工的每个数字都只含本商家，含 Python 侧算的穿行表。"""
    o1, p1 = make_flight("paid")
    o2, p2 = make_flight("planned")        # 第二条带计划，挪商家时要连计划一起挪
    db.session.execute(text("UPDATE orders SET merchant_id=2 WHERE id=:i"), {"i": o2.id})
    db.session.execute(text("UPDATE flight_plan SET merchant_id=2 WHERE id=:i"), {"i": p2.id})
    db.session.commit()

    admin = _check_shape(_data(api.as_("admin").get("/api/stats/overview")))
    mine = _check_shape(_data(api.as_("merchant").get("/api/stats/overview")))
    assert admin["meta"]["scope"] == "platform" and mine["meta"]["scope"] == "merchant"
    assert (admin["kpi"]["orderCount"], mine["kpi"]["orderCount"]) == (2, 1)
    # 计划走的是另一张表的 merchant_id，收窄必须各判各的：搬走计划后本商家就看不到了
    assert (admin["kpi"]["planCount"], mine["kpi"]["planCount"]) == (1, 0)
    assert mine["kpi"]["payAmount"] == pytest.approx(float(o1.pay_amount))
    assert admin["kpi"]["payAmount"] > mine["kpi"]["payAmount"]

    am = _data(api.as_("admin").get("/api/stats/merchant"))
    assert [r["merchantName"] for r in _section(am, "merchantDetail")["rows"]] == [
        "温州天枢示范运营有限公司", "测试·隔壁市运营公司", "合计（2 家）"]
    mm = _data(api.as_("merchant").get("/api/stats/merchant"))
    assert mm["kpi"]["merchantCount"] == 1 and mm["kpi"]["orderCount"] == 1
    assert [r["merchantName"] for r in _section(mm, "merchantDetail")["rows"]] == [
        "温州天枢示范运营有限公司", "合计（1 家）"]
    # 自定义报表也不能漏：换个入口还是同一道闸门
    cc = _data(api.as_("merchant").get("/api/reports/custom", query_string={"dims": "merchant"}))
    assert [r["merchant"] for r in cc["table"]["rows"]] == ["温州天枢示范运营有限公司"]


def test_zone_usage_keeps_unrelated_zones_out_of_merchant_view(api, make_flight):
    _order, plan = make_flight("flying")
    compliance_monitor.record_violation(plan, {"type": 1, "desc": "闯禁飞"}, *NO_FLY_POINT)
    db.session.commit()
    admin = _data(api.as_("admin").get("/api/stats/airspace"))
    rows = _section(admin, "zoneUsage")["rows"]
    # 条数跟着种子库走，不写死：§7.8 补了 AS-WZ-004 鹿城禁飞区之后种子是 4 条，
    # 这里要断的是"台账里的区一条不漏地进报表"，不是"温州恰好有三块区"。
    seed_zones = db.session.execute(text(
        "SELECT COUNT(*) FROM airspace WHERE deleted = 0")).scalar()
    assert len(rows) == seed_zones and admin["kpi"]["zoneTotal"] == seed_zones
    assert {r["type"] for r in rows} == {"禁飞区", "限飞区", "可飞区"}
    assert all(r["status"] == "生效/已通过" for r in rows)   # 台账状态带审核口径
    mine = _data(api.as_("merchant").get("/api/stats/airspace"))
    kept = _section(mine, "zoneUsage")["rows"]
    assert kept and all(r["planCount"] or r["violationCount"] for r in kept), \
        "商家侧只保留与本商家有关的空域"
    assert len(kept) < len(rows)


def test_airspace_report_counts_and_ratios(api, make_flight):
    """空管口径：通过率看 approve_result，冲突率分母用实飞架次而不是违规条数。"""
    _order, plan = make_flight("flying")
    db.session.execute(text("UPDATE flight_plan SET approved_at=DATE_ADD(submitted_at, "
                            "INTERVAL 5 MINUTE) WHERE id=:i"), {"i": plan.id})
    db.session.commit()
    assert compliance_monitor.record_violation(plan, {"type": 1, "desc": "闯禁飞"},
                                              *NO_FLY_POINT) is not None
    assert compliance_monitor.record_violation(plan, {"type": 2, "desc": "偏航"},
                                              *NO_FLY_POINT) is not None
    d = _check_shape(_data(api.as_("admin").get("/api/stats/airspace")))
    k = d["kpi"]
    assert (k["submitCount"], k["approvedCount"], k["approvalRate"]) == (1, 1, 100.0)
    assert k["avgApproveMin"] == 5.0
    assert k["flightCount"] == 1
    # 一次任务刷出两条违规，只算一次冲突
    assert (k["violationCount"], k["conflictRate"]) == (2, 100.0)
    assert _section(d, "trend")["rows"][-1]["submitCount"] == 1
    assert _section(d, "trend")["rows"][-1]["violationCount"] == 2
    assert _section(d, "violationDist")["rows"][0]["planCount"] == 1
    assert k["zoneActiveCount"] >= 1



# ---------------------------------------------------------------- 5. 门禁

def test_read_endpoints_require_report_view(api):
    for who in ("customer", "pilot"):
        for url in READ_URLS:
            r = api.as_(who).get(url)
            assert r.status_code == 403 and "report:view" in r.get_json()["message"], (who, url)
    for who in ("admin", "merchant"):
        for url in READ_URLS:
            assert api.as_(who).get(url).get_json()["code"] == 0, (who, url)


@pytest.fixture()
def finance_readonly(app_ctx, fresh_user):
    """财务只留 report:view：验证两扇门真的分开。

    _truncate 不动 role_permission（它是种子数据），所以摘掉的授权必须在 teardown 还回去，
    不然下一次的权限矩阵会莫名多出一个 403。
    """
    pid = db.session.execute(text("SELECT id FROM permission WHERE perm_code='report:export'")).scalar()
    assert pid, "report:export 权限点没建出来，检查迁移 p16"
    db.session.execute(text("DELETE FROM role_permission WHERE role_id=6 AND permission_id=:p"),
                       {"p": pid})
    db.session.commit()
    try:
        yield fresh_user("u_fin_ro", "finance")
    finally:
        db.session.execute(text("INSERT IGNORE INTO role_permission (role_id, permission_id) "
                                "VALUES (6,:p)"), {"p": pid})
        db.session.commit()


def test_view_permission_does_not_imply_export(api, finance_readonly, make_flight):
    make_flight("paid")
    caller = api.as_id(finance_readonly.id)
    body = caller.get("/api/stats/overview").get_json()
    assert body["code"] == 0 and body["data"]["kpi"]["orderCount"] == 1
    r = caller.get("/api/reports/export")
    assert r.status_code == 403 and "report:export" in r.get_json()["message"]
    # 403 必须挡在取数之前：导出数据不能已经被算出来又被写进任何地方
    assert caller.get("/api/reports/custom", query_string={"dims": "day"}).get_json()["code"] == 0


# ---------------------------------------------------------------- 6. 导出与页面同源

def test_exported_kpi_rows_equal_the_page_line_by_line(api, full_chain):
    page = _check_shape(_data(api.as_("admin").get("/api/stats/overview")))
    resp = api.as_("admin").get("/api/reports/export", query_string={"report": "overview"})
    assert resp.status_code == 200 and "text/csv" in resp.content_type
    assert "attachment" in resp.headers["Content-Disposition"]
    rows = _csv_rows(resp)
    assert rows[0] == ["综合运营报表"]
    assert rows[1][:2] == ["统计区间", f"{page['meta']['start']} ~ {page['meta']['end']}"]
    assert "全平台" in rows[1] and "按日" in rows[1]

    kpi = _csv_kpi(rows)
    assert kpi == {page["kpiLabels"][k]: str(v) for k, v in page["kpi"].items()}

    head, body = _csv_block(rows, "日期")
    trend = _section(page, "trend")
    assert head == [c["label"] for c in trend["columns"]]
    assert body == [["" if r[c["key"]] is None else str(r[c["key"]]) for c in trend["columns"]]
                    for r in trend["rows"]]


@pytest.mark.parametrize("report,title,first_head", [
    ("merchant", "商家经营报表", "商家"),
    ("airspace", "空域合规监管报表", "日期"),
    ("overview", "综合运营报表", "日期"),
])
def test_export_titles_follow_the_report(api, full_chain, report, title, first_head):
    resp = api.as_("admin").get("/api/reports/export", query_string={"report": report})
    rows = _csv_rows(resp)
    assert rows[0] == [title]
    assert rows[1][-1] in ("全平台", "本商家")
    _csv_block(rows, first_head)


def test_export_custom_uses_the_same_columns(api, make_flight):
    make_flight("paid")
    qs = {"report": "custom", "dataset": "order", "dims": "day,merchant",
          "metrics": "orderCount,payAmount"}
    rows = _csv_rows(api.as_("admin").get("/api/reports/export", query_string=qs))
    assert rows[0] == ["自定义报表"]
    assert "按日" in rows[1]
    head, body = _csv_block(rows, "按日")
    assert head == ["按日", "商家", "订单量", "实付金额"]
    assert len(body) == 1 and body[0][1] == "温州天枢示范运营有限公司"
    assert body[0][2] == "1"


def test_export_marks_empty_sections(api):
    """没有数据也要把表头和"确实没有数据"写进文件：否则打开 CSV 会以为是导出坏了。"""
    rows = _csv_rows(api.as_("admin").get("/api/reports/export", query_string={"report": "overview"}))
    assert any(r and r[0] == "（区间内没有数据）" for r in rows)


def test_export_rejects_unknown_report(api):
    r = api.as_("admin").get("/api/reports/export", query_string={"report": "salary"}).get_json()
    assert r["code"] == 40000 and "不认识的报表" in r["message"]


def test_filename_carries_the_range(api, make_flight):
    from urllib.parse import unquote

    make_flight("paid")
    r = api.as_("admin").get("/api/reports/export", query_string={
        "report": "merchant", "start": "2026-09-01", "end": TODAY})
    name = unquote(r.headers["Content-Disposition"])
    assert f'filename="商家经营报表-2026-09-01_{TODAY}.csv"' in name
    assert "filename*=UTF-8''" in name        # 中文名走 RFC 5987，否则 Safari 存成乱码文件
    plain = unquote(api.as_("admin").get("/api/reports/export").headers["Content-Disposition"])
    assert 'filename="综合运营报表.csv"' in plain
