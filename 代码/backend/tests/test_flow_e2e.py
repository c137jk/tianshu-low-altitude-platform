"""全流程 HTTP 走通回归：一条订单从客户下单一直走到发票开具、报表导出。

分工：test_state_machine.py 直接调 service 验业务规则，test_perms_matrix.py 验门禁声明，
各模块用例只在自己的现场里打转。这个文件管的是**模块交接的那一拍**——
上一步落的东西，下一步的入口读不读得到：
  · 领券中心领的券，下单时钱到底减没减、订单上看不看得到券名；
  · 支付成功后，运营侧的支付流水页查不查得到这笔单；
  · 确认收货生成的账单，财务对不得上、对完能不能开票；
  · 机长上报的轨迹，回放页放不放得出来、设备在线页认不认这次心跳；
  · 跑完之后，报表中心的每个数字和 CSV 里的那一行是不是同一笔。
这类缺陷只会出现在链路中间，单模块用例永远碰不到，所以每一步都走真实 HTTP 口，
并且用页面会用到的那个身份：客户下单、运营接单调度、空管审批、机长起降与上报、
财务对账开票、管理员看报表。
"""
import csv
import io
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import FlightPlan, Vertiport
from app.services import geo

CP_NAME = "e2e·无门槛减 5"
CP_AMOUNT = 5.0


def _ok(resp, step):
    """只看 envelope 的 code：HTTP 200 也可能带着业务错误码（参数错、状态冲突）。"""
    body = resp.get_json()
    assert body is not None, f"{step}：响应不是 JSON（HTTP {resp.status_code}）"
    assert body["code"] == 0, f"{step}：HTTP {resp.status_code} code={body['code']} {body['message']}"
    return body.get("data")


def _csv_kpi(resp) -> dict:
    """报表 CSV 的「指标 / 数值」块 → dict（KPI 块在文件最前面，空行结束）。"""
    rows = list(csv.reader(io.StringIO(resp.data.decode("utf-8-sig"))))
    for i, r in enumerate(rows):
        if r[:1] == ["指标"]:
            return {line[0]: line[1] for line in rows[i + 1:]
                    if line and line[0].strip() and len(line) > 1}
    raise AssertionError("CSV 里没有「指标」块")


def _order_payload() -> dict:
    """两个种子场站各偏移一点，和 conftest.make_flight 同一条演示航线，保证过服务范围闸口。"""
    vp1, vp2 = db.session.get(Vertiport, 1), db.session.get(Vertiport, 2)
    s = geo.offset_meters(float(vp1.lng), float(vp1.lat), 60.0, 40.0)
    r = geo.offset_meters(float(vp2.lng), float(vp2.lat), 120.0, -80.0)
    return {
        "senderName": "全流程·寄件", "senderPhone": "13900008801", "senderAddress": "全流程取货点",
        "senderLng": round(s[0], 6), "senderLat": round(s[1], 6),
        "receiverName": "全流程·收件", "receiverPhone": "13900008802", "receiverAddress": "全流程收货点",
        "receiverLng": round(r[0], 6), "receiverLat": round(r[1], 6),
        "goodsType": "文件", "goodsName": "全流程件", "weightKg": 1.0,
        "deliveryType": 1, "orderType": 1, "remark": "pytest 全流程链路",
    }


def _route_frames(plan: FlightPlan, n: int = 4) -> list[dict]:
    """沿计划航线取 n 个点当上报帧：点都落在线段上，偏航判定不会误报。"""
    route = plan.route_json or []
    assert len(route) >= 2, "航线点数不足，检查 build_route 是否还产出折线"
    idxs = [round(i * (len(route) - 1) / (n - 1)) for i in range(n)]
    base = datetime.now().replace(microsecond=0) - timedelta(seconds=5 * n)
    return [{"t": (base + timedelta(seconds=5 * k)).strftime("%Y-%m-%d %H:%M:%S"),
             "lng": route[i]["lng"], "lat": route[i]["lat"],
             "altitude": float(plan.altitude_m or 80), "speed": 12.0,
             "battery": 92 - k} for k, i in enumerate(idxs)]


def _reload_plan(plan_id: int) -> FlightPlan:
    """HTTP 请求写完再读：共用一条 session，不 expire 会拿到请求前那个旧对象。"""
    db.session.expire_all()
    return db.session.get(FlightPlan, plan_id)


