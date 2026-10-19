"""空域高度准入（§7.10）：申报/提交时按航线穿过的每个空域上限判能不能飞。

补的是"超高只在飞行中判"这条：以前 create_plan 只拦禁飞区，申报高度填多少都放行，
飞机一起飞就吃一条"超高"违规——闸口变成了事后处罚。现在同一条规则在三个地方生效，
用例也就分三层钉：

  · 纯函数 binding_ceiling：多空域套叠时必须是上限最低的那个生效。旧实现飞行中取
    "第一个命中"配"最大超限值"，会写出「超过甲上限 60m」而 60 其实来自乙；
  · 申报前：穿限飞区且申报高度超上限要拦，改成合法高度要放行（限飞区不是禁飞区，
    不能因为穿了就直接拒）；
  · 提交前：方案起草后限高被收紧，老方案不能带着失效的合规结论飞出去。

限高阈值走 sys_config（altitude_limit_max_m），用例里会改参数并清缓存，
证明它是配置驱动而不是写死的 120。
"""
import json

import pytest
from sqlalchemy import text

from app.common.errors import ApiError
from app.common.scope import invalidate_cfg, sys_cfg_num
from app.extensions import db
from app.models import User
from app.services import compliance_monitor, dispatch_service
from app.services.geo import binding_ceiling

LIMITED_POINT = (120.6300, 28.0000)     # 瓯海中心限飞区（120.60~120.66 / 27.98~28.03，限高 60m）
OVER_ANY_LIMIT = 999                    # 高过所有上限的申报高度，用来验生效的是分区上限而不是兜底值
TMP_PREFIX = "T-ALT-"                   # 本模块造的临时空域，teardown 按前缀清


def _tmp_no(name: str) -> str:
    return TMP_PREFIX + name


def _insert_zone(no: str, name: str, ceiling, center: tuple[float, float],
                 zone_type: int = 2) -> None:
    """在指定点周围盖一个 ±0.01° 的方框空域（约 ±1km，必定罩住那个航点）。

    不靠种子空域的地理位置：把区盖在真实航线的中点上，测的就是"这条航线穿了限飞区"，
    而不是碰巧温州南站离瓯海有多远。
    """
    lng, lat = center
    half = 0.01
    ring = [[lng - half, lat - half], [lng + half, lat - half],
            [lng + half, lat + half], [lng - half, lat + half], [lng - half, lat - half]]
    db.session.execute(text(
        "INSERT INTO airspace (airspace_no, airspace_name, airspace_type, source,"
        " geometry_json, altitude_min, altitude_max, status, deleted, audit_status)"
        " VALUES (:no, :nm, :t, 2, CAST(:g AS JSON), 0, :c, 1, 0, 1)"),
        {"no": no, "nm": name, "t": zone_type, "c": ceiling,
         "g": json.dumps({"type": "Polygon", "coordinates": [ring]})})
    db.session.commit()
    db.session.expire_all()


def _u(user_ids, name) -> User:
    return db.session.get(User, user_ids[name])


def _global_limit() -> int:
    return int(db.session.execute(text(
        "SELECT config_value FROM sys_config WHERE config_key='altitude_limit_max_m'"))
        .scalar())


def _set_global_limit(value: int) -> None:
    db.session.execute(text("UPDATE sys_config SET config_value=:v "
                            "WHERE config_key='altitude_limit_max_m'"), {"v": str(value)})
    db.session.commit()
    invalidate_cfg("altitude_limit_max_m")


@pytest.fixture()
def restore_global_limit(app_ctx):
    """全局限高原样归还：sys_config 是种子配置，不在 conftest 的 _truncate 名单里。"""
    before = _global_limit()
    yield before
    _set_global_limit(before)


@pytest.fixture()
def clean_tmp_zone(app_ctx):
    """临时空域用完删掉：airspace 是参考数据，conftest 只按 AS-WZ-* 重置种子区。"""
    yield
    db.session.execute(text("DELETE FROM airspace WHERE airspace_no LIKE :p"),
                       {"p": TMP_PREFIX + "%"})
    db.session.commit()


# ---------------------------------------------------------------- 上限选取口径

def test_binding_ceiling_picks_the_lowest_exceeded_limit():
    """两个空域套叠时，结论要说卡住人的那一个，不能看命中顺序。"""
    cands = [("全局限高", 120), ("限飞空域「甲」", 60), ("限飞空域「乙」", 30)]
    assert binding_ceiling(cands, 100) == ("限飞空域「乙」", 30.0)
    assert binding_ceiling(cands, 61) == ("限飞空域「乙」", 30.0)


def test_binding_ceiling_returns_none_when_nothing_is_exceeded():
    assert binding_ceiling([("全局限高", 120), ("「甲」限高", 200)], 100) is None
    # altitude_max 为 NULL 表示该空域不限高，不能当成 0 把什么都拦下
    assert binding_ceiling([("「甲」限高", None)], 500) is None


def test_binding_ceiling_boundary_is_not_a_violation():
    """正好等于上限算合规：否则 60m 的限飞区会把 60m 的申报全拒掉。"""
    assert binding_ceiling([("「甲」限高", 60)], 60) is None
    assert binding_ceiling([("「甲」限高", 60)], 60.5) == ("「甲」限高", 60.0)


# ---------------------------------------------------------------- 申报前闸门

