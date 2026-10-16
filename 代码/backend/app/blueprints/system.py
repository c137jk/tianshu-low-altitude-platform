"""系统管理：账号维护 + 角色权限维护（设计06 M-81/M-83）。

权限点：system:user 账号 · system:role 角色与授权 · system:config 参数 · system:log 日志 ·
        system:article 公告帮助（在 aux 蓝图）
数据范围：商家员工（user_type=3）只看/只管本商家账号；平台侧（4/5/6 或 platform_admin）全量。
状态口径：users.status 只有 1 正常 / 2 冻结 / 3 注销 三种取值（与 DDL 注释、登录校验一致；
          早期前端开关用 0/1 是 bug，已同步改为 1/2）。
"""
import random
import re
from datetime import datetime

from flask import Blueprint, request
from flask_jwt_extended import jwt_required
from sqlalchemy import or_

from app.common import scope
from app.common.crypto import decrypt_phone, encrypt_phone, mask_phone, phone_digest
from app.common.decorators import current_user, perm_required
from app.common.errors import ApiError, BizCode
from app.common.response import success
from app.extensions import db
from app.models import Merchant, User
from app.models.rbac import Permission, Role, role_permission, user_role
from app.services import notify

bp = Blueprint("system", __name__, url_prefix="/api/system")

USER_STATUS_NAME = {1: "正常", 2: "冻结", 3: "注销"}
# 商家侧可自助创建的岗位；平台侧岗位（空管/运维/平台管理员）需要 system:role 授权
MERCHANT_ROLES = {"merchant_admin", "dispatcher", "pilot", "finance",
                  "merchant_maintainer", "customer"}
PLATFORM_ROLES = {"platform_admin", "air_traffic", "ops_maintainer"}
ROLE_USER_TYPE = {"dispatcher": 3, "merchant_admin": 3, "finance": 3, "pilot": 3,
                  "merchant_maintainer": 3, "customer": 1, "air_traffic": 5,
                  "ops_maintainer": 6, "platform_admin": 4}


def _int_or_400(raw, field: str) -> int:
    """入参里的数字一律先过这一关：脏值要 40000，不能让 int() 抛 ValueError 变 500。"""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise ApiError(BizCode.PARAM_ERROR, f"{field}格式不对：{raw!r} 不是合法数字")


# ------------------------------------------------------------------
# 内部校验：账号分级，防止低权限账号操作高权限账号（横向/纵向越权）
# ------------------------------------------------------------------
def _is_admin(operator: User) -> bool:
    return operator.user_type == 4 or "platform_admin" in operator.role_codes


def _load_target(uid: int) -> User:
    target = db.session.get(User, uid)
    if target is None or target.deleted:
        raise ApiError(BizCode.NOT_FOUND, "用户不存在")
    return target


def _assert_manageable(operator: User, target: User, action: str):
    """操作前门禁：不能操作自己；平台最高权限账号只有管理员能动；商家只能动本商家的人。"""
    if target.id == operator.id:
        raise ApiError(BizCode.FORBIDDEN, f"不能对自己执行「{action}」", 403)
    target_is_platform = target.user_type in scope.PLATFORM_TYPES or _is_admin(target)
    if target_is_platform:
        if not _is_admin(operator):
            raise ApiError(BizCode.FORBIDDEN, "只有平台管理员可以操作平台侧账号", 403)
        if target.id == 1:
            raise ApiError(BizCode.FORBIDDEN, "超级管理员账号不允许被修改", 403)
        return
    if not scope.is_platform(operator):
        if operator.merchant_id != target.merchant_id:
            raise ApiError(BizCode.FORBIDDEN, "只能操作本商家的账号", 403)


def _serialize(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "realName": u.real_name,
        "userType": u.user_type,
        "roles": u.role_codes,
        "roleIds": [r.id for r in u.roles],
        "roleNames": [r.role_name for r in u.roles],
        "phone": mask_phone(u.phone_cipher),
        "merchantId": u.merchant_id,
        "merchantName": u.merchant.merchant_name if u.merchant else None,
        "realnameStatus": u.realname_status,
        "status": u.status,
        "statusName": USER_STATUS_NAME.get(u.status, "未知"),
        "lastLoginAt": u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else None,
        "createdAt": u.created_at.strftime("%Y-%m-%d") if u.created_at else None,
    }


