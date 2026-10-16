"""D2 订单领域服务：下单 / 支付 / 取消 / 确认收货 / 查询 / 导出 / 强制终止 / 账单。

状态机（设计02，客户侧只参与其中几段）：
  下单 → 0待支付 ─pay→ 1待接单（商家 W3 接单后继续流转）
  0/1 ─cancel→ 8已取消（已支付则 Mock 原路退款）
  1  ─商家拒单→ 8已取消（退款）
  1~4 ─平台/商家强制终止→ 8已取消（在途方案一并作废 + 退款）
  6已送达 ─confirm→ 7已完成（同时生成结算单）

每一段流转都往 `message_notification` 写一条（差距分析 §7：这张表过去只有读、没有写入）。
统一走 `_notify_party()`：收件人按"订单的另一端"算——客户动的动作通知商家侧，
商家动的动作通知客户本人，别把站内信写成自言自语。
"""
import random
from datetime import datetime, timedelta
from decimal import Decimal

from app.adapters.payment import payment_adapter, METHOD_NAME
from app.common import scope
from app.common.crypto import (decrypt_phone, encrypt_phone, mask_phone,
                               phone_digest, phone_tail_digest)
from app.common.errors import ApiError
from app.common.response import BizCode
from app.extensions import db
from app.models import Order, Settlement, PaymentTransaction, User
from app.services import coupon_service, notify, pricing_service

# 订单状态字典（前端有同口径副本 src/constants/dict.ts）
ORDER_STATUS = {
    0: "待支付", 1: "待接单", 2: "待调度", 3: "待审批", 4: "待起飞",
    5: "飞行中", 6: "已送达", 7: "已完成", 8: "已取消",
}
PAY_METHOD = {1: "微信支付", 2: "支付宝", 3: "对公转账"}
# 商家侧"谁该收到新单"：接单口只认登录 + user_type（没有独立权限点），
# 所以按"能看订单列表的人"找人 = 商家管理员 + 调度员，并按 merchant_id 收窄——
# A 商家的新单不能出现在 B 商家的铃铛里，标题带着品类和金额，那是一条跨商家信息泄露。
MERCHANT_ORDER_PERM = "order:list"

REQUIRED_FIELDS = ["senderName", "senderPhone", "senderAddress", "senderLng", "senderLat",
                   "receiverName", "receiverPhone", "receiverAddress", "receiverLng", "receiverLat",
                   "goodsType", "weightKg"]


def _amount(order: Order) -> str:
    return f"{Decimal(str(order.pay_amount or order.total_amount or 0)):.2f} 元"


def _notify_party(order: Order, title: str, content: str, to: str = "customer",
                  biz_type: str = "order_status"):
    """订单流转通知。to=customer 发给下单人本人；to=merchant 发给本商家能接单的账号。"""
    if to == "customer":
        notify.notify(order.customer_id, title, content, biz_type=biz_type, biz_id=order.id)
    else:
        notify.notify_many(
            scope.user_ids_with_perm(MERCHANT_ORDER_PERM, order.merchant_id),
            title, content, biz_type=biz_type, biz_id=order.id)


def _gen_no(prefix: str = "TS") -> str:
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 9999):04d}"


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "").replace("/", "-"))
    except ValueError:
        raise ApiError(BizCode.PARAM_ERROR, "预约时间格式不正确")


