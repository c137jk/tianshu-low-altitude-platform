"""D5 空域蓝图（需求 4.2.4 空域合规运营：一张图 + 台账审核 + 电子围栏）。

读：/api/airspaces 仅需登录（大屏、调度台合规图层都要用，非敏感数据），给的是"生效且已过审"那批；
    台账视角 all=1 另需 airspace:draw 或 airspace:audit 任一，见 list_airspaces。
写：空域与围栏的绘制/改范围需 airspace:draw，审核与下架需 airspace:audit。
    绘制与审核故意分成两个动作点——自绘空域落库即"待审核"，
    只有审核通过（audit_status=1）才参与合规判定，见 dispatch_service.active_airspaces。
    删除（下架）也归 audit：撤区和批区是同一级裁量，不能让画完的人自己把区撤走。
"""
from datetime import datetime
import random

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.common import scope
from app.common.decorators import current_user, perm_required
from app.common.errors import ApiError, BizCode
from app.common.response import success
from app.extensions import db
from app.models import Airspace, FlightPlan, Geofence, Merchant, User
from app.models.airspace import AIRSPACE_SOURCE_NAME as SOURCE_NAME
from app.models.airspace import AIRSPACE_TYPE_NAME as TYPE_NAME
from app.models.airspace import AUDIT_STATUS_NAME as AUDIT_NAME
from app.services import notify
from app.services.geo import point_in_polygon, polygon_rings

bp = Blueprint("airspace", __name__, url_prefix="/api")

FENCE_TYPE_NAME = {1: "限入", 2: "限出", 3: "接近告警"}


def _gen_no(prefix: str) -> str:
    """同一秒内批量绘制会撞 airspace_no 唯一键，随机位取到 6 位（VARCHAR(32) 放得下）。"""
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999999):06d}"


def _validate_polygon(geo) -> dict:
    if not isinstance(geo, dict) or geo.get("type") != "Polygon":
        raise ApiError(BizCode.PARAM_ERROR, "geometry 必须是 GeoJSON Polygon")
    rings = geo.get("coordinates") or []
    if not rings or len(rings[0]) < 3:
        raise ApiError(BizCode.PARAM_ERROR, "多边形至少需要 3 个顶点")
    return geo


def _fmt(a: Airspace, auditor: str | None = None) -> dict:
    return {
        "id": a.id, "airspaceNo": a.airspace_no, "airspaceName": a.airspace_name,
        "airspaceType": a.airspace_type, "typeName": TYPE_NAME.get(a.airspace_type),
        "source": a.source, "sourceName": SOURCE_NAME.get(a.source),
        "geometry": a.geometry_json,
        "altitudeMin": a.altitude_min, "altitudeMax": a.altitude_max,
        "regionName": a.region_name,
        "status": a.status, "statusName": "生效" if a.status == 1 else "失效",
        "auditStatus": a.audit_status, "auditName": AUDIT_NAME.get(a.audit_status),
        "auditedBy": auditor, "auditedAt": a.audited_at.strftime("%Y-%m-%d %H:%M") if a.audited_at else None,
        "auditOpinion": a.audit_opinion,
        "controlStartAt": a.control_start_at.strftime("%Y-%m-%d %H:%M") if a.control_start_at else None,
        "controlEndAt": a.control_end_at.strftime("%Y-%m-%d %H:%M") if a.control_end_at else None,
        "controlReason": a.control_reason,
        "createdAt": a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else None,
    }


