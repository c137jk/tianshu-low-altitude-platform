"""W5 低空资源蓝图：场站、飞行器台账、资质台账（读 + 写）。

门禁口径（差距分析 P0-1）：
  读  —— resource:vertiport / resource:aircraft / resource:qualification，
          以及调度台、监控台需要的台账读（dispatch:plan / monitor:realtime）；
  写  —— 对应台账权限点；场站与飞行器新增属平台侧资产（_platform_only），
          商家运维只能更新本机状态。
  查看 ≠ 管理 —— 人员台账与资质台账按"能不能打开这一页"放行（qualification /
          schedule / aircraft 任一），复审动作只认 resource:qualification；
          本人的资质与值班走 /my-duty 自助口，不需要任何 resource:* 权限点。
  新建 ≠ 有效 —— 资质新建有两条口（管理侧 /qualifications、本人 /qualifications/mine），
          落库一律是 0 待审核，审核岗自己登记也不例外；原件没有直读通道，
          只有 GET /qualifications/<id>/file（台账名单 + 商家归属）和
          GET /qualifications/mine/<id>/file（本人，subject 条件写在 WHERE 里）两条出口。
"""
import os
import random
import re
import uuid
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, request, send_from_directory, abort

from app.common import scope
from app.common.decorators import current_user, perm_any_required, perm_required, staff_required
from app.common.errors import ApiError
from app.common.response import BizCode, success
from app.extensions import db
from app.models import Aircraft, Qualification, StationReservation, Vertiport
from app.services import notify, order_service
from app.services.dispatch_service import PLAN_STATUS, station_occupancy

bp = Blueprint("resource", __name__, url_prefix="/api/resource")

SHIFT_NAME = {1: "早班", 2: "中班", 3: "晚班", 4: "全天"}


def _platform_only():
    """写操作收口：场站/飞行器台账属平台侧资产，商家员工一律 403。"""
    if not scope.is_platform(current_user()):
        abort(403, description='仅平台侧账号可以修改资源台账')

VP_TYPE = {1: "起降场", 2: "换电站", 3: "综合站"}
VP_STATUS = {1: "开放", 2: "维护中", 3: "关闭"}
AC_STATUS = {1: "闲置", 2: "执行中", 3: "充换电", 4: "维保中", 5: "故障离线"}

# 请求体统一 camelCase，ORM 属性是 snake_case，这里显式映射。
# 早先 PUT 直接拿 snake_case 去 dict 里找键，字段全部落空，接口"保存成功"其实什么都没改。
VP_FIELDS = {
    "stationNo": "station_no", "stationName": "station_name", "stationType": "station_type",
    "lng": "lng", "lat": "lat", "address": "address", "capacity": "capacity",
    "chargerTotal": "charger_total", "chargerAvailable": "charger_available", "status": "status",
}
AC_FIELDS = {
    "aircraftNo": "aircraft_no", "brand": "brand", "model": "model",
    "enduranceMin": "endurance_min", "maxLoadKg": "max_load_kg",
    "maxSpeed": "max_speed", "maxAltitude": "max_altitude", "status": "status",
}


def _apply(obj, data, mapping) -> int:
    """按映射把请求字段写进 ORM 属性，返回实际写入的条数。"""
    changed = 0
    for k, col in mapping.items():
        if k in data:
            setattr(obj, col, data[k])
            changed += 1
    return changed


@bp.get("/vertiports")
@perm_any_required("resource:vertiport", "dispatch:plan", "monitor:realtime")
def list_vertiports():
    rows = db.session.query(Vertiport).filter(Vertiport.deleted == 0).order_by(Vertiport.id).all()
    occ = station_occupancy([v.id for v in rows])
    data = [{
        "id": v.id, "stationNo": v.station_no, "stationName": v.station_name,
        "stationType": v.station_type, "typeName": VP_TYPE.get(v.station_type),
        "lng": float(v.lng), "lat": float(v.lat), "address": v.address,
        "capacity": v.capacity,
        "chargerTotal": v.charger_total, "chargerAvailable": v.charger_available,
        "occupiedCount": occ.get(v.id, 0),
        "status": v.status, "statusName": VP_STATUS.get(v.status),
    } for v in rows]
    return success({"list": data, "total": len(data)})


@bp.get("/aircraft")
@perm_any_required("resource:aircraft", "dispatch:plan", "monitor:realtime")
def list_aircraft():
    """飞行器台账。飞行器是商家资产（aircraft.merchant_id），商家账号只看自有机；
    调度台/监控台是平台侧在用，走 dispatch:plan / monitor:realtime 进来时看到全量。"""
    u = current_user()
    q = scope.scope_orm(db.session.query(Aircraft).filter(Aircraft.deleted == 0), u, Aircraft)
    rows = q.order_by(Aircraft.id).all()
    station_names = {v.id: v.station_name for v in db.session.query(Vertiport).all()}
    merchants = dict(db.session.execute(db.text(
        "SELECT id, merchant_name FROM merchants WHERE deleted = 0")).all())
    data = [{
        "id": a.id, "aircraftNo": a.aircraft_no,
        "brand": a.brand, "model": f"{a.brand or ''} {a.model or ''}".strip(),
        "modelName": a.model,
        "merchantId": a.merchant_id, "merchantName": merchants.get(a.merchant_id),
        "enduranceMin": a.endurance_min, "maxLoadKg": float(a.max_load_kg or 0),
        "maxAltitude": a.max_altitude,
        "nextMaintainAt": a.next_maintain_at.strftime("%Y-%m-%d") if a.next_maintain_at else None,
        "status": a.status, "statusName": AC_STATUS.get(a.status),
        "battery": float(a.battery or 0), "online": int(a.online or 0),
        "healthScore": a.health_score, "taskCount": a.task_count,
        "totalFlightMin": a.total_flight_min, "faultCount": a.fault_count,
        "vertiportName": station_names.get(a.current_vertiport_id),
        "lng": float(a.current_lng) if a.current_lng else None,
        "lat": float(a.current_lat) if a.current_lat else None,
    } for a in rows]
    return success({"list": data, "total": len(data)})


MT_TYPE_NAME = {1: "定期维保", 2: "故障维修", 3: "巡检"}
MT_STATUS_NAME = {1: "待处理", 2: "处理中", 3: "已完成"}


