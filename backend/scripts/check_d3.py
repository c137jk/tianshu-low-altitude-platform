"""W3 D3 调度审批域全链路自测。

覆盖（后端状态机唯一入口，全部走 HTTP）：
  商家接单 → 一键推荐 → 保存方案 → 提交申报 → 空管审批 → 下发机长 →
  起飞 → 降落 → 客户确认收货（订单 1→2→3→4→5→6→7，计划 0→1→2→3→4）
  驳回与改后重报、批量审批、商家拒单退款、权限拒绝、机长非本人 403、
  禁飞航线 blocked/409、资源占用、计划列表与机长数据隔离、空域只读接口。

运行（backend 目录）：.venv\\Scripts\\python.exe scripts\\check_d3.py
注意：HTTP 请求不要包在长驻 app_context 里——Flask 测试请求会复用外层
     app context 的 g，导致 current_user 缓存成第一个登录的用户。
测试产生的订单/计划/流水在末尾清理，不污染演示库。
"""
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.adapters.weather import weather_adapter
from app.extensions import db
from app.models import (
    User, Order, PaymentTransaction, Settlement, FlightPlan, ApprovalRecord,
)
from app.services import pricing_service

app = create_app()
passed = 0


def check(name, cond, extra=""):
    global passed
    assert cond, f"✗ {name} {extra}"
    passed += 1
    print(f"✓ {name} {extra}")


client = app.test_client()


def login(username):
    r = client.post("/api/auth/login", json={"username": username, "password": "Admin@123"})
    j = r.get_json()
    assert j["code"] == 0, j
    return {"Authorization": f"Bearer {j['data']['token']}", "Device-Type": "PC"}


def call(method, path, headers, json=None):
    r = client.open(path, method=method, json=json, headers=headers)
    j = r.get_json()
    return j["code"], j.get("message"), j.get("data")


def order_payload(lng1, lat1, lng2, lat2):
    return {
        "senderName": "D3测试寄件人", "senderPhone": "13900000001",
        "senderAddress": "D3测试寄件点", "senderLng": lng1, "senderLat": lat1,
        "receiverName": "D3测试收件人", "receiverPhone": "13900000002",
        "receiverAddress": "D3测试收件点", "receiverLng": lng2, "receiverLat": lat2,
        "goodsType": "文件票据", "weightKg": 2.5, "deliveryType": 1,
    }


def place_paid_order(h_cust, lng1, lat1, lng2, lat2):
    c, m, o = call("POST", "/api/orders", h_cust, order_payload(lng1, lat1, lng2, lat2))
    assert c == 0, (c, m)
    c, m, _ = call("POST", f"/api/orders/{o['id']}/pay", h_cust, {"payMethod": 1})
    assert c == 0, (c, m)
    return o["id"]


# 选一组当前气象适飞的演示坐标（气象 Mock 按经纬度+小时确定性生成）
with app.app_context():
    candidates = [
        # 高德核准的瓯江口可飞示范区内短途点（新月公园/医院/管委会/未来科技城）
        (120.6860, 27.9529, 120.6790, 27.9450),
        (120.6900, 27.9500, 120.6720, 27.9600),
        (120.6950, 27.9480, 120.6790, 27.9450),
        (120.6860, 27.9529, 120.6820, 27.9500),
    ]
    flyable_pair = None
    for lng1, lat1, lng2, lat2 in candidates:
        w = weather_adapter.current((lng1 + lng2) / 2, (lat1 + lat2) / 2)
        if w["flyable"]:
            flyable_pair = (lng1, lat1, lng2, lat2)
            break
    assert flyable_pair, "候选坐标当前全部不适飞（小时级 Mock），稍后重试"
    pilot_id_by_name = {
        u.username: u.id for u in db.session.query(User).filter(
            User.username.in_(["pilot", "pilot02"])).all()}
print(f"· 选用适飞坐标 {flyable_pair}")

