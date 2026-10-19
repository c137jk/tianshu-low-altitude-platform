"""辅助域蓝图（aux）：优惠券、站内信、违规处置、地址簿、工单、公告、参数、日志等。

**为什么文件名是 auxiliary.py 而不是 aux.py**：
`aux` 是 DOS 时代的保留设备名（CON / PRN / AUX / NUL）。Windows 上 `git add aux.py`
会把这个名字解析成设备，报 `open(".../aux.py"): No such file or directory`，
文件永远进不了版本库 —— 这也是本模块此前"代码里有 import、仓库里没文件"的根因。
文件名换掉即可，蓝图名与 URL 前缀仍保持 `aux`，所以接口路径（/api/aux/**）
和端点名（aux.cp_list 等）都不变。

本模块按设计 03 的「辅助功能」切分，承载那些不属于 D2~D9 主链路、但每个页面都要用到的
边角能力。之所以集中在一个蓝图里，是因为它们都是"薄壳"：真正的业务规则在
`coupon_service` / `notify` / `violation` 各处，这里只负责参数、权限与出参。

口径约定：
  · **出参沿用库表 snake_case**（前端 api 层按原样声明，不在前端二次转换），
    只有后加的派生字段（remain / claimed / myCouponId / used_count / unread）用 camelCase。
  · **入参用 camelCase**（前端表单直接提交），在视图里一次性落成库字段。
  · 客户自助类接口只要求登录态，数据范围一律写死本人 user_id；
    运营侧接口挂权限点，跨商家收窄交给 `scope.merchant_scope`。
  · 金额/时间统一在 `_row()` 里转成 JSON 友好类型：Decimal → float，
    datetime → 'YYYY-MM-DD HH:MM:SS'，避免 jsonify 直接 500。
"""
from datetime import datetime
from decimal import Decimal

from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from sqlalchemy import text

from app.common import scope
from app.common.decorators import current_user, perm_any_required, perm_required
from app.common.errors import ApiError, BizCode
from app.common.response import success
from app.extensions import db
from app.services import coupon_service, notify

bp = Blueprint("aux", __name__, url_prefix="/api/aux")

VIOL_TYPE = {1: "闯禁飞区", 2: "偏航", 3: "超时", 4: "超高", 5: "无证", 6: "其他"}
PENALTY_NAME = {0: "未处罚", 1: "警告", 2: "罚款", 3: "暂停服务", 4: "永久封禁"}
WO_STATUS = {1: "待受理", 2: "处理中", 3: "已解决", 4: "已关闭"}
EMERGENCY_STATUS = {1: "发生中", 2: "处置中", 3: "已解除"}
EMERGENCY_LEVEL = {1: "一般", 2: "较大", 3: "重大"}
MAINT_TYPE = {1: "定期维保", 2: "故障维修", 3: "巡检"}
MAINT_STATUS = {1: "待处理", 2: "处理中", 3: "已完成"}
SHIFT_TYPE = {1: "早班", 2: "中班", 3: "晚班", 4: "全天"}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def _q(sql, **params):
    return db.session.execute(text(sql), params)


def _all(sql, **params):
    return _q(sql, **params).mappings().all()


def _first(sql, **params):
    return _q(sql, **params).mappings().first()


def _val(sql, **params):
    return _q(sql, **params).scalar()


def _row(m):
    """把一行映射转成 JSON 友好 dict：Decimal→float，datetime→字符串。"""
    if m is None:
        return None
    out = {}
    for k, v in dict(m).items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, datetime):
            out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        else:
            out[k] = v
    return out


def _rows(items):
    return [_row(x) for x in items]


def _int(name, default=None, required=False):
    raw = request.args.get(name)
    if raw in (None, ""):
        if required:
            raise ApiError(BizCode.PARAM_ERROR, f"缺少参数：{name}")
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ApiError(BizCode.PARAM_ERROR, f"参数 {name} 必须是整数")


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(BizCode.PARAM_ERROR, "请求体必须是 JSON 对象")
    return data


def _need(data, key, label=None):
    v = data.get(key)
    if v in (None, ""):
        raise ApiError(BizCode.PARAM_ERROR, f"缺少字段：{label or key}")
    return v


def _page(rows, total=None):
    """列表出参统一 {list, total}，前端表格不用为两种形状各写一套。"""
    return {"list": rows, "total": total if total is not None else len(rows)}


# ==========================================================================
# 一、优惠券
# ==========================================================================
def _cp_available(user):
    """领券中心：在架、在有效期内、还有余量的券 + 当前用户的领取状态。

    `claimed` 包含已核销的历史（领过就是领过），`myCouponId` 只给「未使用」那一张，
    下单时传的是它。核销后 myCouponId 变空，前端就不会把已用掉的券再传一次。
    """
    now = datetime.now()
    rows = _all(
        "SELECT c.*, (c.total_count - c.got_count) AS remain FROM coupon c "
        "WHERE c.deleted = 0 AND c.status = 1 AND c.start_at <= :now AND c.end_at >= :now "
        "ORDER BY c.end_at ASC, c.id DESC", now=now)
    mine = {r["coupon_id"]: r for r in _all(
        "SELECT id, coupon_id, status FROM user_coupon WHERE user_id = :u AND deleted = 0",
        u=user.id)}
    out = []
    for r in rows:
        item = _row(r)
        got = mine.get(r["id"])
        item["claimed"] = got is not None
        item["myCouponId"] = got["id"] if (got is not None and got["status"] == 1) else None
        item["ruleText"] = coupon_service.rule_text(
            r["coupon_type"], r["threshold"] or 0, r["amount"], r["discount"])
        out.append(item)
    return out


