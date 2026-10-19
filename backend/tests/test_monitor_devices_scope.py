"""设备在线列表（/api/monitor/devices）的行门。

这一页的权限点 monitor:device 同时发给了平台侧（platform_admin / air_traffic / ops_maintainer）
和商家侧（merchant_maintainer），只按权限点放行就等于把别家的机号、实时坐标、在飞任务号
一起端给竞争对手——和上一轮修的订单读口、回放读口是同一族问题（D-189：门关了、行没裁）。
这里钉三条：飞行器按本商家收窄，场站按"本商家 + 公共场站"收窄，平台侧口径不动。
"""
import pytest
from sqlalchemy import text

from app.extensions import db

AC_A, AC_B, VP_A = 1, 2, 1          # 种子：1 号机/2 号机/1 号场站都挂在演示主商家名下


@pytest.fixture()
def two_merchants(app_ctx, second_merchant):
    """把 2 号种子飞行器划给第二家商家，再补一台 B 商家自有场站和一台公共场站。

    aircraft.merchant_id 不在 conftest 的复位名单里，必须自己还回去（场站由清场名单删）；
    公共场站的 merchant_id 为 NULL，是 DDL 就认的"谁都能用"那一档。
    """
    db.session.execute(text("UPDATE aircraft SET merchant_id=:m WHERE id=:a"),
                       {"m": second_merchant, "a": AC_B})
    db.session.execute(text(
        "INSERT INTO vertiport (station_no, station_name, station_type, lng, lat,"
        " merchant_id, capacity, status, deleted) VALUES"
        " ('VP-SC-B','乙商家专用站',1,121.100000,28.100000,:m,4,1,0),"
        " ('VP-SC-PUB','公共起降点',1,121.200000,28.200000,NULL,2,1,0)"),
        {"m": second_merchant})
    db.session.commit()
    try:
        ids = dict(db.session.execute(text(
            "SELECT station_no, id FROM vertiport WHERE station_no IN ('VP-SC-B','VP-SC-PUB')"
        )).all())
        yield {"vp_b": ids["VP-SC-B"], "vp_pub": ids["VP-SC-PUB"], "mid_b": second_merchant}
    finally:
        db.session.execute(text("UPDATE aircraft SET merchant_id=1 WHERE id=:a"), {"a": AC_B})
        db.session.commit()


def _sets(api, uid: int) -> tuple[set[int], set[int]]:
    rows = api.as_id(uid).get("/api/monitor/devices").get_json()["data"]["list"]
    return ({r["id"] for r in rows if r["terminalType"] == 1},
            {r["id"] for r in rows if r["terminalType"] == 2})


def test_merchant_side_device_list_only_shows_own_fleet(api, fresh_user, two_merchants):
    """两家商家的运维各自只看见自己的机、自己的场站加公共场站。"""
    mine = fresh_user("ops_mine", role_code="merchant_maintainer", merchant_id=1)
    rival = fresh_user("ops_rival", role_code="merchant_maintainer",
                       merchant_id=two_merchants["mid_b"])

    ac, vp = _sets(api, mine.id)
    assert AC_A in ac and AC_B not in ac
    assert {VP_A, two_merchants["vp_pub"]} <= vp and two_merchants["vp_b"] not in vp

    ac2, vp2 = _sets(api, rival.id)
    assert AC_B in ac2 and AC_A not in ac2
    assert {two_merchants["vp_b"], two_merchants["vp_pub"]} <= vp2 and VP_A not in vp2


def test_platform_side_still_sees_everything(api, fresh_user, two_merchants):
    """平台侧（运维/空管/平台管理员）口径不动：设备总览要的就是全局，收错了等于蒙上眼睛。"""
    uids = [db.session.execute(text("SELECT id FROM users WHERE username='admin'")).scalar(),
            fresh_user("ops_plat", role_code="ops_maintainer", user_type=6, merchant_id=None).id,
            fresh_user("atc_dev", role_code="air_traffic", user_type=5, merchant_id=None).id]
    for uid in uids:
        ac, vp = _sets(api, uid)
        assert {AC_A, AC_B} <= ac, uid
        assert {VP_A, two_merchants["vp_b"], two_merchants["vp_pub"]} <= vp, uid


def test_visible_rows_are_all_ones_own_and_counts_follow_them(api, fresh_user, two_merchants):
    """收窄之后每一行都得是本商家的，顶部统计也跟着可见行走——漏一行就是定位跟踪。"""
    rival = fresh_user("ops_rival2", role_code="merchant_maintainer",
                       merchant_id=two_merchants["mid_b"])
    data = api.as_id(rival.id).get("/api/monitor/devices").get_json()["data"]
    rows = data["list"]
    assert {r["id"] for r in rows if r["terminalType"] == 1} == {AC_B}
    assert {r["id"] for r in rows if r["terminalType"] == 2} == {
        two_merchants["vp_b"], two_merchants["vp_pub"]}
    # counts 的键是全量状态字典（缺项也要出现），所以只对总数：可见行每人算一次
    assert sum(data["counts"].values()) == len(rows)
