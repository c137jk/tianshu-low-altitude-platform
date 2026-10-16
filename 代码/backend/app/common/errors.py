"""全局异常与业务异常。

业务代码里想报错就 raise ApiError(BizCode.CONFLICT, "当前状态不能起飞")，
由这里的 handler 统一转成信封，不在每个视图里手写 try/except。
"""
from flask import Flask
from werkzeug.exceptions import HTTPException

from .response import BizCode, error


class ApiError(Exception):
    def __init__(self, code=BizCode.BIZ_ERROR, message="业务处理失败", http_status=200, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data


def register_error_handlers(app: Flask):
    @app.errorhandler(ApiError)
    def _handle_api_error(e: ApiError):
        return error(e.code, e.message, e.http_status, e.data)

    @app.errorhandler(HTTPException)
    def _handle_http_error(e: HTTPException):
        # 404/405 等也包成统一信封
        code_map = {401: BizCode.UNAUTHORIZED, 403: BizCode.FORBIDDEN, 404: BizCode.NOT_FOUND}
        if e.code == 413:
            # 上传超限由 Werkzeug 在进入视图前抛出，默认只有英文 "Request Entity Too Large"，
            # 前端拿到就是一句看不懂的提示 —— 这里给中文并说明上限。
            return error(BizCode.PARAM_ERROR, "文件过大，单次上传上限 10MB，请压缩后重试", 413)
        biz_code = code_map.get(e.code, BizCode.PARAM_ERROR)
        return error(biz_code, e.description or e.name, e.code or 500)

    @app.errorhandler(Exception)
    def _handle_unknown_error(e: Exception):
        # 开发期打印堆栈，生产环境只回笼统提示（避免泄露内部信息）
        app.logger.exception("未捕获异常: %s", e)
        if app.debug:
            return error(BizCode.BIZ_ERROR, f"服务器异常：{e}", 500)
        return error(BizCode.BIZ_ERROR, "服务器内部错误，请稍后再试", 500)