# ------------------------------------------------------------------
# 1. 账号维护（system:user）
# ------------------------------------------------------------------
@bp.get("/users")
@perm_required("system:user")
def list_users():
    page = max(_int_or_400(request.args.get("page", 1) or 1, "页码"), 1)
    size = min(max(_int_or_400(request.args.get("pageSize", 20) or 20, "每页条数"), 1), 100)
    keyword = (request.args.get("keyword") or "").strip()
    status = request.args.get("status")
    role_code = (request.args.get("roleCode") or "").strip()

    q = db.session.query(User).filter(User.deleted == 0)
    mid = scope.merchant_scope(current_user())
    if mid is not None:
        q = q.filter(User.merchant_id == mid)
    if keyword:
        q = q.filter(or_(User.username.like(f"%{keyword}%"),
                         User.real_name.like(f"%{keyword}%")))
    if status not in (None, ""):
        q = q.filter(User.status == _int_or_400(status, "状态值"))
    if role_code:
        q = q.filter(User.id.in_(db.session.query(user_role.c.user_id).join(
            Role, Role.id == user_role.c.role_id).filter(Role.role_code == role_code)))

    total = q.count()
    rows = q.order_by(User.id).offset((page - 1) * size).limit(size).all()
    return success({"list": [_serialize(u) for u in rows],
                    "total": total, "page": page, "pageSize": size})


@bp.post("/users")
@perm_required("system:user")
def create_user():
    """创建子账号：商家侧只能建本商家岗位，平台侧岗位需要 system:role。"""
    import bcrypt

    user = current_user()
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or "Admin@123"
    role_code = (d.get("roleCode") or "").strip()
    phone = (d.get("phone") or "").strip()
    if not username:
        raise ApiError(BizCode.PARAM_ERROR, "账号不能为空")
    if not role_code:
        raise ApiError(BizCode.PARAM_ERROR, "请选择岗位角色")
    if len(password) < 8:
        raise ApiError(BizCode.PARAM_ERROR, "初始密码至少 8 位")
    if role_code not in MERCHANT_ROLES | PLATFORM_ROLES:
        raise ApiError(BizCode.PARAM_ERROR, f"未知角色：{role_code}")
    if role_code in PLATFORM_ROLES and not user.has_perm("system:role"):
        raise ApiError(BizCode.FORBIDDEN, "创建平台侧账号需要「角色权限管理」权限", 403)
    if not phone or len(phone) != 11 or not phone.isdigit():
        raise ApiError(BizCode.PARAM_ERROR, "请填写 11 位手机号")
    if User.query.filter_by(username=username, deleted=0).first():
        raise ApiError(BizCode.CONFLICT, "账号已存在")
    if User.query.filter_by(phone_digest=phone_digest(phone), deleted=0).first():
        raise ApiError(BizCode.CONFLICT, "该手机号已注册")

    user_type = ROLE_USER_TYPE[role_code]
    wanted_mid = d.get("merchantId")
    if scope.is_platform(user) and user_type != 1:
        merchant_id = (_int_or_400(wanted_mid, "所属商家") if wanted_mid not in (None, "")
                       else (1 if user_type == 3 else None))
    else:
        merchant_id = user.merchant_id or 1
    if user_type == 1:
        merchant_id = None

    u = User(username=username,
             password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(10)).decode(),
             user_type=user_type, real_name=(d.get("realName") or username).strip(),
             phone_cipher=encrypt_phone(phone), phone_digest=phone_digest(phone),
             realname_status=2, gender=_int_or_400(d.get("gender") or 0, "性别"),
             merchant_id=merchant_id, status=1)
    db.session.add(u)
    db.session.flush()
    role = Role.query.filter_by(role_code=role_code).first()
    if role:
        db.session.execute(user_role.insert().values(
            user_id=u.id, role_id=role.id, granted_by=user.id))
    db.session.commit()
    return success({"id": u.id, "username": u.username, "initialPassword": password},
                   "子账号创建成功")


