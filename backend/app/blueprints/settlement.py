"""W6 结算蓝图：账单查询 / 对账 / 支付流水 / 发票（设计06 M-72~M-74）。

门禁口径（差距分析 P0-1/P0-3）：
  settlement:list     账单列表、流水查询、发票申请（商家只看本商家，平台看全量）
  settlement:reconcile 对账与发票开具（财务/平台管理员；只在本商家范围内生效）
客户侧的"我的结算单"仍走 /api/bills/settlements（order_service，按 customer_id 收口）。

对账为什么不能只"全部标已结算"：账单金额是平台侧记账（订单确认收货时按订单实付生成），
钱有没有真收到要看支付流水。所以 reconcile 逐单拿 payment_transaction 的净额
（成功支付 - 已退款）与账单核：平→2 已结算，不平→4 有差异并写差异原因。
"""
from datetime import datetime

from flask import Blueprint, request
from sqlalchemy import and_, case

from app.common import scope
from app.common.decorators import current_user, perm_required
from app.common.errors import ApiError, BizCode
from app.common.response import success
from app.extensions import db
from app.models import Merchant, Order, PaymentTransaction, Settlement

bp = Blueprint("settlement", __name__, url_prefix="/api/settlement")

STATUS_NAME = {1: "待结算", 2: "已结算", 3: "对账中", 4: "有差异"}
PAY_STATUS_NAME = {1: "待支付", 2: "支付成功", 3: "支付失败", 4: "已退款"}
PAY_DIRECTION_NAME = {1: "支付", 2: "退款"}
PAY_METHOD_NAME = {1: "微信", 2: "支付宝", 3: "对公转账"}
# 允许被拉进对账的状态：待结算，以及上一次没核平、财务处理完要重核的
RECON_FROM = (1, 3, 4)


def _scoped(user):
    """账单查询基础查询：商家员工收窄到本商家。"""
    q = db.session.query(Settlement).filter(Settlement.deleted == 0)
    mid = scope.merchant_scope(user)
    if mid is not None:
        q = q.filter(Settlement.merchant_id == mid)
    return q


def _net_paid(order_ids):
    """按订单聚合支付净额 = 成功支付 - 已退款。

    只认两种终态流水：direction=1 且 status=2（支付成功）、
    direction=2 且 status=4（退款成功）。待支付/失败/退款中的流水不算收到钱，
    这正是对账要挑出来的差异来源。
    """
    if not order_ids:
        return {}
    rows = db.session.query(
        PaymentTransaction.order_id,
        db.func.coalesce(db.func.sum(case(
            (and_(PaymentTransaction.direction == 1, PaymentTransaction.status == 2),
             PaymentTransaction.amount), else_=0)), 0),
        db.func.coalesce(db.func.sum(case(
            (and_(PaymentTransaction.direction == 2, PaymentTransaction.status == 4),
             PaymentTransaction.amount), else_=0)), 0),
        db.func.count(PaymentTransaction.id),
    ).filter(PaymentTransaction.order_id.in_(order_ids),
             PaymentTransaction.deleted == 0
             ).group_by(PaymentTransaction.order_id).all()
    return {r[0]: {"paid": float(r[1]), "refunded": float(r[2]), "net": float(r[1]) - float(r[2]),
                   "txnCount": int(r[3])} for r in rows}


def _diff_of(bill, flow):
    """返回 (差异金额, 差异原因)；平了返回 (0, None)。"""
    due = float(bill.pay_amount)
    if flow is None or flow["txnCount"] == 0:
        return due, "缺支付流水：该订单没有任何支付流水记录"
    if flow["paid"] == 0:
        return due, "款项未到账：没有支付成功的流水（待支付或支付失败）"
    if abs(flow["net"] - due) < 0.005:
        return 0, None
    diff = round(due - flow["net"], 2)
    if flow["refunded"]:
        return diff, (f"有退款：支付 {flow['paid']:.2f} 元、退款 {flow['refunded']:.2f} 元，"
                      f"净额 {flow['net']:.2f} 元 ≠ 账单 {due:.2f} 元")
    return diff, f"金额不符：流水净额 {flow['net']:.2f} 元 ≠ 账单 {due:.2f} 元"