def _dt(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


@bp.get("/aircraft/<int:aid>")
@perm_any_required("resource:aircraft", "dispatch:plan", "monitor:realtime")
def aircraft_detail(aid):
    """飞行器详情（M-52）：台账 + 在飞任务 + 维保历史 + 飞行履历，一屏看全。

    调度台/监控台也在放行权限里：它们点开机号时要能看到"这台机为什么被停"，
    否则运维在台账里改了状态、调度那边只看到一个数字，说不出依据。
    """
    u = current_user()
    ac = db.session.query(Aircraft).filter(Aircraft.id == aid, Aircraft.deleted == 0).first()
    if not ac:
        raise ApiError(BizCode.NOT_FOUND, "飞行器不存在", 404)
    frag, params = scope.merchant_filter(u, "merchant_id")
    if frag and not db.session.execute(
            db.text("SELECT id FROM aircraft WHERE id=:i AND deleted=0" + frag),
            dict(params, i=aid)).scalar():
        raise ApiError(BizCode.FORBIDDEN, "只能查看本商家飞行器的详情", 403)

    station = db.session.get(Vertiport, ac.current_vertiport_id) if ac.current_vertiport_id else None
    merchant = db.session.execute(db.text(
        "SELECT merchant_name FROM merchants WHERE id=:i"), {"i": ac.merchant_id}).scalar()

    flying = db.session.execute(db.text("""
        SELECT id, plan_no, task_name, altitude_m, current_lng, current_lat,
               current_battery, last_report_at, actual_takeoff_at
        FROM flight_plan WHERE aircraft_id=:a AND status=3 AND deleted=0
        ORDER BY id DESC LIMIT 1"""), {"a": aid}).mappings().first()

    mt = db.session.execute(db.text("""
        SELECT m.id, m.record_type, m.fault_type, m.description, m.status, m.result,
               m.started_at, m.finished_at, m.flight_minutes_at, u.real_name AS handler_name
        FROM maintenance_record m LEFT JOIN users u ON u.id = m.handler_id
        WHERE m.aircraft_id=:a AND m.deleted=0 ORDER BY m.id DESC LIMIT 30"""), {"a": aid}).mappings().all()

    fl = db.session.execute(db.text("""
        SELECT p.id, p.plan_no, p.task_name, p.status, p.altitude_m, p.route_distance_m,
               p.est_duration_sec, p.plan_start_at, p.actual_takeoff_at, p.actual_landing_at,
               o.order_no, vt.station_name AS takeoff_name, vl.station_name AS landing_name,
               (SELECT COUNT(*) FROM violation_record v
                 WHERE v.plan_id = p.id AND v.deleted = 0) AS violations
        FROM flight_plan p
        LEFT JOIN orders o ON o.id = p.order_id
        LEFT JOIN vertiport vt ON vt.id = p.takeoff_vertiport_id
        LEFT JOIN vertiport vl ON vl.id = p.landing_vertiport_id
        WHERE p.aircraft_id=:a AND p.deleted=0 ORDER BY p.id DESC LIMIT 30"""), {"a": aid}).mappings().all()

    stat = db.session.execute(db.text("""
        SELECT COUNT(*) AS plans,
               COALESCE(SUM(status=4),0) AS done,
               COALESCE(SUM(status IN (5,6)),0) AS aborted,
               COALESCE(SUM(actual_landing_at IS NOT NULL),0) AS landed
        FROM flight_plan WHERE aircraft_id=:a AND deleted=0"""), {"a": aid}).mappings().first()
    open_vio = db.session.execute(db.text("""
        SELECT COUNT(*) c FROM violation_record v JOIN flight_plan p ON p.id = v.plan_id
        WHERE p.aircraft_id=:a AND v.deleted=0 AND v.status IN (1,2)"""), {"a": aid}).scalar()

    return success({
        "aircraft": {
            "id": ac.id, "aircraftNo": ac.aircraft_no, "serialNo": ac.serial_no,
            "brand": ac.brand, "model": ac.model,
            "modelName": f"{ac.brand or ''} {ac.model or ''}".strip(),
            "merchantId": ac.merchant_id, "merchantName": merchant,
            "enduranceMin": ac.endurance_min, "maxLoadKg": float(ac.max_load_kg or 0),
            "maxSpeed": float(ac.max_speed or 0), "maxAltitude": ac.max_altitude,
            "status": ac.status, "statusName": AC_STATUS.get(ac.status, "未知"),
            "battery": int(ac.battery or 0), "online": int(ac.online or 0),
            "healthScore": ac.health_score, "taskCount": ac.task_count,
            "totalFlightMin": ac.total_flight_min, "faultCount": ac.fault_count,
            "purchaseAt": ac.purchase_at.strftime("%Y-%m-%d") if ac.purchase_at else None,
            "lastMaintainAt": _dt(ac.last_maintain_at), "nextMaintainAt": _dt(ac.next_maintain_at),
            "maintainOverdue": bool(ac.next_maintain_at and ac.next_maintain_at.date() < date.today()),
            "vertiportId": ac.current_vertiport_id,
            "vertiportName": station.station_name if station else None,
            "lng": float(ac.current_lng) if ac.current_lng is not None else None,
            "lat": float(ac.current_lat) if ac.current_lat is not None else None,
            "updatedAt": _dt(ac.updated_at),
        },
        "flying": None if not flying else {
            "id": flying["id"], "planNo": flying["plan_no"], "taskName": flying["task_name"],
            "altitudeM": flying["altitude_m"], "battery": flying["current_battery"],
            "lng": float(flying["current_lng"]) if flying["current_lng"] is not None else None,
            "lat": float(flying["current_lat"]) if flying["current_lat"] is not None else None,
            "takeoffAt": _dt(flying["actual_takeoff_at"]), "lastReportAt": _dt(flying["last_report_at"]),
        },
        "maintenance": [{
            "id": r["id"], "recordType": r["record_type"],
            "typeName": MT_TYPE_NAME.get(r["record_type"], "其他"),
            "faultType": r["fault_type"], "description": r["description"],
            "status": r["status"], "statusName": MT_STATUS_NAME.get(r["status"], "未知"),
            "result": r["result"], "handlerName": r["handler_name"],
            "startedAt": _dt(r["started_at"]), "finishedAt": _dt(r["finished_at"]),
            "flightMinutesAt": r["flight_minutes_at"],
        } for r in mt],
        "flights": [{
            "id": r["id"], "planNo": r["plan_no"], "taskName": r["task_name"],
            "orderNo": r["order_no"], "status": r["status"],
            "statusName": PLAN_STATUS.get(r["status"], "未知"),
            "altitudeM": r["altitude_m"],
            "distanceKm": round(float(r["route_distance_m"] or 0) / 1000, 2),
            # 落过地就用真耗时，没落地只能给申报值——两者必须让看的人分得清
            "durationMin": round((r["actual_landing_at"] - r["actual_takeoff_at"]).total_seconds() / 60, 1)
            if r["actual_landing_at"] and r["actual_takeoff_at"]
            else round(float(r["est_duration_sec"] or 0) / 60, 1),
            "actual": bool(r["actual_landing_at"] and r["actual_takeoff_at"]),
            "takeoffAt": _dt(r["actual_takeoff_at"] or r["plan_start_at"]),
            "landingAt": _dt(r["actual_landing_at"]),
            "route": " → ".join(x for x in (r["takeoff_name"], r["landing_name"]) if x) or "—",
            "violations": r["violations"],
        } for r in fl],
        "stats": {
            # SUM(...) 在 MySQL 里是 DECIMAL，不转 int 出参就变成字符串 "4"，前端拿去做比较会出错
            "plans": int(stat["plans"]), "done": int(stat["done"]), "aborted": int(stat["aborted"]),
            "landed": int(stat["landed"]), "openViolations": int(open_vio or 0),
            "maintenanceOpen": sum(1 for r in mt if r["status"] in (1, 2)),
        },
    })

@bp.get("/personnel")
@perm_any_required("resource:qualification", "resource:schedule", "resource:aircraft",
                   "resource:schedule:view")
def list_personnel():
    """人员台账（需求 4.2.3 人员管理）：花名册 + 任务统计 + 资质到期 + 今日排班。

    一次查清，前端就不必再为了拿机长名单去调 /system/users —— 平台运维有
    resource:schedule 但没有 system:user，原来排班页的"新增排班"下拉在那里直接 403。
    `resource:schedule:view`（只读排班岗）也在这份名单里，理由同上：排班页 onMounted
    必拉花名册来标归属，不给它就会让只读岗一进页面吃 403——这道门必须始终是排班页
    准入名单的超集，由 tests/test_schedule_readonly.py 钉住。这里只放"读"，
    返回的字段里没有手机号与凭证，且商家侧身份仍按 merchant_id 收窄。
    飞行小时按实际起降时间算，不用申报时长（申报值和真飞出来的经常差很多）。
    """
    u = current_user()
    params = {}
    frag = ""
    if not scope.is_platform(u):
        frag = " AND us.merchant_id = :mid"
        params["mid"] = u.merchant_id or 1
    people = db.session.execute(db.text(
        "SELECT us.id, us.username, us.real_name, us.status, m.merchant_name,"
        " GROUP_CONCAT(r.role_name ORDER BY r.sort) AS role_names"
        " FROM users us"
        " JOIN user_role ur ON ur.user_id = us.id"
        " JOIN role r ON r.id = ur.role_id"
        " LEFT JOIN merchants m ON m.id = us.merchant_id"
        " WHERE us.deleted = 0"
        " AND r.role_code IN ('pilot','merchant_maintainer','ops_maintainer')"
        f"{frag}"
        " GROUP BY us.id, us.username, us.real_name, us.status, m.merchant_name"
        " ORDER BY us.id"), params).mappings().all()

    stats = {r["pilot_id"]: r for r in db.session.execute(db.text(
        "SELECT pilot_id,"
        " COUNT(*) AS task_total,"
        " SUM(status = 4) AS task_done,"
        " SUM(status = 3) AS flying,"
        " COALESCE(SUM(TIMESTAMPDIFF(SECOND, actual_takeoff_at, actual_landing_at)),0) AS flown_sec"
        " FROM flight_plan WHERE deleted = 0 AND pilot_id IS NOT NULL GROUP BY pilot_id"
    )).mappings().all()}
    quals = {r["subject_id"]: r for r in db.session.execute(db.text(
        "SELECT subject_id, COUNT(*) AS qual_total,"
        " SUM(status = 3) AS qual_expired, MIN(expire_at) AS nearest_expire_at"
        " FROM qualification WHERE deleted = 0 AND subject_type = 2 GROUP BY subject_id"
    )).mappings().all()}
    shifts = {}
    for r in db.session.execute(db.text(
            "SELECT user_id, shift_type, responsible_area, status FROM staff_schedule"
            " WHERE deleted = 0 AND shift_date = CURDATE() ORDER BY id DESC")).mappings().all():
        shifts.setdefault(r["user_id"], r)

    warn_days = scope.sys_cfg_num("staff_qual_warn_days", 15, int)
    out = []
    for p in people:
        st, q = stats.get(p["id"]), quals.get(p["id"])
        nearest = q["nearest_expire_at"] if q else None
        days_left = (nearest.date() - date.today()).days if nearest else None
        out.append({
            "userId": p["id"], "username": p["username"], "realName": p["real_name"],
            "accountStatus": p["status"], "merchantName": p["merchant_name"],
            "roleNames": p["role_names"],
            "taskTotal": int(st["task_total"]) if st else 0,
            "taskDone": int(st["task_done"] or 0) if st else 0,
            "flying": int(st["flying"] or 0) if st else 0,
            "flownHours": round(int(st["flown_sec"] or 0) / 3600, 1) if st else 0,
            "qualTotal": int(q["qual_total"]) if q else 0,
            "qualExpired": int(q["qual_expired"] or 0) if q else 0,
            "nearestExpireAt": nearest.strftime("%Y-%m-%d") if nearest else None,
            "qualExpiring": bool(days_left is not None and 0 <= days_left <= warn_days),
            "onDutyToday": bool(shifts.get(p["id"])),
            "shiftName": SHIFT_NAME.get(shifts[p["id"]]["shift_type"]) if p["id"] in shifts else None,
        })
    return success({"list": out, "total": len(out), "qualWarnDays": warn_days})


@bp.post("/qualifications/<int:qid>/audit")
@perm_required("resource:qualification")
def audit_qualification(qid):
    """资质审核（需求 4.2.3 人员/飞行器资质）：{"result": 1 通过 | 4 驳回, "opinion": "..."}"""
    u = current_user()
    q = db.session.get(Qualification, qid)
    if q is None or q.deleted:
        abort(404, description="资质记录不存在")
    d = request.get_json(silent=True) or {}
    result = int(d.get("result") or 0)
    if result not in (1, 4):
        abort(400, description="result 需为 1（通过）或 4（驳回）")
    opinion = (d.get("opinion") or "").strip()
    if result == 4 and not opinion:
        abort(400, description="驳回必须写明原因")
    q.status = result
    q.audited_by = u.id
    q.audited_at = datetime.now()
    q.audit_opinion = opinion[:500] or None
    db.session.commit()
    if q.subject_type == 2 and q.subject_id:
        notify.notify(q.subject_id, f"资质审核{'通过' if result == 1 else '驳回'}：{q.qual_name}",
                      opinion or "资质已确认有效，可正常排班执行任务。",
                      biz_type="qualification", biz_id=q.id)
    return success({"id": q.id}, "资质已通过" if result == 1 else "资质已驳回")


QUAL_STATUS = {0: "待审核", 1: "有效", 2: "即将到期", 3: "已过期", 4: "已驳回"}
SUBJECT_TYPE = {1: "商家", 2: "人员", 3: "飞行器"}
# 台账读侧的放行名单 = 人员管理页的准入名单，台账本体与证照原件共用这一份：
# 能进这一页的人就该看得到他看得懂的那几张证的原件，反之不给——
# 分两个名单迟早又长歪（详见差距分析 §二十二）。
QUAL_READ_PERMS = ("resource:qualification", "system:user",
                   "resource:schedule", "resource:aircraft")
QUAL_PERM = "resource:qualification"          # 复审岗（也是新建件的收口人）


@bp.get("/qualifications")
@perm_any_required(*QUAL_READ_PERMS)
def list_qualifications():
    """资质台账（读）。

    放行名单 = 人员管理页的准入名单（见 /personnel 与 constants/menu.ts 的 resource 段）：
    只有 resource:schedule 的角色（平台运维、调度员、设备运维员）本来就进得来这一页，
    也得看得到"我要排的这个人证还有效吗"。原来这里只认 qualification / system:user，
    于是他们一进来就吃 403。放宽的只是读，复审动作仍然只认 resource:qualification；
    数据范围不变，非平台账号只看得到本商家的主体。"""
    u = current_user()
    sql = (
        "SELECT t.* FROM ("
        " SELECT q.id, q.subject_type, q.subject_id, q.qual_type, q.qual_name, q.file_url,"
        "        q.issue_at, q.expire_at, q.status, q.audit_opinion, q.audited_at, q.deleted,"
        "        COALESCE(pu.real_name, CONCAT(ac.brand, ' ', ac.model), mr.merchant_name) AS subject_name,"
        "        mr.merchant_name AS merchant_name,"
        "        CASE q.subject_type WHEN 1 THEN q.subject_id WHEN 2 THEN pu.merchant_id"
        "             ELSE ac.merchant_id END AS owner_merchant"
        "   FROM qualification q"
        "   LEFT JOIN users pu ON q.subject_type = 2 AND pu.id = q.subject_id"
        "   LEFT JOIN aircraft ac ON q.subject_type = 3 AND ac.id = q.subject_id"
        "   LEFT JOIN merchants mr ON mr.id = (CASE q.subject_type"
        "        WHEN 1 THEN q.subject_id WHEN 2 THEN pu.merchant_id ELSE ac.merchant_id END)"
        " ) t WHERE t.deleted = 0"
    )
    params = {}
    if not scope.is_platform(u):
        sql += " AND t.owner_merchant = :mid"
        params["mid"] = u.merchant_id or 1
    stype = request.args.get("subjectType")
    if stype:
        sql += " AND t.subject_type = :stype"
        params["stype"] = int(stype)
    sql += " ORDER BY t.status ASC, t.expire_at ASC"
    rows = db.session.execute(db.text(sql), params).mappings().all()
    out = [{
        "id": r["id"], "subjectType": r["subject_type"],
        "subjectTypeName": SUBJECT_TYPE.get(r["subject_type"]),
        "subjectId": r["subject_id"],
        "subjectName": r["subject_name"] or "?", "pilotName": r["subject_name"] or "?",
        "merchantName": r["merchant_name"],
        "qualType": r["qual_type"], "qualName": r["qual_name"],
        "fileUrl": r["file_url"],
        "issueAt": r["issue_at"].strftime("%Y-%m-%d") if r["issue_at"] else None,
        "expireAt": r["expire_at"].strftime("%Y-%m-%d") if r["expire_at"] else None,
        "status": r["status"], "statusName": QUAL_STATUS.get(r["status"]),
        "auditOpinion": r["audit_opinion"],
        "auditedAt": r["audited_at"].strftime("%Y-%m-%d %H:%M") if r["audited_at"] else None,
    } for r in rows]
    return success({"list": out, "total": len(out)})


# ------------------------------------------------------------------
# 资质新建 + 证照原件（需求 4.2.3「资质文件上传与审核」）
# ------------------------------------------------------------------
QUAL_TYPES = {"license": "营业执照", "permit": "运营许可", "airworthiness": "适航证",
              "driver": "驾驶证", "training": "培训证"}
# 类别与主体的归属：执照是公司的、适航证是飞机的、驾驶证培训证是人的。
# 新建口一开，没有这条约束，台账第一天就会出现"某个机长名下一本营业执照"。
QUAL_TYPE_SUBJECT = {"license": 1, "permit": 1, "airworthiness": 3, "driver": 2, "training": 2}
QUAL_FILE_MIME = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "png": "image/png", "webp": "image/webp"}
# file_url 里只允许这个形状：目录、日期段、文件名全部由服务端生成，读的时候按同一条正则
# 再校验一次。库里就算被塞进 "../x" 或绝对路径也送不出去（send_from_directory 自己还挡一层）。
QUAL_FILE_KEY_RE = re.compile(r"^qualification/\d{6}/[0-9a-f]{32}\.[a-z]{3,4}$")