@bp.put("/users/<int:uid>/status")
@perm_required("system:user")
def set_user_status(uid):
    """启用/冻结/注销：status 只接受 1/2/3（1 正常 2 冻结 3 注销）。"""
    operator = current_user()
    target = _load_target(uid)
    _assert_manageable(operator, target, "修改状态")
    d = request.get_json(silent=True) or {}
    raw = d.get("status", None)
    if raw in (False, 0, "0", None, ""):
        raise ApiError(BizCode.PARAM_ERROR,
                       "状态值非法：1 正常 / 2 冻结 / 3 注销（停用请传 2）")
    try:
        status = int(str(raw).strip())
    except (TypeError, ValueError):
        # 前端传脏值（"abc"/"冻结"）也要 400 并带上口径提示，不能变 500
        raise ApiError(BizCode.PARAM_ERROR,
                       "状态值非法：1 正常 / 2 冻结 / 3 注销（停用请传 2）")
    if status not in USER_STATUS_NAME:
        raise ApiError(BizCode.PARAM_ERROR, f"未知状态：{status}")
    if status == 3:
        if _is_admin(target):
            raise ApiError(BizCode.FORBIDDEN, "平台管理员账号不能注销")
        target.deleted = 1
    else:
        target.deleted = 0
    target.status = status
    target.frozen_reason = (d.get("reason") or "")[:255] if status == 2 else None
    db.session.commit()
    return success({"id": target.id, "status": target.status,
                    "statusName": USER_STATUS_NAME.get(target.status)}, "状态已更新")


@bp.put("/users/<int:uid>/password")
@perm_required("system:user")
def reset_password(uid):
    """重置密码：管理员可重置所有人；商家管理员只能重置本商家员工。"""
    import bcrypt

    operator = current_user()
    target = _load_target(uid)
    _assert_manageable(operator, target, "重置密码")
    d = request.get_json(silent=True) or {}
    new_pw = d.get("password") or "Admin@123"
    if len(new_pw) < 8:
        raise ApiError(BizCode.PARAM_ERROR, "密码至少 8 位")
    target.password_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt(10)).decode()
    db.session.commit()
    return success({"id": target.id}, "密码已重置")


@bp.put("/users/<int:uid>/roles")
@perm_required("system:role")
def set_user_roles(uid):
    """分配角色（M-81 账号详情）：不能给自己改角色，平台岗位需 system:role。"""
    operator = current_user()
    target = _load_target(uid)
    _assert_manageable(operator, target, "分配角色")
    d = request.get_json(silent=True) or {}
    role_ids = [_int_or_400(x, "角色 ID") for x in (d.get("roleIds") or [])]
    if not role_ids:
        raise ApiError(BizCode.PARAM_ERROR, "至少选择一个角色")
    roles = db.session.query(Role).filter(Role.id.in_(role_ids), Role.deleted == 0).all()
    if len(roles) != len(role_ids):
        raise ApiError(BizCode.PARAM_ERROR, "存在无效角色")
    held = {r.role_code for r in roles}
    if held - MERCHANT_ROLES and not scope.is_platform(operator):
        raise ApiError(BizCode.FORBIDDEN, "只有平台侧账号可以分配平台岗位", 403)

    db.session.execute(user_role.delete().where(user_role.c.user_id == target.id))
    for r in roles:
        db.session.execute(user_role.insert().values(
            user_id=target.id, role_id=r.id, granted_by=operator.id))
    # 角色变了，用户类型跟着岗位走，避免出现"机长却是平台身份"
    codes = {r.role_code for r in roles}
    target.user_type = next((ROLE_USER_TYPE[c] for c in codes
                             if c in ROLE_USER_TYPE), target.user_type)
    db.session.commit()
    return success({"id": target.id, "roles": sorted(codes)}, "角色已更新")


# ------------------------------------------------------------------
# 2. 角色与权限维护（M-83，system:role）
# ------------------------------------------------------------------
def _perm_tree() -> list[dict]:
    rows = db.session.query(Permission).filter(Permission.deleted == 0).order_by(
        Permission.sort, Permission.id).all()
    nodes = {p.id: {"id": p.id, "parentId": p.parent_id, "permCode": p.perm_code,
                    "permName": p.perm_name, "permType": p.perm_type,
                    "path": p.path, "children": []} for p in rows}
    tree = []
    for n in nodes.values():
        parent = nodes.get(n["parentId"])
        (parent["children"] if parent else tree).append(n)
    return tree