@bp.get("/coupons")
@jwt_required()
def cp_list():
    """优惠券列表。scope=available 走客户视角，不带 scope 走营销台账（需权限）。"""
    user = current_user()
    if request.args.get("scope") == "available":
        return success(_cp_available(user))
    if not user.has_perm("marketing:coupon"):
        raise ApiError(BizCode.FORBIDDEN, "仅营销台账可查看全部优惠券", 403)
    rows = _all(
        "SELECT c.*, (c.total_count - c.got_count) AS remain, "
        "(SELECT COUNT(*) FROM user_coupon uc WHERE uc.coupon_id = c.id "
        " AND uc.status = 2 AND uc.deleted = 0) AS used_count "
        "FROM coupon c WHERE c.deleted = 0 ORDER BY c.id DESC")
    return success(_rows(rows))


@bp.post("/coupons/<int:cid>/claim")
@jwt_required()
def cp_claim(cid):
    user = current_user()
    uc_id = coupon_service.claim(user.id, cid)
    return success({"id": uc_id})


@bp.get("/coupons/mine")
@jwt_required()
def cp_mine():
    user = current_user()
    status = _int("status")
    return success(_page(coupon_service.mine(user.id, status)))


@bp.post("/coupons")
@perm_required("marketing:coupon")
def cp_create():
    data = _body()
    name = _need(data, "couponName", "券名称")
    ctype = int(_need(data, "couponType", "券类型"))
    if ctype not in (1, 2):
        raise ApiError(BizCode.PARAM_ERROR, "券类型只能是 1 满减 / 2 折扣")
    total = int(data.get("totalCount") or 0)
    start = _dt(data.get("startAt"), "开始时间")
    end = _dt(data.get("endAt"), "结束时间")
    if end <= start:
        raise ApiError(BizCode.PARAM_ERROR, "结束时间必须晚于开始时间")
    if ctype == 1 and (data.get("amount") in (None, "")):
        raise ApiError(BizCode.PARAM_ERROR, "满减券必须填写减免金额")
    if ctype == 2 and (data.get("discount") in (None, "")):
        raise ApiError(BizCode.PARAM_ERROR, "折扣券必须填写折扣率")
    new_id = _q(
        "INSERT INTO coupon (coupon_name, coupon_type, threshold, amount, discount, "
        "total_count, got_count, start_at, end_at, status, created_at, updated_at, deleted) "
        "VALUES (:n, :t, :th, :a, :d, :tc, 0, :s, :e, 1, NOW(), NOW(), 0)",
        n=name, t=ctype, th=data.get("threshold") or 0,
        a=data.get("amount"), d=data.get("discount"), tc=total, s=start, e=end).lastrowid
    db.session.commit()
    return success({"id": new_id})


@bp.put("/coupons/<int:cid>")
@perm_required("marketing:coupon")
def cp_update(cid):
    data = _body()
    if "status" not in data:
        raise ApiError(BizCode.PARAM_ERROR, "缺少字段：status")
    status = int(data["status"])
    if status not in (1, 2):
        raise ApiError(BizCode.PARAM_ERROR, "状态只能是 1 启用 / 2 停用")
    hit = _q("UPDATE coupon SET status = :s, updated_at = NOW() "
             "WHERE id = :i AND deleted = 0", s=status, i=cid).rowcount
    if not hit:
        raise ApiError(BizCode.NOT_FOUND, "优惠券不存在", 404)
    db.session.commit()
    return success(None)


def _dt(raw, label):
    if isinstance(raw, datetime):
        return raw
    if not raw:
        raise ApiError(BizCode.PARAM_ERROR, f"缺少字段：{label}")
    text_val = str(raw).replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text_val, fmt)
        except ValueError:
            continue
    raise ApiError(BizCode.PARAM_ERROR, f"{label} 格式不正确")


# ==========================================================================
# 二、站内信
# ==========================================================================
@bp.get("/messages")
@jwt_required()
def msg_list():
    """本人消息；user_id=0 的广播也要算进来（按 biz 去重由 notify 保证）。"""
    user = current_user()
    limit = _int("limit", 50) or 50
    rows = _all(
        "SELECT id, title, content, biz_type, biz_id, channel, is_read, read_at, created_at "
        "FROM message_notification WHERE user_id IN (:u, 0) "
        "ORDER BY is_read ASC, created_at DESC LIMIT :lim", u=user.id, lim=limit)
    unread = _val(
        "SELECT COUNT(*) FROM message_notification "
        "WHERE user_id IN (:u, 0) AND is_read = 0", u=user.id) or 0
    return success({"list": _rows(rows), "unread": int(unread)})


@bp.post("/messages/<int:mid>/read")
@jwt_required()
def msg_read(mid):
    user = current_user()
    hit = _q("UPDATE message_notification SET is_read = 1, read_at = NOW() "
             "WHERE id = :i AND user_id = :u AND is_read = 0", i=mid, u=user.id).rowcount
    db.session.commit()
    if not hit:
        exists = _val("SELECT COUNT(*) FROM message_notification "
                      "WHERE id = :i AND user_id = :u", i=mid, u=user.id)
        if not exists:
            raise ApiError(BizCode.NOT_FOUND, "消息不存在", 404)
    return success(None)


