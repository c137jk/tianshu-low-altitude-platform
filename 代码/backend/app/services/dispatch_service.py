"""D3 调度与飞行任务领域服务（设计02/03：状态机唯一入口，任何端只能调动作接口）。

订单 × 飞行计划合流状态机：
  订单 1待接单 ─accept→ 2待调度
       2待调度 ─建方案(计划0待申报)─submit→ 3待审批(计划1)
       3待审批 ─approve→ 4待起飞(计划2) ─assign下发机长
              └reject→ 2待调度(计划5已驳回，可改后重新申报)
       4待起飞 ─takeoff→ 5飞行中(计划3执行中)
       5飞行中 ─land→ 6已送达(计划4已完成)；客户 confirm 后 7已完成（D2）
  计划 0/1/2 可 cancel→6，订单回到 2待调度（未起飞前）
"""
import math
import random
from datetime import datetime, timedelta
from decimal import Decimal

from app.adapters.gcs import gcs_adapter
from app.adapters.utm import utm_adapter
from app.adapters.weather import weather_adapter
from app.common.crypto import decrypt_phone
from app.common.errors import ApiError
from app.common.response import BizCode
from app.common.scope import sys_cfg_num, user_ids_with_perm
from app.extensions import db
from app.models import (
    Order, FlightPlan, ApprovalRecord, Aircraft, Vertiport, Airspace, User,
)
from app.models.rbac import Role, user_role
from app.services import notify, order_service
from app.services.geo import (
    haversine_m, sample_segment, check_route_against_airspaces, binding_ceiling,
)

PLAN_STATUS = {
    0: "待申报", 1: "待审批", 2: "已批准待执行", 3: "执行中",
    4: "已完成", 5: "已驳回", 6: "已取消",
}
AIRCRAFT_STATUS = {1: "闲置", 2: "执行中", 3: "充换电", 4: "维保中", 5: "故障离线"}
VERTIPORT_STATUS = {1: "开放", 2: "维护中", 3: "关闭"}

CRUISE_SPEED = 12.0      # m/s，与定价口径一致
DEFAULT_ALTITUDE = 100   # 申报高度 m
RESERVE_RATIO = 1.3      # 续航预留 30%
# 全局限高兜底值：与 compliance_monitor.ALTITUDE_LIMIT_M 读同一个 sys_config 键
# （altitude_limit_max_m），两边各自 import 会绕成环，所以只在这里重复常量。
ALTITUDE_LIMIT_MAX_M = 120
# 已下发待执行(2 且 assigned_at 已填)与执行中(3)才真正占用机长/飞机；
# 待申报/待审批/已批准未下发的方案不锁定资源（可能被驳回或改派）。


def _gen_no(prefix: str = "FP") -> str:
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 9999):04d}"


def _is_staff(user: User) -> bool:
    return user.user_type in (3, 4, 5, 6)


def _merchant_scope(user: User):
    """商家员工返回本商家 id；平台/空管/运维返回 None（全量）。"""
    if user.user_type == 3 and user.merchant_id:
        return user.merchant_id
    return None


def _require_merchant_order(order: Order, user: User):
    if not _is_staff(user):
        raise ApiError(BizCode.FORBIDDEN, "只有运营方可操作", 403)
    scope = _merchant_scope(user)
    if scope is not None and order.merchant_id != scope:
        raise ApiError(BizCode.FORBIDDEN, "无权操作其他商家的订单", 403)


# ---------------------------------------------------------------- 资源

def _pilots(merchant_id: int) -> list[User]:
    """商家下持机长角色且账号正常的用户。"""
    return (db.session.query(User)
            .join(user_role, user_role.c.user_id == User.id)
            .join(Role, Role.id == user_role.c.role_id)
            .filter(Role.role_code == "pilot", User.merchant_id == merchant_id,
                    User.status == 1, User.deleted == 0)
            .all())


def _occupied_plan_query():
    """占用资源的在途计划：执行中，或已下发待执行。"""
    return (db.session.query(FlightPlan)
            .filter(FlightPlan.deleted == 0,
                    db.or_(FlightPlan.status == 3,
                           db.and_(FlightPlan.status == 2,
                                   FlightPlan.assigned_at.isnot(None)))))


def _pilot_busy_ids() -> set[int]:
    return {p.pilot_id for p in _occupied_plan_query()
            .filter(FlightPlan.pilot_id.isnot(None)).all()}


def _aircraft_busy_ids() -> set[int]:
    return {p.aircraft_id for p in _occupied_plan_query()
            .filter(FlightPlan.aircraft_id.isnot(None)).all()}


def idle_aircraft(merchant_id: int) -> list[Aircraft]:
    """可立即调度的飞机：闲置、在线、未派在途任务、且已停放于某场站。

    对外暴露（一键演示/大屏统计要用），免得别处再复制一遍在途判定。
    """
    busy = _aircraft_busy_ids()
    return [a for a in db.session.query(Aircraft).filter(
        Aircraft.merchant_id == merchant_id, Aircraft.status == 1,
        Aircraft.online == 1, Aircraft.deleted == 0).all()
        if a.id not in busy and a.current_vertiport_id]


def station_occupancy(station_ids: list[int] | None = None) -> dict[int, int]:
    """场站实占机位数 {场站 id: 架数}——对外展示的 occupiedCount 只从这里算。

    vertiport.occupied_count 那一列不作数：建库以来没有任何写入方（场站心跳只报桩位
    charger_available，不报机位），读它就会把「0/4」这种假空闲摆上调度台。
    机位被谁占着平台自己是知道的，但 current_vertiport_id 有个坑：起飞只把状态置 2、
    场站列不动，降落才由 _do_land 迁到终点场，所以在飞的机器数据上还挂在出发场，
    必须按 status<>2 排除；充换电/维保/故障都算占用——它们确实停在那个机位上。
    场站预约是另一本账（_slot_used 按预占时段算），不并进来：实占与预占混成一个数字，
    资源台账那个进度条就没人能解释了。
    """
    q = (db.session.query(Aircraft.current_vertiport_id, db.func.count(Aircraft.id))
         .filter(Aircraft.deleted == 0, Aircraft.status != 2,
                 Aircraft.current_vertiport_id.isnot(None))
         .group_by(Aircraft.current_vertiport_id))
    if station_ids is not None:
        q = q.filter(Aircraft.current_vertiport_id.in_(station_ids))
    return {int(vp_id): int(n) for vp_id, n in q.all()}