def create_order(customer: User, payload: dict) -> Order:
    missing = [f for f in REQUIRED_FIELDS if payload.get(f) in (None, "")]
    if missing:
        raise ApiError(BizCode.PARAM_ERROR, f"缺少必填项：{ '、'.join(missing) }")
    try:
        weight = Decimal(str(payload["weightKg"]))
        if weight <= 0:
            raise ValueError
    except (ArithmeticError, ValueError):
        raise ApiError(BizCode.PARAM_ERROR, "重量必须为大于 0 的数字")
    delivery_type = int(payload.get("deliveryType", 1))
    if delivery_type not in (1, 2):
        raise ApiError(BizCode.PARAM_ERROR, "配送类型不合法")

    pricing_service.check_goods(str(payload["goodsType"]), payload.get("goodsName"))
    merchant = pricing_service.get_demo_merchant()
    quote = pricing_service.estimate(
        sender_lng=payload["senderLng"], sender_lat=payload["senderLat"],
        receiver_lng=payload["receiverLng"], receiver_lat=payload["receiverLat"],
        weight_kg=weight, delivery_type=delivery_type, merchant=merchant,
    )
    # 券在原价上叠抵扣（门槛按原价判），前端传来的费用数字一律不作数
    coupon = None
    if payload.get("couponId"):
        quote, coupon = coupon_service.discount_for(
            quote, customer.id, int(payload["couponId"]))

    order = Order(
        order_no=_gen_no(),
        customer_id=customer.id,
        merchant_id=merchant.id,
        order_type=int(payload.get("orderType", 1)),
        status=0,
        sender_name=str(payload["senderName"])[:50],
        sender_phone=encrypt_phone(str(payload["senderPhone"])),
        sender_phone_digest=phone_digest(str(payload["senderPhone"])),
        sender_phone_tail=phone_tail_digest(str(payload["senderPhone"])),
        sender_address=str(payload["senderAddress"])[:255],
        sender_lng=payload["senderLng"], sender_lat=payload["senderLat"],
        receiver_name=str(payload["receiverName"])[:50],
        receiver_phone=encrypt_phone(str(payload["receiverPhone"])),
        receiver_phone_digest=phone_digest(str(payload["receiverPhone"])),
        receiver_phone_tail=phone_tail_digest(str(payload["receiverPhone"])),
        receiver_address=str(payload["receiverAddress"])[:255],
        receiver_lng=payload["receiverLng"], receiver_lat=payload["receiverLat"],
        goods_type=str(payload["goodsType"])[:40],
        goods_name=(str(payload["goodsName"])[:100] if payload.get("goodsName") else None),
        weight_kg=weight,
        volume_m3=payload.get("volumeM3"),
        quantity=int(payload.get("quantity", 1) or 1),
        delivery_type=delivery_type,
        expect_at=_parse_dt(payload.get("expectAt")),
        base_fee=quote["baseFee"], airspace_fee=quote["airspaceFee"],
        urgent_fee=quote["urgentFee"], discount_amount=quote["discountAmount"],
        total_amount=quote["totalAmount"],
        remark=(str(payload["remark"])[:255] if payload.get("remark") else None),
    )
    db.session.add(order)
    db.session.flush()                              # 先拿到 order.id，券与订单要落在同一次提交里
    if coupon:
        coupon_service.settle(customer.id, coupon["id"], order.id)   # 失败会整笔回滚，不会只留下订单
    db.session.commit()
    return order


def pay_order(order: Order, user: User, pay_method: int) -> PaymentTransaction:
    if order.customer_id != user.id:
        raise ApiError(BizCode.FORBIDDEN, "只能支付自己的订单", 403)
    if order.status != 0 or order.paid:
        raise ApiError(BizCode.CONFLICT, f"当前状态（{ORDER_STATUS.get(order.status)}）不能支付", 409)
    if pay_method not in PAY_METHOD:
        raise ApiError(BizCode.PARAM_ERROR, "支付方式不合法")

    txn = PaymentTransaction(
        txn_no=_gen_no("PT"), order_id=order.id, user_id=user.id,
        amount=order.total_amount, direction=1, pay_method=pay_method, status=1,
    )
    db.session.add(txn)
    db.session.flush()
    resp = payment_adapter.pay(txn_no=txn.txn_no, amount=float(order.total_amount),
                               pay_method=pay_method, order_no=order.order_no)
    if not resp["success"]:
        txn.status = 3
        db.session.commit()
        raise ApiError(BizCode.THIRD_PARTY_ERROR, "支付渠道异常，请稍后重试", 503)
    txn.channel_txn_no = resp["channel_txn_no"]
    txn.status = 2
    txn.paid_at = resp["paid_at"]
    txn.raw_resp = resp["raw"]
    order.paid = 1
    order.pay_amount = order.total_amount
    order.pay_method = pay_method
    order.paid_at = resp["paid_at"]
    order.status = 1  # 待接单
    # 钱到账才算一张真单子，所以"待接单"通知挂在支付成功之后，不挂在下单那一刻
    # （待支付的单占着商家的工作台只会练出"看到通知也不用管"的坏习惯）。
    _notify_party(order, f"新订单待接单：{order.order_no}",
                  f"{order.goods_type} · {order.weight_kg}kg，"
                  f"{order.sender_address} → {order.receiver_address}，"
                  f"应收 {_amount(order)}。请在商家工作台接单。",
                  to="merchant", biz_type="order_new")
    db.session.commit()
    return txn


