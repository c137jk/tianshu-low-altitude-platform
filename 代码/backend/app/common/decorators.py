"""鉴权装饰器与当前用户读取。

用法：
    @bp.post("/xxx")
    @jwt_required()
    def view(): user = current_user() ...

    @bp.post("/plans/submit")
    @perm_required("dispatch:submit")
    def view(): ...
"""
from functools import wraps

from flask import g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.common.errors import ApiError
from app.common.response import BizCode
from app.extensions import db
from app.models import User


def current_user() -> User:
    """从 JWT 解析出当前用户对象（同一请求内只查一次库）。

    缓存键必须带上 JWT 的 sub：flask.g 挂在 app context 上，而不是 request 上。
    正常一个请求一个 app context 时没问题，但只要外层已经 push 过 app context
    （CLI 里发内部请求、测试里长驻上下文、中间件包住多个请求），
    只判断 "g 里有没有值" 就会把第一个访问者的身份复用给后面所有人 —— 等于越权。
    """
    uid = get_jwt_identity()
    cached = getattr(g, "current_user", None)
    if cached is not None and getattr(g, "current_user_uid", None) == str(uid):
        return cached
    user = db.session.query(User).filter_by(id=uid, deleted=0).first()
    if user is None:
        raise ApiError(BizCode.UNAUTHORIZED, "用户不存在或已注销", 401)
    g.current_user = user
    g.current_user_uid = str(uid)
    return user


def perm_required(perm_code: str):
    """接口级权限校验：先登录，再校验是否拥有该权限点。

    wrapper 上挂 req_perms / auth_kind，给"接口权限清单"和 pytest 门禁扫描用
    （tests/test_perms_matrix.py 靠它证明没有端点在裸奔）。
    """
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            user = current_user()
            if user.status != 1:
                raise ApiError(BizCode.FORBIDDEN, "账号状态异常，无法操作", 403)
            if not user.has_perm(perm_code):
                raise ApiError(BizCode.FORBIDDEN, f"没有操作权限：{perm_code}", 403)
            return fn(*args, **kwargs)
        wrapper.req_perms = (perm_code,)
        wrapper.auth_kind = "perm"
        wrapper.perm_mode = "all"
        return wrapper
    return decorator


def perm_any_required(*perm_codes: str):
    """任意一个权限点满足即放行（客服工单/资源台账这类多角色共用接口用）。"""
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            user = current_user()
            if user.status != 1:
                raise ApiError(BizCode.FORBIDDEN, "账号状态异常，无法操作", 403)
            if not any(user.has_perm(c) for c in perm_codes):
                raise ApiError(
                    BizCode.FORBIDDEN, f"没有操作权限：{' 或 '.join(perm_codes)}", 403)
            return fn(*args, **kwargs)
        wrapper.req_perms = tuple(perm_codes)
        wrapper.auth_kind = "perm"
        wrapper.perm_mode = "any"
        return wrapper
    return decorator


def staff_required(fn):
    """只要求"运营侧身份"（user_type 3~6）：客户账号一律 403。

    用在客户自助之外的运营读接口上，具体的数据范围由 scope.merchant_filter 收窄。
    """
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user = current_user()
        if user.user_type not in (3, 4, 5, 6):
            raise ApiError(BizCode.FORBIDDEN, "该接口仅运营侧可见", 403)
        return fn(*args, **kwargs)
    wrapper.req_perms = ()
    wrapper.auth_kind = "staff"
    return wrapper