def list_resources(user: User, merchant_id: int | None = None) -> dict:
    """调度台右侧面板所需的飞机/机长/起降场选项。"""
    mid = merchant_id or _merchant_scope(user) or 1
    busy_pilots, busy_aircraft = _pilot_busy_ids(), _aircraft_busy_ids()
    aircraft = (db.session.query(Aircraft)
                .filter(Aircraft.merchant_id == mid, Aircraft.deleted == 0).all())
    vertiports = (db.session.query(Vertiport)
                  .filter(db.or_(Vertiport.merchant_id == mid, Vertiport.merchant_id.is_(None)),
                          Vertiport.deleted == 0).all())
    occ = station_occupancy([v.id for v in vertiports])
    return {
        "aircraft": [{
            "id": a.id, "aircraftNo": a.aircraft_no, "brand": a.brand, "model": a.model,
            "status": a.status, "statusName": AIRCRAFT_STATUS.get(a.status),
            "maxLoadKg": float(a.max_load_kg or 0), "enduranceMin": a.endurance_min,
            "maxSpeed": float(a.max_speed or 0), "battery": a.battery,
            "online": bool(a.online), "healthScore": a.health_score,
            "currentVertiportId": a.current_vertiport_id,
            "busy": a.id in busy_aircraft or a.status != 1 or not a.online,
        } for a in aircraft],
        "pilots": [{
            "id": p.id, "realName": p.real_name, "username": p.username,
            "phone": decrypt_phone(p.phone_cipher), "busy": p.id in busy_pilots,
        } for p in _pilots(mid)],
        "vertiports": [{
            "id": v.id, "stationNo": v.station_no, "stationName": v.station_name,
            "stationType": v.station_type, "lng": float(v.lng), "lat": float(v.lat),
            "address": v.address, "status": v.status,
            "statusName": VERTIPORT_STATUS.get(v.status),
            "capacity": v.capacity, "occupiedCount": occ.get(v.id, 0),
        } for v in vertiports],
    }


# ---------------------------------------------------------------- 航线/合规

def active_airspaces() -> list[Airspace]:
    """当前生效的空域（含临时管制时段过滤）；申报预检与飞行中合规判定共用。

    audit_status=1 才参与判定：平台自绘的空域要空管审核通过才算数，
    否则画一半的草稿多边形会直接把在飞计划判成闯禁飞。
    """
    now = datetime.now()
    rows = (db.session.query(Airspace)
            .filter(Airspace.status == 1, Airspace.deleted == 0,
                    Airspace.audit_status == 1).all())
    result = []
    for a in rows:
        if a.airspace_type == 4:  # 临时管制要看管制时段
            if a.control_start_at and a.control_end_at and not (
                    a.control_start_at <= now <= a.control_end_at):
                continue
        result.append(a)
    return result


def build_route(order: Order, takeoff: Vertiport, landing: Vertiport) -> dict:
    """航线航点：起飞场 → 寄件点 → 收件点 → 降落场，逐段等距采样后合并。

    收件点之后的「调机返场」航段单独返回 routeReturn，前端用浅色虚线绘制，
    与载货航段（金色实线）区分，避免被误读成“中途降落”。
    """
    legs = [
        ((float(takeoff.lng), float(takeoff.lat)),
         (float(order.sender_lng), float(order.sender_lat))),
        ((float(order.sender_lng), float(order.sender_lat)),
         (float(order.receiver_lng), float(order.receiver_lat))),
        ((float(order.receiver_lng), float(order.receiver_lat)),
         (float(landing.lng), float(landing.lat))),
    ]
    points: list[tuple[float, float]] = []
    distance = 0.0
    receiver_index = 0
    for leg_no, (p1, p2) in enumerate(legs):
        seg = sample_segment(p1, p2, 12)
        if points:
            seg = seg[1:]
        points.extend(seg)
        distance += haversine_m(p1[0], p1[1], p2[0], p2[1])
        if leg_no == 1:  # 第二段结束于收件点
            receiver_index = len(points) - 1
    duration = int(distance / CRUISE_SPEED)
    route = [{"lng": round(lng, 6), "lat": round(lat, 6)} for lng, lat in points]
    return {
        "route": route,
        "routeMain": route[: receiver_index + 1],
        "routeReturn": route[receiver_index:],
        "distanceM": int(round(distance)),
        "durationSec": duration,
    }


def recalc_route(user: User, body: dict) -> dict:
    """调度台切换起降场后实时重算：不碰飞机/机长，只按新场站组合算航程。"""
    order = db.session.get(Order, int(body.get("orderId", 0)))
    if not order or order.deleted:
        raise ApiError(BizCode.NOT_FOUND, "订单不存在")
    scope = _merchant_scope(user)
    if scope is not None and order.merchant_id != scope:
        raise ApiError(BizCode.FORBIDDEN, "无权查看该订单")
    takeoff = db.session.get(Vertiport, int(body["takeoffVertiportId"]))
    landing = db.session.get(Vertiport, int(body["landingVertiportId"]))
    if not takeoff or not landing:
        raise ApiError(BizCode.PARAM_ERROR, "场站不存在")
    r = build_route(order, takeoff, landing)
    comp = _compliance(r["route"])
    label, ceiling = _route_ceiling(comp["ceiled"])
    return {"distanceM": r["distanceM"], "durationSec": r["durationSec"],
            "route": r["route"], "routeReturn": r["routeReturn"],
            # 调度台把"飞行高度"按这个封顶，操作员在提交前就知道这条线最高能填多少，
            # 而不是点保存被后端拒了再回来猜
            "altitudeMax": None if ceiling == float("inf") else int(ceiling),
            "altitudeLimitFrom": label,
            "forbiddenAirspaces": [z["airspaceName"] for z in comp["forbidden"]]}