def _plan(order, user_ids, altitude, base=None):
    """按同一套资源再申报一次。base 传 make_flight 已经建好的方案：
    沿用它的场站和飞机，航程就和基准一致，不会被电量闸口先拒掉。"""
    return dispatch_service.create_plan(_u(user_ids, "merchant"), {
        "orderId": order.id,
        "aircraftId": base.aircraft_id if base else 1,
        "pilotId": base.pilot_id if base else user_ids["pilot"],
        "takeoffVertiportId": base.takeoff_vertiport_id if base else 1,
        "landingVertiportId": base.landing_vertiport_id if base else 1,
        "altitudeM": altitude, "cruiseSpeed": 12})


def test_route_crossing_low_ceiling_zone_blocks_plan_creation(
        session, make_flight, user_ids, clean_tmp_zone):
    """真实航线中点盖一个 60m 限飞区、申报 80m：申报阶段就拦下，而不是起飞后补违规。

    make_flight("planned") 先按同一航线上报 80m 成功，说明这条用例里唯一的变化
    就是那个限飞区——不是航线本身不合规、也不是电量或场站把请求挡在了别的闸口。
    """
    order, plan = make_flight("planned")
    mid = plan.route_json[len(plan.route_json) // 2]
    _insert_zone(_tmp_no("LOW"), "pytest 临时限飞区", 60,
                 (float(mid["lng"]), float(mid["lat"])))

    with pytest.raises(ApiError) as e:
        _plan(order, user_ids, 80, base=plan)
    assert "pytest 临时限飞区" in e.value.message
    assert "限高 60" in e.value.message


def test_plan_under_zone_ceiling_is_allowed(
        session, make_flight, user_ids, clean_tmp_zone):
    """限飞区不是禁飞区：高度压到上限以内就该放行，否则这条闸口变成一律拒。"""
    order, plan = make_flight("planned")
    mid = plan.route_json[len(plan.route_json) // 2]
    _insert_zone(_tmp_no("LOW"), "pytest 临时限飞区", 60,
                 (float(mid["lng"]), float(mid["lat"])))

    new_plan = _plan(order, user_ids, 50, base=plan)
    assert new_plan.altitude_m == 50 and new_plan.status == 0
    # 先确认它真的被判成了"穿区"，否则放行只是因为压根没命中，测不到闸口
    assert "pytest 临时限飞区" in [z["airspaceName"] for z in
                                    dispatch_service._compliance(new_plan.route_json)["ceiled"]]


def test_flyable_zone_ceiling_is_enforced_before_takeoff(session, make_flight, user_ids):
    """可飞区（type 3）也带 altitude_max，以前只按 type 2 筛，它的上限整个没人管。

    种子航线穿过瓯江口低空可飞示范区（上限 120m）：拒批理由必须点出这个区名，
    而不是只说"全局限高"——两者数值相同，只有落到区名上才证明走的是分区准入。
    """
    order, _ = make_flight("accepted")
    db.session.commit()
    with pytest.raises(ApiError) as e:
        _plan(order, user_ids, OVER_ANY_LIMIT)
    assert "瓯江口低空可飞示范区" in e.value.message
    assert "全局限高" not in e.value.message


def test_admission_follows_configured_global_limit(restore_global_limit):
    """闸门读 sys_config 而不是常量：把全局限高抬到 200，150m 就该放行。"""
    assert restore_global_limit == 120
    assert dispatch_service._altitude_admission(150, []) == ("全局限高", 120.0)
    _set_global_limit(200)
    assert sys_cfg_num("altitude_limit_max_m", 120, int) == 200
    assert dispatch_service._altitude_admission(150, []) is None


def test_admission_ignores_zone_without_a_ceiling():
    assert dispatch_service._altitude_admission(
        100, [{"airspaceName": "无上限区", "altitudeMax": None}]) is None


def test_admission_names_the_tightest_zone_on_the_route():
    """航线穿过多个限高空域时，拒批理由要指向最低的那一个。"""
    cands = [{"airspaceName": "宽松区", "altitudeMax": 90},
             {"airspaceName": "严格区", "altitudeMax": 40}]
    assert dispatch_service._altitude_admission(80, cands) == ("「严格区」限高", 40.0)


# ---------------------------------------------------------------- 提交前再判一次

def test_submit_revalidates_after_global_limit_tightened(
        session, make_flight, user_ids, restore_global_limit):
    """方案起草后限高被收紧：提交申报要重判，不能拿旧的合规结论蒙混过关。"""
    _, plan = make_flight("planned")
    assert plan.altitude_m == 80
    _set_global_limit(50)
    with pytest.raises(ApiError) as e:
        dispatch_service.submit_plan(_u(user_ids, "merchant"), plan)
    assert "空域条件已变更" in e.value.message
    assert plan.status == 0                       # 拦下来时不能把方案推进到待审批

    _set_global_limit(200)
    dispatch_service.submit_plan(_u(user_ids, "merchant"), plan)
    assert plan.status == 1


# ---------------------------------------------------------------- 飞行中同款口径

def test_inflight_desc_names_the_binding_zone(session, make_flight, clean_tmp_zone):
    """飞行中超高的理由也要指向最低上限那个空域，而不只是"数字报对了就行"。

    临时盖一个 30m 的限飞区在瓯海中心限飞区上面：旧实现取第一个命中配最大超限值，
    会写「超过瓯海中心限飞区上限」而超限值其实来自 30m 那个区。
    """
    _, plan = make_flight("flying")
    _insert_zone(_tmp_no("STACK"), "临时高压线限飞区", 30, LIMITED_POINT)

    over = next(h for h in compliance_monitor.findings(plan, *LIMITED_POINT, 100)
                if h["type"] == 4)
    assert "临时高压线限飞区" in over["desc"]
    assert "30m" in over["desc"] and "超出 70m" in over["desc"]