@bp.get("/airspaces")
@jwt_required()
def list_airspaces():
    """空域列表。默认只给生效且已审核通过的（地图渲染）；all=1 返回台账全量，需绘制或审核权限。

    type 按类型过滤；keyword 命中名称/编号/区域。
    """
    query = db.session.query(Airspace).filter(Airspace.deleted == 0)
    # all=1 是台账视角：待审核/被驳回的空域连几何一起给，还带审核人和审核意见。
    # 前端用到它的两张页（一张图、台账与审核）本来就要求 airspace:draw 或 airspace:audit 才进得来，
    # 这个条件不能只写在路由守卫里——把参数拼一下就能绕过。没有这两个点的人退回已公开那批。
    me = current_user()
    if request.args.get("all") != "1" or not (me.has_perm("airspace:draw") or me.has_perm("airspace:audit")):
        query = query.filter(Airspace.status == 1, Airspace.audit_status == 1)
    atype = request.args.get("type")
    if atype:
        query = query.filter(Airspace.airspace_type == int(atype))
    keyword = (request.args.get("keyword") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(db.or_(Airspace.airspace_name.like(like),
                                    Airspace.airspace_no.like(like),
                                    Airspace.region_name.like(like)))
    rows = query.order_by(Airspace.airspace_type.asc(), Airspace.id.desc()).all()
    # 审核人只查这批空域真引用到的那几个 id：整表 query(User).all() 让地图每次渲染都拖一遍 users。
    ids = {a.audited_by for a in rows if a.audited_by}
    auditors = {} if not ids else {u.id: (u.real_name or u.username)
                                   for u in db.session.query(User).filter(User.id.in_(ids)).all()}
    out = [_fmt(a, auditors.get(a.audited_by)) for a in rows]
    return success({"list": out, "total": len(out)})


@bp.post("/airspaces")
@perm_required("airspace:draw")
def create_airspace():
    """绘制新空域：落库即「待审核」，审核通过才参与合规判定。"""
    d = request.get_json(silent=True) or {}
    name = (d.get("airspaceName") or "").strip()
    if not name:
        raise ApiError(BizCode.PARAM_ERROR, "airspaceName 必填")
    atype = int(d.get("airspaceType") or 0)
    if atype not in TYPE_NAME:
        raise ApiError(BizCode.PARAM_ERROR, "airspaceType 需为 1禁飞 2限飞 3可飞 4临时管制")
    geo = _validate_polygon(d.get("geometry"))
    if atype == 4 and not (d.get("controlStartAt") and d.get("controlEndAt")):
        raise ApiError(BizCode.PARAM_ERROR, "临时管制必须给管制起止时间")
    a = Airspace(
        airspace_no=d.get("airspaceNo") or _gen_no("AS-SYS-"),
        airspace_name=name[:80], airspace_type=atype, source=2,
        geometry_json=geo,
        altitude_min=_int_or_none(d.get("altitudeMin")), altitude_max=_int_or_none(d.get("altitudeMax")),
        region_name=(d.get("regionName") or "")[:80] or None,
        control_reason=(d.get("controlReason") or "")[:255] or None,
        control_start_at=_dt(d.get("controlStartAt")), control_end_at=_dt(d.get("controlEndAt")),
        status=1, audit_status=0,
    )
    db.session.add(a)
    db.session.commit()
    operator = current_user()
    notify.notify_many(
        scope.user_ids_with_perm("airspace:audit"), "空域待审核",
        f"{a.airspace_name}（{TYPE_NAME[atype]}）由 "
        f"{operator.real_name or operator.username} 绘制提交，请到「空域台账与审核」处理。",
        biz_type="airspace_audit", biz_id=a.id)
    return success({"id": a.id, "airspaceNo": a.airspace_no}, "空域已绘制，等待审核生效")


def _int_or_none(raw):
    """限高/限低允许 0（禁飞区就是 0~0），只挡空串和非数字。"""
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        raise ApiError(BizCode.PARAM_ERROR, f"高度需为数字：{raw}")


def _dt(raw):
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw)[:19], fmt)
        except ValueError:
            continue
    raise ApiError(BizCode.PARAM_ERROR, f"时间格式不支持：{raw}")


def _edge_touched(a: Airspace, d: dict) -> bool:
    """这份请求会不会改动"判定边界"：几何 / 类型 / 管制时段。

    比的是新旧值，不是请求里有没有带这个字段——前端编辑表单每次都会把 geometry 原样回填提交。
    """
    if d.get("geometry") is not None and _validate_polygon(d["geometry"]) != a.geometry_json:
        return True
    if d.get("airspaceType") is not None and int(d["airspaceType"]) != a.airspace_type:
        return True
    return any(key in d and _dt(d[key]) != getattr(a, col)
               for key, col in (("controlStartAt", "control_start_at"),
                                ("controlEndAt", "control_end_at")))


