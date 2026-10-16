"""P0-2 大屏鉴权与脱敏：投屏能看态势，但不能看乘客名单。

/api/monitor/screen 是唯一"可选登录"的业务读接口（设计04：指挥大屏要能直接投到墙上）。
口径是"态势保留、身份脱敏"：
  匿名 / 客户（user_type=1）→ pilot 姓名打码、收货地址只留行政区、货物名换成货物类型，
                              masked=true、authed=false；
  运营侧（3~6）→ 出参不脱敏；其中商家侧（user_type=3）行级只出本商家，平台侧全量。
另外要证明"脱敏不是把字段删掉就完事"：整个响应里不许出现手机号、密文、密码哈希。
"""
import json

import pytest


def _dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture()
def flying_screen(app, api, anon, make_flight, user_ids):
    """造一条真在飞计划，返回 {身份: 大屏 data}，供各脱敏断言复用。"""
    from datetime import timedelta

    from flask_jwt_extended import create_access_token

    from app.models import User
    from app.extensions import db

    order, plan = make_flight("flying")
    pilot_name = db.session.get(User, plan.pilot_id).real_name

    def fetch(username=None):
        resp = (api.as_(username).get("/api/monitor/screen") if username
                else anon.get("/api/monitor/screen"))
        body = resp.get_json()
        assert resp.status_code == 200 and body["code"] == 0, body
        return body["data"]

    def expired():
        """投屏挂了三天之后凭证过期的场景：必须降级成脱敏，不能黑屏。"""
        with app.app_context():
            tok = create_access_token(identity=str(user_ids["merchant"]),
                                      expires_delta=timedelta(seconds=-10))
        return anon.get("/api/monitor/screen",
                        headers={"Authorization": f"Bearer {tok}"}).get_json()

    yield {"data": fetch, "order": order, "plan": plan, "pilot_name": pilot_name,
           "expired": expired}


def test_anonymous_screen_is_masked(flying_screen):
    """未登录投屏：位置/电量/航迹这些态势数据一个都不能少，身份数据必须打码。"""
    order = flying_screen["order"]
    data = flying_screen["data"]()
    assert data["authed"] is False
    assert data["flying"], "在飞计划没出现在大屏上，后面的断言都是空的"
    item = data["flying"][0]
    assert item["masked"] is True
    assert item["pilot"] != flying_screen["pilot_name"]
    assert "*" in item["pilot"], f"机长姓名没打码：{item['pilot']}"
    assert item["receiver"].endswith("****"), f"收货地址没脱敏：{item['receiver']}"
    assert item["cargo"] != order.goods_name, "货物名称仍然可见"
    # 态势数据保留：坐标/高度/速度/电量/航线/进度一个都不许缺
    for key in ("lng", "lat", "altitude", "speed", "battery", "route", "progress", "phase"):
        assert key in item, f"脱敏顺手把态势字段 {key} 也删了，大屏会开天窗"
    assert item["route"], "航线点被清空了"
    assert data["fleet"] and data["vertiports"] and data["airspaces"]
    assert "masked" not in _dump(data["fleet"]), "机队/场站是资产数据，不属于脱敏范围"


def test_operator_screen_is_full(flying_screen):
    """运营侧（商家调度/平台管理员）登录态：全量出参，姓名地址货物都在。"""
    order = flying_screen["order"]
    pilot_name = flying_screen["pilot_name"]
    for username in ("merchant", "admin"):
        data = flying_screen["data"](username)
        assert data["authed"] is True, username
        item = data["flying"][0]
        assert item["masked"] is False
        assert item["pilot"] == pilot_name
        assert item["receiver"] == order.receiver_address
        assert item["cargo"] == order.goods_name