def _row(s: Settlement, o: Order | None, flow: dict | None = None) -> dict:
    diff, reason = _diff_of(s, flow) if flow is not None else (None, None)
    return {
        "id": s.id, "settlementNo": s.settlement_no,
        "orderId": s.order_id,
        "orderNo": o.order_no if o else None,
        "goodsName": o.goods_name if o else None,
        "merchantId": s.merchant_id,
        "baseFee": float(s.base_fee), "airspaceFee": float(s.airspace_fee),
        "urgentFee": float(s.urgent_fee), "platformFee": float(s.platform_fee),
        "payAmount": float(s.pay_amount),
        # 账单金额是"向客户收的钱"，商家到手要扣掉平台抽成，这一列是派生值
        "merchantIncome": round(float(s.pay_amount) - float(s.platform_fee), 2),
        "status": s.status, "statusName": STATUS_NAME.get(s.status),
        "invoiceStatus": s.invoice_status,
        "netPaid": (flow or {}).get("net"), "diffAmount": diff, "diffReason": reason,
        "reconciledAt": s.reconciled_at.strftime("%Y-%m-%d %H:%M") if s.reconciled_at else None,
        "createdAt": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else None,
    }


@bp.get("/bills")
@perm_required("settlement:list")
def list_bills():
    user = current_user()
    q = _scoped(user)
    st = request.args.get("status")
    if st:
        q = q.filter(Settlement.status == int(st))
    start, end = request.args.get("start"), request.args.get("end")
    if start:
        q = q.filter(Settlement.created_at >= f"{start} 00:00:00")
    if end:
        q = q.filter(Settlement.created_at <= f"{end} 23:59:59")
    keyword = (request.args.get("keyword") or "").strip()
    only_diff = request.args.get("onlyDiff") in ("1", "true", "yes")
    if only_diff:
        q = q.filter(Settlement.status == 4)

    rows = q.order_by(Settlement.id.desc()).limit(200).all()
    order_map = {o.id: o for o in db.session.query(Order).filter(
        Order.id.in_([s.order_id for s in rows] or [0])).all()} if rows else {}
    flows = _net_paid([s.order_id for s in rows])
    data = [_row(s, order_map.get(s.order_id), flows.get(s.order_id)) for s in rows]
    if keyword:
        data = [d for d in data if keyword in (d["orderNo"] or "")
                or keyword in (d["goodsName"] or "")
                or keyword in (d["settlementNo"] or "")]

    # 汇总口径与列表一致（同一批过滤条件），不再返回全平台合计
    agg_q = _scoped(user)
    if st:
        agg_q = agg_q.filter(Settlement.status == int(st))
    if start:
        agg_q = agg_q.filter(Settlement.created_at >= f"{start} 00:00:00")
    if end:
        agg_q = agg_q.filter(Settlement.created_at <= f"{end} 23:59:59")
    if only_diff:
        agg_q = agg_q.filter(Settlement.status == 4)
    count, total_fee, pending_fee, diff_count, diff_fee = agg_q.with_entities(
        db.func.count(Settlement.id),
        db.func.coalesce(db.func.sum(Settlement.pay_amount), 0),
        db.func.coalesce(db.func.sum(case(
            (Settlement.status == 1, Settlement.pay_amount), else_=0)), 0),
        db.func.coalesce(db.func.sum(case((Settlement.status == 4, 1), else_=0)), 0),
        db.func.coalesce(db.func.sum(case(
            (Settlement.status == 4, Settlement.pay_amount), else_=0)), 0),
    ).one()
    return success({
        "list": data, "total": len(data),
        # diffFee 是"差异账单的应结金额合计"，不是差额本身：差额要逐单比流水，
        # 在 SQL 里聚合不出可信数字，宁可给口径明确的数，也别给个看着像差额的假数。
        "summary": {"count": count, "totalFee": float(total_fee or 0),
                    "pendingFee": float(pending_fee or 0),
                    "diffCount": int(diff_count or 0), "diffFee": float(diff_fee or 0)},
        "scope": "merchant" if scope.merchant_scope(user) is not None else "platform",
    })