@bp.put("/airspaces/<int:aid>")
@perm_required("airspace:draw")
def update_airspace(aid):
    """修改空域范围/限高/管制时段。几何、类型或管制时段真被改动才回到「待审核」——
    改禁飞区边界等于改判定口径，必须留一次审核痕迹。

    比的是"新旧值是否不同"，不是"请求里有没有带这个字段"：前端编辑表单每次都会把
    geometry 原样回填提交，只改个名字不该把已生效的空域打成待审核（那会立刻让它
    从合规判定里消失，等于误撤区）。

    空管同步来源（source=1）另说：那是属地下发的台账，平台只做起停与限高维护，
    动边界一律挡回去。原先只是"改了也不退回审核"，等于让有绘制权的人原地改写机场禁飞区
    还继续生效、不留任何审核痕迹——和 delete 那条"同步来源不能删"的闸门也不一致。
    """
    a = db.session.get(Airspace, aid)
    if a is None or a.deleted:
        raise ApiError(BizCode.NOT_FOUND, "空域不存在")
    d = request.get_json(silent=True) or {}
    if a.source == 1 and _edge_touched(a, d):
        raise ApiError(BizCode.CONFLICT,
                       "空管同步来源的空域只能改启停与限高，边界/类型/管制时段以属地下一次同步为准", 409)
    touched_geometry = False
    if d.get("geometry") is not None:
        geo = _validate_polygon(d["geometry"])
        if geo != a.geometry_json:
            a.geometry_json = geo
            touched_geometry = True
    if d.get("airspaceType") is not None and int(d["airspaceType"]) != a.airspace_type:
        a.airspace_type = int(d["airspaceType"])
        touched_geometry = True
    if d.get("altitudeMax") is not None:
        amax = float(d["altitudeMax"])
        if not 0 <= amax <= 3000:
            raise ApiError(BizCode.PARAM_ERROR, "限高需在 0~3000m 之间")
        a.altitude_max = amax
    if d.get("altitudeMin") is not None:
        a.altitude_min = float(d["altitudeMin"])
    for key, col in (("airspaceName", "airspace_name"), ("regionName", "region_name"),
                     ("controlReason", "control_reason")):
        if d.get(key) is not None:
            setattr(a, col, str(d[key])[:255])
    for key, col in (("controlStartAt", "control_start_at"), ("controlEndAt", "control_end_at")):
        if key in d:
            new_dt = _dt(d[key])
            if new_dt != getattr(a, col):
                setattr(a, col, new_dt)
                touched_geometry = True
    if not touched_geometry and d.get("status") in (1, 2):
        a.status = int(d["status"])
    if touched_geometry:      # 走到这里必然是平台自绘：同步来源在入口就挡掉了
        a.audit_status = 0
        a.status = 1
    db.session.commit()
    return success({"id": a.id, "auditStatus": a.audit_status},
                   "已提交待审核" if a.audit_status == 0 else "空域已更新")


@bp.post("/airspaces/<int:aid>/audit")
@perm_required("airspace:audit")
def audit_airspace(aid):
    """空域审核：{"result": 1 通过 | 2 驳回, "opinion": "意见/驳回原因"}"""
    u = current_user()
    a = db.session.get(Airspace, aid)
    if a is None or a.deleted:
        raise ApiError(BizCode.NOT_FOUND, "空域不存在")
    d = request.get_json(silent=True) or {}
    result = int(d.get("result") or 0)
    if result not in (1, 2):
        raise ApiError(BizCode.PARAM_ERROR, "result 需为 1（通过）或 2（驳回）")
    opinion = (d.get("opinion") or "").strip()
    if result == 2 and not opinion:
        raise ApiError(BizCode.PARAM_ERROR, "驳回必须写明原因")
    a.audit_status = result
    a.audited_by = u.id
    a.audited_at = datetime.now()
    a.audit_opinion = opinion[:500] or None
    a.status = 1 if result == 1 else 2
    db.session.commit()
    # airspace 表没有"提交人"列，只能把审核结果推给全体有绘制权的人（空管/平台管理员）
    notify.notify_many(scope.user_ids_with_perm("airspace:draw"),
                       f"空域审核{'通过' if result == 1 else '驳回'}：{a.airspace_name}",
                       f"{a.airspace_no}｜{opinion or '同意生效，已纳入合规判定'}",
                       biz_type="airspace_audit", biz_id=a.id)
    return success({"id": a.id}, "审核通过，空域已纳入合规判定" if result == 1 else "已驳回，空域置为失效")


