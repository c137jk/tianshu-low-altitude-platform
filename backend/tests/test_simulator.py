"""飞行仿真器（设计07）：按帧推进、倍速、5 类场景注入、到点落地必须走 D3 入口。

测试环境把仿真线程关掉（FLASK_SIM_DISABLED=1），全部靠显式 tick() 驱动，
所以这里把 SIM_SPEED_FACTOR 钉成 1×，用"回拨 actual_takeoff_at"来控制进度——
比调高倍速更可复现（倍速那条路径另有专门的用例验证生效与上限）。
"""
from datetime import datetime, timedelta

import pytest
from flask import current_app
from sqlalchemy import text

from app.common.scope import invalidate_cfg
from app.extensions import db
from app.models import Aircraft, EmergencyEvent, FlightPlan, FlightTrack, Order, ViolationRecord
from app.services import compliance_monitor, dispatch_service, flight_simulator


@pytest.fixture(autouse=True)
def _sim_env(monkeypatch):
    """每个用例前后把仿真器的进程内状态清空：它是模块级全局字典，不清就会跨用例串味。"""
    def _reset():
        flight_simulator._start_bat.clear()
        flight_simulator._prev_pos.clear()
        flight_simulator._frames.clear()
        flight_simulator._drift.clear()
        flight_simulator._scenario.clear()
        flight_simulator._scenario_default = None
        flight_simulator.set_paused(False)
    _reset()
    monkeypatch.setenv("SIM_SPEED_FACTOR", "1")
    yield
    _reset()


@pytest.fixture()
def set_cfg():
    """写 sys_config 并清 10s TTL 缓存（用例结束恢复原值）。"""
    originals: dict[str, str | None] = {}

    def apply(key: str, value: str | None):
        row = db.session.execute(
            text("SELECT config_value FROM sys_config WHERE config_key=:k"),
            {"k": key}).scalar()
        originals.setdefault(key, row)
        if row is None:
            db.session.execute(
                text("INSERT INTO sys_config (config_group, config_key, config_value, value_type) "
                     "VALUES ('sim',:k,:v,'number')"), {"k": key, "v": value})
        else:
            db.session.execute(
                text("UPDATE sys_config SET config_value=:v WHERE config_key=:k"),
                {"k": key, "v": value})
        db.session.commit()
        invalidate_cfg(key)

    yield apply
    for key, value in originals.items():
        if value is not None:
            db.session.execute(
                text("UPDATE sys_config SET config_value=:v WHERE config_key=:k"),
                {"k": key, "v": value})
        else:
            db.session.execute(text("DELETE FROM sys_config WHERE config_key=:k"), {"k": key})
    db.session.commit()
    invalidate_cfg()


def _rewind(plan: FlightPlan, fraction: float) -> None:
    """把起飞时刻往前挪，让下一次 tick 的进度≈fraction（倍速固定 1× 时最直观）。"""
    plan.actual_takeoff_at = datetime.now() - timedelta(
        seconds=float(plan.est_duration_sec or 120) * fraction)
    db.session.commit()


def _frames_of(plan_id: int) -> list[FlightTrack]:
    return (db.session.query(FlightTrack).filter(FlightTrack.plan_id == plan_id)
            .order_by(FlightTrack.track_time.asc()).all())


# ---------------------------------------------------------------- 推进与落地

def test_tick_moves_aircraft_and_writes_one_frame_per_tick(session, make_flight):
    _, plan = make_flight("flying")
    start = (float(plan.current_lng), float(plan.current_lat))
    battery_before = plan.current_battery

    _rewind(plan, 0.2)
    assert flight_simulator.tick() == 1
    assert flight_simulator.tick() == 1
    frames = _frames_of(plan.id)
    assert len(frames) == 2                      # 设计07：1 tick 1 帧，不是每 4 tick 一帧
    moved = (float(plan.current_lng), float(plan.current_lat))
    assert moved != start
    assert plan.last_report_at is not None
    assert plan.current_battery < battery_before
    assert frames[1].altitude > 0 and frames[1].speed > 0
    ac = db.session.get(Aircraft, plan.aircraft_id)
    assert ac.battery == plan.current_battery     # 台账电量跟着遥测同步
    assert ac.online == 1 and ac.status == 2


def test_landing_goes_through_d3_entry(session, make_flight, monkeypatch):
    """进度到 1 的落库动作必须由 dispatch_service.finish_landing 完成（状态机唯一入口）。"""
    calls: list[int] = []
    real = dispatch_service.finish_landing

    def spy(p):
        calls.append(p.id)
        return real(p)

    monkeypatch.setattr(dispatch_service, "finish_landing", spy)
    order, plan = make_flight("flying")
    _rewind(plan, 1.2)                            # 已经超过全程 → 本帧落地
    flight_simulator.tick()

    assert calls == [plan.id]
    assert plan.status == 4 and plan.actual_landing_at is not None
    assert db.session.get(Order, order.id).status == 6
    ac = db.session.get(Aircraft, plan.aircraft_id)
    assert ac.status == 1 and ac.current_vertiport_id == plan.landing_vertiport_id
    assert ac.task_count == 1
    # 收尾要清掉进程内缓存，否则同名计划下次起飞会继承上一架的电量基线
    assert plan.id not in flight_simulator._frames
    assert plan.id not in flight_simulator._start_bat