h_cust = login("customer")
h_merch = login("merchant")
h_admin = login("admin")
h_pilot = login("pilot")
h_pilot02 = login("pilot02")
test_order_ids = []

# 清理此前失败运行残留的测试单（D3测试寄件人 / D3禁飞测试）
with app.app_context():
    stale = db.session.query(Order.id).filter(
        Order.sender_name.in_(["D3测试寄件人", "D3禁飞测试"])).all()
    stale_ids = [x[0] for x in stale]
    if stale_ids:
        sp = db.session.query(FlightPlan.id).filter(FlightPlan.order_id.in_(stale_ids)).all()
        sp_ids = [x[0] for x in sp]
        if sp_ids:
            ApprovalRecord.query.filter(ApprovalRecord.plan_id.in_(sp_ids)).delete(
                synchronize_session=False)
        FlightPlan.query.filter(FlightPlan.order_id.in_(stale_ids)).delete(
            synchronize_session=False)
        PaymentTransaction.query.filter(
            PaymentTransaction.order_id.in_(stale_ids)).delete(synchronize_session=False)
        Settlement.query.filter(Settlement.order_id.in_(stale_ids)).delete(
            synchronize_session=False)
        Order.query.filter(Order.id.in_(stale_ids)).delete(synchronize_session=False)
        db.session.commit()
        print(f"· 清理上次残留测试单 {len(stale_ids)} 条")

# ========== 一、主流程：申请→审批→调度→监控→结算 ==========
oid = place_paid_order(h_cust, *flyable_pair)
test_order_ids.append(oid)

c, m, _ = call("POST", f"/api/orders/{oid}/accept", h_cust)
check("客户接单被拒(40300)", c == 40300, m)
c, m, _ = call("POST", "/api/dispatch/recommend", h_cust, {"orderId": oid})
check("客户调推荐被拒(40300)", c == 40300, m)

c, m, _ = call("POST", f"/api/orders/{oid}/accept", h_merch)
check("商家接单成功 1→2", c == 0, m)

c, m, _ = call("POST", "/api/dispatch/recommend", h_merch, {"orderId": 999999})
check("不存在订单推荐→40400", c == 40400, m)

c, m, rec = call("POST", "/api/dispatch/recommend", h_merch, {"orderId": oid})
check("推荐成功且未阻断", c == 0 and rec["blocked"] is False,
      f"距离{rec['recommended']['distanceM']}m 航时{rec['recommended']['durationSec']}s "
      f"飞机{rec['recommended']['aircraftId']} 机长{rec['recommended']['pilotId']}")
r = rec["recommended"]
check("推荐给出人机场站", all([r["aircraftId"], r["pilotId"],
                              r["takeoffVertiportId"], r["landingVertiportId"]]))
check("推荐含气象与合规", r["weather"]["flyable"] and "forbidden" in r["compliance"])
check("资源选项含飞机/机长/场站",
      len(rec["options"]["aircraft"]) >= 1 and len(rec["options"]["pilots"]) >= 2
      and len(rec["options"]["vertiports"]) >= 1)

plan_payload = {
    "orderId": oid, "aircraftId": r["aircraftId"], "pilotId": r["pilotId"],
    "takeoffVertiportId": r["takeoffVertiportId"],
    "landingVertiportId": r["landingVertiportId"],
    "altitudeM": 100, "cruiseSpeed": 12, "taskName": "D3自测任务",
}
c, m, plan = call("POST", "/api/flight-plans", h_merch, plan_payload)
check("保存方案成功 计划=0", c == 0 and plan["status"] == 0, m)
pid = plan["id"]
c, _, od = call("GET", f"/api/orders/{oid}", h_merch)
check("方案保存后订单仍=2待调度", od["status"] == 2)

c, m, _ = call("POST", f"/api/flight-plans/{pid}/approve", h_admin, {"opinion": "x"})
check("待申报方案审批→40900", c == 40900, m)