@bp.delete("/airspaces/<int:aid>")
@perm_required("airspace:audit")
def delete_airspace(aid):
    """空域下架（逻辑删除）。要 airspace:audit 而不是 draw：删除等于把判定依据整体撤掉，
    和审核通过一样是"改口径"的动作，必须留给同一批有裁量权的人。

    两道闸门：
      · 空管同步来源（source=1）不给删——那是属地空管的台账，删了下次同步原样回来，
        要挡飞行就在台账里「停用」；
      · 有在飞计划的航线正落在这个范围里不给删——空中撤区等于把它的合规判定抽掉，
        落地后再下架。
    """
    a = db.session.get(Airspace, aid)
    if a is None or a.deleted:
        raise ApiError(BizCode.NOT_FOUND, "空域不存在")
    if a.source == 1:
        raise ApiError(BizCode.CONFLICT,
                       "空管同步来源的空域不能删除，请改为停用（删除后下一次同步仍会下发）", 409)
    rings = polygon_rings(a.geometry_json or {})
    busy = []
    if rings:
        flying = (db.session.query(FlightPlan)
                  .filter(FlightPlan.status == 3, FlightPlan.deleted == 0).all())
        for p in flying:
            pts = [(float(pt["lng"]), float(pt["lat"])) for pt in (p.route_json or [])
                   if isinstance(pt, dict) and pt.get("lng") is not None]
            if any(point_in_polygon(lng, lat, ring) for lng, lat in pts for ring in rings):
                busy.append(p.plan_no or f"#{p.id}")
    if busy:
        raise ApiError(BizCode.CONFLICT,
                       f"该空域内仍有 {len(busy)} 条在飞任务（{'、'.join(busy[:3])}），"
                       f"落地后再下架，或先按「停用」观察", 409)
    a.deleted = 1
    a.status = 2
    db.session.commit()
    notify.notify_many(scope.user_ids_with_perm("airspace:draw"),
                       f"空域已下架：{a.airspace_name}",
                       f"{a.airspace_no}｜由空管侧删除，不再参与航线合规判定与飞行计划自动校验。",
                       biz_type="airspace_change", biz_id=a.id)
    return success({"id": a.id}, "空域已下架，不再参与合规判定")


@bp.post("/airspaces/sync")
@perm_required("airspace:draw")
def sync_airspaces():
    """空管数据同步（Mock 通道，需求 4.2.4-1）：
    把管制时段已结束的「临时管制」自动置失效，并回报当前各来源空域数量。"""
    now = datetime.now()
    expired = (db.session.query(Airspace)
               .filter(Airspace.status == 1, Airspace.deleted == 0,
                       Airspace.airspace_type == 4,
                       Airspace.control_end_at.isnot(None),
                       Airspace.control_end_at < now).all())
    for a in expired:
        a.status = 2
        a.audit_opinion = f"管制时段已于 {a.control_end_at:%Y-%m-%d %H:%M} 结束，同步置失效"
    db.session.commit()
    rows = db.session.execute(db.text(
        "SELECT source, status, COUNT(*) c FROM airspace WHERE deleted=0 "
        "GROUP BY source, status")).all()
    return success({
        "syncedAt": f"{now:%Y-%m-%d %H:%M:%S}",
        "expired": [a.airspace_name for a in expired],
        "stat": [{"source": SOURCE_NAME.get(r[0]), "status": "生效" if r[1] == 1 else "失效",
                  "count": r[2]} for r in rows],
    }, f"同步完成，{len(expired)} 个过期临时管制已失效")


# ---------------------------------------------------------------- 电子围栏
def _fence_fmt(g: Geofence, merchant_name: str | None) -> dict:
    return {
        "id": g.id, "fenceName": g.fence_name, "merchantId": g.merchant_id,
        "merchantName": merchant_name or "平台（全部商家）",
        "geometry": g.geometry_json, "fenceType": g.fence_type,
        "typeName": FENCE_TYPE_NAME.get(g.fence_type),
        "altitudeLimit": g.altitude_limit, "warnDistanceM": g.warn_distance_m,
        "status": g.status, "statusName": "启用" if g.status == 1 else "停用",
        "createdAt": g.created_at.strftime("%Y-%m-%d %H:%M") if g.created_at else None,
    }


def _merchant_names() -> dict:
    rows = db.session.execute(db.text("SELECT id, merchant_name FROM merchants")).all()
    return {r[0]: r[1] for r in rows}


@bp.get("/geofences")
@jwt_required()
def list_geofences():
    """围栏台账/图层。商家侧只看与自己相关的：本商家的 + 平台发布的全局围栏
    （merchant_id 为 NULL 表示对所有商家生效，之前用等值过滤把这类漏掉了）。"""
    u = current_user()
    query = db.session.query(Geofence).filter(Geofence.deleted == 0)
    if not scope.is_platform(u):
        query = query.filter(db.or_(Geofence.merchant_id.is_(None),
                                    Geofence.merchant_id == (u.merchant_id or 0)))
    rows = query.order_by(Geofence.id.desc()).all()
    names = _merchant_names()
    out = [_fence_fmt(g, names.get(g.merchant_id)) for g in rows]
    return success({"list": out, "total": len(out)})


