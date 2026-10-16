"""统一响应信封（设计03 全局约定）：所有接口都返回 {code, message, data}。

- 成功：code=0
- 业务错误：code 为非 0 业务码（见 BizCode），HTTP 状态尽量仍为 200，前端只看 code
- 未登录/无权限等用标准 HTTP 状态，但 body 仍是同一信封
"""
from flask import jsonify


class BizCode:
    SUCCESS = 0
    # 通用 4xxxx
    PARAM_ERROR = 40000        # 参数校验失败
    UNAUTHORIZED = 40100       # 未登录/token 失效
    FORBIDDEN = 40300          # 已登录但无权限
    NOT_FOUND = 40400          # 资源不存在
    CONFLICT = 40900           # 状态冲突（如非法状态跳转）
    # 业务 5xxxx
    BIZ_ERROR = 50000
    THIRD_PARTY_ERROR = 50300  # 外部 Mock/真实服务异常


def success(data=None, message="ok"):
    """成功响应。"""
    return jsonify({"code": BizCode.SUCCESS, "message": message, "data": data})


def error(code, message, http_status=200, data=None):
    """错误响应（信封统一）。"""
    return jsonify({"code": code, "message": message, "data": data}), http_status