@bp.get("/roles")
@perm_required("system:role")
def list_roles():
    rows = db.session.query(Role).filter(Role.deleted == 0).order_by(Role.sort, Role.id).all()
    counts = dict(db.session.execute(db.text(
        "SELECT role_id, COUNT(*) FROM role_permission GROUP BY role_id")).all())
    user_counts = dict(db.session.execute(db.text(
        "SELECT role_id, COUNT(*) FROM user_role GROUP BY role_id")).all())
    return success({"list": [{
        "id": r.id, "roleCode": r.role_code, "roleName": r.role_name,
        "roleScope": r.role_scope, "builtIn": r.built_in, "remark": r.remark,
        "permCount": counts.get(r.id, 0), "userCount": user_counts.get(r.id, 0),
    } for r in rows], "total": len(rows)})


@bp.get("/permissions")
@perm_required("system:role")
def permissions():
    return success({"tree": _perm_tree()})


@bp.get("/roles/<int:rid>/permissions")
@perm_required("system:role")
def role_permissions(rid):
    role = db.session.get(Role, rid)
    if role is None or role.deleted:
        raise ApiError(BizCode.NOT_FOUND, "角色不存在")
    ids = [row[0] for row in db.session.execute(db.text(
        "SELECT permission_id FROM role_permission WHERE role_id=:r"), {"r": rid}).all()]
    return success({"roleId": rid, "roleCode": role.role_code, "permissionIds": ids})


@bp.put("/roles/<int:rid>/permissions")
@perm_required("system:role")
def update_role_permissions(rid):
    """整组替换角色权限：内置平台管理员角色不可改，也不能改自己所属的角色（防自锁）。"""
    operator = current_user()
    role = db.session.get(Role, rid)
    if role is None or role.deleted:
        raise ApiError(BizCode.NOT_FOUND, "角色不存在")
    if role.role_code == "platform_admin":
        raise ApiError(BizCode.FORBIDDEN, "内置平台管理员角色的权限不允许修改", 403)
    if role.role_code in operator.role_codes:
        raise ApiError(BizCode.FORBIDDEN,
                       f"不能修改自己所属角色「{role.role_name}」的权限，请用其他管理员账号操作", 403)

    d = request.get_json(silent=True) or {}
    ids = sorted({_int_or_400(x, "权限 ID") for x in (d.get("permissionIds") or [])})
    if not ids:
        raise ApiError(BizCode.PARAM_ERROR, "权限集合不能为空")
    valid = {row[0] for row in db.session.execute(db.text(
        "SELECT id FROM permission WHERE deleted=0")).all()}
    unknown = [i for i in ids if i not in valid]
    if unknown:
        raise ApiError(BizCode.PARAM_ERROR, f"存在无效权限点：{unknown[:5]}")
    # 勾了子权限必须带上父菜单，否则动态菜单树会挂空
    id_set = set(ids)
    parents = db.session.query(Permission.id, Permission.parent_id).filter(
        Permission.id.in_(id_set), Permission.deleted == 0).all()
    extra = {pid for _, pid in parents if pid and pid not in id_set}
    ids = sorted(id_set | extra)

    db.session.execute(role_permission.delete().where(role_permission.c.role_id == rid))
    for pid in ids:
        db.session.execute(role_permission.insert().values(role_id=rid, permission_id=pid))
    db.session.commit()
    return success({"roleId": rid, "permCount": len(ids)}, "角色权限已保存")