@bp.post("/bills/reconcile")
@perm_required("settlement:reconcile")
def reconcile():
    """对账：逐单拿支付流水核账单，平→已结算，不平→有差异。

    可选 body：{"ids": [...]} 只对勾选项对账；不传则按当前范围全量对账。
    范围只认 待结算(1)/对账中(3)/有差异(4) —— 差异单在财务补完流水后能重核，
    对账中(3) 是给上一次崩在中途留下的口子，不然会永久卡住。
    先把命中的单置成 3 再核，等于给这批账单上锁，避免两个财务同时点按钮重复处理。
    """
    user = current_user()
    d = request.get_json(silent=True) or {}
    ids = [int(x) for x in (d.get("ids") or [])]
    if ids:
        q = db.session.query(Settlement).filter(
            Settlement.id.in_(ids), Settlement.deleted == 0,
            Settlement.status.in_(RECON_FROM))
        mid = scope.merchant_scope(user)
        if mid is not None:
            q = q.filter(Settlement.merchant_id == mid)
    else:
        q = _scoped(user).filter(Settlement.status.in_(RECON_FROM))
    rows = q.all()
    if not rows:
        raise ApiError(BizCode.CONFLICT, "没有待对账的账单（待结算 / 有差异都会被抓进对账）")

    for s in rows:
        s.status = 3
    db.session.commit()

    flows = _net_paid([s.order_id for s in rows])
    orders = {o.id: o for o in db.session.query(Order).filter(
        Order.id.in_([s.order_id for s in rows])).all()}
    matched, diffs = 0, []
    for s in rows:
        diff, reason = _diff_of(s, flows.get(s.order_id))
        if reason is None:
            s.status = 2
            s.reconciled_at = datetime.now()
            matched += 1
        else:
            s.status = 4
            o = orders.get(s.order_id)
            diffs.append({"settlementNo": s.settlement_no,
                          "orderNo": o.order_no if o else None,
                          "payAmount": float(s.pay_amount),
                          "netPaid": (flows.get(s.order_id) or {}).get("net"),
                          "diffAmount": diff, "reason": reason})
    db.session.commit()
    return success({"checked": len(rows), "matched": matched, "diffCount": len(diffs),
                    "diffs": diffs[:50]},
                   f"对账 {len(rows)} 笔：{matched} 笔平账已结算，{len(diffs)} 笔有差异")