def _nearest_vertiport(lng: float, lat: float, stations: list[Vertiport]) -> Vertiport:
    return min(stations, key=lambda v: haversine_m(lng, lat, float(v.lng), float(v.lat)))


def split_return_leg(route_points: list[dict] | None, receiver_lng: float,
                     receiver_lat: float) -> list[dict]:
    """从已存航线中拆出「收件点 → 降落场」调机返场航段（含收件点）。"""
    if not route_points:
        return []
    idx = None
    for i, pt in enumerate(route_points):
        if abs(float(pt["lng"]) - float(receiver_lng)) < 1e-5 and \
                abs(float(pt["lat"]) - float(receiver_lat)) < 1e-5:
            idx = i  # 取最后一个匹配点
    if idx is None:
        return []
    return route_points[idx:]


def route_compliance(route_points: list[dict]) -> dict:
    """航线合规预览（_compliance 的公开入口：一键演示在下单前就要筛掉穿禁飞区的组合）。"""
    return _compliance(route_points)


def _compliance(route_points: list[dict]) -> dict:
    airspaces = active_airspaces()
    pts = [(p["lng"], p["lat"]) for p in route_points]
    hit = check_route_against_airspaces(pts, airspaces)
    return {
        "forbidden": [{"id": a.id, "airspaceName": a.airspace_name, "airspaceType": a.airspace_type}
                      for a in hit["forbidden"]],
        "limited": [{"id": a.id, "airspaceName": a.airspace_name,
                     "altitudeMax": a.altitude_max, "altitudeMin": a.altitude_min}
                    for a in hit["limited"]],
        "ceiled": [{"id": a.id, "airspaceName": a.airspace_name, "airspaceType": a.airspace_type,
                    "altitudeMax": a.altitude_max} for a in hit["ceiled"]],
    }


def _ceiling_candidates(ceiled: list[dict]) -> list[tuple[str, float | None]]:
    """把航线穿过的限高空域 + 全局限高凑成 (显示文案, 上限m) 候选。"""
    cands = [(f"「{z['airspaceName']}」限高", z["altitudeMax"]) for z in ceiled]
    cands.append(("全局限高", sys_cfg_num("altitude_limit_max_m", ALTITUDE_LIMIT_MAX_M, int)))
    return cands


def _altitude_admission(altitude, ceiled: list[dict]):
    """空域高度准入：申报高度能不能在航线穿过的每个上限之内飞。

    返回被卡住的那一个 (显示文案, 上限m)，能通过时返回 None。
    超高过去只在飞行中判，飞机一起飞就吃一条违规记录，申报时无人过问 ——
    闸口应该拦在起飞前，不是事后处罚。
    """
    return binding_ceiling(_ceiling_candidates(ceiled), float(altitude or 0))


def _route_ceiling(ceiled: list[dict]) -> tuple[str, float]:
    """本航线上最严的高度上限（含全局限高兜底），给调度台把"飞行高度"封顶用。

    拿 binding_ceiling 比一个极高的高度：所有上限都算被"超过"，胜出的自然是最低的
    那一个，于是分区准入和这里显示给操作员的是同一份算法、同一个结论。
    """
    return binding_ceiling(_ceiling_candidates(ceiled), 10 ** 6) or ("全局限高", float("inf"))


# ---------------------------------------------------------------- 推荐