@bp.post("/roles")
@perm_required("system:role")
def create_role():
    d = request.get_json(silent=True) or {}
    code = (d.get("roleCode") or "").strip()
    name = (d.get("roleName") or "").strip()
    if not code or not name:
        raise ApiError(BizCode.PARAM_ERROR, "角色编码与名称必填")
    # role_code 上是唯一索引：软删掉的角色同样占着编码，只查 deleted=0 会撞 1062 变成 500
    if Role.query.filter_by(role_code=code).first():
        raise ApiError(BizCode.CONFLICT, "角色编码已存在（含已删除角色，请换一个编码）")
    # 先校验再落库，避免脏 ID 让角色建出来却缺权限半截
    perm_ids = sorted({_int_or_400(pid, "权限 ID") for pid in (d.get("permissionIds") or [])})
    if perm_ids:
        valid = {row[0] for row in db.session.execute(db.text(
            "SELECT id FROM permission WHERE deleted=0")).all()}
        unknown = [i for i in perm_ids if i not in valid]
        if unknown:
            raise ApiError(BizCode.PARAM_ERROR, f"存在无效权限点：{unknown[:5]}")

    r = Role(role_code=code, role_name=name,
             role_scope=_int_or_400(d.get("roleScope") or 2, "角色范围"), built_in=0,
             sort=_int_or_400(d.get("sort") or 50, "排序值"), remark=d.get("remark"))
    db.session.add(r)
    db.session.flush()
    for pid in perm_ids:
        db.session.execute(role_permission.insert().values(role_id=r.id, permission_id=pid))
    db.session.commit()
    return success({"id": r.id, "roleCode": r.role_code}, "角色已创建")


@bp.delete("/roles/<int:rid>")
@perm_required("system:role")
def delete_role(rid):
    role = db.session.get(Role, rid)
    if role is None or role.deleted:
        raise ApiError(BizCode.NOT_FOUND, "角色不存在")
    if role.built_in:
        raise ApiError(BizCode.FORBIDDEN, "内置角色不允许删除", 403)
    used = db.session.execute(db.text(
        "SELECT COUNT(*) FROM user_role WHERE role_id=:r"), {"r": rid}).scalar()
    if used:
        raise ApiError(BizCode.CONFLICT, f"该角色下还有 {used} 个账号，请先解绑")
    db.session.execute(role_permission.delete().where(role_permission.c.role_id == rid))
    role.deleted = 1
    db.session.commit()
    return success(message="角色已删除")


# ------------------------------------------------------------------
# 3. 只给登录页用的角色下拉（避免前端写死）
# ------------------------------------------------------------------
@bp.get("/role-options")
@jwt_required()
def role_options():
    """账号创建用：商家侧账号只看到本商家岗位，平台侧看全部。"""
    user = current_user()
    rows = db.session.query(Role).filter(Role.deleted == 0).order_by(Role.sort).all()
    if not user.has_perm("system:role"):
        rows = [r for r in rows if r.role_code in MERCHANT_ROLES]
    return success({"list": [{"id": r.id, "roleCode": r.role_code, "roleName": r.role_name}
                             for r in rows],
                    "total": len(rows)})


# ------------------------------------------------------------------
# 4. 商家入驻与审核（M-82，需求 4.2.1 运营方准入）
# ------------------------------------------------------------------
MERCHANT_AUDIT_PERM = "merchant:audit"
AUDIT_STATUS_NAME = {0: "待提交", 1: "审核中", 2: "已通过", 3: "已驳回", 4: "停用注销"}
PRICING_DEFAULT = {"basePerKm": 3.5, "perKg": 1.2, "urgentRate": 1.5}


def _merchant_ser(m: Merchant, viewer: User) -> dict:
    """企业联系电话是 PII：只有审核侧解密，申请人自己看脱敏值就够。"""
    return {
        "id": m.id, "merchantNo": m.merchant_no, "merchantName": m.merchant_name,
        "unifiedCreditCode": m.unified_credit_code, "legalPerson": m.legal_person,
        "contactPhone": (decrypt_phone(m.contact_phone_cipher) if scope.is_platform(viewer)
                         else mask_phone(m.contact_phone_cipher)) if m.contact_phone_cipher else None,
        "contactUserId": m.contact_user_id,
        "serviceDistricts": (m.service_area_json or {}).get("districts")
                            if isinstance(m.service_area_json, dict) else (m.service_area_json or []),
        "pricing": m.pricing_json,
        "auditStatus": m.audit_status, "auditStatusName": AUDIT_STATUS_NAME.get(m.audit_status),
        "auditOpinion": m.audit_opinion,
        "auditedAt": m.audited_at.strftime("%Y-%m-%d %H:%M:%S") if m.audited_at else None,
        "expireAt": m.expire_at.strftime("%Y-%m-%d") if m.expire_at else None,
        "status": m.status,
        "createdAt": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else None,
    }


def _parse_expire(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "").replace("/", "-"))
    except ValueError:
        raise ApiError(BizCode.PARAM_ERROR, "资质到期时间格式不正确（应为 YYYY-MM-DD）")


