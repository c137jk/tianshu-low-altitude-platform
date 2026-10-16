"""M-23 批量导入下单：模板 / 解析 / 逐行校验 / 落单。

为什么单独一个模块：批量这条路的"对错"和普通下单不一样——普通单一次一笔、失败就改；
批量是一份文件几十笔，必须逐行给结论，而且绝不能因为第 17 行填错把前 16 行
已经建好的单回滚掉（客户看到"导入失败"以为没下成，其实库里躺着 16 单）。
所以每一行独立提交、独立报错，汇总里一行一条。

三道闸和普通下单完全共用（都在 pricing_service.estimate / order_service.create_order 里）：
禁运品、商家服务空域、禁飞区航线。批量导入不许有自己的校验逻辑，
否则就出现"页面上进不去的单，改成 CSV 就能进来"。

为什么是 CSV 而不是 xlsx：需求原文写"Excel/模板"，CSV 用 Excel/WPS 双击即是表格，
而读 xlsx 要往运行时里加一个 openpyxl 依赖（后端 + 前端各一份）。演示环境不值当，
口径记录在设计决策 D-81。
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

from app.common.errors import ApiError
from app.common.response import BizCode
from app.extensions import db
from app.models import User
from app.services import order_service, pricing_service

MAX_ROWS = 200

# (字段键, 列名, 是否必填, 提示)——提示直接写进表头，模板就是唯一口径
COLUMNS = [
    ("senderName", "寄件人姓名", True, "不超过50字"),
    ("senderPhone", "寄件人电话", True, "11位手机号"),
    ("senderAddress", "寄件地址", True, ""),
    ("senderLng", "寄件经度", True, "GCJ-02 例120.929500"),
    ("senderLat", "寄件纬度", True, "GCJ-02 例27.955500"),
    ("receiverName", "收件人姓名", True, "不超过50字"),
    ("receiverPhone", "收件人电话", True, "11位手机号"),
    ("receiverAddress", "收件地址", True, ""),
    ("receiverLng", "收件经度", True, "GCJ-02 例120.815000"),
    ("receiverLat", "收件纬度", True, "GCJ-02 例27.945000"),
    ("goodsType", "物品类型", True, "不得含禁运品"),
    ("goodsName", "物品名称", False, "可空"),
    ("weightKg", "重量kg", True, "数字 例1.5"),
    ("deliveryType", "配送类型", False, "1普通 2加急，默认1"),
    ("expectAt", "预约时间", False, "2026-09-30 14:30，空=即时"),
    ("remark", "备注", False, "可空"),
]
LABELS = {k: label for k, label, _req, _hint in COLUMNS}
REQUIRED_KEYS = [k for k, _l, req, _h in COLUMNS if req]
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
DELIVERY_ALIAS = {"1": 1, "2": 2, "普通": 1, "加急": 2}


def _header_of(key: str, label: str, required: bool, hint: str) -> str:
    return f"{label}{'*' if required else ''}（{hint}）" if hint else f"{label}{'*' if required else ''}"


HEADER_MAP = {}
for _k, _l, _r, _h in COLUMNS:
    HEADER_MAP[_k] = _k
    HEADER_MAP[_l] = _k
    HEADER_MAP[_l + ("*" if _r else "")] = _k
    HEADER_MAP[_header_of(_k, _l, _r, _h)] = _k


def template_csv() -> str:
    """下载模板：只有表头一行，不带示例数据行。

    不塞示例行是刻意的——示例行会被连文件一起传上来，然后真的下成一单。
    格式提示全部写进表头括号里，一眼看得见又不会误当成数据。

    BOM 由 common/export.csv_response 统一加，这里不再拼一次（拼两遍 Excel 会看见两个 BOM）。
    """
    buf = io.StringIO()
    csv.writer(buf).writerow([_header_of(k, l, r, h) for k, l, r, h in COLUMNS])
    return buf.getvalue()


def decode(raw: bytes) -> str:
    """CSV 文本解码：Excel/WPS 另存的 CSV 既可能是 UTF-8 也可能是 GBK。

    不兜 GBK 的话，用 WPS 编辑过的模板传上来就是一堆乱码列名，
    系统报"表头无法识别"，客户根本想不到是自己保存格式的问题。
    """
    body = raw or b""
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def parse_rows(text: str) -> list[tuple[int, dict]]:
    """表头 + 数据行 → [(文件行号, {字段键: 值})]。列名不认识的直接报错，不猜列顺序。"""
    header: list[str | None] | None = None
    records: list[tuple[int, dict]] = []
    for lineno, cells in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not any(str(c).strip() for c in cells):
            continue
        if header is None:
            keys = [HEADER_MAP.get(str(c).strip().lstrip("\ufeff")) for c in cells]
            unknown = [str(c).strip() for c, k in zip(cells, keys) if k is None and str(c).strip()]
            if unknown:
                raise ApiError(BizCode.PARAM_ERROR,
                               f"表头无法识别（第 {lineno} 行）：{'、'.join(unknown[:5])}。"
                               "请先下载标准模板，不要改列名或调换列顺序")
            header = keys
            continue
        row = {k: (cells[i].strip() if i < len(cells) else "")
               for i, k in enumerate(header) if k}
        if not any(row.values()):
            continue
        if len(records) >= MAX_ROWS:
            raise ApiError(BizCode.PARAM_ERROR,
                           f"一次最多导入 {MAX_ROWS} 行，已超出，请拆分文件")
        records.append((lineno, row))
    if not records:
        raise ApiError(BizCode.PARAM_ERROR, "文件里只有表头，没有数据行")
    return records


def normalize(row: dict) -> dict:
    """CSV 字符串 → create_order 的入参。只管 CSV 特有的坑，业务校验交给下单链路。

    create_order/estimate 已经会查必填、禁运品、服务空域、禁飞区、重量>0；
    这里补的是"表格里一定有、单条表单里不会有的"三类：电话带空格横杠、
    数字被写成 1,5 或全角逗号、配送类型写成中文"加急"。
    """
    payload = dict(row)
    for field, who in (("senderPhone", "寄件人"), ("receiverPhone", "收件人")):
        phone = re.sub(r"[^\d]", "", payload.get(field) or "")
        if not PHONE_RE.match(phone):
            raise ApiError(BizCode.PARAM_ERROR, f"{who}电话「{payload.get(field)}」不是有效手机号")
        payload[field] = phone
    for field in ("weightKg", "senderLng", "senderLat", "receiverLng", "receiverLat"):
        raw = (payload.get(field) or "").replace(",", "").replace("，", "").strip()
        try:
            payload[field] = Decimal(raw)
        except InvalidOperation:
            raise ApiError(BizCode.PARAM_ERROR, f"{LABELS[field]}「{payload.get(field)}」不是数字")
    dt = (payload.get("deliveryType") or "1").strip()
    if dt not in DELIVERY_ALIAS:
        raise ApiError(BizCode.PARAM_ERROR, f"配送类型「{dt}」不合法（填 1普通 / 2加急）")
    payload["deliveryType"] = DELIVERY_ALIAS[dt]
    if payload.get("expectAt"):
        payload["expectAt"] = payload["expectAt"].replace(" ", "T")
    missing = [LABELS[k] for k in REQUIRED_KEYS if payload.get(k) in (None, "")]
    if missing:
        raise ApiError(BizCode.PARAM_ERROR, f"缺少必填列：{'、'.join(missing)}")
    return payload


def import_orders(customer: User, text: str, *, validate_only: bool = False) -> dict:
    """逐行下单；validate_only=True 只跑校验（含服务空域/禁飞区/禁运品），不落库。"""
    if customer.user_type not in (1, 2):
        raise ApiError(BizCode.FORBIDDEN, "只有客户账号能批量导入下单", 403)
    rows, ok, fail = [], 0, 0
    for lineno, raw in parse_rows(text):
        key = f"{raw.get('senderName') or '未填'} → {raw.get('receiverName') or '未填'}"
        try:
            payload = normalize(raw)
            if validate_only:
                quote = pricing_service.estimate(
                    sender_lng=payload["senderLng"], sender_lat=payload["senderLat"],
                    receiver_lng=payload["receiverLng"], receiver_lat=payload["receiverLat"],
                    weight_kg=payload["weightKg"], delivery_type=payload["deliveryType"],
                    goods_type=payload["goodsType"], goods_name=payload.get("goodsName"))
                rows.append({"line": lineno, "key": key, "ok": True, "reason": None,
                             "totalAmount": quote["totalAmount"], "distanceM": quote["distanceM"]})
            else:
                order = order_service.create_order(customer, payload)
                rows.append({"line": lineno, "key": key, "ok": True, "reason": None,
                             "orderId": order.id, "orderNo": order.order_no,
                             "totalAmount": float(order.total_amount)})
            ok += 1
        except ApiError as e:
            # 单行的业务失败不能带走整批：回滚这一行的半截对象，继续下一行
            db.session.rollback()
            rows.append({"line": lineno, "key": key, "ok": False, "reason": e.message})
            fail += 1
        except Exception as e:
            db.session.rollback()
            rows.append({"line": lineno, "key": key, "ok": False,
                         "reason": f"未预期错误：{type(e).__name__}"})
            fail += 1
    return {"total": len(rows), "succeeded": ok, "failed": fail,
            "validateOnly": bool(validate_only), "maxRows": MAX_ROWS, "rows": rows}