def test_pause_stops_progress(session, make_flight):
    _, plan = make_flight("flying")
    _rewind(plan, 0.3)
    flight_simulator.set_paused(True)
    assert flight_simulator.tick() == 0
    assert _frames_of(plan.id) == []
    assert flight_simulator.status()["paused"] is True
    flight_simulator.set_paused(False)
    assert flight_simulator.tick() == 1
    assert len(_frames_of(plan.id)) == 1


def test_frame_cap_protects_partition_table(session, make_flight, set_cfg):
    """落帧上限：长时间在飞不能把 flight_track 分区表刷爆。"""
    set_cfg("sim_frame_points", "2")
    _, plan = make_flight("flying")
    for i in range(1, 5):
        _rewind(plan, 0.1 * i)
        flight_simulator.tick()
    assert len(_frames_of(plan.id)) == 2
    assert plan.status == 3                      # 帧到上限只是停止记录，任务本身继续飞


def test_paused_plan_is_skipped_not_error(session, make_flight):
    _, plan = make_flight("approved")            # 没起飞：route/actual_takeoff_at 不满足推进条件
    assert plan.actual_takeoff_at is None
    assert flight_simulator.tick() == 0


# ---------------------------------------------------------------- 场景注入

def test_no_fly_injection_creates_violation_emergency_and_marked_frame(session, make_flight):
    """注入"闯入禁飞区"→ 台账、应急事件、轨迹打标三处都要出现，这是告警链路的地基。"""
    _, plan = make_flight("flying")
    flight_simulator.inject("no_fly", plan.id)
    assert flight_simulator.scenario_for(plan.id) == "no_fly"
    _rewind(plan, 0.5)
    flight_simulator.tick()

    frames = _frames_of(plan.id)
    assert frames and frames[-1].is_violation == 1
    assert frames[-1].violation_type == "no_fly_intrusion"
    rec = db.session.query(ViolationRecord).filter_by(plan_id=plan.id, violation_type=1).first()
    assert rec is not None and rec.status == 1
    assert (db.session.query(EmergencyEvent)
            .filter_by(plan_id=plan.id, event_type=2).count()) == 1


def test_deviate_injection_exceeds_deviation_limit(session, make_flight):
    """偏航场景是"每帧再横移 25m/18m"：位移累积越过 100m 阈值才入账，所以要走若干帧。"""
    _, plan = make_flight("flying")
    flight_simulator.inject("deviate", plan.id)
    dev = 0.0
    for i in range(1, 11):
        _rewind(plan, 0.06 * i)                  # 最高推到 60% 进度，不会自己落地
        flight_simulator.tick()
        dev = compliance_monitor.deviation_m(plan.route_json,
                                            float(plan.current_lng), float(plan.current_lat))
    assert plan.status == 3
    assert dev is not None and dev > 100, f"10 帧后仍只偏 {dev}m，注入位移或阈值口径变了"
    assert (db.session.query(ViolationRecord)
            .filter_by(plan_id=plan.id, violation_type=2).count()) == 1
    last = _frames_of(plan.id)[-1]
    assert last.is_violation == 1 and last.violation_type == "route_deviation"


def test_low_battery_injection_raises_return_threshold_alarm(session, make_flight):
    _, plan = make_flight("flying")
    plan.current_battery = 60                    # 起飞电量基线压低，才能演到"低于返航阈值"
    db.session.commit()
    flight_simulator.inject("low_battery", plan.id)
    _rewind(plan, 0.95)
    flight_simulator.tick()
    assert plan.current_battery <= 20
    ev = (db.session.query(EmergencyEvent)
          .filter_by(plan_id=plan.id, event_type=3).first())
    assert ev is not None and ev.level == 2
    assert "返航阈值" in ev.description


def test_bad_weather_injection_lifts_altitude_into_violation(session, make_flight):
    _, plan = make_flight("flying")
    plan.altitude_m = 100                        # 100×1.35=135 > 全局限高 120
    db.session.commit()
    flight_simulator.inject("bad_weather", plan.id)
    _rewind(plan, 0.5)
    flight_simulator.tick()
    assert float(plan.current_altitude) == pytest.approx(135, abs=1)
    assert (db.session.query(EmergencyEvent)
            .filter_by(plan_id=plan.id, event_type=4).count()) == 1
    assert (db.session.query(ViolationRecord)
            .filter_by(plan_id=plan.id, violation_type=4).count()) == 1