def _form_int(name: str, default=None):
    """表单里的数字字段：填了但不是数字要当参数错误报出来，不能拖成 500。"""
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ApiError(BizCode.PARAM_ERROR, f"{name} 需为数字", 400)


def _parse_qual_date(raw, label: str):
    v = (raw or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    raise ApiError(BizCode.PARAM_ERROR, f"{label}格式应为 YYYY-MM-DD", 400)


def _subject_owner_merchant(stype: int, sid: int) -> tuple[bool, int | None]:
    """主体在不在、归属哪个商家。

    qualification.subject_id 是不建物理外键的多态列，所以"这个人/这台机/这个商家到底存不存在"
    必须在这里查一遍：不然台账里会躺着指向空气的资质，到期扫描还会给不存在的主体发信。
    第二个返回值在主体没有商家归属时（平台侧人员）为 None。
    """
    sql = {
        1: "SELECT id FROM merchants WHERE id=:i AND deleted=0",
        2: "SELECT merchant_id FROM users WHERE id=:i AND deleted=0",
        3: "SELECT merchant_id FROM aircraft WHERE id=:i AND deleted=0",
    }.get(stype)
    if sql is None:
        return False, None
    row = db.session.execute(db.text(sql), {"i": sid}).first()
    if row is None:
        return False, None
    return True, (sid if stype == 1 else row[0])


def _store_qual_file(f) -> str:
    """附件落盘，返回写进 file_url 的相对键（不是 URL，也不是绝对路径）。

    文件名用 uuid 重造：原名既可能撞车（两个人都叫 IMG_0001.JPG），也可能带路径分隔符；
    复审要看的是"这张证"，不是"这个文件名"。目录在 static 外面，没有直读通道，
    字节只能从下面的 /qualifications/<id>/file 出去。
    """
    ext = (f.filename.rsplit(".", 1)[-1] if "." in (f.filename or "") else "").lower()
    if ext not in QUAL_FILE_MIME:
        raise ApiError(BizCode.PARAM_ERROR, "证照原件只支持 PDF / JPG / PNG / WebP", 400)
    day = datetime.now().strftime("%Y%m")
    name = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(current_app.config["UPLOAD_ROOT"], "qualification", day)
    os.makedirs(folder, exist_ok=True)
    f.save(os.path.join(folder, name))
    return f"qualification/{day}/{name}"


def _reviewer_ids(submitter_id: int, owner_mid: int | None) -> list[int]:
    """该接着办这件新件的人：本商家的资质审核岗 + 平台侧审核岗。

    不能用 user_id=0 广播代替：广播在 /aux/messages 里按 user_id 精确匹配，谁都读不到。
    提交人自己也在名单里时要剔掉——审核岗自己登记还给自己发一条待办，纯属刷屏。
    """
    ids: list[int] = []
    if owner_mid:
        ids += scope.user_ids_with_perm(QUAL_PERM, owner_mid)
    ids += scope.user_ids_with_perm(QUAL_PERM, platform_only=True)
    return [i for i in dict.fromkeys(ids) if i != submitter_id]


def _create_qualification(stype: int, sid: int) -> Qualification:
    """资质新建的唯一落库口：校验 → 存原件 → 以 0 待审核插入 → 通知审核岗。

    两条入口（管理侧代登记、本人自助上传）共用它，所以不存在"谁建谁有效"这种绕过路径：
    这里写死 status=0，复审动作只有 /qualifications/<id>/audit 一个出口。
    """
    u = current_user()
    qtype = (request.form.get("qualType") or "").strip()
    qname = (request.form.get("qualName") or "").strip()
    if qtype not in QUAL_TYPES:
        raise ApiError(BizCode.PARAM_ERROR, f"资质类别需为 {' / '.join(QUAL_TYPES)}", 400)
    if not qname:
        raise ApiError(BizCode.PARAM_ERROR, "请填写资质名称", 400)
    want = QUAL_TYPE_SUBJECT[qtype]
    if want != stype:
        raise ApiError(BizCode.PARAM_ERROR,
                       f"「{QUAL_TYPES[qtype]}」是{SUBJECT_TYPE[want]}主体的资质，"
                       f"不能登记到{SUBJECT_TYPE[stype]}主体上", 400)

    exists, owner_mid = _subject_owner_merchant(stype, sid)
    if not exists:
        raise ApiError(BizCode.NOT_FOUND, f"{SUBJECT_TYPE.get(stype)}主体不存在（id={sid}）", 404)
    mid = scope.merchant_scope(u)
    if mid is not None and owner_mid != mid:
        raise ApiError(BizCode.FORBIDDEN, "只能为本商家的人员 / 飞行器 / 商家登记资质", 403)

    issue = _parse_qual_date(request.form.get("issueAt"), "签发日期")
    expire = _parse_qual_date(request.form.get("expireAt"), "到期日期")
    if not expire:
        raise ApiError(BizCode.PARAM_ERROR, "到期日期必填：资质到期预警按它扫描", 400)
    if issue and expire <= issue:
        raise ApiError(BizCode.PARAM_ERROR, "到期日期需晚于签发日期", 400)

    f = request.files.get("file")
    if f is None or not f.filename:
        raise ApiError(BizCode.PARAM_ERROR, "请上传证照原件：复审要看原件，不看一句话", 400)

    # 同主体同类别只留一件在办：重复提交会把台账堆成"一个人五本驾驶证"，
    # 到期预警还会按行重复发信。真要看新证，先把在办的那件驳掉，或让本人重新递。
    dup = db.session.execute(db.text(
        "SELECT id FROM qualification WHERE deleted=0 AND subject_type=:t AND subject_id=:s"
        " AND qual_type=:q AND status IN (0, 1) LIMIT 1"),
        {"t": stype, "s": sid, "q": qtype}).scalar()
    if dup:
        raise ApiError(BizCode.CONFLICT,
                       f"该主体已有「{QUAL_TYPES[qtype]}」处于待审核或有效状态（id={dup}），"
                       f"请勿重复提交", 409)

    q = Qualification(subject_type=stype, subject_id=sid, qual_type=qtype, qual_name=qname,
                      issue_at=issue, expire_at=expire, file_url=_store_qual_file(f), status=0)
    db.session.add(q)
    db.session.commit()
    notify.notify_many(
        _reviewer_ids(u.id, owner_mid), f"待复审资质：{q.qual_name}",
        f"{u.real_name or u.username} 登记了「{SUBJECT_TYPE[stype]}」主体的"
        f"{QUAL_TYPES[qtype]}，有效期至 {expire:%Y-%m-%d}。"
        f"请在 人员管理 · 资质台账 复核原件后再放行。",
        biz_type="qualification", biz_id=q.id)
    return q


@bp.post("/qualifications")
@perm_required(QUAL_PERM)
def create_qualification():
    """登记资质（管理侧）：multipart 表单 subjectType/subjectId/qualType/qualName/issueAt/expireAt
    加 file，新建一律落「0 待审核」。

    为什么要给审核岗自己开这条口：商家营业执照、飞行器适航证这类主体的"本人"并不存在
    （商家是个组织、飞行器是台机器），只能由平台或商家管理员代登记。
    代登记不等于复核：插进去的行同样停在待审核，放行只有 /audit 一个动作。
    """
    stype = _form_int("subjectType", 0)
    sid = _form_int("subjectId", 0)
    if stype not in SUBJECT_TYPE or not sid:
        raise ApiError(BizCode.PARAM_ERROR, "subjectType / subjectId 必填且需合法", 400)
    q = _create_qualification(stype, sid)
    return success({"id": q.id, "status": q.status, "statusName": QUAL_STATUS[q.status]},
                   "已登记，等待复审确认后生效")


@bp.post("/qualifications/mine")
@staff_required
def upload_my_qualification():
    """本人自助上传资质：主体写死 (2, 本人)，不需要任何 resource:* 权限点。

    和 /my-duty 是一对：预警信写"你的证过期了"，收信人就得有个能把新证递回去的地方，
    否则换证只能靠线下找人。这里不给台账权限（那是"看别人的证"），只做"交自己的证"这一件事；
    递上去停在待审核，复审仍只认 resource:qualification。
    客户账号（user_type 1/2）没有"人员资质"这个概念，staff_required 已经挡在外面。
    """
    q = _create_qualification(2, current_user().id)
    return success({"id": q.id, "status": q.status, "statusName": QUAL_STATUS[q.status]},
                   "已提交，等待资质审核岗复审")


@bp.get("/qualifications/<int:qid>/file")
@perm_any_required(*QUAL_READ_PERMS)
def qualification_file(qid):
    """证照原件（读）：放行名单与数据范围都和资质台账逐字一致。

    为什么不叫前端直读 file_url：那个字段存的是服务端生成的相对键，目录在 static 外面，
    本来就没有直读通道；而台账读侧已经放宽到"能打开人员管理页就看得到台账"，
    原件必须跟着同一份名单——否则运维列得出证却点不开证，等于只修了一半。
    """
    u = current_user()
    row = db.session.execute(db.text(
        "SELECT q.file_url,"
        "       CASE q.subject_type WHEN 1 THEN q.subject_id"
        "            WHEN 2 THEN pu.merchant_id ELSE ac.merchant_id END AS owner_merchant"
        "  FROM qualification q"
        "  LEFT JOIN users pu ON q.subject_type = 2 AND pu.id = q.subject_id"
        "  LEFT JOIN aircraft ac ON q.subject_type = 3 AND ac.id = q.subject_id"
        " WHERE q.id = :i AND q.deleted = 0"), {"i": qid}).mappings().first()
    if row is None:
        raise ApiError(BizCode.NOT_FOUND, "资质记录不存在", 404)
    mid = scope.merchant_scope(u)
    if mid is not None and row["owner_merchant"] != mid:
        raise ApiError(BizCode.FORBIDDEN, "只能查看本商家主体的资质原件", 403)
    key = row["file_url"] or ""
    return _send_qual_file(key)


def _send_qual_file(key: str):
    """按 file_url 的相对键把原件发出去。两条读原件的口（台账、本人）都收在这里。"""
    if not QUAL_FILE_KEY_RE.match(key):
        # 历史行（这批演示数据入库时还没有上传口子）file_url 全为空：不是错误，
        # 但也不能把任意字符串当路径往外发，所以只认服务端自己生成的那个形状。
        raise ApiError(BizCode.NOT_FOUND, "该资质没有存档的证照原件", 404)
    resp = send_from_directory(current_app.config["UPLOAD_ROOT"], key,
                               mimetype=QUAL_FILE_MIME[key.rsplit(".", 1)[-1]], max_age=0)
    # 白名单里没有 html/svg，再加一层 nosniff，兜住浏览器"自己猜类型"这条路
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.get("/qualifications/mine/<int:qid>/file")
@staff_required
def my_qualification_file(qid):
    """本人读自己那份证照原件：subject 条件写死在 WHERE 里，不是靠读完再判断。

    为什么不复用上一条端点：那道门认的是台账权限名单（qualification / system:user /
    schedule / aircraft），机长四条都没有——装饰器先拦，视图里的"本人"分支根本轮不到执行。
    自助上传的闭环必须有读的一侧，否则一个人连自己交上去的证都看不到。
    越界读别人的原件在这里表现为 404（查不到行），不是 403：不用告诉试探者"这条 id 存在"。
    """
    row = db.session.execute(db.text(
        "SELECT file_url FROM qualification"
        " WHERE id = :i AND deleted = 0 AND subject_type = 2 AND subject_id = :u"),
        {"i": qid, "u": current_user().id}).mappings().first()
    if row is None:
        raise ApiError(BizCode.NOT_FOUND, "没有这条本人资质记录", 404)
    return _send_qual_file(row["file_url"] or "")


DUTY_STATUS = {1: "计划", 2: "已出勤", 3: "请假", 4: "缺勤"}


def _duty_date(name: str, default):
    v = (request.args.get(name) or "").strip()
    if not v:
        return default
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ApiError(BizCode.PARAM_ERROR, f"{name} 需为 YYYY-MM-DD", 400)


@bp.get("/my-duty")
@staff_required
def my_duty():
    """我的资质与值班（个人中心自助读，不需要任何 resource:* 权限点）。

    资质到期预警是按"主体=本人"发站内信的（scheduler._qual_recipients），但机长手里
    只有 dispatch:execute + monitor:realtime，收信人没有一个打得开的台账界面——
    信里写"你的证 N 天后到期"却无处可查，等于把这些人全推给运维问。
    这里给一条只读自己的口：数据范围写死 subject_id=本人、user_id=本人，
    不放宽任何台账权限，也不给别人的名字（在岗只给计数）。
    """
    u = current_user()
    warn_days = scope.sys_cfg_num("staff_qual_warn_days", 15, int)
    d_from = _duty_date("from", date.today())
    d_to = _duty_date("to", d_from + timedelta(days=6))
    if d_to < d_from:
        raise ApiError(BizCode.PARAM_ERROR, "结束日期不能早于开始日期", 400)

    quals = []
    for r in db.session.execute(db.text(
            "SELECT id, qual_type, qual_name, file_url, issue_at, expire_at, status, audit_opinion"
            " FROM qualification WHERE deleted = 0 AND subject_type = 2 AND subject_id = :uid"
            " ORDER BY expire_at IS NULL, expire_at ASC"), {"uid": u.id}).mappings().all():
        exp = r["expire_at"].date() if r["expire_at"] else None
        left = (exp - date.today()).days if exp else None
        quals.append({
            "id": r["id"], "qualType": r["qual_type"], "qualName": r["qual_name"],
            "fileUrl": r["file_url"],
            "issueAt": r["issue_at"].strftime("%Y-%m-%d") if r["issue_at"] else None,
            "expireAt": exp.strftime("%Y-%m-%d") if exp else None,
            "status": r["status"], "statusName": QUAL_STATUS.get(r["status"]),
            "daysLeft": left,
            "warn": bool(left is not None and 0 <= left <= warn_days),
            "auditOpinion": r["audit_opinion"],
        })

    shifts = []
    for r in db.session.execute(db.text(
            "SELECT id, shift_date, shift_type, start_time, end_time, responsible_area, status"
            " FROM staff_schedule WHERE deleted = 0 AND user_id = :uid"
            " AND shift_date BETWEEN :df AND :dt"
            " ORDER BY shift_date ASC, id ASC"),
            {"uid": u.id, "df": d_from, "dt": d_to}).mappings().all():
        shifts.append({
            "id": r["id"], "shiftDate": str(r["shift_date"]),
            "shiftType": r["shift_type"], "shiftName": SHIFT_NAME.get(r["shift_type"]),
            "startTime": _dt(r["start_time"]), "endTime": _dt(r["end_time"]),
            "area": r["responsible_area"],
            "status": r["status"], "statusName": DUTY_STATUS.get(r["status"]),
        })

    # 在岗看板：只出计数。"今天谁在值班"对机长是排班情况，对平台是隐私，给人数就够用了
    board = {r["shift_type"]: int(r["c"]) for r in db.session.execute(db.text(
        "SELECT shift_type, COUNT(DISTINCT user_id) AS c FROM staff_schedule"
        " WHERE deleted = 0 AND shift_date = CURDATE() GROUP BY shift_type")).mappings().all()}
    today_total = db.session.execute(db.text(
        "SELECT COUNT(DISTINCT user_id) FROM staff_schedule WHERE deleted = 0 AND shift_date = CURDATE()"
    )).scalar() or 0
    # 不跟着查询范围走：查下个月的班表时，"我今天上不上班"也得照常回答
    my_today = db.session.execute(db.text(
        "SELECT COUNT(*) FROM staff_schedule WHERE deleted = 0 AND user_id = :uid"
        " AND shift_date = CURDATE()"), {"uid": u.id}).scalar()

    return success({
        "qualifications": quals,
        "shifts": shifts,
        "range": {"from": d_from.strftime("%Y-%m-%d"), "to": d_to.strftime("%Y-%m-%d")},
        "dutyBoard": [{"shiftType": s, "shiftName": SHIFT_NAME[s], "count": board.get(s, 0)}
                      for s in (1, 2, 3, 4)],
        "todayOnDuty": bool(my_today),
        "todayOnDutyTotal": int(today_total),
        "qualWarnDays": warn_days,
    })


@bp.post("/vertiports")
@perm_required("resource:vertiport")
def create_vertiport():
    _platform_only()
    d = request.get_json() or {}
    if not d.get("stationName") or d.get("lng") is None or d.get("lat") is None:
        abort(400, description="场站名称与经纬度必填")
    v = Vertiport(
        station_no=d.get("stationNo") or f"VP-WZ-{Vertiport.query.count() + 1:03d}",
        station_name=d["stationName"],
        station_type=d.get("stationType", 1),
        lng=d["lng"], lat=d["lat"], address=d.get("address"),
        capacity=d.get("capacity", 4),
        charger_total=d.get("chargerTotal", 2),
        charger_available=d.get("chargerAvailable", 2),
        status=d.get("status", 1),
    )
    db.session.add(v); db.session.commit()
    return success({"id": v.id})


@bp.put("/vertiports/<int:vid>")
@perm_required("resource:vertiport")
def update_vertiport(vid):
    _platform_only()
    v = Vertiport.query.get_or_404(vid)
    d = request.get_json() or {}
    if _apply(v, d, VP_FIELDS) == 0:
        abort(400, description="没有需要更新的字段")
    # 前端只填充电桩总数：可用数跟着总数收口，别留下"可用 5 / 总数 2"这种脏值
    if v.charger_total is not None:
        v.charger_available = min(int(v.charger_available or 0), int(v.charger_total))
    db.session.commit()
    return success({"id": v.id})


@bp.delete("/vertiports/<int:vid>")
@perm_required("resource:vertiport")
def del_vertiport(vid):
    _platform_only()
    v = Vertiport.query.get_or_404(vid)
    used = db.session.query(Aircraft).filter(
        Aircraft.current_vertiport_id == vid, Aircraft.deleted == 0).count()
    if used:
        abort(409, description=f"该场站仍有 {used} 架飞行器停放，不能删除")
    v.deleted = 1
    db.session.commit()
    return success({"id": v.id})


@bp.get("/merchants")
@perm_required("resource:aircraft")
def list_merchants():
    """「所属商家」下拉：飞行器登记必须落到某个商家（aircraft.merchant_id 非空）。"""
    _platform_only()
    rows = db.session.execute(db.text(
        "SELECT id, merchant_name FROM merchants WHERE deleted = 0 ORDER BY id")).mappings().all()
    return success([{"id": r["id"], "name": r["merchant_name"]} for r in rows])


@bp.post("/aircraft")
@perm_required("resource:aircraft")
def create_aircraft():
    """新增飞行器：平台侧代商家登记（merchantId 必填或落 1），商家侧走自己的账号。"""
    _platform_only()
    d = request.get_json() or {}
    vid = d.get("vertiportId", 1)
    if not db.session.get(Vertiport, vid):
        abort(400, description="停放场站不存在")
    mid = int(d.get("merchantId") or 1)
    if not db.session.execute(db.text(
            "SELECT id FROM merchants WHERE id=:i AND deleted=0"), {"i": mid}).scalar():
        abort(400, description="所属商家不存在")
    a = Aircraft(
        aircraft_no=d.get("aircraftNo") or f"TS-UAV-{Aircraft.query.count() + 1:03d}",
        merchant_id=mid,
        brand=d.get("brand", "大疆"), model=d.get("model", "FlyCart 30"),
        endurance_min=d.get("enduranceMin", 30),
        max_load_kg=d.get("maxLoadKg", 20),
        max_speed=d.get("maxSpeed", 15),
        max_altitude=d.get("maxAltitude", 200),
        battery=100, online=1, health_score=100, status=1,
        current_vertiport_id=d.get("vertiportId", 1),
    )
    db.session.add(a); db.session.commit()
    return success({"id": a.id})


@bp.put("/aircraft/<int:aid>")
@perm_required("resource:aircraft")
def update_aircraft(aid):
    """飞行器台账：平台侧可改全部参数；商家运维只能改自有机状态（1闲置/3充换电/4维保/5故障）。"""
    u = current_user()
    a = Aircraft.query.get_or_404(aid)
    mid = scope.merchant_scope(u)
    if mid is not None and a.merchant_id != mid:
        abort(403, description="只能维护本商家的飞行器")
    d = request.get_json() or {}
    if "status" in d and int(d["status"]) not in AC_STATUS:
        abort(400, description="未知的飞行器状态")
    if a.status == 2 and not scope.is_platform(u) \
            and "status" in d and int(d["status"]) != 2:
        abort(409, description="该飞行器正在执行任务，请先在调度台结束/取消任务再改状态")
    mapping = AC_FIELDS if scope.is_platform(u) else {"status": "status"}
    if _apply(a, d, mapping) == 0:
        abort(403, description="没有可修改的字段（商家侧仅允许更新状态）")
    db.session.commit()
    return success({"id": a.id})


@bp.post("/aircraft/charge-all")
@perm_required("resource:aircraft")
def charge_all():
    """一键充换电（演示/演练用）：全场飞行器回 100% 并置为闲置。"""
    _platform_only()
    if db.session.query(Aircraft).filter(Aircraft.status == 2).count():
        abort(409, description="有飞行器正在执行任务，不能全量换电")
    n = Aircraft.query.filter(Aircraft.deleted == 0).update(
        {"battery": 100, "status": 1}, synchronize_session=False)
    db.session.commit()
    return success({"count": n})


# ============================ 场站预约（M-55） ============================

RSV_PURPOSE = {1: "起降", 2: "换电", 3: "充电"}
RSV_STATUS = {1: "待审", 2: "已通过", 3: "已驳回", 4: "已完成", 5: "已取消"}
# 待审也占位：两个商家各自排队申请时都只看"已通过"会双双通过，一起放下来就超容
RSV_HOLDING = (1, 2)


def _rsv_no() -> str:
    return "SR" + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 9999):04d}"