def _mock_refund(order: Order, user_id: int) -> PaymentTransaction | None:
    """已支付订单作废 → 原路退回（Mock 渠道），并落一条 direction=2 流水。

    三处作废口径共用这一个函数：客户自助取消、商家拒单、平台强制终止。
    以前是同一段代码抄了两遍，改退款口径时很容易只改一处，留下一条不退款的取消路径。
    退款流水只由本函数产出，所以结算对账（settlement.reconcile）能拿它当"净额"依据。
    """
    if not order.paid or not order.pay_amount:
        return None
    refund = PaymentTransaction(
        txn_no=_gen_no("RF"), order_id=order.id, user_id=user_id,
        amount=order.pay_amount, direction=2, pay_method=order.pay_method or 1, status=1,
    )
    db.session.add(refund)
    db.session.flush()
    resp = payment_adapter.refund(txn_no=refund.txn_no, amount=float(order.pay_amount),
                                  pay_method=order.pay_method or 1, order_no=order.order_no)
    refund.channel_txn_no = resp["channel_txn_no"]
    refund.status = 4 if resp["success"] else 3
    refund.paid_at = resp["paid_at"]
    refund.raw_resp = resp["raw"]
    return refund


def _return_coupon(order: Order) -> str | None:
    """订单作废 → 把券还给客户，返回券名（没用券就返回 None）。

    三处作废路径（客户取消 / 商家拒单 / 平台强制终止）共用这一个函数，
    理由和当初合并 `_mock_refund` 一模一样：各写一遍，迟早有一条路径忘了退券，
    而"钱退了、券没退"是客户第一时间会发现的事。
    """
    name = coupon_service.bound_coupon_name(order.id)
    return name if name and coupon_service.release_for_order(order.id) else None


def cancel_order(order: Order, user: User, reason: str | None) -> Order:
    if order.customer_id != user.id:
        raise ApiError(BizCode.FORBIDDEN, "只能取消自己的订单", 403)
    if order.status not in (0, 1):
        raise ApiError(BizCode.CONFLICT, "订单已进入调度/飞行环节，不能自助取消，请联系客服", 409)
    _mock_refund(order, user.id)
    order.status = 8
    order.cancel_reason = (reason or "客户主动取消")[:255]
    back = _return_coupon(order)
    _notify_party(order, f"订单已取消：{order.order_no}",
                  f"客户主动取消（{order.cancel_reason}）。"
                  + (f"优惠券「{back}」已退回券包，可继续使用。" if back else ""),
                  to="merchant", biz_type="order_cancel")
    db.session.commit()
    return order


def staff_accept_order(order: Order, user: User) -> Order:
    """商家接单：1待接单 → 2待调度（W3 D2/D3 交界动作）。"""
    if user.user_type not in (3, 4, 5, 6):
        raise ApiError(BizCode.FORBIDDEN, "只有运营方能接单", 403)
    if user.user_type == 3 and user.merchant_id and order.merchant_id != user.merchant_id:
        raise ApiError(BizCode.FORBIDDEN, "无权承接其他商家的订单", 403)
    if order.status != 1:
        raise ApiError(BizCode.CONFLICT,
                       f"订单当前为「{ORDER_STATUS.get(order.status)}」，不能接单", 409)
    order.status = 2
    _notify_party(order, f"商家已接单：{order.order_no}",
                  "订单已进入待调度，接下来会生成飞行方案并提交空管申报，"
                  "审批结果会另行通知。", biz_type="order_accept")
    db.session.commit()
    return order