@bp.post("/messages/read-all")
@jwt_required()
def msg_read_all():
    user = current_user()
    hit = _q("UPDATE message_notification SET is_read = 1, read_at = NOW() "
             "WHERE user_id = :u AND is_read = 0", u=user.id).rowcount
    db.session.commit()
    return success({"updated": int(hit or 0)})


# ==========================================================================
# 三、违规处置
# ==========================================================================
@bp.get("/violations")
@perm_required("monitor:violation")
def vio_list():
    user = current_user()
    where, params = ["v.deleted = 0"], {}
    mid = scope.merchant_scope(user)
    if mid is not None:
        where.append("v.merchant_id = :mid")
        params["mid"] = mid
    status = _int("status")
    if status:
        where.append("v.status = :st")
        params["st"] = status
    kw = (request.args.get("keyword") or "").strip()
    if kw:
        where.append("(v.violation_no LIKE :kw OR v.description LIKE :kw)")
        params["kw"] = f"%{kw}%"
    sql = (
        "SELECT v.*, m.merchant_name, a.aircraft_no, u.real_name AS pilot_name, "
        "p.plan_no FROM violation_record v "
        "LEFT JOIN merchants m ON m.id = v.merchant_id "
        "LEFT JOIN aircraft a ON a.id = v.aircraft_id "
        "LEFT JOIN users u ON u.id = v.pilot_id "
        "LEFT JOIN flight_plan p ON p.id = v.plan_id "
        "WHERE " + " AND ".join(where) + " ORDER BY v.violation_time DESC LIMIT 200")
    rows = _rows(_all(sql, **params))
    for r in rows:
        r["type_name"] = VIOL_TYPE.get(r.get("violation_type"), "其他")
        r["penalty_name"] = PENALTY_NAME.get(r.get("penalty_type"), "未处罚")
    return success(rows)


@bp.post("/violations/<int:vid>/penalty")
@perm_required("monitor:violation")
def vio_penalty(vid):
    """处罚落库并给被处罚机长发通知书消息。"""
    user = current_user()
    data = _body()
    ptype = int(data.get("penaltyType") or 0)
    if ptype not in (1, 2, 3, 4):
        raise ApiError(BizCode.PARAM_ERROR, "处罚类型不合法")
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise ApiError(BizCode.PARAM_ERROR, "必须填写处罚事由")
    rec = _first("SELECT * FROM violation_record WHERE id = :i AND deleted = 0", i=vid)
    if rec is None:
        raise ApiError(BizCode.NOT_FOUND, "违规记录不存在", 404)
    if rec["penalty_type"]:
        raise ApiError(BizCode.CONFLICT, "该违规已处罚，不能重复处罚")
    amount = data.get("penaltyAmount") if ptype == 2 else None
    days = data.get("penaltyDays") if ptype == 3 else None
    _q("UPDATE violation_record SET penalty_type = :pt, penalty_amount = :pa, "
       "penalty_days = :pd, penalty_reason = :pr, penalty_by = :pb, penalty_at = NOW(), "
       "notice_url = :nu, status = 3, updated_at = NOW() "
       "WHERE id = :i AND deleted = 0",
       pt=ptype, pa=amount, pd=days, pr=reason, pb=user.id,
       nu=f"/api/aux/violations/{vid}/notice", i=vid)
    db.session.commit()
    if rec["pilot_id"]:
        notify.notify(
            rec["pilot_id"],
            f"违规处理通知书 {rec['violation_no']}",
            f"{VIOL_TYPE.get(rec['violation_type'], '违规')}：{PENALTY_NAME.get(ptype)}，事由：{reason}",
            biz_type="violation_penalty", biz_id=vid)
        db.session.commit()
    return success({"id": vid, "penaltyName": PENALTY_NAME.get(ptype)})