c, m, resp = call("POST", f"/api/flight-plans/{pid}/submit", h_merch)
check("提交空管申报成功", c == 0 and resp["accepted"] is True, f"申报号 {resp.get('applyNo')}")
c, _, plan = call("GET", f"/api/flight-plans/{pid}", h_merch)
check("计划=1待审批", plan["status"] == 1 and bool(plan["submittedAt"]))
c, _, od = call("GET", f"/api/orders/{oid}", h_merch)
check("订单=3待审批", od["status"] == 3)

c, m, _ = call("POST", f"/api/flight-plans/{pid}/approve", h_merch)
check("商家审批被拒(40300)", c == 40300, m)

c, m, _ = call("POST", f"/api/flight-plans/{pid}/approve", h_admin, {"opinion": "同意，注意风速"})
check("审批通过", c == 0, m)
c, _, plan = call("GET", f"/api/flight-plans/{pid}", h_merch)
check("计划=2已批准 订单=4待起飞", plan["status"] == 2 and plan["order"]["status"] == 4)
check("审批流水留痕", len(plan["approvals"]) == 1 and plan["approvals"][0]["result"] == 1
      and plan["approvals"][0]["approverName"] == "平台管理员")

# 推荐机长可能是 pilot 或 pilot02，用计划实际指派的机长来测
_own_h = h_pilot if plan["pilotId"] == pilot_id_by_name["pilot"] else h_pilot02
c, m, _ = call("POST", f"/api/flight-plans/{pid}/takeoff", _own_h)
check("未下发起飞→40900", c == 40900, m)

c, m, resp = call("POST", f"/api/flight-plans/{pid}/assign", h_merch)
check("下发机长成功", c == 0 and resp["delivered"] is True, f"指令号 {resp.get('commandNo')}")
c, _, plan = call("GET", f"/api/flight-plans/{pid}", h_merch)
check("已记下下发时间", plan["status"] == 2 and bool(plan["assignedAt"]))

assigned_pilot_id = plan["pilotId"]
other = h_pilot02 if assigned_pilot_id == pilot_id_by_name["pilot"] else h_pilot
own = h_pilot if assigned_pilot_id == pilot_id_by_name["pilot"] else h_pilot02
c, m, _ = call("POST", f"/api/flight-plans/{pid}/takeoff", other)
check("非指派机长起飞→40300", c == 40300, m)

c, m, _ = call("POST", f"/api/flight-plans/{pid}/takeoff", own)
check("机长确认起飞", c == 0, m)
c, _, plan = call("GET", f"/api/flight-plans/{pid}", own)
check("计划=3执行中 订单=5飞行中", plan["status"] == 3 and plan["order"]["status"] == 5
      and plan["currentBattery"] is not None and plan["lastReportAt"])

c, m, _ = call("POST", f"/api/flight-plans/{pid}/land", h_merch)
check("商家确认降落被拒(40300)", c == 40300, m)

c, m, _ = call("POST", f"/api/flight-plans/{pid}/land", own)
check("机长确认降落", c == 0, m)
c, _, plan = call("GET", f"/api/flight-plans/{pid}", own)
check("计划=4已完成 订单=6已送达", plan["status"] == 4 and plan["order"]["status"] == 6
      and plan["actualTakeoffAt"] and plan["actualLandingAt"])

c, m, _ = call("POST", f"/api/orders/{oid}/confirm", h_cust)
check("客户确认收货", c == 0, m)
c, _, od = call("GET", f"/api/orders/{oid}", h_cust)
check("订单=7已完成", od["status"] == 7)
with app.app_context():
    st = db.session.query(Settlement).filter_by(order_id=oid).first()
    check("完成后生成结算单", st is not None and st.status == 1)

