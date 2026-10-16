"""D2 订单蓝图：试算 / 下单 / 列表 / 详情 / 支付 / 取消 / 确认收货 / 导出 / 批量导入 / 账单。"""
import csv
import io

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.common.decorators import current_user, perm_required
from app.common.errors import ApiError
from app.common.export import csv_response
from app.common.response import BizCode, success
from app.services import coupon_service, order_import, order_service, pricing_service

bp = Blueprint("orders", __name__, url_prefix="/api")

# 管理端列表/导出共用一套查询参数：两边条件不一样，导出就成了另一个系统
FILTER_ARGS = ("status", "keyword", "start", "end", "deliveryType",
               "orderType", "merchantId", "paid", "goodsType")


def _paging() -> tuple[int, int]:
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("pageSize", 10)), 1), 100)
    return page, page_size


def _filters() -> dict:
    out = {}
    for key in FILTER_ARGS:
        snake = {"deliveryType": "delivery_type", "orderType": "order_type",
                 "merchantId": "merchant_id", "goodsType": "goods_type"}.get(key, key)
        out[snake] = request.args.get(key)
    return out


def _reader():
    """订单读门：客户按登录态放行，员工侧另需 order:list。

    为什么不能直接挂 @perm_required("order:list")：客户角色一个权限点都没有，
    门户那几页（我的订单/订单详情/账单里的订单卡）会整片 403。
    但只有登录态也不对：_scope_query 对平台岗位（空管、运维）和机长是全量放开的，
    这三口又在 SELF 白名单里不进权限矩阵，结果是他们直接敲 GET /api/orders
    就能拿到整张客户台账（含收发货人手机号与地址）——管控台侧边栏没给他们这一页，
    只是没显示，不是拿不到。审批页要的只是航线要素，走的是计划详情自己的出参，
    不依赖这条口，所以这里收紧不影响空管干活。
    """
    user = current_user()
    if user.user_type not in (1, 2) and not user.has_perm("order:list"):
        raise ApiError(BizCode.FORBIDDEN, "没有操作权限：order:list", 403)
    return user


@bp.post("/orders/estimate")
@jwt_required()
def estimate():
    body = request.get_json(silent=True) or {}
    data = pricing_service.estimate(
        sender_lng=body.get("senderLng"), sender_lat=body.get("senderLat"),
        receiver_lng=body.get("receiverLng"), receiver_lat=body.get("receiverLat"),
        weight_kg=body.get("weightKg", 0),
        delivery_type=int(body.get("deliveryType", 1)),
        goods_type=body.get("goodsType", "文件"), goods_name=body.get("goodsName"),
    )
    # couponId 传的是「我的券包」里那张券的 id（user_coupon.id），不是券模板 id：
    # 同一张模板券人人都有，只有券包 id 才分得清是谁的那一张。
    # 试算与下单走同一个 discount_for，页面显示的减价和最后扣的钱是同一个口径。
    if body.get("couponId"):
        data, _cp = coupon_service.discount_for(
            data, current_user().id, int(body["couponId"]))
    return success(data, "试算成功")


@bp.post("/orders")
@jwt_required()
def create_order():
    body = request.get_json(silent=True) or {}
    order = order_service.create_order(current_user(), body)
    return success(order_service.to_order_dict(order, current_user()), "下单成功")


@bp.get("/orders")
@jwt_required()
def list_orders():
    user = _reader()
    page, page_size = _paging()
    data = order_service.list_orders(user, page=page, page_size=page_size, **_filters())
    return success(data)


EXPORT_COLUMNS = [
    ("订单号", lambda r: r["orderNo"]), ("状态", lambda r: r["statusName"]),
    ("配送类型", lambda r: {1: "普通", 2: "加急"}.get(r["deliveryType"], "")),
    ("订单类型", lambda r: {1: "普通配送", 2: "应急配送"}.get(r["orderType"], "其他")),
    ("物品类型", lambda r: r["goodsType"]), ("物品名称", lambda r: r["goodsName"] or ""),
    ("重量kg", lambda r: r["weightKg"]),
    ("寄件人", lambda r: r["senderName"]), ("寄件人电话", lambda r: r["senderPhone"]),
    ("寄件地址", lambda r: r["senderAddress"]),
    ("收件人", lambda r: r["receiverName"]), ("收件人电话", lambda r: r["receiverPhone"]),
    ("收件地址", lambda r: r["receiverAddress"]),
    ("应付合计", lambda r: r["totalAmount"]), ("实付", lambda r: r["payAmount"]),
    ("支付方式", lambda r: r["payMethodName"] or ""),
    ("商家ID", lambda r: r["merchantId"]), ("客户ID", lambda r: r["customerId"]),
    ("下单时间", lambda r: r["createdAt"]), ("支付时间", lambda r: r["paidAt"] or ""),
    ("送达时间", lambda r: r["deliveredAt"] or ""), ("完成时间", lambda r: r["finishedAt"] or ""),
    ("送达用时分钟", lambda r: "" if r["deliveryMinutes"] is None else r["deliveryMinutes"]),
    ("取消或终止原因", lambda r: r["cancelReason"] or ""), ("备注", lambda r: r["remark"] or ""),
]