@bp.get("/violations/<int:vid>/notice")
@jwt_required()
def vio_notice(vid):
    """违规通知书：本商家、被处罚机长本人、或有违规处置权的人可读。"""
    user = current_user()
    rec = _first(
        "SELECT v.*, m.merchant_name, u.real_name AS pilot_name, a.aircraft_no "
        "FROM violation_record v LEFT JOIN merchants m ON m.id = v.merchant_id "
        "LEFT JOIN users u ON u.id = v.pilot_id LEFT JOIN aircraft a ON a.id = v.aircraft_id "
        "WHERE v.id = :i AND v.deleted = 0", i=vid)
    if rec is None:
        raise ApiError(BizCode.NOT_FOUND, "违规记录不存在", 404)
    mid = scope.merchant_scope(user)
    allowed = (mid is not None and rec["merchant_id"] == mid) or rec["pilot_id"] == user.id \
        or user.has_perm("monitor:violation")
    if not allowed:
        raise ApiError(BizCode.NOT_FOUND, "违规记录不存在", 404)
    if not rec["penalty_type"]:
        raise ApiError(BizCode.CONFLICT, "该违规尚未处罚，暂无通知书")
    ptype = rec["penalty_type"]
    head = f"关于 {rec['merchant_name'] or ''}{rec['aircraft_no'] or ''} 违规行为的处理决定"
    body_lines = [
        head,
        f"被处理人：{rec['pilot_name'] or '—'}（{rec['merchant_name'] or '—'}）",
        f"违规事实：{rec['description'] or VIOL_TYPE.get(rec['violation_type'], '违规')}",
        f"处理决定：{PENALTY_NAME.get(ptype)}",
        f"事由：{rec['penalty_reason'] or '—'}",
        "如对本决定有异议，可在收到通知书之日起 3 个工作日内向平台提出申诉。",
    ]
    return success({
        "noticeNo": rec["violation_no"],
        "pilotName": rec["pilot_name"],
        "merchantName": rec["merchant_name"],
        "aircraftNo": rec["aircraft_no"],
        "description": rec["description"],
        "typeName": VIOL_TYPE.get(rec["violation_type"], "其他"),
        "violationTime": _row(rec)["violation_time"],
        "lng": _row(rec)["lng"],
        "lat": _row(rec)["lat"],
        "penaltyName": PENALTY_NAME.get(ptype),
        "penaltyReason": rec["penalty_reason"],
        "issuerName": "平台运营",
        "issuedAt": _row(rec)["penalty_at"],
        "appeal": "如对本决定有异议，可在收到通知书之日起 3 个工作日内向平台提出申诉。",
        "body": "\n".join(body_lines),
    })


# ==========================================================================
# 四、地址簿
# ==========================================================================
@bp.get("/addresses")
@jwt_required()
def addr_list():
    user = current_user()
    rows = _all(
        "SELECT id, contact_name, contact_phone, address, lng, lat, label, is_default, "
        "created_at FROM address_book WHERE user_id = :u AND deleted = 0 "
        "ORDER BY is_default DESC, id DESC", u=user.id)
    return success(_rows(rows))


@bp.post("/addresses")
@jwt_required()
def addr_save():
    user = current_user()
    data = _body()
    name = _need(data, "contactName", "联系人")
    phone = _need(data, "contactPhone", "联系电话")
    address = _need(data, "address", "地址")
    is_default = 1 if data.get("isDefault") else 0
    if is_default:
        _q("UPDATE address_book SET is_default = 0 WHERE user_id = :u", u=user.id)
    new_id = _q(
        "INSERT INTO address_book (user_id, contact_name, contact_phone, address, lng, lat, "
        "label, is_default, created_at, updated_at, deleted) "
        "VALUES (:u, :n, :p, :a, :lng, :lat, :lb, :d, NOW(), NOW(), 0)",
        u=user.id, n=name, p=phone, a=address, lng=data.get("lng"), lat=data.get("lat"),
        lb=data.get("label") or "其他", d=is_default).lastrowid
    db.session.commit()
    return success({"id": new_id})


@bp.put("/addresses/<int:aid>/default")
@jwt_required()
def addr_default(aid):
    user = current_user()
    if not _val("SELECT COUNT(*) FROM address_book WHERE id = :i AND user_id = :u AND deleted = 0",
                i=aid, u=user.id):
        raise ApiError(BizCode.NOT_FOUND, "地址不存在", 404)
    _q("UPDATE address_book SET is_default = 0 WHERE user_id = :u", u=user.id)
    _q("UPDATE address_book SET is_default = 1 WHERE id = :i", i=aid)
    db.session.commit()
    return success(None)


@bp.delete("/addresses/<int:aid>")
@jwt_required()
def addr_del(aid):
    user = current_user()
    hit = _q("UPDATE address_book SET deleted = 1 WHERE id = :i AND user_id = :u AND deleted = 0",
             i=aid, u=user.id).rowcount
    db.session.commit()
    if not hit:
        raise ApiError(BizCode.NOT_FOUND, "地址不存在", 404)
    return success(None)


# ==========================================================================
# 五、评价与反馈
# ==========================================================================
@bp.get("/feedback")
@jwt_required()
def fb_list():
    """客户看自己的、运营看权限范围内的（客服/商家管理员）。"""
    user = current_user()
    where, params = ["ec.deleted = 0"], {}
    if not user.has_perm("service:workorder"):
        where.append("ec.user_id = :u")
        params["u"] = user.id
    rows = _all(
        "SELECT ec.*, o.order_no, u.username FROM evaluation_complaint ec "
        "LEFT JOIN orders o ON o.id = ec.order_id LEFT JOIN users u ON u.id = ec.user_id "
        "WHERE " + " AND ".join(where) + " ORDER BY ec.id DESC LIMIT 100", **params)
    return success(_page(_rows(rows)))


@bp.post("/feedback")
@jwt_required()
def fb_create():
    user = current_user()
    data = _body()
    ftype = int(data.get("type") or 2)
    if ftype not in (1, 2):
        raise ApiError(BizCode.PARAM_ERROR, "类型只能是 1 评价 / 2 投诉")
    content = _need(data, "content", "内容").strip()
    order_id = data.get("orderId")
    merchant_id = None
    if order_id:
        order = _first("SELECT id, merchant_id, customer_id FROM orders WHERE id = :i",
                       i=order_id)
        if order is None:
            raise ApiError(BizCode.NOT_FOUND, "订单不存在", 404)
        if order["customer_id"] != user.id:
            raise ApiError(BizCode.FORBIDDEN, "只能评价自己的订单", 403)
        merchant_id = order["merchant_id"]
    new_id = _q(
        "INSERT INTO evaluation_complaint (order_id, user_id, merchant_id, type, score, "
        "content, created_at, updated_at, deleted) "
        "VALUES (:o, :u, :m, :t, :s, :c, NOW(), NOW(), 0)",
        o=order_id, u=user.id, m=merchant_id, t=ftype, s=data.get("score"), c=content).lastrowid
    db.session.commit()
    return success({"id": new_id})