def staff_reject_order(order: Order, user: User, reason: str | None) -> Order:
    """商家拒单：1待接单 → 8已取消；已支付则 Mock 原路退款。"""
    if user.user_type not in (3, 4, 5, 6):
        raise ApiError(BizCode.FORBIDDEN, "只有运营方能拒单", 403)
    if user.user_type == 3 and user.merchant_id and order.merchant_id != user.merchant_id:
        raise ApiError(BizCode.FORBIDDEN, "无权操作其他商家的订单", 403)
    if order.status != 1:
        raise ApiError(BizCode.CONFLICT, "只有待接单订单能拒单", 409)
    if not reason or not reason.strip():
        raise ApiError(BizCode.PARAM_ERROR, "拒单必须填写原因")
    _mock_refund(order, order.customer_id)
    order.status = 8
    order.cancel_reason = f"商家拒单：{reason.strip()}"[:255]
    back = _return_coupon(order)
    _notify_party(order, f"商家未能接单：{order.order_no}",
                  f"商家拒单原因：{reason.strip()}。"
                  + ("已支付金额将原路退回（Mock）。" if order.paid else "")
                  + (f"优惠券「{back}」已退回券包，可继续使用。" if back else ""),
                  biz_type="order_reject")
    db.session.commit()
    return order


def confirm_receipt(order: Order, user: User) -> Order:
    if order.customer_id != user.id:
        raise ApiError(BizCode.FORBIDDEN, "只能确认自己的订单", 403)
    if order.status != 6:
        raise ApiError(BizCode.CONFLICT, "订单尚未送达，不能确认收货", 409)
    order.status = 7
    order.finished_at = datetime.now()
    # 送达确认即生成结算单（待财务 W5 对账）
    exists = db.session.query(Settlement).filter_by(order_id=order.id, deleted=0).first()
    if not exists:
        platform_fee = (Decimal(order.pay_amount) * pricing_service.PLATFORM_RATE).quantize(Decimal("0.01"))
        db.session.add(Settlement(
            settlement_no=_gen_no("ST"), order_id=order.id,
            merchant_id=order.merchant_id, customer_id=order.customer_id,
            base_fee=order.base_fee, airspace_fee=order.airspace_fee,
            urgent_fee=order.urgent_fee, platform_fee=platform_fee,
            pay_amount=order.pay_amount, status=1,
        ))
    _notify_party(order, f"客户已确认收货：{order.order_no}",
                  f"订单完成，实收 {_amount(order)}，结算单已生成，"
                  "可在「结算对账」查看平台分账与应结金额。",
                  to="merchant", biz_type="order_finish")
    db.session.commit()
    return order


def _scope_query(query, user: User):
    """按身份收窄订单可见范围。"""
    if user.user_type in (1, 2):          # 个人/企业客户：只看自己
        return query.filter(Order.customer_id == user.id)
    if user.user_type == 3 and user.merchant_id:   # 商家员工：本商家
        return query.filter(Order.merchant_id == user.merchant_id)
    return query                          # 平台/空管/运维：全量


def force_terminate_order(order: Order, user: User, reason: str) -> Order:
    """平台/商家强制终止订单（需求 2.2.2-3-1）。

    和"客户自助取消""商家拒单"的分工：
      - 客户只能取消 0/1（cancel_order），商家只能拒 1（staff_reject_order）；
      - 订单一旦进了调度池/审批流，前两条路都关着，以前系统里就没有任何出口了
        —— 差距分析原文是"没有任何管理员终止路径，cancel_plan 只到 status≤2"。
    所以这里开到 4 待起飞：方案已经下发但还没起飞，是可以整体作废的最后窗口。
    5 飞行中往后不许走这条路（空中飞着的飞机要的是 D3 降落/应急流程，不是一句取消）。
    """
    if user.user_type not in (3, 4, 5, 6):
        raise ApiError(BizCode.FORBIDDEN, "只有运营方或平台能强制终止订单", 403)
    if user.user_type == 3 and user.merchant_id and order.merchant_id != user.merchant_id:
        raise ApiError(BizCode.FORBIDDEN, "无权终止其他商家的订单", 403)
    if not reason or not reason.strip():
        raise ApiError(BizCode.PARAM_ERROR, "强制终止必须填写原因（会写入订单与通知客户）")
    if order.status >= 5:
        raise ApiError(BizCode.CONFLICT,
                       f"订单已进入「{ORDER_STATUS.get(order.status)}」，"
                       "请走飞行中的应急/落地流程，不能强制终止", 409)
    if order.status == 8:
        raise ApiError(BizCode.CONFLICT, "订单已经取消，无需再次终止", 409)

    # 关联的在途方案（待申报/待审批/已批准未执行）一并作废，否则调度台还会看到它
    from app.models import FlightPlan
    from app.services import dispatch_service, notify
    live = (db.session.query(FlightPlan)
            .filter(FlightPlan.order_id == order.id, FlightPlan.deleted == 0,
                    FlightPlan.status.in_((0, 1, 2))).all())
    for plan in live:
        dispatch_service.cancel_plan(user, plan, f"订单强制终止：{reason.strip()}")

    refund = _mock_refund(order, order.customer_id)
    order.status = 8
    order.cancel_reason = f"强制终止：{reason.strip()}"[:255]
    back = _return_coupon(order)
    db.session.commit()

    money = (f"，已支付 {float(order.pay_amount or 0):.2f} 元原路退回（Mock）"
             if refund and refund.status == 4 else "，未支付无需退款"
             if not order.paid else "，退款受理中，请到支付流水核对")
    notify.notify(order.customer_id, f"订单 {order.order_no} 已被终止",
                  f"原因：{reason.strip()}{money}"
                  + (f"。优惠券「{back}」已退回券包，可继续使用" if back else ""),
                  biz_type="order", biz_id=order.id)
    return order


