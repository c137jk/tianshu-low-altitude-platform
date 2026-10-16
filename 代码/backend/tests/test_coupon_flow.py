"""优惠券全链路：领取 → 试算抵扣 → 下单核销 → 作废退券（需求 6.3.2）。

这里钉住的是六条容易被写歪的规则，而不只是"接口能通"：
  · 限量与每人一张——抢最后一张时不能超发（条件自增），重复领取必须 409。
  · 门槛按**原价**判、抵扣只算一次口径——试算接口和下单服务共用 `discount_for`，
    否则会出现"页面显示 12 元、实际扣 15 元"这种最难解释的账。
  · 下单即核销、作废即退券——客户取消 / 商家拒单 / 平台强制终止三条路都退券，
    三处共用 `release_for_order`；漏一条就是"钱退了券没退"。
  · 别人的券给 404——不能从错误信息里认出"这张券存在但不归你"。
  · 过期不落库——`coupon.end_at` 一改，券包读出来立刻是"已过期"，不依赖定时任务。
  · 券停用/下架后不能再用于新单——但已核销在订单上的抵扣结果不动。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.common.errors import ApiError
from app.extensions import db
from app.models import User, Vertiport
from app.services import coupon_service, geo, order_service, pricing_service


def _sql(stmt, **p):
    return db.session.execute(text(stmt), p)


def _mk_coupon(name="pytest 满 20 减 10", coupon_type=1, threshold=20, amount=10,
               discount=None, total=100, start=None, end=None, status=1) -> int:
    """测试券自己造：DDL 里没有 coupon 种子，而且 conftest 每例都清 coupon/user_coupon 表。"""
    now = datetime.now()
    cid = int(_sql(
        "INSERT INTO coupon (coupon_name, coupon_type, threshold, amount, discount,"
        " total_count, got_count, start_at, end_at, status, created_at, updated_at, deleted)"
        " VALUES (:n,:t,:th,:a,:d,:tc,0,:s,:e,:st,NOW(),NOW(),0)",
        n=name, t=coupon_type, th=threshold, a=amount, d=discount, tc=total,
        s=start or now - timedelta(days=1), e=end or now + timedelta(days=30),
        st=status).lastrowid)
    db.session.commit()
    return cid


def _order_payload() -> dict:
    """和 conftest.make_flight 同一条演示航线（两个种子场站各偏移一点），保证能过服务范围。"""
    vp1, vp2 = db.session.get(Vertiport, 1), db.session.get(Vertiport, 2)
    s = geo.offset_meters(float(vp1.lng), float(vp1.lat), 60.0, 40.0)
    r = geo.offset_meters(float(vp2.lng), float(vp2.lat), 120.0, -80.0)
    return {
        "senderName": "券·寄件", "senderPhone": "13900001111", "senderAddress": "券测试取货点",
        "senderLng": Decimal(str(round(s[0], 6))), "senderLat": Decimal(str(round(s[1], 6))),
        "receiverName": "券·收件", "receiverPhone": "13900002222", "receiverAddress": "券测试收货点",
        "receiverLng": Decimal(str(round(r[0], 6))), "receiverLat": Decimal(str(round(r[1], 6))),
        "goodsType": "文件", "goodsName": "券测试件", "weightKg": Decimal("1.0"),
        "deliveryType": 1, "orderType": 1, "remark": "pytest 券链路",
    }


@pytest.fixture()
def customer(app_ctx, user_ids):
    return db.session.get(User, user_ids["customer"])


@pytest.fixture()
def staff(app_ctx, user_ids):
    return db.session.get(User, user_ids["merchant"])


@pytest.fixture()
def admin(app_ctx, user_ids):
    return db.session.get(User, user_ids["admin"])


def _uc_row(user_coupon_id) -> dict:
    db.session.expire_all()
    return _sql("SELECT status, order_id, user_id FROM user_coupon WHERE id=:i",
                i=user_coupon_id).mappings().first()


# ------------------------------------------------------------------ 领取
def test_claim_once_per_user(customer):
    cid = _mk_coupon()
    first = coupon_service.claim(customer.id, cid)
    assert _uc_row(first)["status"] == 1
    assert int(_sql("SELECT got_count FROM coupon WHERE id=:i", i=cid).scalar()) == 1
    with pytest.raises(ApiError) as e:
        coupon_service.claim(customer.id, cid)
    assert "每人限领一张" in str(e.value.message)
    # 失败的重复领取不能把名额吃掉
    assert int(_sql("SELECT got_count FROM coupon WHERE id=:i", i=cid).scalar()) == 1


def test_claim_respects_stock_and_disabled(api, user_ids):
    """领完的券不能再领；停用/未到期的券也不能领。走 HTTP 一并验领取接口的错误码。

    第二个领取人用种子里的 pilot 而不是临时账号：临时账号会被 fresh_user 夹具删掉，
    而 user_coupon.user_id 上有外键，删人时会连带把清场顺序搅乱。
    """
    sold_out = _mk_coupon(name="券·限量一张", total=1)
    assert api.as_("customer").post(f"/api/aux/coupons/{sold_out}/claim").get_json()["code"] == 0
    body = api.as_("pilot").post(f"/api/aux/coupons/{sold_out}/claim").get_json()
    assert body["code"] == 40900 and "已领完" in body["message"], body

    off = _mk_coupon(name="券·已停用", status=2)
    b2 = api.as_("pilot").post(f"/api/aux/coupons/{off}/claim").get_json()
    assert b2["code"] == 40900 and "已停用" in b2["message"], b2
    future = _mk_coupon(name="券·未到期", start=datetime.now() + timedelta(days=3))
    b3 = api.as_("pilot").post(f"/api/aux/coupons/{future}/claim").get_json()
    assert b3["code"] == 40900 and "不在有效期内" in b3["message"], b3
    assert int(_sql("SELECT got_count FROM coupon WHERE id=:i", i=off).scalar()) == 0


def test_available_list_marks_claimed_and_usable(api, customer):
    """下单页选券的两个字段：claimed（我有没有）与 myCouponId（能用的那张）。

    核销之后券模板还在列表里（别人还能领），但 myCouponId 必须变空——
    否则前端把这张已经用掉的券再传一次，客户只会收到一句"已核销"。
    """
    cid = _mk_coupon(name="券·下单页可见")
    mine = coupon_service.claim(customer.id, cid)
    rows = api.as_("customer").get("/api/aux/coupons?scope=available").get_json()["data"]
    hit = [r for r in rows if r["id"] == cid]
    assert hit and hit[0]["claimed"] is True and hit[0]["myCouponId"] == mine

    order_service.create_order(customer, dict(_order_payload(), couponId=mine))
    rows2 = api.as_("customer").get("/api/aux/coupons?scope=available").get_json()["data"]
    hit2 = [r for r in rows2 if r["id"] == cid]
    assert hit2 and hit2[0]["claimed"] is True and hit2[0]["myCouponId"] is None


# ------------------------------------------------------------------ 抵扣与核销
def test_estimate_and_create_share_one_discount(customer):
    """试算接口与下单必须是同一个价：两处各算一遍迟早分叉成"页面价 ≠ 扣款价"。"""
    cid = _mk_coupon(name="券·门槛20减10", threshold=20, amount=10)
    uc = coupon_service.claim(customer.id, cid)
    payload = _order_payload()
    quote, _ = coupon_service.discount_for(
        pricing_service.estimate(
            sender_lng=payload["senderLng"], sender_lat=payload["senderLat"],
            receiver_lng=payload["receiverLng"], receiver_lat=payload["receiverLat"],
            weight_kg=payload["weightKg"], delivery_type=1),
        customer.id, uc)
    order = order_service.create_order(customer, dict(payload, couponId=uc))
    assert Decimal(str(order.discount_amount)) == Decimal(str(quote["discountAmount"]))
    assert Decimal(str(order.total_amount)) == Decimal(str(quote["totalAmount"]))
    # 原价列不能被券改脏：基础/空域/加急三项还是原价，减钱只体现在 discount_amount
    assert Decimal(str(order.base_fee)) == Decimal(str(quote["baseFee"]))
    row = _uc_row(uc)
    assert row["status"] == 2 and row["order_id"] == order.id


def test_threshold_judged_on_original_price(customer):
    """门槛按原价判，不是按折后价——否则同一张券能叠加着把价格压低。"""
    cid = _mk_coupon(name="券·原价才够门槛", threshold=20, amount=15)
    uc = coupon_service.claim(customer.id, cid)
    order = order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    assert float(order.total_amount) >= 0
    assert Decimal(str(order.discount_amount)) == Decimal("15.00")


def test_discount_never_goes_negative(customer):
    """满减额大于订单金额时按订单金额封顶，不能减出负数。"""
    cid = _mk_coupon(name="券·超额满减", threshold=0, amount=9999)
    uc = coupon_service.claim(customer.id, cid)
    order = order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    assert Decimal(str(order.total_amount)) == Decimal("0.00")
    assert Decimal(str(order.discount_amount)) == Decimal(str(order.base_fee)) \
        + Decimal(str(order.airspace_fee)) + Decimal(str(order.urgent_fee))


def test_percentage_coupon(customer):
    cid = _mk_coupon(name="券·85折", coupon_type=2, threshold=0, amount=None, discount=Decimal("0.85"))
    uc = coupon_service.claim(customer.id, cid)
    order = order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    origin = Decimal(str(order.base_fee)) + Decimal(str(order.airspace_fee)) + Decimal(str(order.urgent_fee))
    assert Decimal(str(order.discount_amount)) == (origin * Decimal("0.15")).quantize(Decimal("0.01"))


def test_used_coupon_cannot_be_reused(customer):
    cid = _mk_coupon()
    uc = coupon_service.claim(customer.id, cid)
    order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    with pytest.raises(ApiError) as e:
        coupon_service.usable(customer.id, uc, Decimal("50"))
    assert "已核销" in str(e.value.message)


def test_others_coupon_is_404(customer, user_ids):
    """别人的券给"不存在"，而不是"不是你的"：前者不泄露这张券的 id 真实存在。"""
    cid = _mk_coupon()
    rival = db.session.get(User, user_ids["pilot"])
    uc = coupon_service.claim(rival.id, cid)
    with pytest.raises(ApiError) as e:
        coupon_service.usable(customer.id, uc, Decimal("50"))
    assert e.value.code == 40400


def test_settle_race_loses_second_order(customer):
    """同一张券同时占两单：条件更新只让第一个抢到，第二个整笔失败，不会"一券减两单"。"""
    cid = _mk_coupon()
    uc = coupon_service.claim(customer.id, cid)
    order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    with pytest.raises(ApiError) as e:
        coupon_service.settle(customer.id, uc, 999999)
    assert "刚刚被用掉了" in str(e.value.message)


# ------------------------------------------------------------------ 退券
def test_cancel_returns_coupon(customer, staff):
    cid = _mk_coupon()
    uc = coupon_service.claim(customer.id, cid)
    order = order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    order_service.pay_order(order, customer, pay_method=1)
    order_service.cancel_order(order, customer, "临时不寄了")
    row = _uc_row(uc)
    assert row["status"] == 1 and row["order_id"] is None
    # 退回来的券还能继续用
    order2 = order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    assert _uc_row(uc)["order_id"] == order2.id


def test_reject_and_force_terminate_return_coupon(customer, staff, admin):
    """拒单与强制终止这两条最容易忘退券，逐条验一遍（三处共用一个函数不代表三条都通）。"""
    for step in ("reject", "terminate"):
        cid = _mk_coupon(name=f"券·{step}")
        uc = coupon_service.claim(customer.id, cid)
        order = order_service.create_order(customer, dict(_order_payload(), couponId=uc))
        order_service.pay_order(order, customer, pay_method=1)
        if step == "reject":
            order_service.staff_reject_order(order, staff, "运力不足")
        else:
            order_service.force_terminate_order(order, admin, "演示演练终止")
        row = _uc_row(uc)
        assert row["status"] == 1 and row["order_id"] is None, step
        assert order.status == 8


def test_finished_order_keeps_coupon_consumed(customer):
    """确认收货不是作废，券必须留在"已核销"——否则等于一张券能用两次。"""
    name = "券·完成不退"
    cid = _mk_coupon(name=name)
    uc = coupon_service.claim(customer.id, cid)
    order = order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    order_service.pay_order(order, customer, pay_method=1)
    order.status = 6
    db.session.commit()
    order_service.confirm_receipt(order, customer)
    row = _uc_row(uc)
    assert row["status"] == 2 and row["order_id"] == order.id
    # 详情里要点名这张券减了多少，客户对账时才说得清
    assert coupon_service.bound_coupon_name(order.id) == name
    assert order_service.to_order_dict(order)["couponName"] == name


# ------------------------------------------------------------------ 过期/停用（读时推导）
def test_expired_is_derived_not_stored(customer):
    cid = _mk_coupon(name="券·明天到期", end=datetime.now() + timedelta(days=1))
    uc = coupon_service.claim(customer.id, cid)
    assert coupon_service.mine(customer.id)[0]["statusName"] == "未使用"
    _sql("UPDATE coupon SET end_at=:e WHERE id=:i", e=datetime.now() - timedelta(hours=1), i=cid)
    db.session.commit()
    row = coupon_service.mine(customer.id)[0]
    assert row["status"] == 4 and row["statusName"] == "已过期"
    assert _sql("SELECT status FROM user_coupon WHERE id=:i", i=uc).scalar() == 1
    with pytest.raises(ApiError) as e:
        coupon_service.usable(customer.id, uc, Decimal("50"))
    assert "已过有效期" in str(e.value.message)


def test_disabled_coupon_blocks_new_use_but_not_history(customer):
    cid = _mk_coupon(name="券·会被停用")
    uc = coupon_service.claim(customer.id, cid)
    _sql("UPDATE coupon SET status=2 WHERE id=:i", i=cid)
    db.session.commit()
    with pytest.raises(ApiError) as e:
        coupon_service.usable(customer.id, uc, Decimal("50"))
    assert "已停用" in str(e.value.message)
    assert coupon_service.mine(customer.id)[0]["statusName"] == "券已停用"


def _mine(api, status=None):
    url = "/api/aux/coupons/mine" + (f"?status={status}" if status else "")
    return api.get(url).get_json()["data"]["list"]


# ------------------------------------------------------------------ HTTP 层
def test_api_mine_and_estimate_with_coupon(api, customer):
    cid = _mk_coupon(name="券·接口层")
    r = api.as_("customer").post(f"/api/aux/coupons/{cid}/claim")
    assert r.get_json()["code"] == 0, r.get_json()
    mine = _mine(api.as_("customer"))
    assert len(mine) == 1 and mine[0]["couponName"] == "券·接口层" and mine[0]["ruleText"]
    # 券包按登录人隔离：商家账号一张都没有
    assert _mine(api.as_("merchant")) == []

    payload = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in _order_payload().items()}
    base = api.as_("customer").post("/api/orders/estimate", json=payload).get_json()["data"]
    with_cp = api.as_("customer").post("/api/orders/estimate",
                                       json={**payload, "couponId": mine[0]["id"]}).get_json()["data"]
    assert with_cp["totalAmount"] == pytest.approx(base["totalAmount"] - 10, abs=0.01)
    assert with_cp["coupon"]["name"] == "券·接口层"
    # 试算不落库：券还是未使用
    assert _mine(api.as_("customer"))[0]["status"] == 1

    # 下单带上券：核销发生在创建成功那一刻
    resp = api.as_("customer").post("/api/orders", json={**payload, "couponId": mine[0]["id"]})
    assert resp.get_json()["code"] == 0, resp.get_json()
    row = _mine(api.as_("customer"))[0]
    assert row["status"] == 2 and row["orderNo"] == resp.get_json()["data"]["orderNo"]
    # 券包按状态筛
    assert [x["id"] for x in _mine(api.as_("customer"), status=2)] == [row["id"]]
    assert _mine(api.as_("customer"), status=1) == []


def test_management_list_shows_used_count_and_needs_perm(api, customer):
    cid = _mk_coupon(name="券·管理端")
    uc = coupon_service.claim(customer.id, cid)
    order_service.create_order(customer, dict(_order_payload(), couponId=uc))
    rows = api.as_("admin").get("/api/aux/coupons").get_json()["data"]
    hit = [r for r in rows if r["id"] == cid]
    assert hit and hit[0]["used_count"] == 1 and hit[0]["got_count"] == 1
    # 管理视图必须权限点，客户侧只能走 scope=available
    assert api.as_("customer").get("/api/aux/coupons").get_json()["code"] == 40300