# ==========================================================================
# 六、工单（客服中心）
# ==========================================================================
@bp.get("/work-orders")
@perm_required("service:workorder")
def wo_list():
    user = current_user()
    where, params = ["wo.deleted = 0"], {}
    mid = scope.merchant_scope(user)
    if mid is not None:
        # 工单表本身不带 merchant_id，只有 biz_type/biz_id；商家侧按「本商家订单」收窄
        where.append("(wo.biz_type <> 'order' OR wo.biz_id IN "
                     "(SELECT id FROM orders WHERE merchant_id = :mid))")
        params["mid"] = mid
    status = _int("status")
    if status:
        where.append("wo.status = :st")
        params["st"] = status
    rows = _all(
        "SELECT wo.*, u.real_name AS handler_name FROM work_order wo "
        "LEFT JOIN users u ON u.id = wo.handler_id "
        "WHERE " + " AND ".join(where) + " ORDER BY wo.id DESC LIMIT 100", **params)
    out = _rows(rows)
    for r in out:
        r["status_name"] = WO_STATUS.get(r.get("status"), "")
    return success(_page(out))


@bp.post("/work-orders")
@jwt_required()
def wo_create():
    user = current_user()
    data = _body()
    title = _need(data, "title", "标题")
    order_id = data.get("orderId")
    if order_id and not _val(
            "SELECT COUNT(*) FROM orders WHERE id = :i AND customer_id = :u AND deleted = 0",
            i=order_id, u=user.id):
        raise ApiError(BizCode.NOT_FOUND, "订单不存在", 404)
    ticket_no = "WO" + datetime.now().strftime("%Y%m%d%H%M%S") + str(user.id)[-3:]
    new_id = _q(
        "INSERT INTO work_order (ticket_no, source, biz_type, biz_id, title, content, "
        "priority, status, created_at, updated_at, deleted) "
        "VALUES (:no, 'customer', :bt, :bi, :t, :c, :p, 1, NOW(), NOW(), 0)",
        no=ticket_no, bt="order" if order_id else "other", bi=order_id or 0,
        t=title, c=data.get("content") or "",
        p=int(data.get("priority") or 2)).lastrowid
    db.session.commit()
    return success({"id": new_id, "ticketNo": ticket_no})


@bp.put("/work-orders/<int:wid>")
@perm_required("service:workorder")
def wo_update(wid):
    user = current_user()
    data = _body()
    status = int(_need(data, "status", "状态"))
    if status not in (1, 2, 3, 4):
        raise ApiError(BizCode.PARAM_ERROR, "状态不合法")
    rec = _first("SELECT * FROM work_order WHERE id = :i AND deleted = 0", i=wid)
    if rec is None:
        raise ApiError(BizCode.NOT_FOUND, "工单不存在", 404)
    mid = scope.merchant_scope(user)
    if mid is not None and rec["biz_type"] == "order":
        owned = _val("SELECT COUNT(*) FROM orders WHERE id = :i AND merchant_id = :m",
                     i=rec["biz_id"], m=mid)
        if not owned:
            raise ApiError(BizCode.FORBIDDEN, "无权处理其他商家的工单", 403)
    _q("UPDATE work_order SET status = :s, handler_id = :h, "
       "closed_at = CASE WHEN :s2 = 4 THEN NOW() ELSE closed_at END, updated_at = NOW() "
       "WHERE id = :i", s=status, h=user.id, s2=status, i=wid)
    db.session.commit()
    return success(None)


# ==========================================================================
# 七、实名认证
# ==========================================================================
@bp.get("/realname/status")
@jwt_required()
def realname_status():
    user = current_user()
    rec = _first("SELECT real_name, realname_status FROM users WHERE id = :i", i=user.id)
    status = int(rec["realname_status"] or 0) if rec else 0
    return success({
        "status": status,
        "statusName": {0: "未提交", 1: "审核中", 2: "已通过", 3: "已驳回"}.get(status, "未提交"),
        "realName": rec["real_name"] if rec else None,
    })


@bp.post("/realname")
@jwt_required()
def realname_submit():
    user = current_user()
    data = _body()
    real_name = _need(data, "realName", "真实姓名").strip()
    id_card = (data.get("idCardNo") or "").strip()
    if len(id_card) < 15:
        raise ApiError(BizCode.PARAM_ERROR, "身份证号格式不正确")
    if int(user.realname_status or 0) == 2:
        raise ApiError(BizCode.CONFLICT, "已通过实名认证，无需重复提交")
    _q("UPDATE users SET real_name = :n, id_card_cipher = :c, realname_status = 1, "
       "updated_at = NOW() WHERE id = :i", n=real_name, c=_enc(id_card), i=user.id)
    db.session.commit()
    return success({"status": 1, "statusName": "审核中"})