@bp.get("/geofences/owners")
@perm_required("airspace:draw")
def fence_owners():
    """「适用商家」下拉：平台可选任一商家或留空（=全部商家），商家账号只会看到自己。"""
    u = current_user()
    query = db.session.query(Merchant.id, Merchant.merchant_name).filter(Merchant.deleted == 0)
    if not scope.is_platform(u):
        query = query.filter(Merchant.id == (u.merchant_id or 0))
    rows = query.order_by(Merchant.id).all()
    return success([{"id": r[0], "name": r[1]} for r in rows])


def _broadcast_fence(g: Geofence, action: str) -> int:
    """围栏变更向在飞机长下发预警（需求 4.2.4-4：电子围栏设置并下发机长）。"""
    flying = (db.session.query(FlightPlan)
              .filter(FlightPlan.status == 3, FlightPlan.deleted == 0).all())
    uids = {p.pilot_id for p in flying if p.pilot_id}
    body = (f"{g.fence_name}｜{FENCE_TYPE_NAME.get(g.fence_type)}｜限高 "
            f"{g.altitude_limit or '不限'}m｜告警距离 {g.warn_distance_m or '-'}m。{action}")
    notify.notify_many(list(uids), "电子围栏变更预警", body, biz_type="geofence", biz_id=g.id)
    return len(uids)


@bp.post("/geofences")
@perm_required("airspace:draw")
def create_geofence():
    u = current_user()
    d = request.get_json(silent=True) or {}
    name = (d.get("fenceName") or "").strip()
    if not name:
        raise ApiError(BizCode.PARAM_ERROR, "fenceName 必填")
    ftype = int(d.get("fenceType") or 0)
    if ftype not in FENCE_TYPE_NAME:
        raise ApiError(BizCode.PARAM_ERROR, "fenceType 需为 1限入 2限出 3接近告警")
    mid = d.get("merchantId")
    if not scope.is_platform(u):
        mid = u.merchant_id or 1
    if mid in (0, "0", None, ""):
        mid = None
    g = Geofence(
        fence_name=name[:80], merchant_id=mid, geometry_json=_validate_polygon(d.get("geometry")),
        fence_type=ftype, altitude_limit=_int_or_none(d.get("altitudeLimit")),
        warn_distance_m=_int_or_none(d.get("warnDistanceM")) or 200, status=1,
    )
    db.session.add(g)
    db.session.flush()
    hit = _broadcast_fence(g, "新增围栏已生效，请注意避让。")
    db.session.commit()
    return success({"id": g.id, "notified": hit}, f"围栏已创建，{hit} 位在飞机长已收到预警")


@bp.put("/geofences/<int:gid>")
@perm_required("airspace:draw")
def update_geofence(gid):
    u = current_user()
    g = db.session.get(Geofence, gid)
    if g is None or g.deleted:
        raise ApiError(BizCode.NOT_FOUND, "围栏不存在")
    if not scope.is_platform(u) and g.merchant_id != (u.merchant_id or 0):
        raise ApiError(BizCode.FORBIDDEN, "不能修改其他商家的围栏")
    d = request.get_json(silent=True) or {}
    if d.get("geometry") is not None:
        g.geometry_json = _validate_polygon(d["geometry"])
    if d.get("fenceType") is not None:
        g.fence_type = int(d["fenceType"])
    if "altitudeLimit" in d:
        g.altitude_limit = _int_or_none(d["altitudeLimit"])
    if "warnDistanceM" in d:
        g.warn_distance_m = _int_or_none(d["warnDistanceM"])
    changed_status = False
    if d.get("status") in (1, 2) and int(d["status"]) != g.status:
        g.status = int(d["status"])
        changed_status = True
    if d.get("fenceName"):
        g.fence_name = str(d["fenceName"])[:80]
    hit = _broadcast_fence(g, "围栏已停用，可恢复正常飞行。" if g.status == 2
                           else "围栏已启用，请注意避让。") if changed_status else 0
    db.session.commit()
    return success({"id": g.id, "notified": hit}, "围栏已更新")


@bp.delete("/geofences/<int:gid>")
@perm_required("airspace:draw")
def delete_geofence(gid):
    u = current_user()
    g = db.session.get(Geofence, gid)
    if g is None or g.deleted:
        raise ApiError(BizCode.NOT_FOUND, "围栏不存在")
    if not scope.is_platform(u) and g.merchant_id != (u.merchant_id or 0):
        raise ApiError(BizCode.FORBIDDEN, "不能删除其他商家的围栏")
    g.deleted = 1
    db.session.commit()
    return success({"id": g.id}, "围栏已删除")
