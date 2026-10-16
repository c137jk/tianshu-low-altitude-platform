"""优惠券领取与核销（需求 6.3.2 优惠券抵扣）。

分工：`pricing_service` 只管"这一单原价多少"，本模块只管"这张券能不能给这一单减多少、
减完落在哪张券上"。以前 `pricing_service.estimate()` 把 `discountAmount` 硬编码成 0.0，
后台能建券但下单永远不减钱（差距分析 §7.1），就是因为这两件事没有各自的归属。

三条不肯含糊的口径：
  · **券面规则不快照**——门槛/金额/折扣一律 JOIN `coupon` 现读。运营改了券，已领的跟着改，
    这是改券那一刻的决定；每张券留一份副本，迟早出现"母表改了、发出去的还是旧值"的两张皮。
    要冻结的是**结果**（这一单减了多少钱），那个数字落在 `orders.discount_amount` 上。
  · **过期不落状态**——按 `coupon.end_at` 现判（本模块 `expired_of()`）。
    存一份"已过期"就得再写一个定时任务去维护它，而续期后的券会卡在过期态没人管；
    定时归档留在 B6 的 APScheduler 里做，那是清理不是判定。
  · **下单即核销，作废即退券**——券跟着订单走：创建成功置"已核销"并绑 `order_id`，
    客户取消 / 商家拒单 / 平台强制终止三处退券。退券必须走同一个函数（`release_for_order`），
    和 `_mock_refund` 当初合并三处退款是同一个理由：三处各写一遍，迟早只改一处。
"""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.common.errors import ApiError, BizCode
from app.extensions import db

UC_STATUS = {1: "未使用", 2: "已核销"}
CP_TYPE = {1: "满减", 2: "折扣"}


def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _one(sql, **params):
    rows = db.session.execute(text(sql), params).mappings().all()
    return dict(rows[0]) if rows else None


def in_window(cp: dict, now: datetime | None = None) -> bool:
    """券此刻能不能用。`start_at` 未到 / `end_at` 已过都不行，缺时间视为不限。"""
    now = now or datetime.now()
    start, end = cp.get("start_at"), cp.get("end_at")
    if isinstance(start, datetime) and now < start:
        return False
    if isinstance(end, datetime) and now > end:
        return False
    return True


def expired_of(row: dict) -> bool:
    return int(row.get("uc_status") or 1) == 1 and not in_window(row, datetime.now())


def claimable(user_id: int, coupon_id: int) -> dict:
    """领取前的三道判据：券在架、有余量、这张券没领过。"""
    cp = _one("SELECT * FROM coupon WHERE id=:i AND deleted=0", i=coupon_id)
    if cp is None:
        raise ApiError(BizCode.NOT_FOUND, "优惠券不存在", 404)
    if int(cp["status"]) != 1:
        raise ApiError(BizCode.CONFLICT, f"「{cp['coupon_name']}」已停用，不能领取", 409)
    if not in_window(cp):
        raise ApiError(BizCode.CONFLICT,
                       f"「{cp['coupon_name']}」不在有效期内"
                       f"（{cp['start_at']:%Y-%m-%d} ~ {cp['end_at']:%Y-%m-%d}）", 409)
    total, got = int(cp["total_count"] or 0), int(cp["got_count"] or 0)
    if total <= 0 or got >= total:
        raise ApiError(BizCode.CONFLICT, f"「{cp['coupon_name']}」已领完（限量 {total} 张）", 409)
    held = _one("SELECT id FROM user_coupon WHERE user_id=:u AND coupon_id=:c AND deleted=0",
                u=user_id, c=coupon_id)
    if held:
        raise ApiError(BizCode.CONFLICT, "这张券你已经领过了，每人限领一张", 409)
    return cp


def claim(user_id: int, coupon_id: int) -> int:
    """领取一张券，返回 `user_coupon.id`。

    名额走条件自增（`got_count < total_count` 才加）：先查再写在最后一张上会超发，
    两个人同时点就发出两张。抢中名额后插关系行，唯一键 `uk_user_coupon` 兜重复领取，
    插入失败整笔回滚——名额不能被一次失败的领取吃掉。
    """
    cp = claimable(user_id, coupon_id)
    held = db.session.execute(text(
        "UPDATE coupon SET got_count = got_count + 1, updated_at=NOW()"
        " WHERE id=:i AND deleted=0 AND status=1 AND got_count < total_count"),
        {"i": coupon_id}).rowcount
    if not held:                                    # 抢名额这一瞬被别人清空了
        db.session.rollback()
        raise ApiError(BizCode.CONFLICT,
                       f"「{cp['coupon_name']}」刚刚被领完（限量 {int(cp['total_count'] or 0)} 张）", 409)
    try:
        new_id = db.session.execute(text(
            "INSERT INTO user_coupon (user_id, coupon_id, status, got_at, created_at, updated_at, deleted)"
            " VALUES (:u, :c, 1, NOW(), NOW(), NOW(), 0)"),
            {"u": user_id, "c": coupon_id}).lastrowid
        db.session.commit()
    except IntegrityError:                          # 并发重复领取：唯一键拦下，名额一起回退
        db.session.rollback()
        raise ApiError(BizCode.CONFLICT, "这张券你已经领过了，每人限领一张", 409)
    return int(new_id)