@bp.get("/orders/filter-options")
@jwt_required()
def order_filter_options():
    """筛选栏候选值（当前身份看得见的那些订单里出现过的商家/品类）。"""
    return success(order_service.filter_options(_reader()))


@bp.get("/orders/export")
@perm_required("order:export")
def export_orders():
    """按当前筛选条件导出 CSV（最多 5000 行，服务端收口数据范围）。

    单独设 order:export 权限点而不蹭 order:list：导出是"把姓名+电话+地址成批搬出系统"，
    和在工作台翻一页不是一个量级。导出口径 = 列表口径，同一个 _filters()。
    """
    data = order_service.export_orders(current_user(), **_filters())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([cn for cn, _f in EXPORT_COLUMNS])
    for row in data["list"]:
        w.writerow([f(row) for _cn, f in EXPORT_COLUMNS])
    resp = csv_response(buf.getvalue(), "订单明细.csv")
    # 自定义响应头要给前端提示"只导了一部分"，得显式 expose，否则浏览器读不到
    resp.headers["Access-Control-Expose-Headers"] = "X-Export-Count,X-Export-Truncated"
    resp.headers["X-Export-Count"] = str(data["count"])
    resp.headers["X-Export-Truncated"] = "1" if data["truncated"] else "0"
    return resp


@bp.get("/orders/batch/template")
@perm_required("order:create")
def batch_template():
    """M-23 批量导入模板下载（列名与解析器一一对应，改列名会被拒）。"""
    return csv_response(order_import.template_csv(), "批量导入下单模板.csv")


@bp.post("/orders/batch")
@perm_required("order:create")
def batch_create():
    """M-23 批量导入下单：上传 CSV，逐行校验并落单。

    dryRun=1 只校验不落库（先看看这份文件行不行，再决定下不下）。
    """
    f = request.files.get("file")
    if f is None or not f.filename:
        raise ApiError(BizCode.PARAM_ERROR, "请选择要导入的 CSV 文件")
    dry_run = str(request.form.get("dryRun", "")) in ("1", "true", "True")
    data = order_import.import_orders(current_user(), order_import.decode(f.read()),
                                      validate_only=dry_run)
    tip = ("校验完成" if dry_run else "导入完成") + \
        f"：{data['succeeded']} 行可下单，{data['failed']} 行有问题"
    return success(data, tip)


@bp.get("/orders/<int:order_id>")
@jwt_required()
def order_detail(order_id: int):
    order = order_service.get_visible_order(order_id, _reader())
    return success(order_service.to_order_dict(order, current_user()))


@bp.post("/orders/<int:order_id>/pay")
@jwt_required()
def pay_order(order_id: int):
    body = request.get_json(silent=True) or {}
    order = order_service.get_visible_order(order_id, current_user())
    order_service.pay_order(order, current_user(), int(body.get("payMethod", 1)))
    return success(None, "支付成功（Mock）")


@bp.post("/orders/<int:order_id>/cancel")
@jwt_required()
def cancel_order(order_id: int):
    body = request.get_json(silent=True) or {}
    order = order_service.get_visible_order(order_id, current_user())
    order_service.cancel_order(order, current_user(), body.get("reason"))
    return success(None, "订单已取消")


@bp.post("/orders/<int:order_id>/confirm")
@jwt_required()
def confirm_order(order_id: int):
    order = order_service.get_visible_order(order_id, current_user())
    order_service.confirm_receipt(order, current_user())
    return success(None, "已确认收货")


@bp.post("/orders/<int:order_id>/accept")
@jwt_required()
def accept_order(order_id: int):
    order = order_service.get_visible_order(order_id, current_user())
    order_service.staff_accept_order(order, current_user())
    return success(None, "已接单，订单进入待调度")


@bp.post("/orders/<int:order_id>/reject")
@jwt_required()
def reject_order(order_id: int):
    body = request.get_json(silent=True) or {}
    order = order_service.get_visible_order(order_id, current_user())
    order_service.staff_reject_order(order, current_user(), body.get("reason"))
    return success(None, "已拒单，已支付款项原路退回（Mock）")


@bp.post("/orders/<int:order_id>/force-cancel")
@perm_required("order:force_cancel")
def force_cancel_order(order_id: int):
    """需求 2.2.2-3-1 运营/平台强制终止：0~4 段唯一出口，已支付自动原路退款（Mock）。"""
    body = request.get_json(silent=True) or {}
    order = order_service.get_visible_order(order_id, current_user())
    order_service.force_terminate_order(order, current_user(), body.get("reason"))
    return success(None, "已强制终止，关联调度方案一并作废，已支付款项原路退回（Mock）")


@bp.get("/bills/payments")
@jwt_required()
def bill_payments():
    page, page_size = _paging()
    return success(order_service.list_bills(current_user(), page=page, page_size=page_size))


@bp.get("/bills/settlements")
@jwt_required()
def bill_settlements():
    page, page_size = _paging()
    return success(order_service.list_settlements(current_user(), page=page, page_size=page_size))