def recommend(user: User, order_id: int) -> dict:
    order = order_service.get_visible_order(order_id, user)
    _require_merchant_order(order, user)
    if order.status != 2:
        raise ApiError(BizCode.CONFLICT,
                       f"订单当前为「{order_service.ORDER_STATUS.get(order.status)}」，不在待调度状态", 409)

    mid = order.merchant_id
    stations = [v for v in db.session.query(Vertiport).filter(
        db.or_(Vertiport.merchant_id == mid, Vertiport.merchant_id.is_(None)),
        Vertiport.status == 1, Vertiport.deleted == 0).all()]
    if not stations:
        raise ApiError(BizCode.BIZ_ERROR, "没有可用的起降场站")
    takeoff = _nearest_vertiport(float(order.sender_lng), float(order.sender_lat), stations)
    landing = _nearest_vertiport(float(order.receiver_lng), float(order.receiver_lat), stations)
    route = build_route(order, takeoff, landing)
    compliance = _compliance(route["route"])
    weather = weather_adapter.current(
        (float(order.sender_lng) + float(order.receiver_lng)) / 2,
        (float(order.sender_lat) + float(order.receiver_lat)) / 2)

    reasons, warnings = [], []
    if compliance["forbidden"]:
        names = "、".join(a["airspaceName"] for a in compliance["forbidden"])
        reasons.append(f"航线穿越禁飞/临时管制空域：{names}，不可申报")
    if not weather["flyable"]:
        reasons.append(
            f"气象条件不适飞（{weather['weatherDesc']}，{weather['windSpeed']}m/s，"
            f"能见度{weather['visibilityM']}m，{weather['warningLevelName']}预警）")
    if compliance["limited"]:
        warnings.append("航线穿越限飞空域：" + "、".join(a["airspaceName"] for a in compliance["limited"]))
    # 推荐高度必须落在航线限高之内：否则操作员一进来就看到一个"合法"的 100m，
    # 点保存才被闸口拒掉，等于把服务端的判断推给了人。
    limit_label, limit_m = _route_ceiling(compliance["ceiled"])
    rec_altitude = int(min(DEFAULT_ALTITUDE, limit_m))
    if rec_altitude < DEFAULT_ALTITUDE:
        warnings.append(f"航线受{limit_label} {limit_m:.0f}m 限制，推荐申报高度已压到 {rec_altitude}m")

    need_min = route["durationSec"] / 60 * RESERVE_RATIO
    resources = list_resources(user, mid)
    ac_options = [a for a in resources["aircraft"] if (a.get("currentVertiportId") or a.get("vertiportId")) == takeoff.id]
    resources["aircraft"] = ac_options
    # 载重过滤
    load_ok = [a for a in ac_options if a["maxLoadKg"] >= float(order.weight_kg)]
    # 拒因要说清是哪一层卡住的：场里一架飞机都没有时说"没有载重够的飞行器"，
    # 操作员会去翻载重表，而真正要做的是把飞机调到这个起飞场（或换起飞场）。
    if not ac_options:
        reasons.append(f"起飞场「{takeoff.station_name}」没有停放的飞行器，"
                       "请先在「场站管理」把飞行器调过来，或改选起飞场")
    elif not load_ok:
        reasons.append(f"起飞场「{takeoff.station_name}」停放的飞行器里没有"
                       f"载重 ≥ {order.weight_kg}kg 的机型")
    # 电量/续航过滤
    feasible = []
    for a in load_ok:
        need_battery = math.ceil(need_min / max(a["enduranceMin"] or 1, 1) * 100)
        a["needBatteryPercent"] = need_battery
        if not a["busy"] and (a["battery"] or 0) >= need_battery:
            feasible.append(a)
        elif a["busy"]:
            pass
    if load_ok and not feasible:
        reasons.append("没有同时满足闲置、在线、电量/续航要求的飞行器")

    # 推荐只从 feasible 里挑：它已经按"停在这个起飞场 + 载重够 + 电量够 + 闲置在线"筛过。
    # 以前的候选池是全商家空闲飞机，挑出来的机器可能停在别的场，操作员照着推荐点保存，
    # 就被 _validate_resources 的"该飞机不在起飞场"拒掉——推荐反而把人带偏。
    recommended_ac = max(feasible, key=lambda a: (a["battery"] or 0, -a["id"]))["id"] if feasible else None

    busy_pilots = _pilot_busy_ids()
    pilot_rows = [p for p in _pilots(mid) if p.id not in busy_pilots]
    recommended_pilot = pilot_rows[0].id if pilot_rows else None
    if not pilot_rows:
        reasons.append("没有可排班的空闲机长")

    blocked = bool(reasons)
    return {
        "orderId": order.id, "orderNo": order.order_no,
        "recommended": {
            "aircraftId": recommended_ac, "pilotId": recommended_pilot,
            "takeoffVertiportId": takeoff.id, "landingVertiportId": landing.id,
            "route": route["route"], "routeMain": route["routeMain"],
            "routeReturn": route["routeReturn"],
            "distanceM": route["distanceM"],
            "durationSec": route["durationSec"],
            "altitudeM": rec_altitude, "cruiseSpeed": CRUISE_SPEED,
            "altitudeMax": int(limit_m), "altitudeLimitFrom": limit_label,
            "weather": weather, "compliance": compliance,
        },
        "options": resources,
        "warnings": warnings, "blocked": blocked, "reasons": reasons,
    }


# ---------------------------------------------------------------- 方案/状态机

def _get_plan_for_user(plan_id: int, user: User) -> FlightPlan:
    plan = db.session.query(FlightPlan).filter_by(id=plan_id, deleted=0).first()
    if plan is None:
        raise ApiError(BizCode.NOT_FOUND, "飞行计划不存在", 404)
    if "pilot" in user.role_codes and plan.pilot_id != user.id and user.user_type != 4:
        # 机长只看自己的任务；平台管理员除外
        if user.user_type == 3:
            raise ApiError(BizCode.FORBIDDEN, "只能查看分配给自己的飞行任务", 403)
    scope = _merchant_scope(user)
    if scope is not None and plan.merchant_id != scope:
        raise ApiError(BizCode.FORBIDDEN, "无权查看该计划", 403)
    return plan


def _validate_resources(mid: int, payload: dict, order: Order, route: dict) -> dict:
    aircraft = db.session.query(Aircraft).filter_by(
        id=payload.get("aircraftId"), deleted=0).first()
    pilot = db.session.query(User).filter_by(
        id=payload.get("pilotId"), deleted=0).first()
    takeoff = db.session.query(Vertiport).filter_by(
        id=payload.get("takeoffVertiportId"), deleted=0).first()
    landing = db.session.query(Vertiport).filter_by(
        id=payload.get("landingVertiportId"), deleted=0).first()
    if not aircraft or aircraft.merchant_id != mid:
        raise ApiError(BizCode.PARAM_ERROR, "请选择本商家的飞行器")
    if aircraft.current_vertiport_id != payload.get("takeoffVertiportId"):
        raise ApiError(BizCode.PARAM_ERROR, f"该飞机不在起飞场（当前在{aircraft.vertiport.station_name if aircraft.vertiport else '未知'}）")
    if not pilot or pilot.merchant_id != mid or "pilot" not in pilot.role_codes:
        raise ApiError(BizCode.PARAM_ERROR, "请选择本商家的持证机长")
    if aircraft.id in _aircraft_busy_ids() or aircraft.status != 1:
        raise ApiError(BizCode.CONFLICT, f"飞行器 {aircraft.aircraft_no} 已有在途任务", 409)
    if pilot.id in _pilot_busy_ids():
        raise ApiError(BizCode.CONFLICT, f"机长 {pilot.real_name} 已有在途任务", 409)
    if not takeoff or not landing or takeoff.status != 1 or landing.status != 1:
        raise ApiError(BizCode.PARAM_ERROR, "起降场站必须为开放状态")
    if float(aircraft.max_load_kg or 0) < float(order.weight_kg):
        raise ApiError(BizCode.CONFLICT,
                       f"飞行器载重 {aircraft.max_load_kg}kg 不足（货物 {order.weight_kg}kg）", 409)
    need_min = route["durationSec"] / 60 * RESERVE_RATIO
    need_battery = math.ceil(need_min / max(aircraft.endurance_min or 1, 1) * 100)
    if (aircraft.battery or 0) < need_battery:
        raise ApiError(BizCode.CONFLICT,
                       f"电量 {aircraft.battery}% 不足，本航程至少需要 {need_battery}%", 409)
    return {"aircraft": aircraft, "pilot": pilot, "takeoff": takeoff, "landing": landing,
            "needBattery": need_battery}