@bp.post("/merchants/apply")
@jwt_required()
def merchant_apply():
    """入驻申请（客户账号自助）：提交即进审核中，通过后申请人才变成商家管理员。

    驳回过的同一张统一社会信用代码**复用原行**回到审核中，不新插一行：
    否则企业改材料重提一次就多一条历史记录，`uk_credit_code` 又会把第二次提交撞成 500。
    """
    user = current_user()
    if user.user_type not in (1, 2):
        raise ApiError(BizCode.FORBIDDEN, "只有客户账号能提交入驻申请，商家/平台账号请在后台维护企业信息", 403)
    d = request.get_json(silent=True) or {}
    name = (d.get("merchantName") or "").strip()
    code = (d.get("unifiedCreditCode") or "").strip().upper()
    legal = (d.get("legalPerson") or "").strip()
    phone = re.sub(r"\D", "", str(d.get("contactPhone") or ""))
    if not name or not legal:
        raise ApiError(BizCode.PARAM_ERROR, "企业名称与法人代表必填")
    if len(code) != 18:
        raise ApiError(BizCode.PARAM_ERROR, "统一社会信用代码必须是 18 位")
    if len(phone) != 11:
        raise ApiError(BizCode.PARAM_ERROR, "联系电话必须是 11 位手机号")

    pending = (db.session.query(Merchant)
               .filter(Merchant.contact_user_id == user.id, Merchant.deleted == 0,
                       Merchant.audit_status.in_((0, 1)))
               .order_by(Merchant.id.desc()).first())
    if pending:
        raise ApiError(BizCode.CONFLICT,
                       f"「{pending.merchant_name}」正在审核中，请等待平台处理", 409)
    resubmit = (db.session.query(Merchant)
                .filter_by(unified_credit_code=code, deleted=0).first())
    if resubmit and resubmit.audit_status != 3:
        raise ApiError(BizCode.CONFLICT, "该企业统一社会信用代码已被占用", 409)
    if resubmit and resubmit.contact_user_id != user.id:
        raise ApiError(BizCode.CONFLICT, "该企业资质已被其他账号提交过申请", 409)

    districts = [str(x) for x in (d.get("serviceDistricts") or [])][:20]
    pricing = {k: float((d.get("pricing") or {}).get(k, v)) for k, v in PRICING_DEFAULT.items()}
    if resubmit:
        resubmit.merchant_name, resubmit.legal_person = name[:100], legal[:50]
        resubmit.contact_phone_cipher = encrypt_phone(phone)
        resubmit.service_area_json = {"districts": districts} if districts else None
        resubmit.pricing_json = pricing
        resubmit.expire_at = _parse_expire(d.get("expireAt"))
        resubmit.audit_status, resubmit.audit_opinion = 1, None
        resubmit.audited_by = resubmit.audited_at = None
        merchant = resubmit
    else:
        merchant = Merchant(
            merchant_no=f"MH{datetime.now():%Y%m%d}{random.randint(0, 9999):04d}",
            merchant_name=name[:100], unified_credit_code=code, legal_person=legal[:50],
            contact_user_id=user.id, contact_phone_cipher=encrypt_phone(phone),
            service_area_json={"districts": districts} if districts else None,
            pricing_json=pricing, expire_at=_parse_expire(d.get("expireAt")),
            audit_status=1, status=1)
        db.session.add(merchant)
    db.session.flush()
    notify.notify_many(scope.user_ids_with_perm(MERCHANT_AUDIT_PERM),
                       "新的商家入驻申请待审核",
                       f"{merchant.merchant_name}（信用代码 {merchant.unified_credit_code}，"
                       f"法人 {merchant.legal_person}）提交了入驻申请，"
                       "请核验资质后决定放行或驳回。",
                       biz_type="merchant_audit", biz_id=merchant.id)
    db.session.commit()
    return success(_merchant_ser(merchant, user), "申请已提交，平台会在资质核验后通知您")


@bp.get("/merchants/mine")
@jwt_required()
def merchant_mine():
    """我提交过的入驻申请（最新一条）：门户入驻页刷新状态用。"""
    user = current_user()
    m = (db.session.query(Merchant)
         .filter(Merchant.contact_user_id == user.id, Merchant.deleted == 0)
         .order_by(Merchant.id.desc()).first())
    if m is None:
        return success(None)
    return success(_merchant_ser(m, user))