def _slot_used(station_id, purpose, start_at, end_at, exclude_id=None) -> int:
    """同一时段该场站同类机位已被占用的数量（起降看泊位，换电/充电看充电桩）。"""
    sql = ("SELECT COUNT(*) FROM station_reservation WHERE station_id=:s"
           " AND deleted=0 AND status IN (1,2)"
           " AND ((:p = 1 AND purpose = 1) OR (:p <> 1 AND purpose IN (2,3)))"
           " AND start_at < :e AND end_at > :st")
    params = {"s": station_id, "p": purpose, "st": start_at, "e": end_at}
    if exclude_id:
        sql += " AND id <> :x"
        params["x"] = exclude_id
    return int(db.session.execute(db.text(sql), params).scalar() or 0)


def _slot_capacity(station, purpose) -> int:
    return (station.capacity if purpose == 1 else station.charger_total) or 0


def _check_slot(station, purpose, start_at, end_at, exclude_id=None):
    """机位余量校验：申请和审核放行走同一个函数，两边口径不能长歪。

    exclude_id 只给审核用——待审单自己也是"占位"状态，放行时不能把自己算成对手。
    """
    used = _slot_used(station.id, purpose, start_at, end_at, exclude_id)
    cap = _slot_capacity(station, purpose)
    if used >= cap:
        kind = "泊位" if purpose == 1 else "充电桩"
        abort(409, description=(
            f"{station.station_name}在 {start_at:%m-%d %H:%M}~{end_at:%H:%M} 时段"
            f"的{kind}已被占满（{used}/{cap}），请改时段或换场站"))


