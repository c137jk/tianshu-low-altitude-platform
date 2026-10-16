"""W4 监控大屏与运行告警。

鉴权口径（差距分析 P0-2）：
  /api/monitor/screen        可选登录：平台侧出全量；商家侧只出本商家的行（机队位置、
                             在飞任务、今日单量都是经营数据，隔壁商家不该在自家大屏看到）；
                             不带 token（投屏/访客）自动脱敏 —— 机长姓名、收货地址、货物名称
                             一律降级，态势数据保留。
                             注意这里不能用 @jwt_required(optional=True)：库里的 optional
                             只容忍"没有 token"，坏 token / 过期 token 照样抛 401，
                             而大屏常常一挂就是几天，凭证过期不能变成黑屏。
  /api/monitor/tracks*       需 monitor:track（轨迹属于飞行数据，不再裸奔）
  /api/monitor/alerts        需 monitor:realtime；告警来自真实数据（违规/设备/任务），
                             不再返回写死的"演示环境告警"。
  /api/monitor/devices*      需 monitor:device（M-63 设备在线，运维/空管岗）。列表另带行门：
                             商家侧运维只出本商家的机与"本商家+公共"场站，平台侧全量——
                             这一页给的是机号、实时坐标与在飞任务号，不裁行就是定位跟踪。
  /api/monitor/tracks/batch  需 monitor:track，且行级只认"本人执飞的计划"或平台侧；
  /api/monitor/devices/heartbeat 需 monitor:device 或 monitor:track，行级只认本商家设备。
                             这两个是设计03 D6 / 设计07 的"统一上报入口"，写路径，
                             闸门在蓝图层（谁能报、什么时候能报），落库在 track_service。

回放口径（M-62）：/tracks/<id> 一次把整条轨迹连同 offsetSec / direction / 违规 tag 给前端，
时间轴、倍速、曲线都在前端做，不再让前端只能画一条静态折线。
"""
from datetime import datetime, date, timedelta

from flask import Blueprint, request
from flask_jwt_extended import decode_token

from app.common import scope
from app.common.decorators import current_user, perm_any_required, perm_required
from app.common.errors import ApiError
from app.common.response import BizCode, success
from app.extensions import db
from app.models import Aircraft, FlightPlan, Order, User, Vertiport, ViolationRecord
from app.models.airspace import Airspace
from app.models.compliance import VIOLATION_TAG, VIOLATION_TYPE
from app.models.track import FlightTrack
from app.services import notify, track_service
from app.services.dispatch_service import station_occupancy

bp = Blueprint("monitor", __name__, url_prefix="/api/monitor")

PLAN_STATUS_NAME = {0: "待申报", 1: "待审批", 2: "已批准待执行", 3: "执行中",
                    4: "已完成", 5: "已驳回", 6: "已取消"}
VIO_TYPE_NAME = {1: "闯禁飞区", 2: "偏航", 3: "超时飞行", 4: "超高飞行",
                 5: "无证飞行", 6: "其他违规"}
EMERGENCY_TYPE_NAME = {1: "链路失联", 2: "闯入禁飞区", 3: "低电量返航",
                       4: "恶劣天气", 5: "其他应急"}


# ------------------------------------------------------------------
# 脱敏工具
# ------------------------------------------------------------------
def _mask_name(name: str | None) -> str:
    if not name:
        return "已隐藏"
    return name[0] + "**" if len(name) > 1 else name + "**"


def _mask_address(addr: str | None) -> str:
    """只保留行政区一级（到"区/县/市/镇"），门牌与具体 POI 隐藏。"""
    if not addr:
        return "已隐藏"
    for i, ch in enumerate(addr):
        if ch in ("区", "县", "市", "镇", "街") and i >= 2:
            return addr[:i + 1] + "****"
    return addr[:4] + "****"