# ========== 二、驳回 → 改后重报 → 批量审批 ==========
oid2 = place_paid_order(h_cust, *flyable_pair)
test_order_ids.append(oid2)
call("POST", f"/api/orders/{oid2}/accept", h_merch)
c, _, rec2 = call("POST", "/api/dispatch/recommend", h_merch, {"orderId": oid2})
assert c == 0 and not rec2["blocked"], (c, rec2.get("reasons"))
r2 = rec2["recommended"]
c, _, p2 = call("POST", "/api/flight-plans", h_merch, {
    "orderId": oid2, **{k: r2[k] for k in (
        "aircraftId", "pilotId", "takeoffVertiportId", "landingVertiportId")}})
pid2 = p2["id"]
call("POST", f"/api/flight-plans/{pid2}/submit", h_merch)
c, m, _ = call("POST", f"/api/flight-plans/{pid2}/reject", h_admin, {})
check("驳回必须填原因", c == 40000, m)
c, m, _ = call("POST", f"/api/flight-plans/{pid2}/reject", h_admin,
               {"reason": "申报高度与限高冲突，请修改"})
check("驳回成功", c == 0, m)
c, _, p2d = call("GET", f"/api/flight-plans/{pid2}", h_merch)
check("计划=5已驳回 订单回=2", p2d["status"] == 5 and p2d["order"]["status"] == 2
      and p2d["rejectReason"])
c, _, p2b = call("POST", "/api/flight-plans", h_merch, {
    "orderId": oid2, **{k: r2[k] for k in (
        "aircraftId", "pilotId", "takeoffVertiportId", "landingVertiportId")},
    "altitudeM": 80})
check("驳回后可重报（复用计划行）", c == 0 and p2b["id"] == pid2 and p2b["status"] == 0)
call("POST", f"/api/flight-plans/{pid2}/submit", h_merch)

oid3 = place_paid_order(h_cust, *flyable_pair)
test_order_ids.append(oid3)
call("POST", f"/api/orders/{oid3}/accept", h_merch)
c, _, rec3 = call("POST", "/api/dispatch/recommend", h_merch, {"orderId": oid3})
assert c == 0 and not rec3["blocked"], (c, rec3.get("reasons"))
r3 = rec3["recommended"]
c, _, p3 = call("POST", "/api/flight-plans", h_merch, {
    "orderId": oid3, **{k: r3[k] for k in (
        "aircraftId", "pilotId", "takeoffVertiportId", "landingVertiportId")}})
pid3 = p3["id"]
call("POST", f"/api/flight-plans/{pid3}/submit", h_merch)
c, m, resp = call("POST", "/api/flight-plans/batch-approve", h_admin,
                  {"ids": [pid2, pid3], "opinion": "批量同意"})
check("批量审批通过", c == 0 and resp["count"] == 2, m)
_, _, p2d = call("GET", f"/api/flight-plans/{pid2}", h_admin)
_, _, p3d = call("GET", f"/api/flight-plans/{pid3}", h_admin)
check("两张计划均=2 订单=4", p2d["status"] == 2 and p3d["status"] == 2
      and p2d["order"]["status"] == 4 and p3d["order"]["status"] == 4)
c, m, _ = call("POST", "/api/flight-plans/batch-approve", h_admin, {"ids": [pid2, pid3]})
check("重复批量审批→40900", c == 40900, m)

# ========== 三、商家拒单与退款 ==========
oid4 = place_paid_order(h_cust, *flyable_pair)
test_order_ids.append(oid4)
c, m, _ = call("POST", f"/api/orders/{oid4}/reject", h_merch, {})
check("拒单不填原因→40000", c == 40000, m)
c, m, _ = call("POST", f"/api/orders/{oid4}/reject", h_merch, {"reason": "超出当前配送范围"})
check("商家拒单成功", c == 0, m)
c, _, od = call("GET", f"/api/orders/{oid4}", h_merch)
check("订单=8已取消", od["status"] == 8)
with app.app_context():
    rf = db.session.query(PaymentTransaction).filter_by(order_id=oid4, direction=2).first()
    check("拒单后生成 Mock 退款流水", rf is not None and rf.status == 4)