def _enc(plain):
    """证件号与手机号同用一套 AES-GCM 通道（列名不同、密钥同一把）。"""
    from app.common.crypto import encrypt_phone
    return encrypt_phone(plain)


# ==========================================================================
# 八、应急事件
# ==========================================================================
@bp.get("/emergencies")
@perm_required("monitor:emergency")
def em_list():
    user = current_user()
    where, params = ["e.deleted = 0"], {}
    mid = scope.merchant_scope(user)
    if mid is not None:
        # 应急事件表没有 merchant_id，归属靠飞行器反查
        where.append("a.merchant_id = :mid")
        params["mid"] = mid
    rows = _all(
        "SELECT e.*, a.aircraft_no, u.real_name AS handler_name FROM emergency_event e "
        "LEFT JOIN aircraft a ON a.id = e.aircraft_id "
        "LEFT JOIN users u ON u.id = e.handler_id "
        "WHERE " + " AND ".join(where) + " ORDER BY e.created_at DESC LIMIT 100", **params)
    out = _rows(rows)
    for r in out:
        r["level_name"] = EMERGENCY_LEVEL.get(r.get("level"), "")
        r["status_name"] = EMERGENCY_STATUS.get(r.get("status"), "")
    return success(_page(out))


@bp.put("/emergencies/<int:eid>")
@perm_required("monitor:emergency")
def em_update(eid):
    user = current_user()
    data = _body()
    rec = _first("SELECT e.*, a.merchant_id FROM emergency_event e "
                 "LEFT JOIN aircraft a ON a.id = e.aircraft_id "
                 "WHERE e.id = :i AND e.deleted = 0", i=eid)
    if rec is None:
        raise ApiError(BizCode.NOT_FOUND, "应急事件不存在", 404)
    mid = scope.merchant_scope(user)
    if mid is not None and rec["merchant_id"] != mid:
        raise ApiError(BizCode.FORBIDDEN, "无权处置其他商家的应急事件", 403)
    status = int(data.get("status") or rec["status"])
    _q("UPDATE emergency_event SET status = :s, handle_measure = :m, handler_id = :h, "
       "closed_at = CASE WHEN :s2 = 3 THEN NOW() ELSE closed_at END, updated_at = NOW() "
       "WHERE id = :i",
       s=status, m=data.get("handleMeasure") or rec["handle_measure"],
       h=user.id, s2=status, i=eid)
    db.session.commit()
    return success(None)


# ==========================================================================
# 九、维保记录
# ==========================================================================
@bp.get("/maintenance")
@perm_required("resource:aircraft")
def mt_list():
    user = current_user()
    where, params = ["mr.deleted = 0"], {}
    mid = scope.merchant_scope(user)
    if mid is not None:
        where.append("a.merchant_id = :mid")
        params["mid"] = mid
    rows = _all(
        "SELECT mr.*, a.aircraft_no, u.real_name AS handler_name FROM maintenance_record mr "
        "LEFT JOIN aircraft a ON a.id = mr.aircraft_id "
        "LEFT JOIN users u ON u.id = mr.handler_id "
        "WHERE " + " AND ".join(where) + " ORDER BY mr.id DESC LIMIT 200", **params)
    out = _rows(rows)
    for r in out:
        r["type_name"] = MAINT_TYPE.get(r.get("record_type"), "")
        r["status_name"] = MAINT_STATUS.get(r.get("status"), "")
    return success(_page(out))


@bp.post("/maintenance")
@perm_required("resource:aircraft")
def mt_create():
    user = current_user()
    data = _body()
    aircraft_id = int(_need(data, "aircraftId", "飞行器"))
    # 商家只能给自己的飞行器报修：从飞行器反查商家，别信前端传的 merchant_id
    owned = _first("SELECT merchant_id FROM aircraft WHERE id = :i AND deleted = 0",
                   i=aircraft_id)
    if owned is None:
        raise ApiError(BizCode.NOT_FOUND, "飞行器不存在", 404)
    mid = scope.merchant_scope(user)
    if mid is not None and owned["merchant_id"] != mid:
        raise ApiError(BizCode.FORBIDDEN, "无权操作其他商家的飞行器", 403)
    new_id = _q(
        "INSERT INTO maintenance_record (aircraft_id, record_type, fault_type, description, "
        "photo_urls, handler_id, started_at, status, created_at, updated_at, deleted) "
        "VALUES (:a, :t, :f, :d, :p, :h, NOW(), 1, NOW(), NOW(), 0)",
        a=aircraft_id, t=int(data.get("recordType") or 2), f=data.get("faultType"),
        d=data.get("description"), p=None, h=user.id).lastrowid
    db.session.commit()
    return success({"id": new_id})


