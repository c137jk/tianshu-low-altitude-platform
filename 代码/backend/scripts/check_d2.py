"""W2 D2 全链路自测：geo 几何 + 试算/下单/支付/取消退款/账单/权限/改密。

运行（backend 目录）：.venv\\Scripts\\python.exe scripts\\check_d2.py
测试产生的订单/流水在末尾清理，不污染演示库。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Order, PaymentTransaction, Airspace
from app.services import geo

app = create_app()
passed = 0


def check(name, cond, extra=""):
    global passed
    assert cond, f"✗ {name} {extra}"
    passed += 1
    print(f"✓ {name} {extra}")


with app.app_context():
    # ---------- geo 单测（用库里真实空域） ----------
    asps = db.session.query(Airspace).filter_by(status=1, deleted=0).all()
    by_name = {a.airspace_name: a for a in asps}
    ring = by_name["瓯江口低空可飞示范区"].geometry_json["coordinates"][0]
    check("点在可飞区内", geo.point_in_polygon(120.93, 27.99, ring))
    check("点在可飞区外", not geo.point_in_polygon(120.50, 27.50, ring))
    d = geo.haversine_m(120.93, 27.99, 120.94, 27.99)
    check("Haversine 0.01°经度≈987m", 900 < d < 1100, f"实测 {d:.0f}m")
    samples = geo.sample_segment((120.84, 27.91), (120.87, 27.92), 48)
    cross = geo.check_route_against_airspaces(samples, asps)
    check("航线穿越禁飞区被识别", any("禁飞" in a.airspace_name for a in cross["forbidden"]))
    samples_ok = geo.sample_segment((120.92, 27.97), (120.97, 28.01), 48)
    cross_ok = geo.check_route_against_airspaces(samples_ok, asps)
    check("瓯江口内航线无禁飞", len(cross_ok["forbidden"]) == 0)

client = app.test_client()


def login(username):
    r = client.post("/api/auth/login", json={"username": username, "password": "Admin@123"})
    j = r.get_json()
    assert j["code"] == 0, j
    return {"Authorization": f"Bearer {j['data']['token']}", "Device-Type": "PC"}


def body(r):
    j = r.get_json()
    return j["code"], j.get("message"), j.get("data")


SAFE_ORDER = {
    "senderName": "测试寄件人", "senderPhone": "13900000001",
    "senderAddress": "瓯江口示范站", "senderLng": 120.92, "senderLat": 27.97,
    "receiverName": "测试收件人", "receiverPhone": "13900000002",
    "receiverAddress": "瓯江口灵昆片区", "receiverLng": 120.97, "receiverLat": 28.01,
    "goodsType": "文件", "weightKg": 2.5, "deliveryType": 1,
}

with app.app_context():
    cust_h = login("customer")
    merch_h = login("merchant")

    # 1. 试算（安全航线）
    c, m, data = body(client.post("/api/orders/estimate", json=SAFE_ORDER, headers=cust_h))
    check("安全航线试算成功", c == 0 and data["totalAmount"] > 0,
          f"距离{data['distanceM']}m 合计{data['totalAmount']}元")

    # 2. 试算穿禁飞
    bad = dict(SAFE_ORDER, senderLng=120.84, senderLat=27.91, receiverLng=120.87, receiverLat=27.92)
    c, m, _ = body(client.post("/api/orders/estimate", json=bad, headers=cust_h))
    check("穿禁飞区拒单(40900)", c == 40900, m)

    # 3. 禁运品
    c, m, _ = body(client.post("/api/orders/estimate",
                               json=dict(SAFE_ORDER, goodsType="汽油"), headers=cust_h))
    check("禁运品拦截(40000)", c == 40000, m)

    # 4. 缺字段
    c, m, _ = body(client.post("/api/orders", json={"senderName": "x"}, headers=cust_h))
    check("缺必填字段拦截", c == 40000, m)

    # 5. 未登录 401
    c, _, _ = body(client.get("/api/orders"))
    check("未登录访问订单→40100", c == 40100)

    # 6. 下单 → 支付
    c, m, order = body(client.post("/api/orders", json=SAFE_ORDER, headers=cust_h))
    check("下单成功 status=0", c == 0 and order["status"] == 0, order["orderNo"])
    oid = order["id"]
    fee_frozen = order["totalAmount"]
    c, m, _ = body(client.post(f"/api/orders/{oid}/pay", json={"payMethod": 1}, headers=cust_h))
    check("Mock 支付成功", c == 0, m)
    c, _, detail = body(client.get(f"/api/orders/{oid}", headers=cust_h))
    check("支付后状态=待接单 已付款", detail["status"] == 1 and detail["paid"] is True
          and detail["payAmount"] == fee_frozen)

    # 7. 重复支付被拒
    c, m, _ = body(client.post(f"/api/orders/{oid}/pay", json={"payMethod": 1}, headers=cust_h))
    check("重复支付→40900", c == 40900, m)

    # 8. 未支付单取消
    c, _, o2 = body(client.post("/api/orders", json=dict(SAFE_ORDER, goodsType="数码产品"), headers=cust_h))
    c, m, _ = body(client.post(f"/api/orders/{o2['id']}/cancel", json={"reason": "不想要了"}, headers=cust_h))
    check("未支付单可取消", c == 0, m)

    # 9. 已支付单取消 → 退款流水
    c, _, o3 = body(client.post("/api/orders", json=dict(SAFE_ORDER, goodsType="餐饮"), headers=cust_h))
    client.post(f"/api/orders/{o3['id']}/pay", json={"payMethod": 2}, headers=cust_h)
    c, m, _ = body(client.post(f"/api/orders/{o3['id']}/cancel", json={}, headers=cust_h))
    check("已支付单取消并 Mock 退款", c == 0, m)
    c, _, bills = body(client.get("/api/bills/payments", headers=cust_h))
    has_refund = any(x["direction"] == 2 and x["statusName"] == "已退款" for x in bills["list"])
    check("账单含退款流水", has_refund)

    # 10. 列表与身份隔离
    c, _, lst = body(client.get("/api/orders?pageSize=50", headers=cust_h))
    check("客户能看到自己的订单", lst["total"] >= 3)
    c, _, mlst = body(client.get("/api/orders?pageSize=50", headers=merch_h))
    check("商家能看到本商家订单", mlst["total"] >= lst["total"])
    # 客户电话对自己明文
    c, _, d_self = body(client.get(f"/api/orders/{oid}", headers=cust_h))
    check("本人订单电话明文", d_self["senderPhone"] == "13900000001")

    # 11. 结算单接口（此时可能为空，结构正确即可）
    c, _, st = body(client.get("/api/bills/settlements", headers=cust_h))
    check("结算单接口可用", c == 0 and "list" in st)

    # 12. 修改密码并改回（保持演示密码 Admin@123）
    c, m, _ = body(client.post("/api/auth/change-password",
                               json={"oldPassword": "Admin@123", "newPassword": "Test@1234"}, headers=cust_h))
    check("修改密码成功", c == 0, m)
    c, m, _ = body(client.post("/api/auth/change-password",
                               json={"oldPassword": "Test@1234", "newPassword": "Admin@123"}, headers=cust_h))
    check("密码已改回演示值", c == 0, m)
    c, m, _ = body(client.post("/api/auth/change-password",
                               json={"oldPassword": "wrong", "newPassword": "New@1234"}, headers=cust_h))
    check("旧密码错误被拒", c == 40000, m)

    # ---------- 清理本脚本产生的测试数据 ----------
    test_orders = db.session.query(Order).filter(Order.sender_name == "测试寄件人", Order.deleted == 0).all()
    ids = [o.id for o in test_orders]
    if ids:
        db.session.query(PaymentTransaction).filter(PaymentTransaction.order_id.in_(ids)).delete(synchronize_session=False)
        for o in test_orders:
            db.session.delete(o)
        db.session.commit()
    print(f"✓ 清理测试订单 {len(ids)} 条")

print(f"\n全部通过：{passed} 项断言")