@bp.get("/reservations")
@perm_any_required("resource:reservation", "resource:vertiport", "dispatch:plan")
def list_reservations():
    """场站预约台账：商家看本商家，平台侧看全部；可按场站/状态/日期筛。"""
    u = current_user()
    cond, params = ["r.deleted=0"], {}
    # 列名必须带 r. 前缀：vertiport 也有 merchant_id，JOIN 之后裸列名 MySQL 报 ambiguous
    frag, p = scope.merchant_filter(u, "r.merchant_id")
    if frag:
        cond.append(frag.strip().removeprefix("AND "))
        params.update(p)
    if request.args.get("stationId"):
        cond.append("r.station_id=:sid")
        params["sid"] = int(request.args["stationId"])
    if request.args.get("status"):
        cond.append("r.status=:st")
        params["st"] = int(request.args["status"])
    if request.args.get("date"):
        cond.append("DATE(r.start_at)=:d")
        params["d"] = request.args["date"]
    rows = db.session.execute(db.text(f"""
        SELECT r.id, r.reservation_no, r.station_id, v.station_name, r.merchant_id,
               m.merchant_name, r.user_id, us.real_name AS user_name,
               r.aircraft_id, a.aircraft_no, r.purpose, r.start_at, r.end_at,
               r.status, r.audit_opinion, r.created_at
        FROM station_reservation r
        LEFT JOIN vertiport v ON v.id = r.station_id
        LEFT JOIN merchants m ON m.id = r.merchant_id
        LEFT JOIN users us ON us.id = r.user_id
        LEFT JOIN aircraft a ON a.id = r.aircraft_id
        WHERE {' AND '.join(cond)}
        ORDER BY r.start_at DESC, r.id DESC LIMIT 300"""), params).mappings().all()
    data = [{
        "id": x["id"], "reservationNo": x["reservation_no"],
        "stationId": x["station_id"], "stationName": x["station_name"],
        "merchantId": x["merchant_id"], "merchantName": x["merchant_name"],
        "userId": x["user_id"], "userName": x["user_name"],
        "aircraftId": x["aircraft_id"], "aircraftNo": x["aircraft_no"],
        "purpose": x["purpose"], "purposeName": RSV_PURPOSE.get(x["purpose"]),
        "startAt": _dt(x["start_at"]), "endAt": _dt(x["end_at"]),
        "status": x["status"], "statusName": RSV_STATUS.get(x["status"]),
        "auditOpinion": x["audit_opinion"], "createdAt": _dt(x["created_at"]),
    } for x in rows]
    return success({"list": data, "total": len(data)})