@bp.put("/maintenance/<int:rid>")
@perm_required("resource:aircraft")
def mt_update(rid):
    user = current_user()
    data = _body()
    rec = _first("SELECT mr.*, a.merchant_id FROM maintenance_record mr "
                 "LEFT JOIN aircraft a ON a.id = mr.aircraft_id "
                 "WHERE mr.id = :i AND mr.deleted = 0", i=rid)
    if rec is None:
        raise ApiError(BizCode.NOT_FOUND, "维保记录不存在", 404)
    mid = scope.merchant_scope(user)
    if mid is not None and rec["merchant_id"] != mid:
        raise ApiError(BizCode.FORBIDDEN, "无权操作其他商家的维保记录", 403)
    status = int(data.get("status") or rec["status"])
    _q("UPDATE maintenance_record SET status = :s, result = :r, handler_id = :h, "
       "finished_at = CASE WHEN :s2 = 3 THEN NOW() ELSE finished_at END, updated_at = NOW() "
       "WHERE id = :i", s=status, r=data.get("result") or rec["result"], h=user.id,
       s2=status, i=rid)
    db.session.commit()
    return success(None)


# ==========================================================================
# 十、排班
# ==========================================================================
@bp.get("/schedules")
@perm_any_required("resource:schedule", "resource:schedule:view")
def sc_list():
    user = current_user()
    where, params = ["s.deleted = 0"], {}
    mid = scope.merchant_scope(user)
    if mid is not None:
        where.append("s.merchant_id = :mid")
        params["mid"] = mid
    start = request.args.get("from")
    end = request.args.get("to")
    if start:
        where.append("s.shift_date >= :frm")
        params["frm"] = start
    if end:
        where.append("s.shift_date <= :to")
        params["to"] = end
    rows = _all(
        "SELECT s.*, u.real_name AS user_name, u.username, a.aircraft_no FROM staff_schedule s "
        "LEFT JOIN users u ON u.id = s.user_id LEFT JOIN aircraft a ON a.id = s.bound_aircraft_id "
        "WHERE " + " AND ".join(where) + " ORDER BY s.shift_date DESC, s.id DESC LIMIT 300",
        **params)
    out = _rows(rows)
    for r in out:
        r["shift_name"] = SHIFT_TYPE.get(r.get("shift_type"), "")
        r["shift_date"] = str(r.get("shift_date"))[:10] if r.get("shift_date") else None
    return success(out)


@bp.post("/schedules")
@perm_required("resource:schedule")
def sc_create():
    user = current_user()
    data = _body()
    user_id = int(_need(data, "userId", "值班人"))
    shift_date = _need(data, "shiftDate", "值班日期")
    shift_type = int(data.get("shiftType") or 1)
    if shift_type not in (1, 2, 3, 4):
        raise ApiError(BizCode.PARAM_ERROR, "班次不合法")
    staff = _first("SELECT id, merchant_id FROM users WHERE id = :i AND deleted = 0", i=user_id)
    if staff is None:
        raise ApiError(BizCode.NOT_FOUND, "值班人不存在", 404)
    mid = scope.merchant_scope(user)
    if mid is not None and staff["merchant_id"] != mid:
        raise ApiError(BizCode.FORBIDDEN, "只能给本商家人员排班", 403)
    dup = _val("SELECT COUNT(*) FROM staff_schedule WHERE user_id = :u AND shift_date = :d "
               "AND shift_type = :t AND deleted = 0",
               u=user_id, d=str(shift_date)[:10], t=shift_type)
    if dup:
        raise ApiError(BizCode.CONFLICT, "该人员当天已有同一班次")
    new_id = _q(
        "INSERT INTO staff_schedule (user_id, merchant_id, shift_date, shift_type, start_time, "
        "end_time, responsible_area, bound_aircraft_id, status, created_at, updated_at, deleted) "
        "VALUES (:u, :m, :d, :t, :st, :et, :ra, :ac, 1, NOW(), NOW(), 0)",
        u=user_id, m=staff["merchant_id"], d=str(shift_date)[:10], t=shift_type,
        st=data.get("startTime"), et=data.get("endTime"), ra=data.get("responsibleArea"),
        ac=data.get("boundAircraftId")).lastrowid
    db.session.commit()
    return success({"id": new_id})


@bp.delete("/schedules/<int:sid>")
@perm_required("resource:schedule")
def sc_del(sid):
    user = current_user()
    rec = _first("SELECT * FROM staff_schedule WHERE id = :i AND deleted = 0", i=sid)
    if rec is None:
        raise ApiError(BizCode.NOT_FOUND, "排班不存在", 404)
    mid = scope.merchant_scope(user)
    if mid is not None and rec["merchant_id"] != mid:
        raise ApiError(BizCode.FORBIDDEN, "无权删除其他商家的排班", 403)
    if int(rec["status"] or 1) != 1:
        raise ApiError(BizCode.CONFLICT, "已出勤的班次不能删除")
    _q("UPDATE staff_schedule SET deleted = 1, updated_at = NOW() WHERE id = :i", i=sid)
    db.session.commit()
    return success(None)