_Coupon_JOIN = """
        FROM user_coupon uc
        JOIN coupon c ON c.id = uc.coupon_id AND c.deleted = 0
        LEFT JOIN orders o ON o.id = uc.order_id
        WHERE uc.deleted = 0
"""


def mine(user_id: int, status: int | None = None) -> list[dict]:
    """我的券包。未使用的排前面并按到期升序，让客户先花要过期的那张。"""
    cond = ["uc.user_id = :u"]
    params: dict = {"u": user_id}
    if status in (1, 2):
        cond.append("uc.status = :s")
        params["s"] = status
    rows = db.session.execute(text(
        "SELECT uc.id, uc.status AS uc_status, uc.got_at, uc.used_at, uc.order_id,"
        " o.order_no, c.id AS coupon_id, c.coupon_name, c.coupon_type, c.threshold,"
        " c.amount, c.discount, c.start_at, c.end_at, c.status AS cp_status"
        + _Coupon_JOIN + " AND " + " AND ".join(cond)
        + " ORDER BY uc.status ASC, c.end_at ASC, uc.id DESC"), params).mappings().all()
    return [_ser(r) for r in rows]


def _ser(r) -> dict:
    def fmt(v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v

    def d(v):
        return float(v) if v is not None else None

    expired = expired_of(dict(r))
    if int(r["uc_status"]) == 2:
        state, state_name = 2, "已核销"
    elif expired:
        state, state_name = 4, "已过期"
    elif int(r["cp_status"]) != 1:
        state, state_name = 5, "券已停用"
    else:
        state, state_name = 1, "未使用"
    return {
        "id": r["id"], "couponId": r["coupon_id"], "couponName": r["coupon_name"],
        "couponType": r["coupon_type"], "typeName": CP_TYPE.get(int(r["coupon_type"]), "其他"),
        "threshold": float(r["threshold"] or 0), "amount": d(r["amount"]), "discount": d(r["discount"]),
        "ruleText": rule_text(int(r["coupon_type"]), float(r["threshold"] or 0),
                              d(r["amount"]), d(r["discount"])),
        "startAt": fmt(r["start_at"]), "endAt": fmt(r["end_at"]),
        "status": state, "statusName": state_name, "expired": expired,
        "gotAt": fmt(r["got_at"]), "usedAt": fmt(r["used_at"]),
        "orderId": r["order_id"], "orderNo": r["order_no"],
    }


def rule_text(coupon_type: int, threshold: float, amount, discount) -> str:
    head = f"满 {threshold:.0f} 元可用" if threshold > 0 else "不限金额"
    if int(coupon_type) == 2 and discount:
        return f"{head} · {float(discount) * 10:.1f} 折"
    return f"{head} · 减 {amount if amount is not None else 0:.2f} 元"


def usable(user_id: int, user_coupon_id: int, subtotal) -> dict:
    """这张券能不能给这一单用，能用的话减多少。

    校验顺序是"归属 → 状态 → 有效期 → 门槛"：归属放最前，别人的券连号都不该泄露出去。
    门槛按**原价**判，不是按折后价——否则同一张券能反复叠加着把价格压低。
    """
    row = _one("SELECT uc.id, uc.coupon_id, uc.status AS uc_status, uc.user_id, c.coupon_name,"
               " c.coupon_type, c.threshold, c.amount, c.discount, c.start_at, c.end_at,"
               " c.status AS cp_status, c.deleted AS cp_deleted"
               " FROM user_coupon uc JOIN coupon c ON c.id = uc.coupon_id"
               " WHERE uc.id=:i AND uc.deleted=0", i=user_coupon_id)
    if row is None or int(row["user_id"]) != int(user_id):
        raise ApiError(BizCode.NOT_FOUND, "优惠券不存在")
    if int(row["uc_status"]) == 2:
        raise ApiError(BizCode.CONFLICT, f"「{row['coupon_name']}」已核销，不能重复使用", 409)
    if int(row["cp_deleted"]) == 1:
        raise ApiError(BizCode.CONFLICT, f"「{row['coupon_name']}」已下架，不能再用", 409)
    if int(row["cp_status"]) != 1:
        raise ApiError(BizCode.CONFLICT, f"「{row['coupon_name']}」已停用，不能再用", 409)
    if not in_window(row):
        if row["end_at"]:
            raise ApiError(BizCode.CONFLICT,
                           f"「{row['coupon_name']}」已过有效期（{row['end_at']:%Y-%m-%d}）", 409)
        raise ApiError(BizCode.CONFLICT, f"「{row['coupon_name']}」还没到使用期", 409)
    subtotal = _q2(subtotal)
    threshold = _q2(row["threshold"] or 0)
    if subtotal < threshold:
        raise ApiError(BizCode.CONFLICT,
                       f"订单金额 {subtotal:.2f} 元不满 {threshold:.2f} 元，"
                       f"这张券用不了", 409)
    return dict(row, discountAmount=_discount_of(row, subtotal))


def _discount_of(row: dict, subtotal: Decimal) -> Decimal:
    """满减给固定额、折扣按比例，两者都不能把价格减成负数。"""
    if int(row["coupon_type"]) == 2 and row["discount"] is not None:
        off = subtotal * (Decimal("1") - Decimal(str(row["discount"])))
    else:
        off = Decimal(str(row["amount"] or 0))
    off = _q2(off)
    return off if off <= subtotal else subtotal


def discount_for(quote: dict, user_id: int, coupon_id) -> tuple[dict, dict]:
    """给一份试算结果叠加券抵扣，返回 (新 quote, 券行)。

    下单页试算与真正下单都走这里，**抵扣只算一次口径**：两处各写一遍的话，
    客户看到的价和最后扣的价会分叉，那种"页面 12 元、扣款 15 元"是最难解释的线上问题。
    返回券行是给调用方留作核销用的凭据（含 id，避免二次查询）。
    """
    subtotal = Decimal(str(quote["totalAmount"]))
    cp = usable(user_id, coupon_id, subtotal)
    off = cp["discountAmount"]
    out = dict(quote)
    out["discountAmount"] = float(off)
    out["totalAmount"] = float(_q2(subtotal - off))
    out["coupon"] = {"id": cp["id"], "couponId": cp["coupon_id"], "name": cp["coupon_name"],
                     "rule": rule_text(int(cp["coupon_type"]), float(cp["threshold"] or 0),
                                       cp["amount"], cp["discount"]),
                     "discount": float(off)}
    return out, cp


def settle(user_id: int, user_coupon_id: int, order_id: int) -> None:
    """核销：券置为已使用并绑定这张订单。

    条件更新（只有 `status=1` 才写得进去）——同一张券同时提交两单时，
    只有一单能占住它，另一单在这里失败，整笔下单一起回滚，不会出现"一张券减两单"。
    """
    row = db.session.execute(text(
        "UPDATE user_coupon SET status=2, order_id=:o, used_at=NOW(), updated_at=NOW()"
        " WHERE id=:i AND user_id=:u AND status=1 AND deleted=0"),
        {"o": order_id, "u": user_id, "i": user_coupon_id}).rowcount
    if not row:
        cp = _one("SELECT coupon_name FROM coupon c JOIN user_coupon uc ON uc.coupon_id=c.id"
                  " WHERE uc.id=:i", i=user_coupon_id)
        raise ApiError(BizCode.CONFLICT,
                       f"「{(cp or {}).get('coupon_name') or '这张券'}」刚刚被用掉了，"
                       f"请换一张或不用券", 409)


def bound_coupon_name(order_id: int) -> str | None:
    """这张订单上核销掉的券叫什么——退券时要在通知里点名，客户才知道回来的是哪张。"""
    if not order_id:
        return None
    return db.session.execute(text(
        "SELECT c.coupon_name FROM user_coupon uc JOIN coupon c ON c.id = uc.coupon_id"
        " WHERE uc.order_id=:o AND uc.status=2 AND uc.deleted=0 LIMIT 1"), {"o": order_id}).scalar()


def release_for_order(order_id: int) -> int:
    """退券：订单作废（客户取消 / 商家拒单 / 平台强制终止）时把券还给客户。

    只退**已核销且绑在这张单上**的券。退回来的券 `used_at` 清空、还能继续用——
    有效期是运营给的承诺，不该因为一次商家拒单被吃掉。
    """
    if not order_id:
        return 0
    return db.session.execute(text(
        "UPDATE user_coupon SET status=1, order_id=NULL, used_at=NULL, updated_at=NOW()"
        " WHERE order_id=:o AND status=2 AND deleted=0"), {"o": order_id}).rowcount