def _phone_conds(keyword: str) -> list:
    """手机号检索条件：整号 → HMAC 摘要，4 位 → 尾号摘要。

    这是"订单列表输入框写着『订单号 / 手机号』、输手机号却恒 0 结果"那个 bug 的正解。
    根因不在查询条件少写了一列，而在存的那一列：sender_phone 是 AES-256-GCM 密文，
    每次加密 nonce 随机，同一个手机号在两行里密文不同，LIKE 结构上就不可能命中。
    所以只能加确定性摘要列（迁移 20260929_p12 + scripts/backfill_order_phone.py），
    并且摘要不可逆、不落明文，需求 10.4 的 PII 口径不破。
    """
    digits = "".join(ch for ch in keyword if ch.isdigit())
    if len(digits) == 11:
        dg = phone_digest(digits)
        return [Order.sender_phone_digest == dg, Order.receiver_phone_digest == dg]
    if len(digits) == 4:
        t = phone_tail_digest(digits)
        return [Order.sender_phone_tail == t, Order.receiver_phone_tail == t]
    return []


def _keyword_cond(keyword: str):
    like = f"%{keyword}%"
    conds = [Order.order_no.like(like), Order.goods_type.like(like),
             Order.goods_name.like(like), Order.sender_name.like(like),
             Order.receiver_name.like(like), Order.sender_address.like(like),
             Order.receiver_address.like(like),
             # 备注不参与检索：里面可能有客户随手写的自由文本，命中一堆噪音
             *_phone_conds(keyword)]
    return db.or_(*conds)


def _range_bound(value, *, end: bool = False):
    """时间边界解析：'2026-09-29' 这种只给日期的，右边界补到次日零点（查询用 <）。

    不补的话"今天~今天"会退化成 00:00:00~00:00:00，列表必然空集——
    前端日期范围选择器默认就是同一天，这个坑得在服务端堵。
    """
    if not value or str(value).strip() == "":
        return None
    raw = str(value).strip().replace("/", "-")
    dt = _parse_dt(raw)
    if end and dt.hour == dt.minute == dt.second == 0 and len(raw) <= 10:
        dt = dt + timedelta(days=1)
    return dt


def _filtered(user: User, *, status=None, keyword=None, start=None, end=None,
              delivery_type=None, order_type=None, merchant_id=None,
              paid=None, goods_type=None):
    """列表与导出共用同一套筛选条件——两边口径不一致，导出就成了另一个系统。"""
    query = db.session.query(Order).filter(Order.deleted == 0)
    query = _scope_query(query, user)
    if status is not None and str(status) != "":
        query = query.filter(Order.status == int(status))
    if keyword:
        query = query.filter(_keyword_cond(keyword.strip()))
    dt_start = _range_bound(start)
    dt_end = _range_bound(end, end=True)
    if dt_start:
        query = query.filter(Order.created_at >= dt_start)
    if dt_end:
        query = query.filter(Order.created_at < dt_end)
    if delivery_type is not None and str(delivery_type) != "":
        query = query.filter(Order.delivery_type == int(delivery_type))
    if order_type is not None and str(order_type) != "":
        query = query.filter(Order.order_type == int(order_type))
    if merchant_id is not None and str(merchant_id) != "":
        query = query.filter(Order.merchant_id == int(merchant_id))
    if paid is not None and str(paid) != "":
        query = query.filter(Order.paid == int(bool(paid in (1, "1", True, "true"))))
    if goods_type:
        query = query.filter(Order.goods_type == str(goods_type).strip())
    return query