# ==========================================================================
# 十一、监管统计报告
# ==========================================================================
@bp.get("/report")
@perm_required("report:view")
def report():
    """监管口径的汇总报表：审批通过率、违规处罚率、订单与实付。"""
    user = current_user()
    mid = scope.merchant_scope(user)
    cond, params = ("AND o.merchant_id = :mid", {"mid": mid}) if mid is not None else ("", {})

    def one(sql, **extra):
        return _val(sql.format(cond=cond), **params, **extra) or 0

    orders = int(one("SELECT COUNT(*) FROM orders o WHERE o.deleted = 0 {cond}"))
    paid = float(one("SELECT COALESCE(SUM(o.pay_amount), 0) FROM orders o "
                     "WHERE o.deleted = 0 AND o.status IN (6, 7) {cond}"))
    plans = int(_val("SELECT COUNT(*) FROM flight_plan p WHERE p.deleted = 0"))
    approved = int(_val("SELECT COUNT(*) FROM flight_plan p WHERE p.deleted = 0 AND p.status >= 3"))
    violations = int(_val("SELECT COUNT(*) FROM violation_record v WHERE v.deleted = 0"))
    penalized = int(_val("SELECT COUNT(*) FROM violation_record v "
                         "WHERE v.deleted = 0 AND v.penalty_type > 0"))
    return success({
        "orderCount": orders,
        "paidAmount": round(paid, 2),
        "planCount": plans,
        "approvedCount": approved,
        "approvalRate": round(approved * 100.0 / plans, 1) if plans else 0.0,
        "violationCount": violations,
        "penalizedCount": penalized,
        "penaltyRate": round(penalized * 100.0 / violations, 1) if violations else 0.0,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


# ==========================================================================
# 十二、系统参数 / 日志 / 公告 / 调度任务
# ==========================================================================
@bp.get("/configs")
@perm_required("system:config")
def cfg_list():
    group = request.args.get("group")
    if group:
        rows = _all("SELECT * FROM sys_config WHERE deleted = 0 AND config_group = :g "
                    "ORDER BY config_group, id", g=group)
    else:
        rows = _all("SELECT * FROM sys_config WHERE deleted = 0 "
                    "ORDER BY config_group, id")
    return success(_rows(rows))


@bp.put("/configs/<int:cid>")
@perm_required("system:config")
def cfg_update(cid):
    data = _body()
    rec = _first("SELECT * FROM sys_config WHERE id = :i AND deleted = 0", i=cid)
    if rec is None:
        raise ApiError(BizCode.NOT_FOUND, "参数不存在", 404)
    value = data.get("value")
    if value is None:
        raise ApiError(BizCode.PARAM_ERROR, "缺少字段：value")
    _q("UPDATE sys_config SET config_value = :v, updated_at = NOW() WHERE id = :i",
       v=str(value), i=cid)
    db.session.commit()
    return success(None)


@bp.get("/logs")
@perm_required("system:log")
def log_list():
    # operation_log 是纯流水，没有 deleted 列（日志不做逻辑删除）
    where, params = ["1 = 1"], {}
    module = (request.args.get("module") or "").strip()
    if module:
        where.append("l.module = :m")
        params["m"] = module
    kw = (request.args.get("keyword") or "").strip()
    if kw:
        where.append("(l.username_snap LIKE :kw OR l.request_url LIKE :kw)")
        params["kw"] = f"%{kw}%"
    limit = _int("limit", 200) or 200
    rows = _all(
        "SELECT id, user_id, username_snap, module, action, method, request_url, ip, "
        "status, cost_ms, created_at FROM operation_log l "
        "WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT :lim",
        lim=limit, **params)
    return success(_page(_rows(rows)))


@bp.get("/articles/public")
def art_public():
    """公开的公告与帮助（登录页、门户帮助中心可读）。"""
    rows = _all("SELECT id, category, title, content, sort, view_count, updated_at "
                "FROM help_article WHERE deleted = 0 AND status = 1 "
                "ORDER BY sort ASC, id DESC")
    return success(_rows(rows))


@bp.get("/articles")
@perm_required("system:article")
def art_list():
    rows = _all("SELECT * FROM help_article WHERE deleted = 0 ORDER BY sort ASC, id DESC")
    return success(_rows(rows))


@bp.post("/articles")
@perm_required("system:article")
def art_create():
    data = _body()
    new_id = _q(
        "INSERT INTO help_article (category, title, content, sort, view_count, status, "
        "created_at, updated_at, deleted) VALUES (:c, :t, :n, :s, 0, :st, NOW(), NOW(), 0)",
        c=data.get("category") or "公告", t=_need(data, "title", "标题"),
        n=data.get("content") or "", s=int(data.get("sort") or 0),
        st=int(data.get("status") or 1)).lastrowid
    db.session.commit()
    return success({"id": new_id})


@bp.put("/articles/<int:aid>")
@perm_required("system:article")
def art_update(aid):
    data = _body()
    if not _val("SELECT COUNT(*) FROM help_article WHERE id = :i AND deleted = 0", i=aid):
        raise ApiError(BizCode.NOT_FOUND, "文章不存在", 404)
    _q("UPDATE help_article SET category = :c, title = :t, content = :n, sort = :s, "
       "status = :st, updated_at = NOW() WHERE id = :i",
       c=data.get("category"), t=data.get("title"), n=data.get("content"),
       s=int(data.get("sort") or 0), st=int(data.get("status") or 1), i=aid)
    db.session.commit()
    return success(None)


@bp.delete("/articles/<int:aid>")
@perm_required("system:article")
def art_del(aid):
    hit = _q("UPDATE help_article SET deleted = 1, updated_at = NOW() "
             "WHERE id = :i AND deleted = 0", i=aid).rowcount
    db.session.commit()
    if not hit:
        raise ApiError(BizCode.NOT_FOUND, "文章不存在", 404)
    return success(None)


@bp.get("/scheduler/jobs")
@perm_required("system:config")
def sched_jobs():
    from app.services import scheduler
    return success(scheduler.snapshot())