@bp.get("/payments")
@perm_required("settlement:list")
def list_payments():
    """管理端支付流水（M-73）：微信/支付宝/对公的支付与退款明细，对账差异就在这里溯源。

    原先只有客户本人的 /api/bills/payments，财务想看"这笔账单到底收到没有"只能猜。
    payment_transaction 表没有 merchant_id，归属跟着订单走，所以商家收窄必须 JOIN orders，
    不能按流水自己的字段筛。
    """
    user = current_user()
    q = (db.session.query(PaymentTransaction, Order)
         .outerjoin(Order, Order.id == PaymentTransaction.order_id)
         .filter(PaymentTransaction.deleted == 0))
    mid = scope.merchant_scope(user)
    if mid is not None:
        q = q.filter(Order.merchant_id == mid)
    st = request.args.get("status")
    if st:
        q = q.filter(PaymentTransaction.status == int(st))
    direction = request.args.get("direction")
    if direction:
        q = q.filter(PaymentTransaction.direction == int(direction))
    start, end = request.args.get("start"), request.args.get("end")
    if start:
        q = q.filter(PaymentTransaction.created_at >= f"{start} 00:00:00")
    if end:
        q = q.filter(PaymentTransaction.created_at <= f"{end} 23:59:59")
    keyword = (request.args.get("keyword") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(db.or_(PaymentTransaction.txn_no.like(like),
                            PaymentTransaction.channel_txn_no.like(like),
                            Order.order_no.like(like)))
    rows = q.order_by(PaymentTransaction.id.desc()).limit(200).all()
    data = [{
        "id": t.id, "txnNo": t.txn_no, "orderNo": o.order_no if o else None,
        "orderId": t.order_id, "userId": t.user_id,
        "amount": float(t.amount), "direction": t.direction,
        "directionName": PAY_DIRECTION_NAME.get(t.direction),
        "status": t.status, "statusName": PAY_STATUS_NAME.get(t.status),
        "payMethod": t.pay_method, "payMethodName": PAY_METHOD_NAME.get(t.pay_method),
        "channelTxnNo": t.channel_txn_no,
        "paidAt": t.paid_at.strftime("%Y-%m-%d %H:%M") if t.paid_at else None,
        "createdAt": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else None,
    } for t, o in rows]
    income = sum(r["amount"] for r in data
                 if r["direction"] == 1 and r["status"] == 2)
    refund = sum(r["amount"] for r in data if r["direction"] == 2 and r["status"] == 4)
    return success({
        "list": data, "total": len(data),
        "summary": {"income": round(income, 2), "refund": round(refund, 2),
                    "net": round(income - refund, 2)},
        "scope": "merchant" if mid is not None else "platform"})


@bp.post("/bills/<int:bid>/invoice")
@perm_required("settlement:list")
def apply_invoice(bid):
    """申请发票：0 未申请 → 1 已申请（平台财务随后开具）。"""
    user = current_user()
    b = db.session.get(Settlement, bid)
    if b is None or b.deleted:
        raise ApiError(BizCode.NOT_FOUND, "账单不存在")
    mid = scope.merchant_scope(user)
    if mid is not None and b.merchant_id != mid:
        raise ApiError(BizCode.FORBIDDEN, "不能对他家账单申请发票", 403)
    if b.invoice_status == 1:
        raise ApiError(BizCode.CONFLICT, "该账单已申请过发票")
    if b.invoice_status == 2:
        raise ApiError(BizCode.CONFLICT, "该账单发票已开具，无需重复申请")
    # 只有对账平账（已结算）的账单能申请发票：钱还没核实就开票，等于替一笔可能的差异单背书
    if b.status != 2:
        raise ApiError(BizCode.CONFLICT,
                       f"账单当前是「{STATUS_NAME.get(b.status)}」，先对账平账后再申请发票")
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip()
    if not title:
        merchant = db.session.get(Merchant, b.merchant_id)
        title = merchant.merchant_name if merchant else "平台代开"
    b.invoice_status = 1
    b.invoice_title = title[:120]
    b.invoice_tax_no = (d.get("taxNo") or "").strip()[:40] or None
    b.invoice_applied_at = datetime.now()
    db.session.commit()
    return success({"id": b.id, "invoiceStatus": b.invoice_status,
                    "invoiceTitle": b.invoice_title}, "发票申请已提交")


@bp.post("/bills/<int:bid>/invoice/issue")
@perm_required("settlement:reconcile")
def issue_invoice(bid):
    """开具发票：1 已申请 → 2 已开具（平台财务侧动作，商家只能申请）。"""
    user = current_user()
    b = db.session.get(Settlement, bid)
    if b is None or b.deleted:
        raise ApiError(BizCode.NOT_FOUND, "账单不存在")
    mid = scope.merchant_scope(user)
    if mid is not None and b.merchant_id != mid:
        raise ApiError(BizCode.FORBIDDEN, "不能对他家账单开票", 403)
    if b.invoice_status == 0:
        raise ApiError(BizCode.CONFLICT, "该账单尚未申请发票")
    if b.invoice_status == 2:
        raise ApiError(BizCode.CONFLICT, "该账单发票已开具")
    b.invoice_status = 2
    db.session.commit()
    return success({"id": b.id, "invoiceStatus": b.invoice_status,
                    "invoiceTitle": b.invoice_title}, "发票已开具")
