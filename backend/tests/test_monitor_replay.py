"""M-62 轨迹回放 + M-63 设备在线 的接口口径回归。

这两块都是"演示时被人盯着看"的页面，口径错一处就是当场穿帮：
  · 回放的 offsetSec 必须以第一帧为 0 单调递增（前端时间轴按它摆，帧间隔并不等距）；
  · 违规帧要带中文类型名，前端不必再自己维护一张 tag 表；
  · 设备状态必须由真实心跳推出来：能报在线是因为刚刚真的写过心跳，
    在飞却断供要立刻判失联（这是运维要处置的信号，不是装饰）。
测试环境仿真线程关闭（FLASK_SIM_DISABLED），推进一律显式 tick()，倍速钉在 1×。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.extensions import db
from app.models import Aircraft, FlightTrack, Vertiport
from app.services import flight_simulator


@pytest.fixture(autouse=True)
def _sim_1x(monkeypatch):
    """倍速钉在 1×：默认 60× 下 rewind 0.1 就等于飞完全程自行落地，帧数断言会全乱。
    仿真器还是模块级全局状态，用例间必须清，否则帧计数会跨用例累加。"""
    monkeypatch.setenv("SIM_SPEED_FACTOR", "1")

    def _reset():
        for cache in (flight_simulator._start_bat, flight_simulator._prev_pos,
                      flight_simulator._frames, flight_simulator._drift,
                      flight_simulator._scenario):
            cache.clear()
        flight_simulator._scenario_default = None
    _reset()
    yield
    _reset()


def _rewind(plan, fraction: float) -> None:
    plan.actual_takeoff_at = datetime.now() - timedelta(
        seconds=float(plan.est_duration_sec or 120) * fraction)
    db.session.commit()


def _backdate(table: str, row_id: int, seconds: int, column: str = "updated_at") -> None:
    """显式赋值时间列：MySQL 的 ON UPDATE 只在没被 SET 的列上才自动刷新。"""
    db.session.execute(text(f"UPDATE {table} SET {column}=:t WHERE id=:i"),
                       {"t": datetime.now() - timedelta(seconds=seconds), "i": row_id})
    db.session.commit()


def _fly(session, make_flight, ticks: int = 3):
    order, plan = make_flight("flying")
    for i in range(1, ticks + 1):
        _rewind(plan, 0.1 * i)
        flight_simulator.tick()
    return order, plan


# ---------------------------------------------------------------- M-62 回放

def test_track_list_reports_status_frames_and_span(api, session, make_flight):
    _, plan = _fly(session, make_flight, ticks=2)
    data = api.as_("admin").get("/api/monitor/tracks").get_json()["data"]
    row = next(x for x in data["list"] if x["planId"] == plan.id)
    assert row["status"] == 3 and row["statusName"] == "执行中"
    assert row["frames"] == 2
    assert row["durationSec"] >= 0
    assert row["violationFrames"] == 0


def test_track_detail_carries_timeline_fields_and_violation_name(api, session, make_flight):
    _, plan = _fly(session, make_flight, ticks=2)
    flight_simulator.inject("no_fly", plan.id)
    _rewind(plan, 0.5)
    flight_simulator.tick()

    d = api.as_("admin").get(f"/api/monitor/tracks/{plan.id}").get_json()["data"]
    assert d["planNo"] == plan.plan_no
    assert d["frames"] == len(d["points"]) == 3
    assert d["aircraftNo"] == db.session.get(Aircraft, plan.aircraft_id).aircraft_no
    assert d["batteryThreshold"] == 20

    offsets = [p["offsetSec"] for p in d["points"]]
    assert offsets[0] == 0 and offsets == sorted(offsets), "时间轴必须单调，前端按它摆播放头"
    assert d["durationSec"] == offsets[-1]
    for p in d["points"]:
        assert {"lng", "lat", "altitude", "speed", "battery", "direction", "t", "ts"} <= set(p)

    bad = [p for p in d["points"] if p["violation"]]
    assert bad and bad[-1]["violationType"] == "no_fly_intrusion"
    assert bad[-1]["violationName"] == "闯禁飞区"          # tag 反查在接口侧做完
    assert d["violationFrames"] == len(bad)


def test_track_detail_unknown_plan_is_404_not_empty_list(api):
    body = api.as_("admin").get("/api/monitor/tracks/999999").get_json()
    assert body["code"] == 40400


def test_tracks_need_permission(api, anon, fresh_user):
    assert anon.get("/api/monitor/tracks").status_code == 401
    assert api.as_("customer").get("/api/monitor/tracks").get_json()["code"] == 40300
    at = fresh_user("at_observer", role_code="air_traffic")
    assert api.as_id(at.id).get("/api/monitor/tracks").get_json()["code"] == 0


def test_track_replay_is_row_scoped(api, session, make_flight, fresh_user, user_ids, second_merchant):
    """回放读门按行裁：平台侧全量，商家限本商家，机长限本人执飞。

    轨迹详情是整条 GPS 线，列表里还带任务名（就是客户品名）——商家调度员和机长都持有
    monitor:track，只按权限点放行的话，B 商家能把 A 商家的行踪与客户信息一并回放出来。
    """
    from app.models import User

    _, plan = _fly(session, make_flight, ticks=2)
    ids = lambda who: {x["planId"] for x in
                       api.as_id(who).get("/api/monitor/tracks").get_json()["data"]["list"]}

    atc = fresh_user("atc_replay", role_code="air_traffic", user_type=5, merchant_id=None)
    assert api.as_id(atc.id).get(f"/api/monitor/tracks/{plan.id}").get_json()["code"] == 0
    assert plan.id in ids(atc.id)

    mate = fresh_user("mate_dispatch", role_code="dispatcher")      # 同商家（merchant 1）
    assert api.as_id(mate.id).get(f"/api/monitor/tracks/{plan.id}").get_json()["code"] == 0
    assert plan.id in ids(mate.id)

    rival = fresh_user("rival_boss", role_code="merchant_admin", merchant_id=second_merchant)
    r = api.as_id(rival.id).get(f"/api/monitor/tracks/{plan.id}")
    assert r.status_code == 403 and "回放" in r.get_json()["message"]
    assert plan.id not in ids(rival.id)

    # 机长没有商家归属时，只剩"本人执飞"这一条线。临时把两位种子机长的 merchant 摘掉，
    # 不建新账号：新账号名下挂着这条已审批的计划，拆卸时会撞 fk_ar_plan。
    own = db.session.get(User, plan.pilot_id)
    other = next(u for u in (db.session.get(User, user_ids[n]) for n in ("pilot", "pilot02"))
                 if u.id != own.id)
    for u in (own, other):
        u.merchant_id = None
    db.session.commit()
    try:
        assert api.as_id(own.id).get(f"/api/monitor/tracks/{plan.id}").get_json()["code"] == 0
        assert plan.id in ids(own.id)
        assert api.as_id(other.id).get(f"/api/monitor/tracks/{plan.id}").get_json()["code"] == 40300
        assert plan.id not in ids(other.id)
    finally:
        for u in (own, other):
            u.merchant_id = 1
        db.session.commit()


# ---------------------------------------------------------------- M-63 设备在线

def _devices(api, who="admin"):
    return api.as_(who).get("/api/monitor/devices").get_json()["data"]


def _row(data, device_type: int, device_id: int):
    return next(x for x in data["list"]
                if x["terminalType"] == device_type and x["id"] == device_id)


def test_aircraft_with_fresh_heartbeat_is_online(api, session, make_flight):
    """刚 tick 过 → updated_at 是"现在"，判在线；这条断言的意义是心跳口径真的来自库。"""
    _, plan = make_flight("flying")
    row = _row(_devices(api), 1, plan.aircraft_id)
    assert row["state"] == "online" and row["heartbeatAgeSec"] <= 5
    assert row["planNo"] == plan.plan_no and row["statusName"] == "执行中"
    assert row["lng"] is not None


def test_aircraft_flying_but_silent_is_lost(api, session, make_flight):
    """计划在飞、心跳却停在 10 分钟前 → 失联（运维要处置的那一类），不是"离线"。"""
    _, plan = make_flight("flying")
    _backdate("aircraft", plan.aircraft_id, 600)
    _backdate("flight_plan", plan.id, 600, "last_report_at")
    row = _row(_devices(api), 1, plan.aircraft_id)
    assert row["state"] == "lost" and row["stateName"] == "失联"
    assert row["lostReason"] is None                    # 系统判的，不是人标的


def test_idle_aircraft_without_telemetry_is_offline(api, session, make_flight):
    from app.models import FlightPlan
    _, plan = make_flight("flying")
    busy = {p.aircraft_id for p in db.session.query(FlightPlan).filter(FlightPlan.status == 3).all()}
    idle = (db.session.query(Aircraft)
            .filter(Aircraft.deleted == 0, ~Aircraft.id.in_(busy or {0})).first())
    assert idle is not None, "测试现场只有一架飞行器，造不出'停场无遥测'的对照"
    _backdate("aircraft", idle.id, 7200)
    row = _row(_devices(api), 1, idle.id)
    assert row["state"] == "offline" and row["planNo"] is None


def test_ground_station_state_comes_from_operation_status(api):
    vp = db.session.get(Vertiport, 1)
    assert _row(_devices(api), 2, vp.id)["state"] == "online"
    vp.status = 3
    db.session.commit()
    row = _row(_devices(api), 2, vp.id)
    assert row["state"] == "offline" and row["statusName"] == "关闭"
    assert row["heartbeatKind"] == "record"             # 场站没有遥测链路，别把它说成心跳


def test_mark_and_clear_lost_flag(api, session, make_flight):
    """人工标记盖过系统判定，解除后立刻回到按心跳推出来的状态。"""
    _, plan = make_flight("flying")
    did = plan.aircraft_id
    body = {"terminalType": 1, "deviceId": did, "reason": "图传丢失，仅链路有遥测"}
    assert api.as_("admin").post("/api/monitor/devices/lost-mark", json=body).get_json()["code"] == 0

    row = _row(_devices(api), 1, did)
    assert row["state"] == "lost" and row["lostReason"] == body["reason"]
    assert row["lostBy"] == "平台管理员" and row["lostAt"]

    # 重复标记走 ON DUPLICATE KEY：不新增行，只把原因改掉
    assert api.as_("admin").post("/api/monitor/devices/lost-mark",
                                 json={**body, "reason": "已切换备降场站"}).get_json()["code"] == 0
    assert db.session.execute(text("SELECT COUNT(*) FROM device_lost_mark")).scalar() == 1
    assert _row(_devices(api), 1, did)["lostReason"] == "已切换备降场站"

    api.as_("admin").delete(f"/api/monitor/devices/lost-mark?terminalType=1&deviceId={did}")
    assert _row(_devices(api), 1, did)["state"] == "online"


def test_mark_lost_validates_input(api, session, make_flight):
    _, plan = make_flight("flying")
    bad = api.as_("admin").post("/api/monitor/devices/lost-mark",
                                json={"terminalType": 9, "deviceId": plan.aircraft_id, "reason": "x"})
    assert bad.get_json()["code"] == 40000
    missing = api.as_("admin").post("/api/monitor/devices/lost-mark",
                                    json={"terminalType": 1, "deviceId": 999999, "reason": "x"})
    assert missing.get_json()["code"] == 40400
    assert db.session.execute(text("SELECT COUNT(*) FROM device_lost_mark")).scalar() == 0


def test_devices_page_is_gated_to_ops_and_atc(api, fresh_user):
    assert api.as_("pilot").get("/api/monitor/devices").get_json()["code"] == 40300
    maintainer = fresh_user("maintainer01", role_code="merchant_maintainer")
    assert api.as_id(maintainer.id).get("/api/monitor/devices").get_json()["code"] == 0