def create_plan(user: User, payload: dict) -> FlightPlan:
    order = order_service.get_visible_order(payload.get("orderId"), user)
    _require_merchant_order(order, user)
    if order.status != 2:
        raise ApiError(BizCode.CONFLICT, "只有待调度订单能制定调度方案", 409)
    mid = order.merchant_id
    takeoff = db.session.get(Vertiport, payload.get("takeoffVertiportId"))
    landing = db.session.get(Vertiport, payload.get("landingVertiportId"))
    if not takeoff or not landing:
        raise ApiError(BizCode.PARAM_ERROR, "请选择起降场站")
    route = build_route(order, takeoff, landing)
    speed = Decimal(str(payload.get("cruiseSpeed") or CRUISE_SPEED))
    altitude = int(payload.get("altitudeM") or DEFAULT_ALTITUDE)
    compliance = _compliance(route["route"])
    if compliance["forbidden"]:
        names = "、".join(a["airspaceName"] for a in compliance["forbidden"])
        raise ApiError(BizCode.CONFLICT, f"航线穿越禁飞/管制空域（{names}），不能申报", 409)
    blocked = _altitude_admission(altitude, compliance["ceiled"])
    if blocked:
        raise ApiError(BizCode.CONFLICT,
                       f"申报高度 {altitude}m 超过{blocked[0]} {blocked[1]:.0f}m，"
                       f"请降低申报高度或调整航线", 409)
    res = _validate_resources(mid, payload, order, route)
    weather = weather_adapter.current(
        (float(order.sender_lng) + float(order.receiver_lng)) / 2,
        (float(order.sender_lat) + float(order.receiver_lat)) / 2)
    if not weather["flyable"]:
        raise ApiError(BizCode.CONFLICT, "当前气象条件不适飞，请改期申报", 409)

    plan = db.session.query(FlightPlan).filter_by(order_id=order.id, deleted=0).first()
    if plan and plan.status not in (0, 5):
        raise ApiError(BizCode.CONFLICT, "该订单已有进行中的飞行计划", 409)
    start_at = order_service._parse_dt(payload.get("planStartAt")) or datetime.now()
    end_at = start_at + timedelta(seconds=route["durationSec"])
    if plan is None:
        plan = FlightPlan(plan_no=_gen_no(), order_id=order.id, merchant_id=mid)
        db.session.add(plan)
    plan.status = 0
    plan.aircraft_id = res["aircraft"].id
    plan.pilot_id = res["pilot"].id
    plan.takeoff_vertiport_id = takeoff.id
    plan.landing_vertiport_id = landing.id
    plan.route_json = route["route"]
    plan.route_distance_m = route["distanceM"]
    plan.est_duration_sec = route["durationSec"]
    plan.altitude_m = altitude
    plan.cruise_speed = speed
    plan.weather_json = weather
    plan.task_name = (payload.get("taskName") or f"{order.goods_type}配送-{order.order_no[-6:]}")[:100]
    plan.purpose = (payload.get("purpose") or "城市低空物流配送")[:255]
    plan.plan_start_at = start_at
    plan.plan_end_at = end_at
    plan.remark = (payload.get("remark") or None)
    plan.dispatch_by = user.id
    plan.dispatched_at = datetime.now()
    plan.approve_result = None
    plan.approved_by = None
    plan.approved_at = None
    plan.approve_opinion = None
    plan.reject_reason = None
    plan.submitted_at = None
    plan.assigned_at = None
    db.session.commit()
    return plan


def submit_plan(user: User, plan: FlightPlan) -> dict:
    if plan.status != 0:
        raise ApiError(BizCode.CONFLICT, "只有待申报方案能提交申报", 409)
    order = db.session.get(Order, plan.order_id)
    aircraft = db.session.get(Aircraft, plan.aircraft_id)
    pilot = db.session.get(User, plan.pilot_id)
    # 方案起草后空域可能被重画或收紧，提交前按存下来的航线再判一次高度准入，
    # 否则"待申报"里躺着的老方案能带着已经失效的合规结论直接飞出去。
    blocked = _altitude_admission(
        plan.altitude_m or DEFAULT_ALTITUDE, _compliance(plan.route_json or [])["ceiled"])
    if blocked:
        raise ApiError(BizCode.CONFLICT,
                       f"申报高度 {plan.altitude_m}m 超过{blocked[0]} {blocked[1]:.0f}m，"
                       f"空域条件已变更，请修改方案后再提交", 409)
    resp = utm_adapter.declare(
        plan_no=plan.plan_no, route_points=plan.route_json or [],
        altitude_m=plan.altitude_m or DEFAULT_ALTITUDE,
        aircraft_no=aircraft.aircraft_no if aircraft else "-",
        pilot_name=pilot.real_name if pilot else "-")
    plan.status = 1
    plan.submitted_at = datetime.now()
    order.status = 3
    db.session.commit()
    return resp


