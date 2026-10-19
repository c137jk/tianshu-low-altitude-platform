"""场站机位占用数：资源台账、调度台资源面板、设备在线页共用的那个 x/y 的分子。

钉住的是派生口径本身（`vertiport.occupied_count` 那一列没有写入方，读它永远显示 0）：
  · 实占 = 停在该场站的飞行器；在飞的（status=2）不算——起飞只置状态、降落才把
    current_vertiport_id 迁到终点场，所以在飞的机数据上还挂在出发场；
  · 充换电/维保/故障都算占用机位，它们确实停在那儿；
  · 场站预约不并进这个数（预占按时段另算），否则进度条没法解释；
  · 三个读口出同一个数，同源就是 dispatch_service.station_occupancy()。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.extensions import db
from app.models import Aircraft, User
from app.services import dispatch_service


def _occ() -> dict[int, int]:
    db.session.expire_all()
    return dispatch_service.station_occupancy()


def _station_of(aid: int) -> int:
    db.session.expire_all()
    return int(db.session.execute(
        text("SELECT current_vertiport_id FROM aircraft WHERE id=:a"), {"a": aid}).scalar())


def _set_status(aid: int, status: int) -> None:
    db.session.execute(text("UPDATE aircraft SET status=:s WHERE id=:a"), {"s": status, "a": aid})
    db.session.commit()


def _occupied(payload: dict, station_id: int, key: str = "list") -> int:
    """从一份出参里取这台场站的占用数。

    设备在线列表把机载终端和地面设备放在同一个 list 里，两者的 id 会撞号，
    所以按 terminalType=2（地面设备=场站）挑；另两个读口没有这个键，默认按场站算。
    """
    rows = [x for x in payload[key]
            if x["id"] == station_id and x.get("terminalType", 2) == 2]
    assert len(rows) == 1, (key, station_id, payload[key])
    return rows[0]["occupiedCount"]


# ---------------------------------------------------------------- 口径本体

def test_seeded_park_is_the_baseline(app_ctx):
    """种子现场：1 号机停 1 号场、2 号机停 2 号场，各占一个机位。"""
    assert _occ() == {1: 1, 2: 1}


def test_takeoff_frees_the_berth_even_though_the_aircraft_still_lists_it(app_ctx, make_flight):
    """起飞后出发场必须空出一个机位，哪怕数据上这台机还"挂"在出发场。

    这条正是既不能读 occupied_count 列、也不能简单按 current_vertiport_id 分组的原因。
    """
    _, plan = make_flight("assigned")
    origin = _station_of(plan.aircraft_id)
    assert _occ()[origin] == 1

    dispatch_service.takeoff(db.session.get(User, plan.pilot_id), plan)

    db.session.expire_all()
    ac = db.session.get(Aircraft, plan.aircraft_id)
    assert ac.status == 2 and ac.current_vertiport_id == origin   # 场站列还指着出发场
    assert _occ().get(origin, 0) == 0                             # 但机位已经放开


def test_landing_moves_the_berth_from_origin_to_destination(app_ctx, make_flight):
    """降落把机位从出发场过账到到达场：一进一出，总数不会凭空多出一架。"""
    _, plan = make_flight("assigned")
    origin, dest = plan.takeoff_vertiport_id, plan.landing_vertiport_id
    assert dest != origin
    before, after = _occ(), None
    pilot = db.session.get(User, plan.pilot_id)

    dispatch_service.takeoff(pilot, plan)
    assert sum(_occ().values()) == sum(before.values()) - 1        # 空中不占任何机位
    dispatch_service.land(pilot, plan)
    after = _occ()

    assert after.get(origin, 0) == before[origin] - 1
    assert after.get(dest, 0) == before.get(dest, 0) + 1
    assert sum(after.values()) == sum(before.values())


@pytest.mark.parametrize("status", [3, 4, 5])
def test_charging_and_grounding_statuses_still_hold_a_berth(app_ctx, status):
    """充换电(3)/维保中(4)/故障离线(5) 都还停在机位上，只有执行中(2) 不占。"""
    station = _station_of(2)
    before = _occ()[station]
    _set_status(2, status)
    assert _occ()[station] == before


def test_reservation_does_not_count_as_a_parked_aircraft(app_ctx):
    """通过一条场站预约不该让占用数 +1：预占按时段另算，实占看停放。

    混算会让"约到了机位"和"机已经停在那儿"变成同一个数字，资源台账那个进度条就没人能
    解释；而调度台要的恰恰是"现在这台场站有没有机可派"。
    """
    now = datetime.now()
    db.session.execute(text(
        "INSERT INTO station_reservation (reservation_no, station_id, merchant_id, user_id,"
        " purpose, start_at, end_at, status, deleted)"
        " VALUES ('SR-OCC-TEST', 1, 1, :u, 1, :s, :e, 2, 0)"),
        {"u": db.session.execute(text("SELECT id FROM users WHERE username='merchant'")).scalar(),
         "s": now - timedelta(minutes=10), "e": now + timedelta(minutes=50)})
    db.session.commit()
    assert _occ()[1] == 1


# ---------------------------------------------------------------- 三个读口同源

def test_the_three_read_surfaces_report_the_same_number(api, app_ctx, make_flight):
    """资源台账 / 调度台资源面板 / 设备在线列表必须给同一个分子。

    各算各的就会回到"三层三份名单"那一族问题：页面上两个数字对不上，运营只能猜哪个是真的。
    """
    _, plan = make_flight("flying")
    origin = _station_of(plan.aircraft_id)
    expect = _occ().get(origin, 0)
    # 那一列到这时仍然是 0：证明这三个数不是从它读来的
    assert expect == 0 and db.session.execute(
        text("SELECT occupied_count FROM vertiport WHERE id=:i"), {"i": origin}).scalar() == 0

    def body(url):
        return api.as_("admin").get(url).get_json()["data"]

    assert _occupied(body("/api/resource/vertiports"), origin) == expect
    assert _occupied(body("/api/dispatch/resources"), origin, "vertiports") == expect
    assert _occupied(body("/api/monitor/devices"), origin) == expect