# ========== 四、禁飞航线：推荐 blocked、建方案 409 ==========
with app.app_context():
    # 禁飞单无法通过正常试算/下单接口产生（D2 已在入口拦截），这里直接造库模拟历史脏单
    bad = Order(
        order_no=f"TS{datetime.now().strftime('%Y%m%d%H%M%S')}D3B",
        customer_id=3, merchant_id=1, status=2,
        sender_name="D3禁飞测试", sender_phone="SEED:13900000001",
        sender_address="禁飞框内", sender_lng=Decimal("120.65"), sender_lat=Decimal("28.05"),
        receiver_name="D3禁飞测试", receiver_phone="SEED:13900000002",
        receiver_address="禁飞框内", receiver_lng=Decimal("120.70"), receiver_lat=Decimal("28.06"),
        goods_type="文件", weight_kg=Decimal("1.0"), delivery_type=1,
        base_fee=20, airspace_fee=0, urgent_fee=0,
        total_amount=20, pay_amount=20, paid=1,
        pay_method=1, paid_at=datetime.now())
    db.session.add(bad)
    db.session.commit()
    bad_id = bad.id
    test_order_ids.append(bad_id)
c, m, rec_bad = call("POST", "/api/dispatch/recommend", h_merch, {"orderId": bad_id})
check("禁飞航线推荐 blocked", c == 0 and rec_bad["blocked"] is True
      and any("禁飞" in x for x in rec_bad["reasons"]), str(rec_bad["reasons"]))
c, m, _ = call("POST", "/api/flight-plans", h_merch, {
    "orderId": bad_id, "aircraftId": r["aircraftId"], "pilotId": r["pilotId"],
    "takeoffVertiportId": r["takeoffVertiportId"],
    "landingVertiportId": r["landingVertiportId"]})
check("禁飞航线建方案→40900", c == 40900, m)

# ========== 五、列表/隔离/空域接口 ==========
c, _, lst = call("GET", "/api/flight-plans?pageSize=50", h_merch)
check("商家计划列表含状态计数", c == 0 and "statusCounts" in lst and lst["total"] >= 4)
c, _, mine = call("GET", "/api/flight-plans?mine=true&pageSize=50", h_pilot)
check("机长只看本人任务", c == 0 and all(item["pilotName"] == "陈机长" for item in mine["list"]))
c, _, asp = call("GET", "/api/airspaces", h_merch)
check("空域只读接口返回 3 个生效空域", c == 0 and asp["total"] >= 3)
c, _, asp_c = call("GET", "/api/airspaces", h_cust)
check("登录客户也可读空域（门户地图叠加）", c == 0 and asp_c["total"] >= 3)
c, _, _ = call("GET", "/api/airspaces", {})
check("未登录读空域→40100", c == 40100)

# ========== 清理测试数据 ==========
with app.app_context():
    plans = db.session.query(FlightPlan).filter(
        FlightPlan.order_id.in_(test_order_ids)).all()
    plan_ids = [p.id for p in plans]
    if plan_ids:
        ApprovalRecord.query.filter(ApprovalRecord.plan_id.in_(plan_ids)).delete(
            synchronize_session=False)
    FlightPlan.query.filter(FlightPlan.order_id.in_(test_order_ids)).delete(
        synchronize_session=False)
    PaymentTransaction.query.filter(
        PaymentTransaction.order_id.in_(test_order_ids)).delete(synchronize_session=False)
    Settlement.query.filter(Settlement.order_id.in_(test_order_ids)).delete(
        synchronize_session=False)
    Order.query.filter(Order.id.in_(test_order_ids)).delete(synchronize_session=False)
    db.session.commit()
    print(f"✓ 清理测试订单 {len(test_order_ids)} 条、计划 {len(plan_ids)} 张")

print(f"\n全部通过：{passed} 项断言")