def _set_approval(plan: FlightPlan, user: User, result: int, opinion: str | None):
    db.session.add(ApprovalRecord(
        plan_id=plan.id, approver_id=user.id, result=result,
        opinion=(opinion or None)))
    plan.approve_result = result
    plan.approved_by = user.id
    plan.approved_at = datetime.now()


def _plan_watchers(plan: FlightPlan) -> list[int]:
    """审批结果该告诉商家侧的谁：优先是提交这份申报的那位调度员。

    `dispatch_by` 为空时（历史数据/平台代申报）退到"本商家有调度权限的账号"——
    站内信没有角色订阅机制，只能按权限点查人。跨商家的调度员不在候选里。
    """
    if plan.dispatch_by:
        return [plan.dispatch_by]
    return user_ids_with_perm("dispatch:plan", plan.merchant_id)


def _notify_approval_result(plan: FlightPlan, order: Order | None, ok: bool, opinion: str) -> None:
    """空管审批结论落地 → 通知商家调度侧 + 客户本人。

    差距分析 §7 说的"message_notification 只有读没有写"，这一处最要命：
    驳回会把订单从 3待审批 退回 2待调度，商家不刷新调度台就完全不知道
    有一份申报被驳回了、要重新申报；客户那边更是全程无声。
    """
    title = f"{'空管已批准' if ok else '空管驳回了'}飞行申报：{plan.plan_no}"
    who = f"订单 {order.order_no}" if order else f"订单 #{plan.order_id}"
    if ok:
        window = f"{plan.plan_start_at:%m-%d %H:%M} 起" if plan.plan_start_at else "时段按申报"
        ceiling = f"，限高 {plan.altitude_m} 米" if plan.altitude_m else ""
        staff = (f"空管已批准{who}的飞行申报（{window}{ceiling}），审批意见：{opinion}。"
                 "请在调度台确认人机并下发机长。")
        customer = "您的订单已通过空管审批，运营方会安排起飞，配送进度另行通知。"
    else:
        staff = (f"空管驳回了{who}的飞行申报，原因：{opinion}。"
                 "订单已退回待调度，请调整航线/高度/时段后重新生成方案并申报。")
        customer = (f"您的订单本次未通过空管审批（{opinion}），"
                    "运营方会调整后重新申报，无需您重复下单。")
    biz_type = "plan_approve" if ok else "plan_reject"
    notify.notify_many(_plan_watchers(plan), title, staff, biz_type=biz_type, biz_id=plan.id)
    if order:
        notify.notify(order.customer_id, title, customer, biz_type=biz_type, biz_id=plan.id)


def approve_plan(user: User, plan: FlightPlan, opinion: str | None = None) -> FlightPlan:
    if plan.status != 1:
        raise ApiError(BizCode.CONFLICT, "只有待审批方案能审批", 409)
    _set_approval(plan, user, 1, opinion)
    plan.status = 2
    plan.approve_opinion = (opinion or "同意")[:500]
    plan.reject_reason = None
    order = db.session.get(Order, plan.order_id)
    order.status = 4
    db.session.commit()
    _notify_approval_result(plan, order, True, plan.approve_opinion)
    return plan


def reject_plan(user: User, plan: FlightPlan, reason: str) -> FlightPlan:
    if plan.status != 1:
        raise ApiError(BizCode.CONFLICT, "只有待审批方案能驳回", 409)
    if not reason or not reason.strip():
        raise ApiError(BizCode.PARAM_ERROR, "驳回必须填写原因")
    _set_approval(plan, user, 2, reason)
    plan.status = 5
    plan.reject_reason = reason.strip()[:500]
    order = db.session.get(Order, plan.order_id)
    order.status = 2
    db.session.commit()
    _notify_approval_result(plan, order, False, plan.reject_reason)
    return plan


def batch_approve(user: User, ids: list[int], opinion: str | None) -> dict:
    if not ids:
        raise ApiError(BizCode.PARAM_ERROR, "请勾选要审批的计划")
    plans = [_get_plan_for_user(i, user) for i in ids]
    not_ready = [p.plan_no for p in plans if p.status != 1]
    if not_ready:
        raise ApiError(BizCode.CONFLICT, f"以下计划不在待审批状态：{'、'.join(not_ready)}", 409)
    text_ = (opinion or "同意")[:500]
    results = []
    for p in plans:
        _set_approval(p, user, 1, text_)
        p.status = 2
        p.approve_opinion = text_
        order = db.session.get(Order, p.order_id)
        order.status = 4
        results.append((p, order))
    db.session.commit()
    # 逐单通知：批量通过是"一次点击"，但每份方案的时段/限高不同，客户收到的是自己那一单
    for p, order in results:
        _notify_approval_result(p, order, True, text_)
    return {"count": len(plans), "planNos": [p.plan_no for p in plans]}


def assign_plan(user: User, plan: FlightPlan) -> dict:
    if plan.status != 2:
        raise ApiError(BizCode.CONFLICT, "只有已批准待执行的方案能下发机长", 409)
    if not plan.pilot_id:
        raise ApiError(BizCode.CONFLICT, "方案未指定机长", 409)
    plan.assigned_at = datetime.now()
    pilot = db.session.get(User, plan.pilot_id)
    aircraft = db.session.get(Aircraft, plan.aircraft_id)
    takeoff_vp = db.session.get(Vertiport, plan.takeoff_vertiport_id)
    landing_vp = db.session.get(Vertiport, plan.landing_vertiport_id)
    # 机长客户端没有推送通道，接单靠站内信 + 顶栏铃铛轮询；不发这条他就永远不知道有任务
    eta = f"，计划起飞 {plan.plan_start_at:%m-%d %H:%M}" if plan.plan_start_at else ""
    notify.notify(
        pilot.id, "新飞行任务已下发",
        f"{plan.task_name or plan.plan_no}："
        f"{takeoff_vp.station_name if takeoff_vp else '-'} → "
        f"{landing_vp.station_name if landing_vp else '-'}，"
        f"飞行器 {aircraft.aircraft_no if aircraft else '-'}{eta}。"
        f"请到「我的任务」完成起飞前检查并确认起飞。",
        biz_type="plan_assign", biz_id=plan.id)
    db.session.commit()
    return gcs_adapter.send_command(
        plan_no=plan.plan_no, command="ASSIGN_MISSION",
        payload={"pilot": pilot.real_name if pilot else plan.pilot_id,
                 "takeoffAt": plan.plan_start_at.strftime("%Y-%m-%d %H:%M") if plan.plan_start_at else None})


