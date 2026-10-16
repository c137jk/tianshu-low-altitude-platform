"""CSV 下载响应（订单导出 / 批量导入模板 / D8 报表导出共用）。

BOM 只在这里加一次：调用方再各自拼一遍，Excel 看到的就是两个 BOM。
文件名走 RFC 5987，中文名才不会在 Safari/Edge 上变成一串 %。
"""
from urllib.parse import quote

from flask import Response


def csv_response(text: str, filename: str) -> Response:
    if not text.startswith("\ufeff"):
        text = "\ufeff" + text
    # mimetype 只给 text/csv：charset 由 Response 自动补，写两遍会变成 "charset=utf-8; charset=utf-8"
    resp = Response(text, mimetype="text/csv")
    resp.headers["Content-Disposition"] = (
        f"attachment; filename=\"{quote(filename)}\"; filename*=UTF-8''{quote(filename)}")
    return resp