def test_screen_rows_are_scoped_per_merchant(api, anon, flying_screen, fresh_user,
                                             second_merchant):
    """商家侧大屏只出本商家的行：机队、在飞任务、今日单量都不该带隔壁商家的。

    脱敏只解决"身份看不看得到"，B 商家的飞机在哪、今天飞了几单照样在 A 商家屏上——
    那是经营数据。判据与回放读门同源（monitor._visible_plan_query 那条线）。
    匿名/客户不动：投屏口径是"态势公开、身份打码"，裁行会把脱敏分支变成空屏。
    """
    from decimal import Decimal

    from app.extensions import db
    from app.models import Aircraft, Order

    plan = flying_screen["plan"]
    ac_b = Aircraft(aircraft_no="AC-B-ROWSCOPE", merchant_id=second_merchant, brand="测试",
                    model="裁行验证", status=1, online=1, battery=88, endurance_min=30,
                    max_load_kg=Decimal("5.00"))
    order_b = Order(order_no="B2ROWSCOPE0001", customer_id=1, merchant_id=second_merchant,
                    status=1, sender_name="测试", sender_phone="T", sender_address="温州市龙湾区",
                    sender_lng=Decimal("120.79"), sender_lat=Decimal("27.97"),
                    receiver_name="测试", receiver_phone="T", receiver_address="温州市瓯海区",
                    receiver_lng=Decimal("120.65"), receiver_lat=Decimal("27.95"),
                    goods_type="文件", goods_name="裁行验证件", weight_kg=Decimal("1.00"),
                    total_amount=Decimal("0.00"))
    db.session.add_all([ac_b, order_b])
    db.session.commit()
    boss_b = fresh_user("boss_b_rowscope", role_code="merchant_admin", merchant_id=second_merchant)

    def view(username=None, uid=None):
        resp = ((api.as_(username) if username else api.as_id(uid))
                if (username or uid) else anon).get("/api/monitor/screen")
        body = resp.get_json()
        assert resp.status_code == 200 and body["code"] == 0, body
        return body["data"]

    flying_nos = lambda d: {f["planNo"] for f in d["flying"]}
    fleet_ids = lambda d: {a["id"] for a in d["fleet"]}
    order_total = lambda d: sum(d["today"]["orderStatus"].values())

    plat = view("admin")
    assert plan.plan_no in flying_nos(plat) and ac_b.id in fleet_ids(plat), "平台侧被裁小了"

    mine = view("merchant")                       # 商家 1 的调度台
    assert plan.plan_no in flying_nos(mine) and mine["flying"][0]["masked"] is False
    assert ac_b.id not in fleet_ids(mine), "隔壁商家的机队位置出现在自家大屏上"
    assert order_total(mine) == order_total(plat) - 1, "今日单量把隔壁商家的单子也计进来了"

    rival = view(uid=boss_b.id)                   # B 商家老板
    assert plan.plan_no not in flying_nos(rival), "B 商家能回放/看见 A 商家的在飞任务"
    assert ac_b.id in fleet_ids(rival) and order_total(rival) == 1

    guest = view()                                # 匿名投屏：行级口径不变，仍然脱敏
    assert ac_b.id in fleet_ids(guest) and plan.plan_no in flying_nos(guest)
    assert guest["authed"] is False and guest["flying"][0]["masked"] is True


def test_customer_screen_is_masked(flying_screen):
    """客户登录了也不给全量：user_type=1 不在运营侧口径里（他看自己的订单详情就够）。"""
    data = flying_screen["data"]("customer")
    assert data["authed"] is False
    assert data["flying"][0]["masked"] is True


def test_no_credential_fields_in_payload(flying_screen):
    """三类身份的响应体里都不许出现手机号/密文/哈希/身份证一类字段。"""
    forbidden = ("phone_cipher", "phoneCipher", "password_hash", "passwordHash",
                 "id_card", "idCard", "phone_digest", "phoneDigest", "SEED:")
    for username in (None, "admin", "merchant", "customer", "pilot"):
        dumped = _dump(flying_screen["data"](username))
        hit = [k for k in forbidden if k in dumped]
        assert not hit, f"{username or '匿名'} 的大屏出参泄露字段：{hit}"


def test_mask_helpers_behave_on_edge_inputs():
    """脱敏函数自身：空值/单字姓名/没有行政区的裸地址都不能抛或原样返回。"""
    from app.blueprints.monitor import _mask_address, _mask_name

    assert _mask_name(None) == "已隐藏"
    assert _mask_name("陈") == "陈**"
    assert _mask_name("陈机长") == "陈**"
    assert _mask_address(None) == "已隐藏"
    assert _mask_address("温州市龙湾区瓯江口灵昆岛").endswith("****")
    assert _mask_address("瑷珥").endswith("****")          # 短到没有分隔层级时截前 4 字
    assert "灵昆岛" not in _mask_address("温州市龙湾区瓯江口灵昆岛")


def test_screen_degrades_on_broken_token(anon):
    """损坏的 token 走"匿名脱敏"分支，不能 500，也不能当成登录态。"""
    resp = anon.get("/api/monitor/screen", headers={"Authorization": "Bearer not.a.jwt"})
    body = resp.get_json()
    assert resp.status_code == 200 and body["code"] == 0, body
    assert body["data"]["authed"] is False


def test_screen_degrades_on_expired_token(flying_screen):
    """过期 token 同样降级为脱敏：大屏连续投几天必然跨过 8 小时有效期，不能黑屏。"""
    body = flying_screen["expired"]()
    assert body["code"] == 0, body
    assert body["data"]["authed"] is False
    if body["data"]["flying"]:
        assert body["data"]["flying"][0]["masked"] is True


def test_screen_ignores_refresh_token(anon, user_ids):
    """拿 refresh token 冒充 access token 不能换取全量数据。"""
    from flask_jwt_extended import create_refresh_token

    tok = create_refresh_token(identity=str(user_ids["admin"]))
    body = anon.get("/api/monitor/screen",
                    headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert body["code"] == 0 and body["data"]["authed"] is False, body
