"""飞行仿真器（设计07）：后台线程把「执行中」的计划沿 route_json 真实推进，并逐帧做合规判定。

四个口径（都是相对上一版的改动点）：
  1. 帧间隔读 sys_config.track_frame_interval_sec（默认 1s，系统参数页可改），不再写死 2s；
     倍速读 sys_config.sim_speed_factor（默认 60×），可用环境变量 SIM_SPEED_FACTOR 覆盖，
     没配就退回 .env 的 SIM_DEFAULT_SPEED —— 60 倍速下 10 分钟航程 10 秒飞完，演示才看得见全程。
  2. 每个 tick 落一帧轨迹（原来每 4 tick 才落一帧，回放页 8 秒一个点，与设计"1 秒一帧"不符），
     帧数上限 sys_config.sim_frame_points，防止长时间在飞的计划把分区表刷爆。
  3. 到点降落统一调 dispatch_service.finish_landing()（D3 状态机唯一入口），
     仿真器自己不再改 plan.status / order.status —— 两处实现迟早分叉。
  4. 每帧交给 compliance_monitor 判闯禁飞/偏航/超高/超时，自动落违规台账 + 轨迹打标 + 站内信；
     另支持 5 类场景注入（失联/闯禁飞/低电量/恶劣天气/偏航），用来演应急与告警链路。

前端不需要 WebSocket：把轮询设成和帧间隔一致（默认 1~2s）就能看到飞机连续移动。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta

from app.common.scope import sys_cfg_num
from app.extensions import db
from app.models import Aircraft, FlightPlan, Vertiport
from app.services import compliance_monitor, dispatch_service, track_service
from app.services.geo import bearing_deg, haversine_m, offset_meters, polygon_rings

log = logging.getLogger("flight_simulator")

DEFAULT_TICK_SEC = 1.0
SCENARIO_NAMES = {
    "normal": "正常巡航",
    "link_loss": "链路失联",
    "no_fly": "闯入禁飞区",
    "low_battery": "低电量",
    "bad_weather": "恶劣天气",
    "deviate": "航线偏航",
}

_lock = threading.Lock()
_running = False
_paused = False
_tick_no = 0
_start_bat: dict[int, float] = {}        # plan_id -> 起飞时刻电量%
_prev_pos: dict[int, tuple[float, float]] = {}
_frames: dict[int, int] = {}             # plan_id -> 已落帧数
_drift: dict[int, tuple[float, float]] = {}   # plan_id -> 注入造成的位移（米）
_scenario: dict[int, str] = {}           # plan_id -> 场景
_scenario_default: str | None = None     # 未单独指定时套用（作用于所有在飞计划）


# ---------------------------------------------------------------- 几何

def _polyline_pos(route: list[dict], progress: float) -> tuple[float, float]:
    """按进度（0~1）在折线长度上插值出 (lng, lat)。"""
    if not route:
        return 0.0, 0.0
    if len(route) == 1 or progress <= 0:
        return float(route[0]["lng"]), float(route[0]["lat"])
    if progress >= 1:
        return float(route[-1]["lng"]), float(route[-1]["lat"])

    seg_lens, total = [], 0.0
    for a, b in zip(route, route[1:]):
        d = haversine_m(float(a["lng"]), float(a["lat"]), float(b["lng"]), float(b["lat"]))
        seg_lens.append(d)
        total += d
    target = total * progress
    acc = 0.0
    for i, d in enumerate(seg_lens):
        if acc + d >= target:
            t = 0 if d == 0 else (target - acc) / d
            a, b = route[i], route[i + 1]
            return (float(a["lng"]) + (float(b["lng"]) - float(a["lng"])) * t,
                    float(a["lat"]) + (float(b["lat"]) - float(a["lat"])) * t)
        acc += d
    return float(route[-1]["lng"]), float(route[-1]["lat"])


def _altitude(progress: float, cruise: float) -> float:
    if progress < 0.12:                     # 爬升段
        return cruise * (progress / 0.12)
    if progress > 0.88:                      # 下降段
        return cruise * (1 - (progress - 0.88) / 0.12)
    return cruise


def _forbidden_centroid(lng: float, lat: float) -> tuple[float, float] | None:
    """离当前位置最近的禁飞/管制空域中心（用于"闯入禁飞区"场景注入）。"""
    best, best_d = None, float("inf")
    for asp in dispatch_service.active_airspaces():
        if asp.airspace_type not in (1, 4):
            continue
        for ring in polygon_rings(asp.geometry_json or {}):
            if not ring:
                continue
            cx = sum(p[0] for p in ring) / len(ring)
            cy = sum(p[1] for p in ring) / len(ring)
            d = haversine_m(lng, lat, cx, cy)
            if d < best_d:
                best_d, best = d, (cx, cy)
    return best


# ---------------------------------------------------------------- 场景注入

def scenario_for(plan_id: int) -> str:
    return _scenario.get(plan_id) or _scenario_default or "normal"


def inject(scenario: str | None, plan_id: int | None = None) -> dict:
    """注入场景；plan_id 为空表示设为默认（作用于所有在飞计划）。"""
    global _scenario_default
    key = plan_id if plan_id else 0
    if scenario in (None, "", "normal", "reset"):
        if plan_id:
            _scenario.pop(plan_id, None)
            _drift.pop(plan_id, None)
        else:
            _scenario_default = None
            _scenario.clear()
            _drift.clear()
        return {"planId": key, "scenario": "normal", "cleared": True}
    if scenario not in SCENARIO_NAMES:
        return {"planId": key, "scenario": scenario, "error": "未知场景"}
    if plan_id:
        _scenario[plan_id] = scenario
    else:
        _scenario_default = scenario
    return {"planId": key, "scenario": scenario,
            "scenarioName": SCENARIO_NAMES[scenario]}


def tick_seconds() -> float:
    sec = sys_cfg_num("track_frame_interval_sec", DEFAULT_TICK_SEC, float)
    return max(0.2, min(60.0, float(sec)))


def speed_factor() -> int:
    """倍速：环境变量 > 系统参数 > .env SIM_DEFAULT_SPEED > 1。"""
    env = os.environ.get("SIM_SPEED_FACTOR")
    if env:
        try:
            return max(1, int(float(env)))
        except ValueError:
            pass
    from flask import current_app
    fallback = 1
    try:
        fallback = int(current_app.config.get("SIM_DEFAULT_SPEED", 1) or 1)
    except (RuntimeError, TypeError, ValueError):
        pass
    return max(1, int(sys_cfg_num("sim_speed_factor", fallback, int)))


# ---------------------------------------------------------------- 单帧推进

def _battery_pct(plan: FlightPlan, progress: float, scenario: str) -> float:
    if plan.id not in _start_bat:
        _start_bat[plan.id] = float(plan.current_battery or 90)
    base = _start_bat[plan.id]
    if scenario == "low_battery":
        return max(3.0, base - 70.0 * progress)          # 电量骤降，模拟电池异常
    return max(40.0, base - 25.0 * progress)             # 全程约耗 25%


def _apply_scenario(plan: FlightPlan, progress: float, lng: float, lat: float,
                   scenario: str) -> tuple[float, float]:
    """把场景造成的位置偏移叠加到理论位置上（位移随帧累积，模拟持续偏离）。"""
    if scenario == "no_fly":
        target = _forbidden_centroid(lng, lat)
        if target:
            return target                      # 直接演"已进入禁飞区内部"
    if scenario == "deviate":
        dx, dy = _drift.get(plan.id, (0.0, 0.0))
        dx, dy = dx + 25.0, dy + 18.0          # 每帧继续横移，几帧内越过偏航阈值
        _drift[plan.id] = (dx, dy)
        return offset_meters(lng, lat, dx, dy)
    return lng, lat


def _tick_plan(plan: FlightPlan, speed: int, real_now: datetime) -> None:
    scenario = scenario_for(plan.id)
    duration = float(plan.est_duration_sec or 120)
    sim_elapsed = (real_now - plan.actual_takeoff_at).total_seconds() * speed
    if scenario == "bad_weather":
        sim_elapsed *= 0.55                    # 逆风降速，同样航程飞得更久
    progress = max(0.0, min(sim_elapsed / duration, 1.0))
    sim_now = plan.actual_takeoff_at + timedelta(seconds=sim_elapsed)

    aircraft = db.session.get(Aircraft, plan.aircraft_id)

    if scenario == "link_loss":
        # 失联：遥测断供——不回写位置/电量，只把链路状态打下来并起应急事件
        if aircraft:
            aircraft.online = 0
        last = f"，最后上报 {plan.last_report_at:%H:%M:%S}" if plan.last_report_at else ""
        compliance_monitor.raise_emergency(
            plan, 1, 3,
            f"{aircraft.aircraft_no if aircraft else '飞行器'} 与地面站失去联系{last}")
        db.session.commit()
        return

    lng, lat = _polyline_pos(plan.route_json, progress)
    lng, lat = _apply_scenario(plan, progress, lng, lat, scenario)
    cruise_alt = float(plan.altitude_m or 100)
    altitude = _altitude(progress, cruise_alt)
    if scenario == "bad_weather":
        altitude = cruise_alt * 1.35           # 风切变抬升，演"超高"
    battery = _battery_pct(plan, progress, scenario)
    speed_ms = float(plan.cruise_speed or 12)

    plan.current_lng, plan.current_lat = round(lng, 6), round(lat, 6)
    plan.current_altitude = round(altitude, 1)
    plan.current_speed = round(speed_ms, 1)
    plan.current_battery = round(battery)
    plan.last_report_at = real_now
    if aircraft:
        aircraft.online = 1
        aircraft.status = 2
        aircraft.current_lng, aircraft.current_lat = round(lng, 6), round(lat, 6)
        aircraft.battery = max(0, min(100, int(round(battery))))

    if scenario == "low_battery":
        threshold = sys_cfg_num("battery_return_threshold", 20, int)
        if battery <= threshold:
            compliance_monitor.raise_emergency(
                plan, 3, 1 if battery > threshold * 0.5 else 2,
                f"电量 {battery:.0f}% 低于返航阈值 {threshold}%，建议就近降落/换电")
    if scenario == "bad_weather":
        compliance_monitor.raise_emergency(
            plan, 4, 2, "注入恶劣天气场景：侧风与能见度恶化，实际高度被动超出巡航高度")

    frame_limit = int(sys_cfg_num("sim_frame_points", 600, int))
    if _frames.get(plan.id, 0) < frame_limit:
        # 落帧与合规判定走 track_service：真机 GCS 上报调的是同一个函数，只是 source 不同
        result = track_service.persist_frames(plan, [{
            "t": sim_now.replace(microsecond=0),
            "lng": round(lng, 6), "lat": round(lat, 6), "altitude": round(altitude, 2),
            "speed": round(speed_ms, 2),
            "direction": bearing_deg(_prev_pos.get(plan.id), lng, lat),
            "battery": max(0, min(100, int(round(battery)))),
        }], source=1)
        _frames[plan.id] = _frames.get(plan.id, 0) + result["accepted"]
    _prev_pos[plan.id] = (lng, lat)

    if progress >= 1.0:
        _finish_landing(plan, aircraft, real_now)
    else:
        db.session.commit()


def _finish_landing(plan: FlightPlan, aircraft: Aircraft | None, real_now: datetime) -> None:
    """到达终点：落库动作全部交给 D3（计划→4、订单→6、飞机归位），仿真器只收尾。"""
    vp = db.session.get(Vertiport, plan.landing_vertiport_id)
    plan.last_report_at = real_now
    resolved = compliance_monitor.resolve_emergencies(
        plan, measure="按仿真进度安全降落于" + (vp.station_name if vp else "降落场"))
    dispatch_service.finish_landing(plan)       # 内部已 commit
    if aircraft and scenario_for(plan.id) != "link_loss":
        aircraft.online = 1
        db.session.commit()
    for cache in (_start_bat, _prev_pos, _frames, _drift, _scenario):
        cache.pop(plan.id, None)
    log.info("sim: plan %s auto-landed at %s（解除应急事件 %d 条）",
             plan.plan_no, vp.station_name if vp else "-", resolved)


def tick(app=None) -> int:
    """推进一轮：返回本轮处理的计划数。

    后台线程调用时传 app（线程里没有活动上下文）；脚本/接口里已经在上下文内就别传，
    否则会嵌套 push 一个上下文，Flask-SQLAlchemy 会在内层退出时把 session 拆掉。
    """
    if app is not None:
        with app.app_context():
            return _tick_body()
    return _tick_body()


def _tick_body() -> int:
    global _tick_no
    _tick_no += 1
    if _paused:
        return 0
    speed = speed_factor()
    real_now = datetime.now()
    plans = (db.session.query(FlightPlan)
             .filter(FlightPlan.status == 3, FlightPlan.deleted == 0)
             .order_by(FlightPlan.id.asc()).all())
    handled = 0
    for plan in plans:
        if not plan.route_json or not plan.actual_takeoff_at:
            continue
        try:
            _tick_plan(plan, speed, real_now)
            handled += 1
        except Exception:
            db.session.rollback()
            log.exception("sim: plan %s tick failed", plan.plan_no)
    return handled


def set_paused(paused: bool) -> bool:
    global _paused
    _paused = bool(paused)
    return _paused


def status() -> dict:
    flying = (db.session.query(FlightPlan)
              .filter(FlightPlan.status == 3, FlightPlan.deleted == 0).all())
    return {
        "running": _running,
        "paused": _paused,
        "tickSec": tick_seconds(),
        "speedFactor": speed_factor(),
        "defaultScenario": _scenario_default or "normal",
        "tickNo": _tick_no,
        "flying": [{
            "planId": p.id, "planNo": p.plan_no, "taskName": p.task_name,
            "aircraftId": p.aircraft_id, "progress": _progress_of(p),
            "scenario": scenario_for(p.id),
            "scenarioName": SCENARIO_NAMES.get(scenario_for(p.id), "正常巡航"),
            "frames": _frames.get(p.id, 0),
        } for p in flying],
    }


def _progress_of(plan: FlightPlan) -> float:
    if not plan.actual_takeoff_at:
        return 0.0
    elapsed = (datetime.now() - plan.actual_takeoff_at).total_seconds() * speed_factor()
    return round(max(0.0, min(elapsed / float(plan.est_duration_sec or 120), 1.0)), 4)


def _loop(app):
    while True:
        started = time.time()
        try:
            tick(app)
        except Exception:                        # 仿真器不能拖垮主服务
            log.exception("sim tick failed")
        with app.app_context():                  # 帧间隔可能刚被系统参数页改过
            interval = tick_seconds()
        time.sleep(max(0.05, interval - (time.time() - started)))


def start(app):
    """应用工厂里调用：启动仿真线程（Werkzeug reloader 主进程才起，避免双份）。"""
    global _running
    with _lock:
        if _running:
            return
        if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            return
        if os.environ.get("FLASK_SIM_DISABLED"):
            log.info("flight simulator disabled by FLASK_SIM_DISABLED")
            return
        t = threading.Thread(target=_loop, args=(app,), name="flight-sim", daemon=True)
        t.start()
        _running = True
        with app.app_context():                  # 读配置要 DB，工厂里此时没有活动上下文
            log.info("flight simulator started (tick=%.1fs, speed=%dx)",
                     tick_seconds(), speed_factor())