def takeoff(user: User, plan: FlightPlan) -> dict:
    if plan.status != 2 or not plan.assigned_at:
        raise ApiError(BizCode.CONFLICT, "方案已下发机长后才能起飞", 409)
    if user.user_type == 3 and "pilot" in user.role_codes and plan.pilot_id != user.id:
        raise ApiError(BizCode.FORBIDDEN, "只有被指派的机长能确认起飞", 403)
    aircraft = db.session.get(Aircraft, plan.aircraft_id)
    vp = db.session.get(Vertiport, plan.takeoff_vertiport_id)
    now = datetime.now()
    plan.status = 3
    plan.actual_takeoff_at = now
    plan.current_lng, plan.current_lat = vp.lng, vp.lat
    plan.current_altitude = 0
    plan.current_speed = 0
    plan.current_battery = aircraft.battery if aircraft else 100
    plan.last_report_at = now
    if aircraft:
        aircraft.status = 2
        aircraft.current_lng, aircraft.current_lat = vp.lng, vp.lat
    db.session.get(Order, plan.order_id).status = 5
    db.session.commit()
    return gcs_adapter.send_command(plan_no=plan.plan_no, command="TAKEOFF",
                                    payload={"aircraftNo": aircraft.aircraft_no if aircraft else None})


def _do_land(plan: FlightPlan, at: datetime | None = None) -> tuple[Aircraft | None, Vertiport | None]:
    """降落落库的唯一实现：计划→4已完成、飞机归位闲置、订单→6已送达。

    机长/运营点「降落」与仿真器判定的「到达终点」都走这里，
    避免同一状态迁移出现两份实现（设计02：状态机唯一入口在 D3）。
    """
    now = at or datetime.now()
    aircraft = db.session.get(Aircraft, plan.aircraft_id)
    vp = db.session.get(Vertiport, plan.landing_vertiport_id)
    plan.status = 4
    plan.actual_landing_at = now
    if vp:
        plan.current_lng, plan.current_lat = vp.lng, vp.lat
    plan.current_altitude = 0
    plan.current_speed = 0
    plan.last_report_at = now
    if aircraft:
        aircraft.status = 1
        if vp:
            aircraft.current_vertiport_id = vp.id
            aircraft.current_lng, aircraft.current_lat = vp.lng, vp.lat
        aircraft.task_count = (aircraft.task_count or 0) + 1
        if plan.actual_takeoff_at:
            aircraft.total_flight_min = (aircraft.total_flight_min or 0) + max(
                1, int((now - plan.actual_takeoff_at).total_seconds() / 60))
        if plan.current_battery is not None:
            aircraft.battery = max(0, min(100, int(round(float(plan.current_battery)))))
    order = db.session.get(Order, plan.order_id)
    if order and order.status == 5:
        order.status = 6
        order.delivered_at = now
        order.updated_at = now
    db.session.commit()
    return aircraft, vp


def finish_landing(plan: FlightPlan) -> None:
    """仿真器专用入口：进度到 1 时代客户完成降落（调用方已完成权限校验，这里不做判定）。"""
    _do_land(plan)


def land(user: User, plan: FlightPlan) -> dict:
    if plan.status != 3:
        raise ApiError(BizCode.CONFLICT, "只有执行中的计划能确认降落", 409)
    if user.user_type == 3 and "pilot" in user.role_codes and plan.pilot_id != user.id:
        raise ApiError(BizCode.FORBIDDEN, "只有被指派的机长能确认降落", 403)
    aircraft, vp = _do_land(plan)
    return gcs_adapter.send_command(plan_no=plan.plan_no, command="LAND",
                                    payload={"vertiport": vp.station_name if vp else None,
                                             "aircraftNo": aircraft.aircraft_no if aircraft else None})


def cancel_plan(user: User, plan: FlightPlan, reason: str | None) -> FlightPlan:
    if plan.status not in (0, 1, 2):
        raise ApiError(BizCode.CONFLICT, "计划已进入执行，不能取消（请走应急流程）", 409)
    plan.status = 6
    plan.remark = ((plan.remark or "") + f"｜取消：{reason or '调度取消'}")[:255]
    order = db.session.get(Order, plan.order_id)
    if order.status in (3, 4):
        order.status = 2
    db.session.commit()
    return plan


# ---------------------------------------------------------------- 查询

