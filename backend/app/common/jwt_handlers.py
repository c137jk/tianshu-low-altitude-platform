"""JWT 异常 → 统一响应信封。

不配置的话，Flask-JWT-Extended 默认回 422 JSON，结构和我们 {code,message,data} 不一致，
前端拦截器不好处理；这里统一转成 401 + 业务码 40100。
"""
from flask_jwt_extended import JWTManager

from app.common.response import BizCode, error


def register_jwt_handlers(jwt: JWTManager):

    @jwt.unauthorized_loader
    def _missing_token(reason):
        return error(BizCode.UNAUTHORIZED, "未登录或登录已失效，请重新登录", 401)

    @jwt.invalid_token_loader
    def _invalid_token(reason):
        return error(BizCode.UNAUTHORIZED, "登录凭证无效，请重新登录", 401)

    @jwt.expired_token_loader
    def _expired_token(jwt_header, jwt_payload):
        return error(BizCode.UNAUTHORIZED, "登录已过期，请重新登录", 401)

    @jwt.revoked_token_loader
    def _revoked_token(jwt_header, jwt_payload):
        return error(BizCode.UNAUTHORIZED, "登录已退出，请重新登录", 401)
