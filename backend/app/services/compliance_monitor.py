"""飞行中合规监测（设计05 空域合规 + 设计07 仿真闭环）：把每一帧位置变成违规台账。

差距分析里的原话是"违规记录只能人工录入，自动检测没有实现"。这里补上判定与入账：

  1 闯禁飞   位置落在 status=1 的禁飞(1)/临时管制(4) 空域内（临时管制还看管制时段）
  2 偏航     位置到 plan.route_json 折线的最近距离 > sys_config.route_deviation_limit_m
  3 超时     时间超过 plan.plan_end_at + sys_config.overschedule_tolerance_min
  4 超高     高度 > 所在限飞空域 altitude_max，或 > 全局 altitude_limit_max_m
  5 围栏     电子围栏（geofence）限入/限出/限高并入上面四类入账；「接近告警」
             只给机长发一条站内信，不进台账（见 fence_findings）

去重口径：同一 plan + 同一 violation_type，只要台账里还有「未处理/处理中」的记录就不重复
         入账——否则 60 倍速下每秒都会新增一条，把违规台账刷成垃圾数据。轨迹帧仍然逐帧
         打标（is_violation / violation_type），回放页能看到"违规的是哪一段"。

阈值全部走 sys_config（系统参数页可改，10s TTL），不再写死在代码里。
"""
import math
import random
from datetime import datetime, timedelta

from app.common.scope import sys_cfg_num
from app.extensions import db
from app.models import Airspace, FlightPlan, Geofence, ViolationRecord
from app.models.compliance import EMERGENCY_TYPE, VIOLATION_TAG, VIOLATION_TYPE, EmergencyEvent
from app.services import dispatch_service, notify
from app.services.geo import binding_ceiling, point_in_polygon, polygon_rings

DEVIATION_LIMIT_M = 100      # 兜底默认（配置缺失时用）
ALTITUDE_LIMIT_M = 120
OVERSCHEDULE_TOL_MIN = 10

SEVERE_TYPES = (1, 4)        # 闯禁飞 / 超高：直接广播给平台侧

# 围栏"接近告警"不是过错，不进违规台账，所以不占 violation_type 的数字口径
FENCE_APPROACH = "fence_approach"


def _gen_no(prefix: str) -> str:
    """一帧内可能连着落多条违规/应急事件（偏航+超高），只到 3 位随机数会在同一秒撞唯一键。"""
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999999):06d}"


def _project(lng: float, lat: float, ref_lat: float) -> tuple[float, float]:
    """城市尺度下的等距柱面近似（米）。"""
    return lng * 111320.0 * math.cos(math.radians(ref_lat)), lat * 110540.0