def _viewer():
    """可选登录解析：没有 token、token 坏掉、token 过期，一律返回 None（走脱敏分支）。

    不用 @jwt_required(optional=True) 也不用 get_jwt_identity()：
    flask_jwt_extended 的 optional 只放过"请求里不带 JWT"，一旦带了非法/过期 JWT
    仍然抛错并被 invalid_token_loader 转成 401。大屏通常连续投好几天，
    8 小时的访问凭证必然过期，"凭证过期"绝不能演变成"整块屏幕黑掉"。
    """
    raw = (request.headers.get("Authorization") or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        return None
    try:
        payload = decode_token(raw)
    except Exception:                 # 解码失败/过期/签名不对/类型不对都降级为匿名
        return None
    if payload.get("type") != "access":
        return None
    uid = payload.get("sub")
    if not uid or not str(uid).isdigit():
        return None
    return db.session.query(User).filter_by(id=int(uid), deleted=0).first()


def _screen_rowscope(user):
    """大屏行级范围：返回 (计划条件, 飞行器条件, 订单条件, 违规条件)，None 条件=不限行。

    只有商家侧运营账号（user_type=3 且不是 platform_admin）收本商家；平台侧要全局，
    匿名/客户走 P0-2 的投屏脱敏口径（态势公开、身份打码），给他们裁行会把脱敏分支变成空屏。
    机长没有商家归属时退到"本人执飞"，判据与回放读门 _visible_plan_query 同一份。
    """
    if user is None or scope.is_platform(user) or user.user_type != 3:
        return None, None, None, None
    if user.merchant_id:
        return (FlightPlan.merchant_id == user.merchant_id,
                Aircraft.merchant_id == user.merchant_id,
                Order.merchant_id == user.merchant_id,
                ViolationRecord.merchant_id == user.merchant_id)
    vis = _visible_plan_query(user)
    return (FlightPlan.id.in_(vis),
            Aircraft.id.in_(db.session.query(FlightPlan.aircraft_id).filter(FlightPlan.id.in_(vis))),
            Order.id.in_(db.session.query(FlightPlan.order_id).filter(FlightPlan.id.in_(vis))),
            ViolationRecord.plan_id.in_(vis))


@bp.get("/screen")
def screen():
    """一屏看全区在飞态势。匿名=脱敏，平台侧=全量，商家侧=只出本商家的行。"""
    user = _viewer()
    # 冻结账号不发全量：登录接口不会再给它发 token，但已签发的 token 在有效期内仍可用，
    # 这里和 perm_required 的"账号状态异常"保持同一口径。
    full = bool(user and user.status == 1 and user.user_type in (3, 4, 5, 6))
    plan_c, ac_c, order_c, vio_c = _screen_rowscope(user)
    today = date.today()

    fleet = []
    ac_q = db.session.query(Aircraft).filter(Aircraft.deleted == 0)
    if ac_c is not None:
        ac_q = ac_q.filter(ac_c)
    for a in ac_q.all():
        fleet.append({
            "id": a.id, "no": a.aircraft_no, "model": f"{a.brand} {a.model}",
            "status": a.status,  # 1闲置 2执行中 3充换电 4维保 5故障
            "battery": float(a.battery or 0), "online": int(a.online or 0),
            "lng": float(a.current_lng) if a.current_lng else None,
            "lat": float(a.current_lat) if a.current_lat else None,
        })

    flying = []
    plan_q = db.session.query(FlightPlan).filter(FlightPlan.status == 3)
    if plan_c is not None:
        plan_q = plan_q.filter(plan_c)
    for p in plan_q.all():
        order = db.session.get(Order, p.order_id)
        pilot = db.session.get(User, p.pilot_id) if p.pilot_id else None
        ac = db.session.get(Aircraft, p.aircraft_id)
        progress = 0
        if p.actual_takeoff_at and p.est_duration_sec:
            progress = min(1.0, (datetime.now() - p.actual_takeoff_at).total_seconds()
                           / p.est_duration_sec)
        phase = ("起飞爬升" if progress < 0.15 else "前往取货点" if progress < 0.45
                 else "前往送货点" if progress < 0.8 else "进近降落")
        item = {
            "planNo": p.plan_no, "taskName": p.task_name,
            "lng": float(p.current_lng) if p.current_lng else None,
            "lat": float(p.current_lat) if p.current_lat else None,
            "altitude": float(p.current_altitude or 0),
            "speed": float(p.current_speed or 0),
            "battery": float(p.current_battery or 0),
            "aircraftNo": ac.aircraft_no if ac else None,
            "pilot": pilot.real_name if pilot else None,
            "route": p.route_json or [],
            "cargo": order.goods_name if order else None,
            "receiver": order.receiver_address if order else None,
            "progress": round(progress * 100, 1),
            "phase": phase,
            "takeoffAt": p.actual_takeoff_at.strftime("%H:%M:%S") if p.actual_takeoff_at else None,
            "masked": not full,
        }
        if not full:
            item["pilot"] = _mask_name(item["pilot"])
            item["receiver"] = _mask_address(item["receiver"])
            item["cargo"] = (order.goods_type if order else None) or "已隐藏"
        flying.append(item)

    vertiports = [{
        "id": v.id, "name": v.station_name,
        "lng": float(v.lng), "lat": float(v.lat),
    } for v in db.session.query(Vertiport).filter(Vertiport.deleted == 0).all()]

    takeoff_q = db.session.query(FlightPlan).filter(
        db.func.date(FlightPlan.actual_takeoff_at) == today)
    land_q = db.session.query(FlightPlan).filter(
        db.func.date(FlightPlan.actual_landing_at) == today)
    order_q = (db.session.query(Order.status, db.func.count(Order.id))
               .filter(Order.deleted == 0))
    vio_q = db.session.query(ViolationRecord.id).filter(
        ViolationRecord.deleted == 0, db.func.date(ViolationRecord.violation_time) == today)
    if plan_c is not None:
        takeoff_q, land_q = takeoff_q.filter(plan_c), land_q.filter(plan_c)
    if order_c is not None:
        order_q = order_q.filter(order_c)
    if vio_c is not None:
        vio_q = vio_q.filter(vio_c)

    today_takeoff = takeoff_q.count()
    today_land = land_q.count()
    order_status = dict(order_q.group_by(Order.status).all())
    violation_today = vio_q.count()

    airspaces = [{
        "name": a.airspace_name, "type": a.airspace_type, "geometry": a.geometry_json,
    } for a in db.session.query(Airspace).filter(
        Airspace.status == 1, Airspace.deleted == 0).all()]

    return {"code": 0, "message": "ok", "data": {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fleet": fleet, "flying": flying, "vertiports": vertiports,
        "airspaces": airspaces,
        "today": {"takeoffs": today_takeoff, "landings": today_land,
                  "orderStatus": order_status, "violations": violation_today},
        "authed": full,
    }}


# ---------------- W6 轨迹回放（M-62） ----------------
# 轨迹帧上的 violation_type 是 compliance_monitor 打的英文 tag（VIOLATION_TAG），
# 反查成中文给回放页标注用；库里没有的 tag 原样返回，不要静默变成"未知"。
TAG_NAME = {v: VIOLATION_TYPE[k] for k, v in VIOLATION_TAG.items()}


def _visible_plan_query(user):
    """回放读门的可见计划集合（子查询）；None 表示平台侧不限。

    与 _reportable_plan 的写门同源，只是放宽一档：商家调度要看自己单子的回放，
    但 B 商家不能回放 A 商家——列表里带任务名（就是客户品名），详情里是整条 GPS 轨迹。
    机长没有 merchant_id，落到"本人执飞"分支。
    """
    if scope.is_platform(user):
        return None
    if user.merchant_id:
        return db.session.query(FlightPlan.id).filter(FlightPlan.merchant_id == user.merchant_id)
    return db.session.query(FlightPlan.id).filter(FlightPlan.pilot_id == user.id)


@bp.get("/tracks")
@perm_required("monitor:track")
def track_list():
    """有轨迹帧的计划列表，按最后一帧时间倒序。

    不筛 status：在飞（3）的计划同样逐帧落库，回放页要能边飞边看；
    已完成（4）以外的计划靠 status/statusName 让前端自己标"在飞"。
    """
    q = db.session.query(FlightTrack.plan_id, db.func.count().label("n"),
                         db.func.min(FlightTrack.track_time).label("t0"),
                         db.func.max(FlightTrack.track_time).label("t1"),
                         db.func.sum(FlightTrack.is_violation).label("vio"))
    vis = _visible_plan_query(current_user())
    if vis is not None:
        q = q.filter(FlightTrack.plan_id.in_(vis))
    rows = q.group_by(FlightTrack.plan_id).order_by(
        db.func.max(FlightTrack.track_time).desc()).limit(30).all()
    plans = {p.id: p for p in db.session.query(FlightPlan).filter(
        FlightPlan.id.in_([r.plan_id for r in rows] or [0])).all()}
    out = []
    for r in rows:
        p = plans.get(r.plan_id)
        if not p:
            continue
        out.append({"planId": p.id, "planNo": p.plan_no, "taskName": p.task_name,
                    "status": p.status, "statusName": PLAN_STATUS_NAME.get(p.status, "未知"),
                    "frames": r.n, "startAt": r.t0.strftime("%m-%d %H:%M"),
                    "endAt": r.t1.strftime("%H:%M"),
                    "durationSec": int((r.t1 - r.t0).total_seconds()),
                    "violationFrames": int(r.vio or 0)})
    return {"code": 0, "message": "ok", "data": {"list": out}}


@bp.get("/tracks/<int:plan_id>")
@perm_required("monitor:track")
def track_detail(plan_id):
    """整条轨迹 + 回放元信息。帧数受 sys_config.sim_frame_points 上限约束（默认 600），
    一次全给前端，前端才能在同一份数据上做时间轴拖动和曲线联动，不必反复请求。"""
    plan = db.session.get(FlightPlan, plan_id)
    if not plan:
        raise ApiError(BizCode.NOT_FOUND, "飞行计划不存在", 404)
    vis = _visible_plan_query(current_user())
    if vis is not None and not db.session.query(FlightPlan.id).filter(
            FlightPlan.id == plan.id, FlightPlan.id.in_(vis)).first():
        raise ApiError(BizCode.FORBIDDEN, "只能回放本商家或本人执飞的飞行轨迹", 403)
    ac = db.session.get(Aircraft, plan.aircraft_id)
    rows = db.session.query(FlightTrack).filter(FlightTrack.plan_id == plan_id) \
        .order_by(FlightTrack.track_time).all()
    t0 = rows[0].track_time if rows else None
    points = []
    for x in rows:
        p = {
            "lng": float(x.lng), "lat": float(x.lat), "altitude": float(x.altitude or 0),
            "speed": float(x.speed or 0), "battery": int(x.battery or 0),
            "direction": int(x.direction or 0),
            "violation": int(x.is_violation or 0), "violationType": x.violation_type,
            "violationName": TAG_NAME.get(x.violation_type) if x.violation_type else None,
            "t": x.track_time.strftime("%H:%M:%S"),
            "ts": x.track_time.strftime("%Y-%m-%d %H:%M:%S"),
            # 偏移秒数以第一帧为 0：帧间隔并不严格等距（失联/注入场景会跳过帧），
            # 时间轴必须按真实时间比例摆，不能拿帧号当刻度。
            "offsetSec": round((x.track_time - t0).total_seconds(), 1),
        }
        points.append(p)
    return {"code": 0, "message": "ok", "data": {
        "planId": plan.id, "planNo": plan.plan_no, "taskName": plan.task_name,
        "aircraftNo": ac.aircraft_no if ac else None,
        "status": plan.status, "statusName": PLAN_STATUS_NAME.get(plan.status, "未知"),
        "startAt": t0.strftime("%Y-%m-%d %H:%M:%S") if t0 else None,
        "durationSec": points[-1]["offsetSec"] if points else 0,
        "frames": len(points),
        "violationFrames": sum(1 for p in points if p["violation"]),
        "batteryThreshold": scope.sys_cfg_num("battery_return_threshold", 20, cast=float),
        "points": points,
    }}


# ---------------- 运行告警（真实数据派生） ----------------
@bp.get("/alerts")
@perm_required("monitor:realtime")
def alerts():
    """告警 = 违规记录 + 设备状态 + 任务异常，全部来自库里真实数据。"""
    out = []

    # 1) 违规记录（未处理/处理中）
    vio = db.session.execute(db.text("""
        SELECT v.id, v.violation_no, v.violation_type, v.violation_time, v.description,
               v.plan_id, v.status, p.plan_no, a.aircraft_no, v.merchant_id
        FROM violation_record v
        LEFT JOIN flight_plan p ON p.id = v.plan_id
        LEFT JOIN aircraft a ON a.id = v.aircraft_id
        WHERE v.deleted = 0 AND v.status IN (1, 2)
        ORDER BY v.violation_time DESC LIMIT 50""")).mappings().all()
    for r in vio:
        out.append({
            "level": "严重" if r["violation_type"] in (1, 4) else "警告",
            "type": VIO_TYPE_NAME.get(r["violation_type"], "违规"),
            "target": r["aircraft_no"] or r["plan_no"] or f"#{r['id']}",
            "detail": r["description"] or "空域合规违规，等待处置",
            "time": r["violation_time"].strftime("%m-%d %H:%M:%S") if r["violation_time"] else None,
            "source": "violation", "refId": r["id"], "status": r["status"],
        })

    # 2) 设备：离线 / 低电量（阈值取 sys_config.battery_return_threshold）
    battery_line = scope.sys_cfg_num("battery_return_threshold", 20, cast=float)
    for a in db.session.query(Aircraft).filter(Aircraft.deleted == 0).all():
        if not a.online:
            out.append({"level": "严重", "type": "离线", "target": a.aircraft_no,
                        "detail": "飞行器离线，无法调度", "time": None, "source": "device"})
        elif a.battery and float(a.battery) < battery_line:
            out.append({"level": "严重", "type": "低电量", "target": a.aircraft_no,
                        "detail": f"电量 {float(a.battery):.0f}%，低于返航阈值 {battery_line:.0f}%",
                        "time": None, "source": "device"})
        elif a.battery and float(a.battery) < 50:
            out.append({"level": "提示", "type": "电量偏低", "target": a.aircraft_no,
                        "detail": f"电量 {float(a.battery):.0f}%，低于调度阈值",
                        "time": None, "source": "device"})

    # 3) 在飞任务：超时未降落 / 遥测失联
    now = datetime.now()
    for p in db.session.query(FlightPlan).filter(FlightPlan.status == 3).all():
        est = p.est_duration_sec or 600
        if p.actual_takeoff_at:
            overdue = now - (p.actual_takeoff_at + timedelta(seconds=est * 1.5))
            if overdue.total_seconds() > 0:
                out.append({"level": "严重", "type": "超时飞行", "target": p.plan_no,
                            "detail": f"预计航时 {est // 60} 分钟，已超时 "
                                      f"{int(overdue.total_seconds() // 60)} 分钟未降落",
                            "time": now.strftime("%m-%d %H:%M:%S"), "source": "task"})
        gap = (now - p.last_report_at).total_seconds() if p.last_report_at else None
        if gap is not None and gap > max(60, est * 0.5):
            out.append({"level": "警告", "type": "遥测失联", "target": p.plan_no,
                        "detail": f"最近上报已 {int(gap)} 秒前", "time": now.strftime("%m-%d %H:%M:%S"),
                        "source": "task"})

    # 4) 待审批堆积
    pending = db.session.query(FlightPlan).filter(FlightPlan.status == 1).count()
    if pending:
        out.append({"level": "提示", "type": "待审批堆积", "target": f"{pending} 条",
                    "detail": "有待审批飞行计划未处理", "time": None, "source": "task"})

    # 5) 应急事件（发生中/处置中）——仿真注入的失联、恶劣天气等从这里显形
    emg = db.session.execute(db.text("""
        SELECT e.id, e.event_no, e.event_type, e.level, e.description, e.started_at,
               e.status, p.plan_no, a.aircraft_no
        FROM emergency_event e
        LEFT JOIN flight_plan p ON p.id = e.plan_id
        LEFT JOIN aircraft a ON a.id = e.aircraft_id
        WHERE e.deleted = 0 AND e.status IN (1, 2)
        ORDER BY e.level DESC, e.started_at DESC LIMIT 30""")).mappings().all()
    level_name = {3: "严重", 2: "严重", 1: "警告"}
    for r in emg:
        out.append({
            "level": level_name.get(r["level"], "警告"),
            "type": EMERGENCY_TYPE_NAME.get(r["event_type"], "应急事件"),
            "target": r["aircraft_no"] or r["plan_no"] or r["event_no"],
            "detail": r["description"] or "应急事件待处置",
            "time": r["started_at"].strftime("%m-%d %H:%M:%S") if r["started_at"] else None,
            "source": "emergency", "refId": r["id"], "status": r["status"],
        })

    order_rank = {"严重": 0, "警告": 1, "提示": 2}
    out.sort(key=lambda x: (order_rank.get(x["level"], 3), x["time"] or ""))
    return success({"list": out[:80], "total": len(out),
                    "counts": {lv: sum(1 for x in out if x["level"] == lv)
                               for lv in ("严重", "警告", "提示")}})


# ---------------- M-63 设备在线 ----------------
# 设备台账不另建表：机载终端就是 aircraft，地面设备就是 vertiport（含充换电桩）。
# 在线/离线一律从真实信号推出来，只有"运维口头确认的失联"这种库里推不出的判断
# 才落 device_lost_mark（解除标记即删行，不留下第二份状态源）。
#
# 字段名口径（B6-3 拆开的一处同名歧义）：这里的 **terminalType** 指"终端类别"
# （1 机载终端 / 2 地面设备，DB 列仍是 device_lost_mark.device_type），
# 与登录用的请求头 `Device-Type: PC|Mobile`（`auth.py:13-15`，只决定 token 时效与
# 出参裁剪）没有任何关系。两者以前都叫 deviceType，接入真机时一定会被问住，所以改掉接口层名字。
TERMINAL_TYPE_NAME = {1: "机载终端", 2: "地面设备"}
DEVICE_STATE_NAME = {"lost": "失联", "offline": "离线", "online": "在线"}
_STATE_RANK = {"lost": 0, "offline": 1, "online": 2}
_AIRCRAFT_STATUS = {1: "闲置", 2: "执行中", 3: "充换电", 4: "维保中", 5: "故障离线"}
_STATION_STATUS = {1: "开放", 2: "维护中", 3: "关闭"}


def _lost_marks() -> dict:
    rows = db.session.execute(db.text("""
        SELECT m.device_type, m.device_id, m.reason, m.marked_at, u.real_name
        FROM device_lost_mark m LEFT JOIN users u ON u.id = m.marked_by""")).mappings().all()
    return {(r["device_type"], r["device_id"]): r for r in rows}


def _notify_lost(dtype: int, did: int, on: bool, reason: str | None) -> None:
    """设备失联/恢复 → 站内信告警。

    差距分析 §7 说的是这张通知表"只有读、没有写"，告警是最典型的一处：
    大屏上标了失联，除了点标记的那个人谁都不知道。
    收件人 = 平台侧 monitor:device 持有者 + 该设备所属商家的设备运维员。
    不给"所有商家的运维员"：别家商家的机载终端失联不需要别家来修，
    而 aircraft_no 出现在别家铃铛里本身就是一次跨商家信息泄露。
    """
    if dtype == 1:
        dev = db.session.get(Aircraft, did)
        label = dev.aircraft_no if dev else f"终端 #{did}"
        mid = dev.merchant_id if dev else None
    else:
        dev = db.session.get(Vertiport, did)
        label = dev.station_name if dev else f"地面设备 #{did}"
        mid = dev.merchant_id if dev else None

    recipients = scope.user_ids_with_perm("monitor:device", platform_only=True)
    if mid:
        recipients += scope.user_ids_with_perm("monitor:device", mid)
    title = f"{'设备失联' if on else '设备失联已解除'}：{label}"
    content = (f"运维已确认{TERMINAL_TYPE_NAME[dtype]} {label} 失联"
               f"，原因：{reason or '未填写'}。请核查图传与遥测链路，"
               "恢复后到「设备在线」页解除标记。" if on else
               f"{TERMINAL_TYPE_NAME[dtype]} {label} 的失联标记已解除，"
               "在线状态重新由心跳与运营状态推导。")
    notify.notify_many(recipients, title, content,
                       biz_type="device_lost" if on else "device_recover", biz_id=did)


def _fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _row(terminal_type: int, device_id: int, no: str, name: str, state: str,
         hb: datetime | None, hb_kind: str, lng, lat, extra: dict, mark) -> dict:
    age = int((datetime.now() - hb).total_seconds()) if hb else None
    return {
        "terminalType": terminal_type, "typeName": TERMINAL_TYPE_NAME[terminal_type],
        "id": device_id, "no": no, "name": name,
        "state": state, "stateName": DEVICE_STATE_NAME[state],
        "lastHeartbeat": _fmt(hb), "heartbeatAgeSec": age, "heartbeatKind": hb_kind,
        "lng": float(lng) if lng is not None else None,
        "lat": float(lat) if lat is not None else None,
        "lostReason": (mark["reason"] if mark else None),
        "lostBy": (mark["real_name"] if mark else None),
        "lostAt": _fmt(mark["marked_at"]) if mark else None,
        **extra,
    }


def _aircraft_state(mark, online_flag, flying: bool, fresh: bool) -> str:
    """机载终端三态判定。devices() 与心跳上报共用，两份"看着一样"的推导迟早分叉。

    计划在飞但心跳断了才算"失联"（需要人处置）；停场设备没链路，就是离线。
    """
    if mark:
        return "lost"
    if fresh and online_flag:
        return "online"
    return "lost" if flying else "offline"


def _station_state(mark, status: int, hb_at: datetime | None, fresh: bool) -> str:
    """地面设备三态：报过心跳就按心跳判，从没报过的才回落到运营状态。

    `hb_at is None` 与"有心跳但超时"必须分开：前者是台账里没有链路（演示库里所有场站），
    按"开放即在线"是对的；后者是设备自己停了口，再按运营状态显示在线就是粉饰。
    """
    if mark:
        return "lost"
    if hb_at is not None:
        return "online" if fresh else "offline"
    return "online" if status == 1 else "offline"


@bp.get("/devices")
@perm_required("monitor:device")
def devices():
    """设备在线总览：机载终端按心跳判状态，地面设备有心跳按心跳、没心跳才按运营状态。

    心跳取"最近一次真实写入时间"：在飞的飞行器每个 tick 回写位置（aircraft.updated_at）
    和上报时间（flight_plan.last_report_at），两者取大；停场设备没有遥测链路，
    自然就是离线——这里不做"看起来全在线"的粉饰。
    场站一侧 last_heartbeat_at 为空表示从没报过，此时拿 updated_at 当心跳是撒谎，
    所以 heartbeatKind 给 record，页面会把它标成"资料更新"。

    行门（D-189）：monitor:device 也发给了商家侧运维，只按权限点放行等于把别家的机号、
    实时坐标、在飞任务号一起端出去。所以飞行器按本商家收窄，场站按"本商家 + 公共场站"
    收窄（判据与资源台账 list_aircraft、调度台 list_resources 同一份）；平台侧不裁行。
    机位占用数是场站级的基础设施口径，不跟着裁——预约冲突提示本来就按总数告知余量。
    """
    mid = scope.merchant_scope(current_user())
    ac_cond = "AND a.merchant_id = :__mid" if mid is not None else ""
    vp_cond = ("AND (merchant_id = :__mid OR merchant_id IS NULL)"
               if mid is not None else "")
    params = {"__mid": mid} if mid is not None else {}
    timeout = scope.sys_cfg_num("device_heartbeat_timeout_sec", 60, cast=float)
    now = datetime.now()
    marks = _lost_marks()
    out = []

    aircraft = db.session.execute(db.text(f"""
        SELECT a.id, a.aircraft_no, a.brand, a.model, a.status, a.battery, a.online,
               a.current_lng, a.current_lat, a.updated_at,
               (SELECT MAX(p.last_report_at) FROM flight_plan p
                 WHERE p.aircraft_id = a.id AND p.status = 3 AND p.deleted = 0) AS report_at,
               (SELECT p.plan_no FROM flight_plan p
                 WHERE p.aircraft_id = a.id AND p.status = 3 AND p.deleted = 0
                 ORDER BY p.id DESC LIMIT 1) AS plan_no
        FROM aircraft a WHERE a.deleted = 0 {ac_cond} ORDER BY a.id"""), params).mappings().all()
    for r in aircraft:
        hb = max([x for x in (r["updated_at"], r["report_at"]) if x], default=None)
        flying = bool(r["plan_no"])
        fresh = hb is not None and (now - hb).total_seconds() <= timeout
        mark = marks.get((1, r["id"]))
        state = _aircraft_state(mark, r["online"], flying, fresh)
        out.append(_row(1, r["id"], r["aircraft_no"],
                        f"{r['brand'] or ''} {r['model'] or ''}".strip() or r["aircraft_no"],
                        state, hb, "telemetry", r["current_lng"], r["current_lat"],
                        {"battery": int(r["battery"] or 0), "onlineFlag": int(r["online"] or 0),
                         "statusCode": r["status"], "statusName": _AIRCRAFT_STATUS.get(r["status"], "未知"),
                         "planNo": r["plan_no"]}, mark))

    stations = db.session.execute(db.text(f"""
        SELECT id, station_no, station_name, station_type, status, lng, lat,
               charger_total, charger_available,
               last_heartbeat_at, updated_at
        FROM vertiport WHERE deleted = 0 {vp_cond} ORDER BY id"""), params).mappings().all()
    occ = station_occupancy()
    for r in stations:
        # 有心跳读心跳，没心跳才读台账时钟，并把这一区别原样告诉前端：
        # 时间列后缀"· 资料更新"就是页面在说"这个数字不是心跳，别当在线证据看"
        hb = r["last_heartbeat_at"] or r["updated_at"]
        mark = marks.get((2, r["id"]))
        fresh = hb is not None and (now - hb).total_seconds() <= timeout
        state = _station_state(mark, r["status"], r["last_heartbeat_at"], fresh)
        out.append(_row(2, r["id"], r["station_no"], r["station_name"],
                        state, hb, "telemetry" if r["last_heartbeat_at"] else "record",
                        r["lng"], r["lat"],
                        {"stationType": r["station_type"],
                         "statusName": _STATION_STATUS.get(r["status"], "未知"),
                         "chargerTotal": r["charger_total"], "chargerAvailable": r["charger_available"],
                         "occupiedCount": occ.get(r["id"], 0)}, mark))

    out.sort(key=lambda x: (_STATE_RANK[x["state"]], x["terminalType"], x["no"] or ""))
    return success({
        "list": out,
        "counts": {k: sum(1 for x in out if x["state"] == k) for k in DEVICE_STATE_NAME},
        "timeoutSec": timeout,
        # 电量条的红色分档沿用调度阈值，前端不必再猜一个 20
        "batteryThreshold": scope.sys_cfg_num("battery_return_threshold", 20, cast=float),
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
    })


@bp.post("/devices/lost-mark")
@perm_required("monitor:device")
def mark_lost():
    """人工标记失联：库里推不出"图传丢了但遥测还在报"这类判断，交给运维落一笔标记。"""
    body = request.get_json(silent=True) or {}
    dtype = body.get("terminalType")
    did = body.get("deviceId")
    reason = (body.get("reason") or "").strip()[:200] or None
    if dtype not in TERMINAL_TYPE_NAME:
        raise ApiError(BizCode.PARAM_ERROR, "终端类别只能是 1（机载终端）或 2（地面设备）", 400)
    if not isinstance(did, int) or did <= 0:
        raise ApiError(BizCode.PARAM_ERROR, "缺少设备 id", 400)
    table = "aircraft" if dtype == 1 else "vertiport"
    if not db.session.execute(db.text(f"SELECT id FROM {table} WHERE id=:i AND deleted=0"),
                              {"i": did}).scalar():
        raise ApiError(BizCode.NOT_FOUND, "设备不存在", 404)
    # 已经标记在案的只更新原因，不再重发告警：重复点"标记失联"刷不出第二条站内信，
    # 否则运维连点三下就变成三条一样的告警，很快就没人看了。
    already = db.session.execute(
        db.text("SELECT 1 FROM device_lost_mark WHERE device_type=:t AND device_id=:i"),
        {"t": dtype, "i": did}).scalar()
    db.session.execute(db.text("""
        INSERT INTO device_lost_mark (device_type, device_id, reason, marked_by, marked_at)
        VALUES (:t, :i, :r, :u, NOW())
        ON DUPLICATE KEY UPDATE reason=:r, marked_by=:u, marked_at=NOW()"""),
        {"t": dtype, "i": did, "r": reason, "u": current_user().id})
    db.session.commit()
    if not already:
        _notify_lost(dtype, did, True, reason)
    return success(message="已标记为失联")


@bp.delete("/devices/lost-mark")
@perm_required("monitor:device")
def clear_lost():
    """解除失联标记：删行即可，状态立刻回到由心跳/运营状态推导的结果。"""
    dtype = request.args.get("terminalType", type=int)
    did = request.args.get("deviceId", type=int)
    if dtype not in TERMINAL_TYPE_NAME or not did:
        raise ApiError(BizCode.PARAM_ERROR, "参数不完整", 400)
    rows = db.session.execute(
        db.text("DELETE FROM device_lost_mark WHERE device_type=:t AND device_id=:i"),
        {"t": dtype, "i": did}).rowcount
    db.session.commit()
    if rows:                                  # 本来就没标记：不编出一条"已解除"的告警
        _notify_lost(dtype, did, False, None)
    return success(message="已解除失联标记")


# ---------------- 遥测上报（设计03 D6 · 设计07 的统一上报入口） ----------------
# 契约把 `/tracks/batch` 写成"仿真器与真机 GCS 共用的同一个入口"，`flight_track.source`
# 就是为这一步留的列（sql/tianshu-ddl-v1.sql:484）。落库与合规判定都在 track_service，
# 这一层只管"外部请求能不能进来"：谁能报、什么时候能报、报了回什么。
# 计划落地之后还留 15 分钟补报窗口——链路最后一段断了，航迹不该就此消失。
BACKFILL_WINDOW_SEC = 900


def _reportable_plan(user, plan_id) -> FlightPlan:
    """上报闸门：计划存在 → 是这个人在飞 → 还在能写轨迹的时间窗里。

    行级只认两种身份：平台侧（空管/运维/平台管理员）与该计划的机长。
    商家调度员也持有 monitor:track，但那是给他看回放用的，不等于能往别人的飞行计划里灌轨迹。
    """
    try:
        pid = int(plan_id)
    except (TypeError, ValueError):
        raise ApiError(BizCode.PARAM_ERROR, "planId 缺失或非法", 400)
    plan = db.session.get(FlightPlan, pid)
    if plan is None or plan.deleted:
        raise ApiError(BizCode.NOT_FOUND, "飞行计划不存在", 404)
    if not scope.is_platform(user) and plan.pilot_id != user.id:
        raise ApiError(BizCode.FORBIDDEN, "只能上报本人执飞任务的轨迹", 403)
    landing = plan.actual_landing_at
    in_backfill = (plan.status == 4 and landing is not None
                   and (datetime.now() - landing).total_seconds() <= BACKFILL_WINDOW_SEC)
    if plan.status != 3 and not in_backfill:
        raise ApiError(BizCode.CONFLICT,
                       f"计划当前状态（{PLAN_STATUS_NAME.get(plan.status, '未知')}）不接受轨迹上报，"
                       f"只有执行中、或落地 {BACKFILL_WINDOW_SEC // 60} 分钟内可补报", 409)
    if not plan.aircraft_id:
        raise ApiError(BizCode.CONFLICT, "计划尚未指派飞行器，轨迹无法归属", 409)
    return plan


@bp.post("/tracks/batch")
@perm_required("monitor:track")
def tracks_batch():
    """批量帧落库（真机 GCS 入口，source 服务端写死 2）。

    请求体里自报的 source / violation / violationType 一律不看：source 是"这条轨迹该不该信"
    的依据，让上报方自己填就废了；地面站说自己没偏航也不算证据，
    合规判定只有 compliance_monitor 一个出口。
    """
    body = request.get_json(silent=True) or {}
    plan = _reportable_plan(current_user(), body.get("planId"))
    raw = body.get("frames")
    if not isinstance(raw, list):
        raise ApiError(BizCode.PARAM_ERROR, "frames 必须是数组", 400)
    now = datetime.now().replace(microsecond=0)   # DATETIME(0) 会四舍五入，心跳时钟按整秒记
    frames, errors = track_service.normalize_frames(raw, now=now)
    if not frames:
        # 全批没有一帧能用：这时候给 200 会让上报方以为链路通了、只是数据怪。
        why = "；".join(f"第 {e['index'] + 1} 帧 {e['reason']}" for e in errors[:3]) or "frames 为空"
        raise ApiError(BizCode.PARAM_ERROR, f"没有可用帧（{why}）", 400)
    result = track_service.persist_frames(plan, frames, source=2, dedupe=True)
    track_service.apply_telemetry(
        plan, max(frames, key=lambda f: f["t"]),
        aircraft=db.session.get(Aircraft, plan.aircraft_id), now=now)
    db.session.commit()
    return success({
        "planId": plan.id, "planNo": plan.plan_no, "source": 2,
        "sourceName": track_service.SOURCE_NAME[2],
        "accepted": result["accepted"], "duplicated": result["duplicated"],
        "violations": result["violations"], "newTypes": result["newTypes"],
        "rejectedCount": len(errors), "rejected": errors,
        "firstTime": _fmt(frames[0]["t"]), "lastTime": _fmt(frames[-1]["t"]),
    }, message=f"已接收 {result['accepted']} 帧")


@bp.post("/devices/heartbeat")
@perm_any_required("monitor:device", "monitor:track")
def device_heartbeat():
    """设备心跳：机载终端刷新遥测时钟，地面设备刷新 last_heartbeat_at。

    M-63 的在线状态一直是"从库里最近一次真实写入时间"推出来的，仿真器每个 tick 都在写，
    真机没有这个动作——缺了心跳端点，接上真机也永远显示离线。
    两条边界：一是只动 online 与热快照，不碰 aircraft.status / plan.status（状态机唯一入口
    是 D3 的 dispatch_service，遥测不是审批也不是降落指令）；二是不自动解除人工失联标记，
    运维标了失联、链路一跳就自己变回在线，那标记就成了摆设。
    """
    body = request.get_json(silent=True) or {}
    user = current_user()
    dtype = body.get("terminalType")
    did = body.get("deviceId")
    if dtype not in TERMINAL_TYPE_NAME:
        raise ApiError(BizCode.PARAM_ERROR, "终端类别只能是 1（机载终端）或 2（地面设备）", 400)
    if not isinstance(did, int) or did <= 0:
        raise ApiError(BizCode.PARAM_ERROR, "缺少设备 id", 400)
    now = datetime.now().replace(microsecond=0)     # DATETIME(0) 会四舍五入，心跳时钟按整秒记
    marks = _lost_marks()

    if dtype == 1:
        ac = db.session.get(Aircraft, did)
        if ac is None or ac.deleted:
            raise ApiError(BizCode.NOT_FOUND, "设备不存在", 404)
        if not scope.is_platform(user) and ac.merchant_id != user.merchant_id:
            raise ApiError(BizCode.FORBIDDEN, "只能上报本商家设备的遥测心跳", 403)
        frame = track_service.heartbeat_frame(body)
        plan = db.session.query(FlightPlan).filter(
            FlightPlan.aircraft_id == did, FlightPlan.status == 3,
            FlightPlan.deleted == 0).order_by(FlightPlan.id.desc()).first()
        if plan is not None:
            # 在飞的计划顺带把热快照挪过去：大屏、设备在线页、回放读的都是这几列
            track_service.apply_telemetry(plan, frame, aircraft=ac, now=now)
        else:
            ac.online = 1
            if frame.get("lng") is not None:
                ac.current_lng, ac.current_lat = frame["lng"], frame["lat"]
            if frame.get("battery") is not None:
                ac.battery = frame["battery"]
        ac.updated_at = now      # 显式赋值：MySQL 的 ON UPDATE 在"值没变"时不会刷新，
        # 而心跳要的恰恰是"时钟必须动"，否则在线状态会自己过期的设备永远醒不来
        mark = marks.get((1, did))
        state = _aircraft_state(mark, 1, plan is not None, True)
        extra = {"battery": int(ac.battery or 0), "aircraftNo": ac.aircraft_no,
                 "planId": plan.id if plan else None, "planNo": plan.plan_no if plan else None}
    else:
        st = db.session.get(Vertiport, did)
        if st is None or st.deleted:
            raise ApiError(BizCode.NOT_FOUND, "设备不存在", 404)
        # 公共场站（merchant_id 为空）只有平台侧能报；商家场站只认本商家。
        if not (scope.is_platform(user)
                or (user.merchant_id and st.merchant_id == user.merchant_id)):
            raise ApiError(BizCode.FORBIDDEN, "只能上报本商家场站的心跳", 403)
        charger = body.get("chargerAvailable")
        if isinstance(charger, int) and 0 <= charger <= (st.charger_total or 0):
            st.charger_available = charger   # 桩位上报的是实时值，越界就当没报
        # 心跳时钟单独一列：updated_at 还要留给"资料什么时候改过"，
        # 两者混在一列就分不出"从没报过"和"报过之后停了"
        st.last_heartbeat_at = now
        st.updated_at = now
        mark = marks.get((2, did))
        state = _station_state(mark, st.status, now, True)
        extra = {"stationNo": st.station_no, "chargerAvailable": st.charger_available,
                 "planId": None, "planNo": None}

    db.session.commit()
    return success({
        "terminalType": dtype, "typeName": TERMINAL_TYPE_NAME[dtype], "deviceId": did,
        "state": state, "stateName": DEVICE_STATE_NAME[state],
        "lastHeartbeat": _fmt(now), "lostMarked": bool(mark),
        **extra,
    }, message="心跳已记录")
