"""D1 认证领域服务：登录校验、发 JWT、组权限菜单树。

视图层只负责收发，业务规则都放这里（设计03 分层：blueprint → service → model）。
"""
from datetime import datetime, timedelta

import bcrypt
from flask_jwt_extended import create_access_token, create_refresh_token

from app.common.errors import ApiError
from app.common.response import BizCode
from app.extensions import db
from app.models import User, Permission

# 设计03 D1 契约：PC 8 小时 / 移动 2 小时；刷新 token 只用来换访问 token，7 天。
REFRESH_TOKEN_DAYS = 7


def login(username: str, password: str, device_type: str, ip: str) -> dict:
    """账号密码登录，返回 token + 用户信息 + 菜单树。"""
    if not username or not password:
        raise ApiError(BizCode.PARAM_ERROR, "请输入账号和密码")

    user = db.session.query(User).filter_by(username=username.strip(), deleted=0).first()
    # 安全原则：账号不存在和密码错误给同一句提示，不告诉对方账号是否存在
    if user is None or not user.check_password(password):
        # TODO 非功能要求：连续失败 5 次锁定（W6 接 Redis 计数，sys_config 阈值）
        raise ApiError(BizCode.UNAUTHORIZED, "账号或密码错误", 401)

    if user.status == 2:
        raise ApiError(BizCode.FORBIDDEN, f"账号已冻结：{user.frozen_reason or '请联系管理员'}", 403)
    if user.status == 3:
        raise ApiError(BizCode.FORBIDDEN, "账号已注销", 403)

    # 记录登录痕迹
    user.last_login_at = datetime.now()
    user.last_login_ip = ip
    db.session.commit()

    data = issue_tokens(user, device_type)
    data.update({"userInfo": user.to_login_dict(), "menus": build_menu_tree(user)})
    return data


def issue_tokens(user: User, device_type: str) -> dict:
    """签发一对 token：访问 token 短（PC 8h / 移动 2h），刷新 token 长（7 天）。

    单独抽出来是为了让 /auth/refresh 和登录走同一套 claims 与有效期口径，
    免得出现在"续期后的 token 少了 roles claim"这类只在上机时才暴露的偏差。
    """
    hours = 2 if device_type.upper() == "MOBILE" else 8
    claims = {"user_type": user.user_type, "roles": user.role_codes,
              "device": device_type.upper()}
    return {
        "token": create_access_token(identity=str(user.id), additional_claims=claims,
                                     expires_delta=timedelta(hours=hours)),
        "tokenType": "Bearer",
        "expireSeconds": hours * 3600,
        "refreshToken": create_refresh_token(
            identity=str(user.id), additional_claims=claims,
            expires_delta=timedelta(days=REFRESH_TOKEN_DAYS)),
        "refreshExpireSeconds": REFRESH_TOKEN_DAYS * 86400,
    }


def change_password(user: User, old_password: str, new_password: str) -> None:
    """C-7 修改密码：校验旧密码，新密码至少 6 位。"""
    if not old_password or not new_password:
        raise ApiError(BizCode.PARAM_ERROR, "请填写旧密码和新密码")
    if not user.check_password(old_password):
        raise ApiError(BizCode.PARAM_ERROR, "旧密码不正确")
    if len(new_password) < 6 or len(new_password) > 32:
        raise ApiError(BizCode.PARAM_ERROR, "新密码长度需为 6~32 位")
    if new_password == old_password:
        raise ApiError(BizCode.PARAM_ERROR, "新密码不能与旧密码相同")
    user.password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode()
    db.session.commit()


def build_menu_tree(user: User) -> list[dict]:
    """把用户拥有的权限点组装成前端动态菜单树（只组菜单 perm_type=1，动作权限平铺在 perms）。

    客户角色没有任何菜单权限 → 返回 []，前端据此直接进客户门户。
    """
    perms: list[Permission] = []
    seen = set()
    for role in user.roles:
        for p in role.permissions:
            if p.id not in seen and p.deleted == 0:
                seen.add(p.id)
                perms.append(p)

    menus = sorted((p for p in perms if p.perm_type == 1), key=lambda x: x.sort)
    tree = []
    for m in menus:
        children = sorted(
            (p for p in perms if p.parent_id == m.id and p.perm_type in (2, 3)),
            key=lambda x: x.sort,
        )
        tree.append({
            "id": m.id,
            "permCode": m.perm_code,
            "permName": m.perm_name,
            "path": m.path,
            "icon": m.icon,
            "children": [
                {"id": c.id, "permCode": c.perm_code, "permName": c.perm_name, "path": c.path}
                for c in children
            ],
        })
    return tree