def _point_segment_m(px: float, py: float, ax: float, ay: float,
                     bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def deviation_m(route: list[dict] | None, lng: float, lat: float) -> float | None:
    """当前位置到计划航线的最近距离（米）；没有航线数据返回 None（不判偏航）。"""
    if not route or len(route) < 2:
        return None
    ref = float(route[0].get("lat", lat))
    px, py = _project(lng, lat, ref)
    best = float("inf")
    for a, b in zip(route, route[1:]):
        ax, ay = _project(float(a["lng"]), float(a["lat"]), ref)
        bx, by = _project(float(b["lng"]), float(b["lat"]), ref)
        best = min(best, _point_segment_m(px, py, ax, ay, bx, by))
    return round(best, 1)


def hit_airspaces(lng: float, lat: float, types: tuple[int, ...],
                  airspaces: list[Airspace] | None = None) -> list[Airspace]:
    """点命中的某类生效空域（默认查禁飞+临时管制）。"""
    rows = airspaces if airspaces is not None else dispatch_service.active_airspaces()
    hits = []
    for asp in rows:
        if asp.airspace_type not in types:
            continue
        for ring in polygon_rings(asp.geometry_json or {}):
            if point_in_polygon(lng, lat, ring):
                hits.append(asp)
                break
    return hits


def _ring_distance_m(ring: list, lng: float, lat: float) -> float:
    """点到多边形边界的最近距离（米）；在内部时也是到边界距离，用于接近告警。"""
    if not ring:
        return float("inf")
    ref = float(ring[0][1])
    px, py = _project(lng, lat, ref)
    pts = list(ring)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    best = float("inf")
    for a, b in zip(pts, pts[1:]):
        ax, ay = _project(float(a[0]), float(a[1]), ref)
        bx, by = _project(float(b[0]), float(b[1]), ref)
        best = min(best, _point_segment_m(px, py, ax, ay, bx, by))
    return best


def active_geofences(merchant_id: int | None) -> list[Geofence]:
    """生效围栏：平台级（merchant_id 为空）对全部商家生效，商家级的只管自己机队。"""
    q = db.session.query(Geofence).filter(Geofence.deleted == 0, Geofence.status == 1)
    if merchant_id:
        q = q.filter(db.or_(Geofence.merchant_id.is_(None), Geofence.merchant_id == merchant_id))
    else:
        q = q.filter(Geofence.merchant_id.is_(None))
    return q.all()


def fence_findings(plan: FlightPlan, lng: float, lat: float,
                   altitude: float) -> list[dict]:
    """电子围栏判定（需求 4.2.4-4）。

    1 限入   落在围栏内 → 违规入账
    2 限出   起飞航点在围栏内、当前位置飞出去了 → 违规入账（反过来不适用：
             本来就在围栏外的任务不该被自己的围栏罚）
    3 接近   进入 warn_distance_m 内 → advisory（只发一次站内信，不进台账）
    限高     围栏单独设了 altitude_limit 且被超过 → 超高入账（只在围栏范围内判，
             范围外的限高对整区飞行没意义，会把正常航线全部判成违规）
    """
    out: list[dict] = []
    route = plan.route_json or []
    for fence in active_geofences(plan.merchant_id):
        rings = polygon_rings(fence.geometry_json or {})
        if not rings:
            continue
        inside = any(point_in_polygon(lng, lat, r) for r in rings)
        name = f"电子围栏「{fence.fence_name}」"
        if inside and fence.altitude_limit and altitude > float(fence.altitude_limit):
            # 限高只在围栏范围内生效：否则一个 20km 外的码头围栏会把全区飞机都判超高
            out.append({"type": 4,
                        "desc": f"实际高度 {altitude:.0f}m，超过{name}限高 "
                                f"{float(fence.altitude_limit):.0f}m"})
        if fence.fence_type == 1 and inside:
            out.append({"type": 1, "desc": f"位置进入{name}（限入围栏）范围内"})
        elif fence.fence_type == 2 and route and not inside:
            start = route[0]
            if any(point_in_polygon(float(start["lng"]), float(start["lat"]), r) for r in rings):
                out.append({"type": 2, "desc": f"航迹飞出{name}（限出围栏）设定的活动范围"})
        elif fence.fence_type == 3:
            near = min((_ring_distance_m(r, lng, lat) for r in rings), default=float("inf"))
            limit = fence.warn_distance_m or 200
            if not inside and near <= limit:
                out.append({"type": FENCE_APPROACH, "advisory": True, "fence_id": fence.id,
                            "desc": f"距{name} 边界 {near:.0f}m，已小于告警距离 {limit}m"})
    return out


def findings(plan: FlightPlan, lng: float, lat: float, altitude: float | None,
             now: datetime | None = None) -> list[dict]:
    """一帧位置的全部判定（只读，不落库）。返回 [{"type": 1, "desc": "..."}]；
    围栏接近告警带 advisory=True，调用方（打标/入账）要跳过。"""
    now = now or datetime.now()
    alt = float(altitude or 0)
    airspaces = dispatch_service.active_airspaces()
    out: list[dict] = []

    forbidden = hit_airspaces(lng, lat, (1, 4), airspaces)
    if forbidden:
        names = "、".join(a.airspace_name for a in forbidden)
        out.append({"type": 1, "airspace_id": forbidden[0].id,
                    "desc": f"飞行器位置闯入「{names}」（禁飞/管制空域），高度 {alt:.0f}m"})

    dev = deviation_m(plan.route_json, lng, lat)
    limit = sys_cfg_num("route_deviation_limit_m", DEVIATION_LIMIT_M, int)
    if dev is not None and dev > limit:
        out.append({"type": 2, "desc": f"偏离计划航线 {dev:.0f}m，超过阈值 {limit}m"})

    tol = sys_cfg_num("overschedule_tolerance_min", OVERSCHEDULE_TOL_MIN, int)
    if plan.plan_end_at and now > plan.plan_end_at + timedelta(minutes=tol):
        out.append({"type": 3,
                    "desc": f"超出申报时段 {(now - plan.plan_end_at).total_seconds() / 60:.0f} 分钟"
                            f"（计划 {plan.plan_start_at:%H:%M}~{plan.plan_end_at:%H:%M}）"})

    global_max = sys_cfg_num("altitude_limit_max_m", ALTITUDE_LIMIT_M, int)
    cands: list[tuple[str, float | None]] = [("全局限高", global_max)]
    for asp in hit_airspaces(lng, lat, (2, 3), airspaces):
        if asp.altitude_max is not None:
            kind = "限飞空域" if asp.airspace_type == 2 else "可飞空域"
            cands.append((f"{kind}「{asp.airspace_name}」", asp.altitude_max))
    # 取上限最低的那个来报：以前用第一个命中配最大超限值，两个空域套叠时会把
    # 责任推到宽松的那个上（"超过甲上限 60m"，而 60 是乙的上限）。
    bound = binding_ceiling(cands, alt)
    if bound:
        out.append({"type": 4,
                    "desc": f"实际高度 {alt:.0f}m，超过{bound[0]} {bound[1]:.0f}m"
                            f"（超出 {alt - bound[1]:.0f}m）"})

    out += fence_findings(plan, lng, lat, alt)

    for item in out:
        if not item.get("advisory"):
            item["tag"] = VIOLATION_TAG[item["type"]]
    return out


def _open_record(plan_id: int, vtype: int) -> ViolationRecord | None:
    return (db.session.query(ViolationRecord)
            .filter(ViolationRecord.plan_id == plan_id,
                    ViolationRecord.violation_type == vtype,
                    ViolationRecord.status.in_((1, 2)),
                    ViolationRecord.deleted == 0)
            .first())


def record_violation(plan: FlightPlan, finding: dict, lng: float, lat: float,
                     track_id: int | None = None, now: datetime | None = None
                     ) -> ViolationRecord | None:
    """写一条违规台账；同计划同类型未结案时返回 None（不重复入账）。"""
    now = now or datetime.now()
    vtype = int(finding["type"])
    if _open_record(plan.id, vtype) is not None:
        return None
    rec = ViolationRecord(
        violation_no=_gen_no("VR"), merchant_id=plan.merchant_id,
        order_id=plan.order_id, plan_id=plan.id,
        aircraft_id=plan.aircraft_id, pilot_id=plan.pilot_id,
        violation_type=vtype, lng=round(lng, 6), lat=round(lat, 6),
        track_id=track_id, violation_time=now,
        description=(finding.get("desc") or VIOLATION_TYPE.get(vtype))[:500],
        penalty_type=None, status=1,
    )
    db.session.add(rec)
    db.session.flush()
    title = f"飞行违规：{VIOLATION_TYPE.get(vtype)}"
    pos = "经度%.6f 纬度%.6f" % (lng, lat)
    body = f"{plan.task_name or plan.plan_no}｜{finding.get('desc') or ''}｜{pos}｜台账号 {rec.violation_no}"
    if plan.pilot_id:
        notify.notify(plan.pilot_id, title, body, biz_type="violation", biz_id=rec.id)
    if vtype in SEVERE_TYPES:
        notify.notify(0, title, body, biz_type="violation", biz_id=rec.id)  # 广播给平台侧
    return rec


def raise_emergency(plan: FlightPlan, event_type: int, level: int, description: str,
                    lng: float | None = None, lat: float | None = None,
                    now: datetime | None = None) -> EmergencyEvent | None:
    """产生应急事件（同计划同类型只保持一条未解除的）。"""
    now = now or datetime.now()
    exists = (db.session.query(EmergencyEvent)
              .filter(EmergencyEvent.plan_id == plan.id,
                      EmergencyEvent.event_type == event_type,
                      EmergencyEvent.status.in_((1, 2)),
                      EmergencyEvent.deleted == 0).first())
    if exists:
        return None
    ev = EmergencyEvent(
        event_no=_gen_no("EM"), order_id=plan.order_id, plan_id=plan.id,
        aircraft_id=plan.aircraft_id, event_type=event_type, level=level,
        lng=round(lng, 6) if lng is not None else None,
        lat=round(lat, 6) if lat is not None else None,
        description=description[:500], started_at=now, status=1,
    )
    db.session.add(ev)
    db.session.flush()
    notify.notify(0, f"应急事件：{EMERGENCY_TYPE.get(event_type)}（{'一般较大重大'[level - 1]}）",
                  f"{plan.task_name or plan.plan_no}｜{description}",
                  biz_type="emergency", biz_id=ev.id)
    return ev


def resolve_emergencies(plan: FlightPlan, measure: str = "任务结束自动解除",
                        now: datetime | None = None) -> int:
    """降落/场景恢复后关闭该计划的未解除事件。"""
    now = now or datetime.now()
    rows = (db.session.query(EmergencyEvent)
            .filter(EmergencyEvent.plan_id == plan.id,
                    EmergencyEvent.status.in_((1, 2)),
                    EmergencyEvent.deleted == 0).all())
    for ev in rows:
        ev.status = 3
        ev.closed_at = now
        ev.handle_measure = (measure or ev.handle_measure or "")[:500] or None
    return len(rows)


def annotate(plan: FlightPlan, track, now: datetime | None = None) -> list[dict]:
    """判定 + 给轨迹帧打标。必须在 INSERT 之前调用——

    flight_track 主键是 (id, track_time) 复合键且 track_time 带毫秒：
    插完再改字段，SQLAlchemy 会补发一条按主键匹配的 UPDATE，而库里的毫秒值
    与内存里的 datetime 未必一致，于是 0 rows matched，抛 StaleDataError 整帧回滚。
    """
    lng, lat = float(track.lng), float(track.lat)
    hits = findings(plan, lng, lat, float(track.altitude or 0), now)
    violations = [h for h in hits if not h.get("advisory")]
    if violations:
        track.is_violation = 1
        track.violation_type = violations[0]["tag"][:40]
    return hits


def _warn_fence_approach(plan: FlightPlan, hit: dict) -> None:
    """围栏接近告警：只提醒机长，一人一围栏只发一次（每帧都发会把信箱刷爆）。"""
    if not plan.pilot_id or hit.get("type") != FENCE_APPROACH:
        return
    if notify.exists("geofence_approach", plan.pilot_id, hit.get("fence_id")):
        return
    notify.notify(plan.pilot_id, "电子围栏接近告警",
                  f"{plan.task_name or plan.plan_no}｜{hit['desc']}",
                  biz_type="geofence_approach", biz_id=hit.get("fence_id"))


def commit_findings(plan: FlightPlan, track, hits: list[dict],
                    now: datetime | None = None) -> list[int]:
    """轨迹帧已落库后：写违规台账 + 应急事件 + 站内信。返回本次新增入账的类型。"""
    recorded: list[int] = []
    lng, lat = float(track.lng), float(track.lat)
    for f in hits:
        if f.get("advisory"):
            _warn_fence_approach(plan, f)
            continue
        rec = record_violation(plan, f, lng, lat, track_id=track.id, now=now)
        if rec is not None:
            recorded.append(f["type"])
            if f["type"] == 1:
                raise_emergency(plan, 2, 2, f["desc"], lng, lat, now)
    return recorded


def summarize(plan_ids: list[int]) -> dict:
    """给大屏/报告用：按类型统计未结案违规数。"""
    if not plan_ids:
        return {}
    rows = (db.session.query(ViolationRecord.violation_type, db.func.count(ViolationRecord.id))
            .filter(ViolationRecord.plan_id.in_(plan_ids), ViolationRecord.deleted == 0,
                    ViolationRecord.status.in_((1, 2)))
            .group_by(ViolationRecord.violation_type).all())
    return {VIOLATION_TYPE.get(t, str(t)): c for t, c in rows}
