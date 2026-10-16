"""D1 认证蓝图：登录 / 当前用户 / 菜单树 / 续期 / 登出。"""
from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.common.decorators import current_user
from app.common.errors import ApiError
from app.common.response import BizCode, success
from app.services import auth_service

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _device_type() -> str:
    # 全局约定：请求头 Device-Type: PC|Mobile；也兼容登录体里的 deviceType
    return request.headers.get("Device-Type") or (request.get_json(silent=True) or {}).get("deviceType") or "PC"


@bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    data = auth_service.login(
        username=body.get("username", ""),
        password=body.get("password", ""),
        device_type=_device_type(),
        ip=request.remote_addr or "",
    )
    return success(data, "登录成功")


@bp.get("/me")
@jwt_required()
def me():
    user = current_user()
    return success({"userInfo": user.to_login_dict()})


@bp.get("/menus")
@jwt_required()
def menus():
    user = current_user()
    return success(auth_service.build_menu_tree(user))


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    """设计03 D1 契约里的 POST /auth/refresh：只认 refresh token，换回一对新 token。

    为什么还要现查一次账号状态：token 有效期内账号可能已被冻结/注销，
    续期是唯一的"重新发牌"时机，必须在这里挡住，否则冻结形同虚设。
    """
    user = current_user()
    if user.status != 1:
        raise ApiError(BizCode.FORBIDDEN, "账号状态异常，无法续期，请联系管理员", 403)
    return success(auth_service.issue_tokens(user, _device_type()), "登录态已续期")


@bp.post("/logout")
@jwt_required()
def logout():
    # JWT 无状态：前端丢弃 token 即完成登出；
    # W4 接 Redis 后可把过期前 token 拉入黑名单（预留）。
    return success(None, "已退出登录")


@bp.post("/change-password")
@jwt_required()
def change_password():
    body = request.get_json(silent=True) or {}
    auth_service.change_password(
        current_user(), body.get("oldPassword", ""), body.get("newPassword", "")
    )
    return success(None, "密码修改成功")



@bp.post("/register")
def register():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    phone = (body.get("phone") or "").strip()
    real_name = (body.get("realName") or "").strip()
    if not username or not password or not phone:
        from app.common.errors import ApiError, BizCode
        raise ApiError(BizCode.PARAM_ERROR, "账号、密码、手机号不能为空")
    if len(password) < 6:
        from app.common.errors import ApiError, BizCode
        raise ApiError(BizCode.PARAM_ERROR, "密码至少 6 位")
    import re
    if not re.fullmatch(r"1\d{10}", phone):
        from app.common.errors import ApiError, BizCode
        raise ApiError(BizCode.PARAM_ERROR, "手机号格式不正确（11位、1开头）")

    import bcrypt
    from app.extensions import db
    from app.models.user import User
    from app.models.rbac import Role
    from app.common.crypto import encrypt_phone, phone_digest

    if User.query.filter_by(username=username, deleted=0).first():
        from app.common.errors import ApiError, BizCode
        raise ApiError(BizCode.CONFLICT, "账号已存在")

    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(10)).decode()
    u = User(
        username=username, password_hash=pw_hash, user_type=1,
        real_name=real_name or username,
        phone_cipher=encrypt_phone(phone), phone_digest=phone_digest(phone),
        realname_status=0, status=1,
    )
    db.session.add(u)
    db.session.flush()
    role = Role.query.filter_by(role_code="customer").first()
    if role:
        from sqlalchemy import text
        db.session.execute(
            text("INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)"),
            {"u": u.id, "r": role.id})
    db.session.commit()
    return success({"id": u.id, "username": u.username}, "注册成功，请登录")