def list_orders(user: User, *, page=1, page_size=10, **filters) -> dict:
    query = _filtered(user, **filters).order_by(Order.created_at.desc())
    total = query.count()
    rows = query.limit(page_size).offset((page - 1) * page_size).all()
    return {"list": [to_order_dict(o, user) for o in rows], "total": total,
            "page": page, "pageSize": page_size}


EXPORT_MAX = 5000


def export_orders(user: User, *, limit: int = EXPORT_MAX, **filters) -> dict:
    """按当前筛选条件导出订单明细（管理端，权限点 order:export）。

    上限 5000 行不是性能考虑，是"导出=批量搬 PII"：收件人姓名+电话+地址
    一次能带走五千条，必须留个硬闸，超了让人收窄条件而不是分十几页拖库。
    """
    rows = (_filtered(user, **filters).order_by(Order.created_at.desc())
            .limit(limit + 1).all())
    truncated = len(rows) > limit
    return {"list": [to_order_dict(o, user) for o in rows[:limit]],
            "count": len(rows[:limit]), "truncated": truncated, "limit": limit}


def filter_options(user: User) -> dict:
    """筛选栏的候选值（商家 / 物品类型），直接从订单表取 distinct。

    为什么不查 merchants 表：那是另一个模块的数据范围（resource:merchant 权限），
    订单页只要"我看得见的那些单里出现过哪些商家/品类"，
    既不用新开权限，也不会把没下过单的商家列进下拉框让人搜出空结果。
    """
    base = _scope_query(db.session.query(Order.merchant_id, Order.goods_type).filter(
        Order.deleted == 0), user)
    rows = base.distinct().all()
    names = dict(db.session.execute(
        db.text("SELECT id, merchant_name FROM merchants")).all())
    merchants = [{"id": mid, "name": names.get(mid) or f"商家 {mid}"}
                 for mid in sorted({r[0] for r in rows if r[0] is not None}, key=lambda x: (x is None, x))]
    goods = sorted({r[1] for r in rows if r[1]})
    return {"merchants": merchants, "goodsTypes": goods,
            "scope": "merchant" if user.user_type == 3 else "platform"}


def get_visible_order(order_id: int, user: User) -> Order:
    order = db.session.query(Order).filter_by(id=order_id, deleted=0).first()
    if order is None:
        raise ApiError(BizCode.NOT_FOUND, "订单不存在", 404)
    if user.user_type in (1, 2) and order.customer_id != user.id:
        raise ApiError(BizCode.FORBIDDEN, "无权查看该订单", 403)
    if user.user_type == 3 and user.merchant_id and order.merchant_id != user.merchant_id:
        raise ApiError(BizCode.FORBIDDEN, "无权查看该订单", 403)
    return order