@bp.post("/reservations")
@perm_required("resource:reservation")
def create_reservation():
    """申请场站机位：{"stationId","purpose","startAt","endAt","aircraftId"}。

    商家只能为自己的账号申请（merchant_id 取自登录态，不信前端传的）；
    平台侧账号可以代商家提，必须显式给 merchantId。
    """
    u = current_user()
    d = request.get_json() or {}
    station = db.session.get(Vertiport, int(d.get("stationId") or 0))
    if station is None or station.deleted:
        abort(404, description="场站不存在")
    if station.status != 1:
        abort(409, description=f"{station.station_name}当前为「{VP_STATUS.get(station.status)}」，不接受预约")
    purpose = int(d.get("purpose") or 0)
    if purpose not in RSV_PURPOSE:
        abort(400, description="预约用途需为 1起降 / 2换电 / 3充电")
    start_at = order_service._parse_dt(d.get("startAt"))
    end_at = order_service._parse_dt(d.get("endAt"))
    if not start_at or not end_at:
        abort(400, description="请填写预约时段")
    if end_at <= start_at:
        abort(400, description="结束时间需晚于开始时间")
    if start_at < datetime.now().replace(microsecond=0):
        abort(400, description="不能预约已经开始的时段")
    if (end_at - start_at).total_seconds() > 8 * 3600:
        abort(400, description="单次预约时段不超过 8 小时")

    mid = scope.merchant_scope(u)
    if mid is None:
        mid = int(d.get("merchantId") or 0) or u.merchant_id or None
        if not mid:
            abort(400, description="平台侧代商家预约需指定 merchantId")
    ac_id = int(d["aircraftId"]) if d.get("aircraftId") else None
    if ac_id:
        ac = db.session.get(Aircraft, ac_id)
        # 列表按 aircraft_no 展示，随手填个别家机号就等于把对方的台账编号抄进自己的记录
        if ac is None or ac.deleted:
            abort(404, description="飞行器不存在")
        if ac.merchant_id != mid:
            abort(403, description="只能预约本商家的飞行器")
    # 先判机位再落库：反过来写的话，撞满时那条占位行已经 flush 进当前事务，
    # abort 只负责返回响应、不负责回滚，报错就成了"提示已拒、机位却真被占了"。
    _check_slot(station, purpose, start_at, end_at)
    r = StationReservation(
        reservation_no=_rsv_no(), station_id=station.id, merchant_id=mid,
        user_id=u.id, aircraft_id=ac_id, purpose=purpose,
        start_at=start_at, end_at=end_at, status=1)
    db.session.add(r)
    db.session.commit()
    return success({"id": r.id, "reservationNo": r.reservation_no}, "预约已提交，等待审核")


