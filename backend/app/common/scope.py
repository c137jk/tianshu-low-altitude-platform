"""数据范围与系统参数读取（P0 权限收口配套）。

用途：aux / settlement / resource 这些用 raw SQL 或全表查询的蓝图，
按登录身份收窄可见范围；系统参数从 sys_config 读，不再各处写死。
"""
import time

from flask import g

from app.extensions import db
from app.models import User
from app.models.rbac import Permission, Role, role_permission, user_role

_cfg_cache: dict[str, tuple[float, str | None]] = {}
_CFG_TTL = 10.0

PLATFORM_TYPES = (4, 5, 6)          # 平台管理员 / 空管 / 运维


def is_platform(user: User) -> bool:
    """平台侧身份（全量可见）。"""
    return user.user_type in PLATFORM_TYPES or "platform_admin" in user.role_codes


def merchant_scope(user: User) -> int | None:
    """商家员工返回本商家 id；平台/空管/运维返回 None（全量）。"""
    if user.user_type == 3:
        return user.merchant_id or 1
    return None


def merchant_filter(user: User, column: str = "merchant_id") -> tuple[str, dict]:
    """给 raw SQL 拼商家过滤片段。column 只允许代码里写死的列名。"""
    mid = merchant_scope(user)
    if mid is None:
        return "", {}
    return f" AND {column} = :__mid", {"__mid": mid}


def scope_orm(query, user: User, model):
    """ORM 查询按商家收窄（模型没有 merchant_id 列时原样返回）。"""
    mid = merchant_scope(user)
    if mid is None or not hasattr(model, "merchant_id"):
        return query
    return query.filter(model.merchant_id == mid)


def user_ids_with_perm(perm_code: str, merchant_id: int | None = None,
                       platform_only: bool = False) -> list[int]:
    """持有某权限点的账号 id —— 站内信没有角色订阅机制，"该通知谁"就按权限点查人。

    空域待审核只通知有空域审核权的人；广播（user_id=0）在 /aux/messages 里
    按 user_id 精确匹配，谁都读不到，不能当通知通道用。

    merchant_id 非空时再按商家收窄：A 商家的新订单不能通知到 B 商家的调度台，
    那既是一条噪声，也是一次跨商家的业务信息泄露（单量、品类都在标题里）。
    platform_only 取反：只要平台侧账号（merchant_id 为空）。设备告警就要两边都发——
    平台运维要全局知道，别家商家的设备运维员不需要。
    """
    q = (db.session.query(user_role.c.user_id)
         .join(Role, Role.id == user_role.c.role_id)
         .join(role_permission, role_permission.c.role_id == Role.id)
         .join(Permission, Permission.id == role_permission.c.permission_id)
         .join(User, User.id == user_role.c.user_id)
         .filter(Permission.perm_code == perm_code, Permission.deleted == 0,
                 User.deleted == 0, User.status == 1))
    if platform_only:
        q = q.filter(User.merchant_id.is_(None))
    elif merchant_id is not None:
        q = q.filter(User.merchant_id == merchant_id)
    return [r[0] for r in q.distinct().all()]


def sys_cfg(key: str, default=None):
    """读 sys_config（10 秒 TTL 缓存）。缺键或解析失败回退 default。"""
    now = time.time()
    hit = _cfg_cache.get(key)
    if hit and now - hit[0] < _CFG_TTL:
        return hit[1]
    row = db.session.execute(
        db.text("SELECT config_value FROM sys_config WHERE config_key=:k AND deleted=0 LIMIT 1"),
        {"k": key}).scalar()
    _cfg_cache[key] = (now, row if row is not None else default)
    return _cfg_cache[key][1]


def sys_cfg_num(key: str, default, cast=float):
    """数值型参数，非法值回退默认，避免配置写错直接把服务搞崩。"""
    raw = sys_cfg(key, None)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def invalidate_cfg(key: str | None = None) -> None:
    """参数保存后清缓存，让改动立刻生效。"""
    if key:
        _cfg_cache.pop(key, None)
    else:
        _cfg_cache.clear()