@bp.get("/merchants")
@perm_required(MERCHANT_AUDIT_PERM)
def list_merchants():
    """入驻审核台：默认看待审核，可按状态/名称/信用代码筛。"""
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("pageSize", 10)), 1), 100)
    q = db.session.query(Merchant).filter(Merchant.deleted == 0)
    status = request.args.get("auditStatus")
    if status not in (None, ""):
        q = q.filter(Merchant.audit_status == _int_or_400(status, "审核状态"))
    keyword = (request.args.get("keyword") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(Merchant.merchant_name.like(like), Merchant.merchant_no.like(like),
                         Merchant.unified_credit_code.like(like.upper())))
    total = q.count()
    rows = (q.order_by(Merchant.audit_status.asc(), Merchant.id.desc())
            .limit(page_size).offset((page - 1) * page_size).all())
    return success({"total": total, "page": page, "pageSize": page_size,
                    "list": [_merchant_ser(m, current_user()) for m in rows]})


@bp.post("/merchants/<int:mid>/audit")
@perm_required(MERCHANT_AUDIT_PERM)
def audit_merchant(mid):
    """入驻审核：通过才进承接池，驳回必须给原因。

    通过这一步做了三件事，缺一不可，否则"审核通过"只是个绿灯：
      1. `audit_status=2` —— `pricing_service.get_demo_merchant()` 只认这个值，
         未通过的商家拿不到订单；
      2. 把申请人绑到该商家并转成商家身份（user_type=3 + merchant_admin 角色），
         否则 `scope.merchant_scope()` 对这个账号返回 None，他一登录就是全量数据视野；
      3. 站内信告知结果 —— 需求 4.2.1 要求"审核结果通知申请方"。
    """
    user = current_user()
    m = db.session.get(Merchant, mid)
    if m is None or m.deleted:
        raise ApiError(BizCode.NOT_FOUND, "商家不存在")
    if m.audit_status != 1:
        raise ApiError(BizCode.CONFLICT,
                       f"该申请当前为「{AUDIT_STATUS_NAME.get(m.audit_status)}」，不能重复审核", 409)
    d = request.get_json(silent=True) or {}
    result = _int_or_400(d.get("result"), "审核结论（1通过 2驳回）")
    if result not in (1, 2):
        raise ApiError(BizCode.PARAM_ERROR, "审核结论只能是 1 通过或 2 驳回")
    opinion = (d.get("opinion") or "").strip()
    if result == 2 and not opinion:
        raise ApiError(BizCode.PARAM_ERROR, "驳回必须填写原因（会通知申请方）")

    m.audit_status = 2 if result == 1 else 3
    m.audit_opinion = opinion[:500] if opinion else (
        "资质核验通过，已纳入运营承接范围" if result == 1 else None)
    m.audited_by, m.audited_at = user.id, datetime.now()
    applicant = db.session.get(User, m.contact_user_id) if m.contact_user_id else None
    if result == 1 and applicant and not applicant.merchant_id:
        applicant.merchant_id = m.id
        applicant.user_type = 3
        role = db.session.query(Role).filter_by(role_code="merchant_admin", deleted=0).first()
        held = db.session.execute(user_role.select().where(
            user_role.c.user_id == applicant.id,
            user_role.c.role_id == role.id)).first() if role else None
        if role and not held:
            # 只补不删：申请人原本的"客户"角色留着，他下单寄件的身份和接单的身份并不互斥
            db.session.execute(user_role.insert().values(
                user_id=applicant.id, role_id=role.id, granted_by=user.id))
    notify.notify(m.contact_user_id or 0,
                  f"商家入驻申请{'已通过' if result == 1 else '被驳回'}：{m.merchant_name}",
                  (f"资质核验通过，您已是「{m.merchant_name}」的商家管理员，"
                   "可以登录商家工作台接单与调度了。"
                   if result == 1 else
                   f"驳回原因：{opinion}。补齐材料后可在同一入口重新提交。"),
                  biz_type="merchant_audit", biz_id=m.id)
    db.session.commit()
    return success(_merchant_ser(m, user), "审核完成")