def test_link_loss_stops_telemetry_but_keeps_alarm(session, make_flight):
    """失联 = 遥测断供：不能继续伪造位置帧，但应急事件必须立起来。"""
    _, plan = make_flight("flying")
    ac = db.session.get(Aircraft, plan.aircraft_id)
    ac.online = 1
    reported_at = plan.last_report_at
    db.session.commit()

    flight_simulator.inject("link_loss", plan.id)
    _rewind(plan, 0.5)
    flight_simulator.tick()

    assert _frames_of(plan.id) == []
    assert ac.online == 0
    assert plan.last_report_at == reported_at
    assert (db.session.query(EmergencyEvent)
            .filter_by(plan_id=plan.id, event_type=1).count()) == 1
    assert flight_simulator.status()["flying"][0]["scenario"] == "link_loss"


def test_inject_default_applies_to_all_and_reset_clears(session, make_flight):
    _, plan = make_flight("flying")
    assert flight_simulator.inject("no_fly")["scenario"] == "no_fly"      # 不给 planId → 设为默认
    assert flight_simulator.scenario_for(plan.id) == "no_fly"
    assert flight_simulator.scenario_for(999999) == "no_fly"              # 对所有在飞生效
    flight_simulator.inject(None)
    assert flight_simulator.scenario_for(plan.id) == "normal"
    assert flight_simulator.inject("bogus")["error"] == "未知场景"


def test_emergencies_auto_close_on_simulated_landing(session, make_flight):
    """仿真落地时要把该计划的应急事件一并解除，否则大屏会一直挂着红色告警。"""
    _, plan = make_flight("flying")
    compliance_monitor.raise_emergency(plan, 2, 2, "闯禁飞")
    _rewind(plan, 1.5)
    flight_simulator.tick()
    assert plan.status == 4
    ev = db.session.query(EmergencyEvent).filter_by(plan_id=plan.id).first()
    assert ev.status == 3 and ev.closed_at is not None


# ---------------------------------------------------------------- 倍速与参数

def test_speed_factor_reads_sys_config_when_env_absent(session, set_cfg, monkeypatch):
    """倍速优先级：环境变量 > 系统参数 > .env SIM_DEFAULT_SPEED > 1。"""
    monkeypatch.delenv("SIM_SPEED_FACTOR", raising=False)
    set_cfg("sim_speed_factor", "120")
    assert flight_simulator.speed_factor() == 120
    set_cfg("sim_speed_factor", "60")
    assert flight_simulator.speed_factor() == 60
    monkeypatch.setenv("SIM_SPEED_FACTOR", "1")
    assert flight_simulator.speed_factor() == 1        # 测试环境靠这条把自己钉回 1×
    monkeypatch.setenv("SIM_SPEED_FACTOR", "abc")
    monkeypatch.setitem(current_app.config, "SIM_DEFAULT_SPEED", 30)
    assert flight_simulator.speed_factor() == 60       # 非法环境变量→忽略，仍读系统参数


def test_high_speed_factor_finishes_short_flight_in_few_ticks(session, make_flight, monkeypatch):
    monkeypatch.setenv("SIM_SPEED_FACTOR", "60")
    order, plan = make_flight("flying")
    plan.est_duration_sec = 600                 # 10 分钟航程
    db.session.commit()
    # 真实时间只过了 0.3s：60× 下约推进 18s 仿真时间，仍在途中
    flight_simulator.tick()
    assert plan.status == 3
    _rewind(plan, 12 / 60)                      # 相当于真实过了 12s → 60× 后 720s > 600s
    flight_simulator.tick()
    assert plan.status == 4 and db.session.get(Order, order.id).status == 6


def test_tick_seconds_follows_config_with_sane_bounds(session, set_cfg):
    set_cfg("track_frame_interval_sec", "0.01")
    assert flight_simulator.tick_seconds() == 0.2          # 下限保护：不允许把线程打成忙等
    set_cfg("track_frame_interval_sec", "999")
    assert flight_simulator.tick_seconds() == 60.0         # 上限保护
    set_cfg("track_frame_interval_sec", "1")
    assert flight_simulator.tick_seconds() == 1.0


def test_status_payload_reports_flying_plans(session, make_flight):
    _, plan = make_flight("flying")
    _rewind(plan, 0.5)
    flight_simulator.inject("low_battery", plan.id)
    st = flight_simulator.status()
    assert st["running"] is False                # 测试进程里线程被 FLASK_SIM_DISABLED 关掉
    assert st["tickSec"] == 1.0 and st["speedFactor"] == 1
    row = next(f for f in st["flying"] if f["planId"] == plan.id)
    assert row["scenario"] == "low_battery" and row["scenarioName"] == "低电量"
    assert 0 < row["progress"] <= 1