@bp.post("/reservations/<int:rid>/audit")
@perm_required("resource:reservation:audit")
def audit_reservation(rid):
    """场站预约审核：{"result": 2 通过 | 3 驳回, "opinion": "..."}。

    放行时必须再核一次机位：申请时排队的多条预约可能同时把场站约满。
    """
    u = current_user()
    r = db.session.get(StationReservation, rid)
    if r is None or r.deleted:
        abort(404, description="预约记录不存在")
    if r.status != 1:
        abort(409, description=f"该预约已是「{RSV_STATUS.get(r.status)}」，不能重复审核")
    d = request.get_json() or {}
    result = int(d.get("result") or 0)
    if result not in (2, 3):
        abort(400, description="result 需为 2（通过）或 3（驳回）")
    opinion = (d.get("opinion") or "").strip()
    if result == 3 and not opinion:
        abort(400, description="驳回必须写明原因")
    if result == 2:
        station = db.session.get(Vertiport, r.station_id)
        if station is None or station.deleted:
            abort(409, description="场站已下架，无法放行，请驳回并让商家改约其他场站")
        _check_slot(station, r.purpose, r.start_at, r.end_at, exclude_id=r.id)
    r.status = result
    r.audit_opinion = opinion[:255] or None
    db.session.commit()
    notify.notify(r.user_id, f"场站预约{'已通过' if result == 2 else '被驳回'}：{r.reservation_no}",
                  opinion or "请按预约时段到场执行。",
                  biz_type="reservation", biz_id=r.id)
    return success({"id": r.id}, "预约已通过" if result == 2 else "预约已驳回")