def test_http_full_chain_order_to_invoice(api, fresh_user, user_ids):
    # ---------------------------------------------------------- 营销：建券 → 客户领取
    now = datetime.now()
    _ok(api.as_("admin").post("/api/aux/coupons", json={
        "couponName": CP_NAME, "couponType": 1, "threshold": 0, "amount": CP_AMOUNT,
        "totalCount": 20,
        "startAt": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "endAt": (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")}), "运营建券")
    avail = _ok(api.as_("customer").get("/api/aux/coupons", query_string={"scope": "available"}),
                "客户领券中心")
    tpl = next(c for c in avail if c["coupon_name"] == CP_NAME)
    claimed = _ok(api.as_("customer").post(f"/api/aux/coupons/{tpl['id']}/claim"), "客户领券")
    uc_id = claimed["id"]

    # ---------------------------------------------------------- 客户：试算 → 下单 → 支付
    payload = _order_payload()
    plain = _ok(api.as_("customer").post("/api/orders/estimate", json=payload), "试算（不用券）")
    quoted = _ok(api.as_("customer").post("/api/orders/estimate",
                                          json={**payload, "couponId": uc_id}), "试算（用券）")
    # 门槛 0 的满减券：减的必然是那张面额，且试算两次只差这一笔
    assert quoted["discountAmount"] == min(CP_AMOUNT, plain["totalAmount"])
    assert round(plain["totalAmount"] - quoted["totalAmount"], 2) == quoted["discountAmount"]

    order = _ok(api.as_("customer").post("/api/orders", json={**payload, "couponId": uc_id}), "客户下单")
    oid, order_no = order["id"], order["orderNo"]
    assert order["status"] == 0 and order["couponName"] == CP_NAME, "下单后订单上要带出用券结果"
    assert order["totalAmount"] == quoted["totalAmount"], "下单价必须等于试算价，不许两次口径"
    assert order["discountAmount"] == quoted["discountAmount"]

    _ok(api.as_("customer").post(f"/api/orders/{oid}/pay", json={"payMethod": 1}), "客户支付")
    # pay_amount 只有付完款才有数（下单时是 0），后面账单与报表对的都是这个实付额
    paid = _ok(api.as_("customer").get(f"/api/orders/{oid}"), "订单详情（已支付）")
    pay_amount = paid["payAmount"]
    assert pay_amount == quoted["totalAmount"], f"实付 {pay_amount} 与试算 {quoted['totalAmount']} 不符"
    # 运营侧的支付流水台账（/api/bills/payments 是客户自己的账单，不是这一页）
    flows = _ok(api.as_("merchant").get("/api/settlement/payments"), "运营侧支付流水页")
    assert any(f["orderId"] == oid for f in flows["list"]), "这笔支付在流水页查不到"
    assert flows["summary"]["income"] == pytest.approx(pay_amount)

    # ---------------------------------------------------------- 运营：接单 → 推荐 → 方案 → 申报
    _ok(api.as_("merchant").post(f"/api/orders/{oid}/accept"), "运营接单")
    assert _ok(api.as_("merchant").get(f"/api/orders/{oid}"), "订单详情")["status"] == 2

    rec = _ok(api.as_("merchant").post("/api/dispatch/recommend", json={"orderId": oid}), "智能推荐")
    assert not rec["blocked"], f"推荐被闸口拦住：{rec['reasons']}"
    r = rec["recommended"]
    plan = _ok(api.as_("merchant").post("/api/flight-plans", json={
        "orderId": oid, "aircraftId": r["aircraftId"], "pilotId": r["pilotId"],
        "takeoffVertiportId": r["takeoffVertiportId"], "landingVertiportId": r["landingVertiportId"],
        "altitudeM": r["altitudeM"], "cruiseSpeed": r["cruiseSpeed"],
        "taskName": "全流程-e2e 配送", "purpose": "自动化全流程回归"}), "生成调度方案")
    pid = plan["id"]
    assert plan["status"] == 0

    # 审批故意用一个非平台的真实空管账号：approval:handle 不该只有 admin 能使
    atc = fresh_user("e2e_atc", "air_traffic", user_type=5, real_name="全流程·空管")
    _ok(api.as_("merchant").post(f"/api/flight-plans/{pid}/submit"), "提交空管申报")
    _ok(api.as_id(atc.id).post(f"/api/flight-plans/{pid}/approve",
                               json={"opinion": "全流程审批通过"}), "空管审批")
    _ok(api.as_("merchant").post(f"/api/flight-plans/{pid}/assign"), "下发机长")

    cur = _reload_plan(pid)
    pilot_id, aircraft_id = cur.pilot_id, cur.aircraft_id
    assert cur.status == 2 and cur.order_id == oid

    # ---------------------------------------------------------- 机长：起飞 → 上报 → 降落
    _ok(api.as_id(pilot_id).post(f"/api/flight-plans/{pid}/takeoff"), "机长确认起飞")
    frames = _route_frames(_reload_plan(pid))
    ingest = _ok(api.as_id(pilot_id).post("/api/monitor/tracks/batch",
                                          json={"planId": pid, "frames": frames}), "机载轨迹批量上报")
    assert ingest["accepted"] == len(frames) and ingest["source"] == 2
    assert ingest["violations"] == 0, f"沿航线正常上报不该判出违规：{ingest}"

    hb = _ok(api.as_id(pilot_id).post("/api/monitor/devices/heartbeat", json={
        "terminalType": 1, "deviceId": aircraft_id, "lng": frames[-1]["lng"],
        "lat": frames[-1]["lat"], "battery": 88}), "机载心跳")
    devices = _ok(api.as_("admin").get("/api/monitor/devices"), "设备在线页")
    row = next(d for d in devices["list"] if d["terminalType"] == 1 and d["id"] == aircraft_id)
    assert row["stateName"] == hb["stateName"], "心跳报的状态要在设备在线页上一致"

    replay = _ok(api.as_("admin").get(f"/api/monitor/tracks/{pid}"), "轨迹回放页")
    # frames 是帧数、points 才是帧数组：页面上的"共 N 帧"和曲线条数都读这两个键
    assert replay["frames"] == len(frames) and len(replay["points"]) == len(frames)
    assert replay["points"][0]["offsetSec"] == 0
    assert [p["offsetSec"] for p in replay["points"]] == sorted(
        p["offsetSec"] for p in replay["points"]), "回放时间轴必须单调"

    _ok(api.as_id(pilot_id).post(f"/api/flight-plans/{pid}/land"), "机长确认降落")
    assert _reload_plan(pid).status == 4
    assert _ok(api.as_("merchant").get(f"/api/orders/{oid}"), "订单详情")["status"] == 6

    # ---------------------------------------------------------- 收货 → 账单 → 对账 → 发票
    _ok(api.as_("customer").post(f"/api/orders/{oid}/confirm"), "客户确认收货")
    bills = _ok(api.as_("merchant").get("/api/settlement/bills"), "商家账单页")
    bill = next(b for b in bills["list"] if b["orderId"] == oid)
    bid = bill["id"]
    assert bill["status"] == 1, "刚生成的账单应是待结算"
    assert bill["payAmount"] == pay_amount, "账单金额必须等于客户实付"

    recon = _ok(api.as_("admin").post("/api/settlement/bills/reconcile", json={"ids": [bid]}), "财务对账")
    assert recon["matched"] == 1 and recon["diffCount"] == 0, recon
    invoiced = _ok(api.as_("merchant").post(f"/api/settlement/bills/{bid}/invoice",
                                            json={"title": "温州天枢示范运营有限公司"}), "商家申请发票")
    assert invoiced["invoiceStatus"] == 1
    issued = _ok(api.as_("admin").post(f"/api/settlement/bills/{bid}/invoice/issue"), "财务开具发票")
    assert issued["invoiceStatus"] == 2

    # ---------------------------------------------------------- 券的终点：核销绑定在这张单上
    mine = _ok(api.as_("customer").get("/api/aux/coupons/mine", query_string={"status": 2}), "我的券包")
    used = next(c for c in mine["list"] if c["id"] == uc_id)
    assert used["couponName"] == CP_NAME and used["status"] == 2

    # ---------------------------------------------------------- 报表与台账读到同一笔业务
    ov = _ok(api.as_("admin").get("/api/stats/overview"), "统计总览")
    assert ov["kpi"]["orderCount"] == 1 and ov["kpi"]["planCount"] == 1
    assert ov["kpi"]["payAmount"] == pytest.approx(pay_amount)
    assert ov["kpi"]["doneCount"] == 1, "确认收货后这单要算进完成量"

    # 导出：综合报表是聚合口径，不带单号，所以核"这一笔进没进文件"要看 KPI 块和商家维度表
    export = api.as_("admin").get("/api/reports/export", query_string={"report": "overview"})
    assert export.status_code == 200 and export.data.startswith(b"\xef\xbb\xbf"), "CSV 缺 BOM"
    kpi = _csv_kpi(export)
    assert kpi.get("订单量") == "1" and kpi.get("完成量") == "1", kpi

    by_merchant = api.as_("admin").get("/api/reports/export",
                                       query_string={"report": "custom", "dims": "merchant",
                                                     "metrics": "orderCount,payAmount"})
    assert "温州天枢示范运营有限公司".encode() in by_merchant.data, "商家维度导出里没有这家运营公司"

    reg = _ok(api.as_("admin").get("/api/aux/report"), "监管报告")
    assert reg["orderTotal"] == 1 and reg["orderDone"] == 1 and reg["planTotal"] == 1

    # 站内信：机长被下发任务、客户收到进度，两边都得有信
    assert _ok(api.as_id(pilot_id).get("/api/aux/messages"), "机长站内信")["list"], "机长收件箱是空的"
    assert _ok(api.as_("customer").get("/api/aux/messages"), "客户站内信")["list"], "客户收件箱是空的"

    # 计划详情按各身份收窄：非指派机长读不到别人的任务
    other = next(u for u in (user_ids["pilot"], user_ids["pilot02"]) if u != pilot_id)
    assert api.as_id(other).get(f"/api/flight-plans/{pid}").get_json()["code"] != 0


def test_http_violation_to_penalty_to_notice(api, make_flight, fresh_user):
    """违规 → 处罚 → 通知书 → 监管上报：机长没有 monitor:violation，但通知书是发给他的。

    这条链最容易写歪的地方是"用运营权限卡收件人"：处罚动作要 monitor:violation，
    而成文通知书的读者是当事人，两扇门必须是两道。
    """
    _, plan = make_flight("flying")
    base = {"altitude": 80.0, "speed": 12.0, "battery": 80}
    # 往禁飞区里报一帧（种子 AS-WZ-001 龙湾机场净空禁飞区内部），闯区判定由服务端做出
    bad = dict(base, lng=120.8550, lat=27.9150,
               t=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ingest = _ok(api.as_id(plan.pilot_id).post("/api/monitor/tracks/batch",
                                               json={"planId": plan.id, "frames": [bad]}),
                 "上报一帧闯区位置")
    assert ingest["violations"] >= 1, ingest

    ledger = _ok(api.as_("admin").get("/api/aux/violations"), "违规台账")
    rec = next(v for v in ledger if v["plan_id"] == plan.id)
    assert rec["violation_type"] == 1, f"闯禁飞应记为 type=1，实得 {rec['violation_type']}"

    # 处罚之前，通知书必须是"未生成"——凭空出一份文书比报错更糟
    assert api.as_("admin").get(f"/api/aux/violations/{rec['id']}/notice").get_json()["code"] != 0

    atc = fresh_user("e2e_atc2", "air_traffic", user_type=5, real_name="全流程·空管二")
    _ok(api.as_id(atc.id).post(f"/api/aux/violations/{rec['id']}/penalty", json={
        "penaltyType": 2, "penaltyAmount": 800, "penaltyDays": 0,
        "reason": "闯入机场净空禁飞区"}), "空管作出处罚")

    # 当事人没有台账权限也能取到自己那份通知书
    notice = _ok(api.as_id(plan.pilot_id).get(f"/api/aux/violations/{rec['id']}/notice"), "机长调阅通知书")
    assert notice["violationNo"] == rec["violation_no"]
    assert "800" in notice["penaltyName"] and notice["penaltyReason"] == "闯入机场净空禁飞区"
    assert notice["appeal"], "通知书要写明复议途径"

    reg = _ok(api.as_("admin").get("/api/aux/report"), "监管报告")
    assert reg["violationTotal"] >= 1 and reg["violationPunished"] >= 1