def list_plans(user: User, *, page=1, page_size=10, status=None, keyword=None,
               mine: bool = False) -> dict:
    query = db.session.query(FlightPlan).filter(FlightPlan.deleted == 0)
    scope = _merchant_scope(user)
    if scope is not None:
        query = query.filter(FlightPlan.merchant_id == scope)
    if mine or ("pilot" in user.role_codes and user.user_type == 3
                and not user.has_perm("dispatch:plan")):
        query = query.filter(FlightPlan.pilot_id == user.id)
    if status is not None and str(status) != "":
        query = query.filter(FlightPlan.status == int(status))
    if keyword:
        like = f"%{keyword}%"
        order_nos = [o.id for o in db.session.query(Order.id).filter(Order.order_no.like(like)).all()]
        query = query.filter(db.or_(FlightPlan.plan_no.like(like),
                                    FlightPlan.order_id.in_(order_nos)))
    query = query.order_by(FlightPlan.updated_at.desc())
    total = query.count()
    rows = query.limit(page_size).offset((page - 1) * page_size).all()

    # 状态计数（同一可见范围）
    count_q = db.session.query(FlightPlan.status, db.func.count(FlightPlan.id)).filter(
        FlightPlan.deleted == 0)
    if scope is not None:
        count_q = count_q.filter(FlightPlan.merchant_id == scope)
    if mine:
        count_q = count_q.filter(FlightPlan.pilot_id == user.id)
    status_counts = {s: 0 for s in PLAN_STATUS}
    for s, c in count_q.group_by(FlightPlan.status).all():
        status_counts[s] = c
    return {"list": [to_plan_dict(p) for p in rows], "total": total,
            "page": page, "pageSize": page_size, "statusCounts": status_counts}


def _approvals(plan_id: int) -> list[dict]:
    rows = (db.session.query(ApprovalRecord).filter_by(plan_id=plan_id)
            .order_by(ApprovalRecord.id.asc()).all())
    user_ids = {r.approver_id for r in rows}
    names = {u.id: u.real_name for u in db.session.query(User).filter(User.id.in_(user_ids)).all()}
    return [{"id": r.id, "result": r.result,
             "resultName": "通过" if r.result == 1 else "驳回",
             "opinion": r.opinion, "approverName": names.get(r.approver_id),
             "createdAt": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None}
            for r in rows]


def to_plan_dict(p: FlightPlan, *, with_approvals: bool = False) -> dict:
    order = db.session.get(Order, p.order_id)
    aircraft = db.session.get(Aircraft, p.aircraft_id) if p.aircraft_id else None
    pilot = db.session.get(User, p.pilot_id) if p.pilot_id else None
    tvp = db.session.get(Vertiport, p.takeoff_vertiport_id) if p.takeoff_vertiport_id else None
    lvp = db.session.get(Vertiport, p.landing_vertiport_id) if p.landing_vertiport_id else None
    data = {
        "id": p.id, "planNo": p.plan_no, "orderId": p.order_id,
        "orderNo": order.order_no if order else None,
        "merchantId": p.merchant_id, "status": p.status,
        "statusName": PLAN_STATUS.get(p.status),
        "aircraftId": p.aircraft_id,
        "aircraftNo": aircraft.aircraft_no if aircraft else None,
        "aircraftModel": f"{aircraft.brand or ''}{aircraft.model or ''}".strip() if aircraft else None,
        "pilotId": p.pilot_id, "pilotName": pilot.real_name if pilot else None,
        "takeoffVertiportId": p.takeoff_vertiport_id,
        "takeoffVertiportName": tvp.station_name if tvp else None,
        "takeoffVertiportLng": float(tvp.lng) if tvp else None,
        "takeoffVertiportLat": float(tvp.lat) if tvp else None,
        "landingVertiportId": p.landing_vertiport_id,
        "landingVertiportName": lvp.station_name if lvp else None,
        "landingVertiportLng": float(lvp.lng) if lvp else None,
        "landingVertiportLat": float(lvp.lat) if lvp else None,
        "route": p.route_json, "distanceM": p.route_distance_m,
        "durationSec": p.est_duration_sec,
        "taskName": p.task_name, "purpose": p.purpose,
        "altitudeM": p.altitude_m, "cruiseSpeed": float(p.cruise_speed) if p.cruise_speed else None,
        "weather": p.weather_json,
        "planStartAt": p.plan_start_at.strftime("%Y-%m-%d %H:%M") if p.plan_start_at else None,
        "planEndAt": p.plan_end_at.strftime("%Y-%m-%d %H:%M") if p.plan_end_at else None,
        "submittedAt": p.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if p.submitted_at else None,
        "approveResult": p.approve_result,
        "approveOpinion": p.approve_opinion, "rejectReason": p.reject_reason,
        "assignedAt": p.assigned_at.strftime("%Y-%m-%d %H:%M:%S") if p.assigned_at else None,
        "actualTakeoffAt": p.actual_takeoff_at.strftime("%Y-%m-%d %H:%M:%S") if p.actual_takeoff_at else None,
        "actualLandingAt": p.actual_landing_at.strftime("%Y-%m-%d %H:%M:%S") if p.actual_landing_at else None,
        "currentLng": float(p.current_lng) if p.current_lng is not None else None,
        "currentLat": float(p.current_lat) if p.current_lat is not None else None,
        "currentAltitude": float(p.current_altitude) if p.current_altitude is not None else None,
        "currentSpeed": float(p.current_speed) if p.current_speed is not None else None,
        "currentBattery": p.current_battery,
        "lastReportAt": p.last_report_at.strftime("%Y-%m-%d %H:%M:%S") if p.last_report_at else None,
        "remark": p.remark,
        "createdAt": p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else None,
    }
    if order:
        data["order"] = {
            "orderNo": order.order_no, "status": order.status,
            "statusName": order_service.ORDER_STATUS.get(order.status),
            "senderName": order.sender_name, "senderAddress": order.sender_address,
            "senderLng": float(order.sender_lng), "senderLat": float(order.sender_lat),
            "receiverName": order.receiver_name, "receiverAddress": order.receiver_address,
            "receiverLng": float(order.receiver_lng), "receiverLat": float(order.receiver_lat),
            "goodsType": order.goods_type, "weightKg": float(order.weight_kg),
            "deliveryType": order.delivery_type,
        }
        data["routeReturn"] = split_return_leg(
            p.route_json, float(order.receiver_lng), float(order.receiver_lat))
    if with_approvals:
        data["approvals"] = _approvals(p.id)
    return data