def to_order_dict(o: Order, viewer: User | None = None) -> dict:
    """订单出参；寄收电话对订单相关方解密，无关方脱敏。"""
    show_phone = viewer is None or viewer.user_type in (3, 4, 5, 6) or viewer.id == o.customer_id
    phone = lambda c: decrypt_phone(c) if show_phone else mask_phone(c)  # noqa: E731
    # 只有减过钱的单子才回查券名：绝大多数订单不用券，别让订单列表每行都多跑一条 SQL
    coupon_name = coupon_service.bound_coupon_name(o.id) if (o.discount_amount or 0) else None
    return {
        "id": o.id,
        "orderNo": o.order_no,
        "customerId": o.customer_id,
        "merchantId": o.merchant_id,
        "orderType": o.order_type,
        "status": o.status,
        "statusName": ORDER_STATUS.get(o.status),
        "senderName": o.sender_name,
        "senderPhone": phone(o.sender_phone),
        "senderAddress": o.sender_address,
        "senderLng": float(o.sender_lng), "senderLat": float(o.sender_lat),
        "receiverName": o.receiver_name,
        "receiverPhone": phone(o.receiver_phone),
        "receiverAddress": o.receiver_address,
        "receiverLng": float(o.receiver_lng), "receiverLat": float(o.receiver_lat),
        "goodsType": o.goods_type,
        "goodsName": o.goods_name,
        "weightKg": float(o.weight_kg),
        "volumeM3": float(o.volume_m3) if o.volume_m3 is not None else None,
        "quantity": o.quantity,
        "deliveryType": o.delivery_type,
        "expectAt": o.expect_at.strftime("%Y-%m-%d %H:%M") if o.expect_at else None,
        **o.fee_dict(),
        "couponName": coupon_name,
        "payMethod": o.pay_method,
        "payMethodName": PAY_METHOD.get(o.pay_method) if o.pay_method else None,
        "paid": bool(o.paid),
        "paidAt": o.paid_at.strftime("%Y-%m-%d %H:%M:%S") if o.paid_at else None,
        "deliveredAt": o.delivered_at.strftime("%Y-%m-%d %H:%M:%S") if o.delivered_at else None,
        "finishedAt": o.finished_at.strftime("%Y-%m-%d %H:%M:%S") if o.finished_at else None,
        "deliveryMinutes": (int((o.delivered_at - o.created_at).total_seconds() // 60)
                            if o.delivered_at and o.created_at else None),
        "cancelReason": o.cancel_reason,
        "remark": o.remark,
        "createdAt": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else None,
    }


def list_bills(user: User, *, page=1, page_size=10) -> dict:
    """客户：我的支付/退款流水。"""
    query = (db.session.query(PaymentTransaction)
             .filter(PaymentTransaction.user_id == user.id, PaymentTransaction.deleted == 0)
             .order_by(PaymentTransaction.created_at.desc()))
    total = query.count()
    rows = query.limit(page_size).offset((page - 1) * page_size).all()
    order_nos = {o.id: o.order_no for o in
                 db.session.query(Order).filter(Order.id.in_([r.order_id for r in rows])).all()} or {}
    return {
        "total": total, "page": page, "pageSize": page_size,
        "list": [{
            "id": r.id, "txnNo": r.txn_no, "orderId": r.order_id,
            "orderNo": order_nos.get(r.order_id),
            "amount": float(r.amount),
            "direction": r.direction,
            "directionName": "支付" if r.direction == 1 else "退款",
            "payMethod": r.pay_method, "payMethodName": METHOD_NAME.get(r.pay_method),
            "status": r.status,
            "statusName": {1: "待支付", 2: "成功", 3: "失败", 4: "已退款"}.get(r.status),
            "channelTxnNo": r.channel_txn_no,
            "paidAt": r.paid_at.strftime("%Y-%m-%d %H:%M:%S") if r.paid_at else None,
            "createdAt": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        } for r in rows],
    }


def list_settlements(user: User, *, page=1, page_size=10) -> dict:
    """客户：我的结算单（订单完成后生成）。"""
    query = (db.session.query(Settlement)
             .filter(Settlement.customer_id == user.id, Settlement.deleted == 0)
             .order_by(Settlement.created_at.desc()))
    total = query.count()
    rows = query.limit(page_size).offset((page - 1) * page_size).all()
    order_nos = {o.id: o.order_no for o in
                 db.session.query(Order).filter(Order.id.in_([r.order_id for r in rows])).all()} or {}
    return {
        "total": total, "page": page, "pageSize": page_size,
        "list": [{
            "id": r.id, "settlementNo": r.settlement_no, "orderId": r.order_id,
            "orderNo": order_nos.get(r.order_id),
            "baseFee": float(r.base_fee), "airspaceFee": float(r.airspace_fee),
            "urgentFee": float(r.urgent_fee), "platformFee": float(r.platform_fee),
            "payAmount": float(r.pay_amount),
            "status": r.status,
            "statusName": {1: "待结算", 2: "已结算", 3: "对账中", 4: "有差异"}.get(r.status),
            "invoiceStatus": r.invoice_status,
            "createdAt": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        } for r in rows],
    }