@bp.post("/reservations/<int:rid>/cancel")
@perm_required("resource:reservation")
def cancel_reservation(rid):
    """取消预约：申请方只能取消本商家的，平台侧不限；已开始/已终态的不给撤。"""
    u = current_user()
    r = db.session.get(StationReservation, rid)
    if r is None or r.deleted:
        abort(404, description="预约记录不存在")
    mid = scope.merchant_scope(u)
    if mid is not None and r.merchant_id != mid:
        abort(403, description="只能取消本商家的场站预约")
    if r.status not in (1, 2):
        abort(409, description=f"该预约已是「{RSV_STATUS.get(r.status)}」，不能取消")
    if r.status == 2 and r.start_at <= datetime.now():
        abort(409, description="预约时段已开始，请先完成或改期，不能直接取消")
    r.status = 5
    db.session.commit()
    return success({"id": r.id}, "预约已取消")


@bp.post("/reservations/<int:rid>/finish")
@perm_required("resource:reservation")
def finish_reservation(rid):
    """回执完成：释放机位占用，让台账里的"已通过"有终点。"""
    u = current_user()
    r = db.session.get(StationReservation, rid)
    if r is None or r.deleted:
        abort(404, description="预约记录不存在")
    mid = scope.merchant_scope(u)
    if mid is not None and r.merchant_id != mid:
        abort(403, description="只能确认本商家的场站预约")
    if r.status != 2:
        abort(409, description="只有已通过的预约能确认完成")
    if r.end_at > datetime.now():
        abort(409, description="预约时段还没结束，不能提前确认完成")
    r.status = 4
    db.session.commit()
    return success({"id": r.id}, "预约已完成")
