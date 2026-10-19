"""W6 结算：对账（账单 vs 支付流水）、支付流水查询、发票链路（M-71~M-74）。

对账这条链必须是"真比对"：以前 reconcile 只是把待结算全部标成已结算，
钱到底收没收到根本没查流水，演示时看着像走完了流程，其实等于自欺欺人。
所以这里的用例都围绕"流水与账单不一致必须挂差异"来写。
"""
import pytest

from app.extensions import db
from app.models import PaymentTransaction, Settlement, User
from app.services import order_service


def _u(user_ids, username):
    return db.session.get(User, user_ids[username])


def _done_flight(make_flight, user_ids):
    """造一条已完成订单 + 待结算账单（走真实的确认收货入口，账单口径才和线上一致）。"""
    landed, _ = make_flight("landed")
    order_service.confirm_receipt(landed, _u(user_ids, "customer"))
    bill = db.session.query(Settlement).filter_by(order_id=landed.id, deleted=0).first()
    assert bill is not None and bill.status == 1
    return landed, bill


def _txn(order, amount, *, direction=1, status=2):
    t = PaymentTransaction(
        txn_no=f"PTT{order.id}-{direction}{status}-{int(amount * 100)}",
        order_id=order.id, user_id=order.customer_id,
        amount=amount, direction=direction, pay_method=1, status=status,
        channel_txn_no="MOCK-TEST",
    )
    db.session.add(t)
    db.session.commit()
    return t


def _kill_flow(order_id):
    db.session.execute(
        db.text("UPDATE payment_transaction SET status=1 WHERE order_id=:o"), {"o": order_id})
    db.session.commit()


@pytest.fixture()
def fin(api, fresh_user):
    """商家侧财务岗身份（finance：settlement:list + settlement:reconcile，归属 merchant 1）。

    测试库的种子账号只有 admin/merchant/customer/pilot/pilot02，演示库里的 caiwu 是后建的，
    所以财务身份现场建，不让用例依赖某个环境的账号名。
    """
    return api.as_id(fresh_user("u_settle_finance", "finance", user_type=3, merchant_id=1).id)


# ---------------------------------------------------------------- 对账比对

def test_reconcile_matched_marks_settled(api, make_flight, user_ids):
    """流水与账单相符 → 已结算并写对账时间。"""
    order, bill = _done_flight(make_flight, user_ids)
    assert float(bill.pay_amount) == float(order.pay_amount)   # 账单口径：客户实付

    r = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()
    assert r["code"] == 0 and r["data"]["matched"] == 1 and r["data"]["diffCount"] == 0
    db.session.refresh(bill)
    assert bill.status == 2 and bill.reconciled_at is not None


def test_reconcile_flags_missing_flow(api, make_flight, user_ids):
    """账单有了、支付流水一条都没有 → 缺流水差异，不能标成已结算。"""
    order, bill = _done_flight(make_flight, user_ids)
    db.session.execute(db.text("DELETE FROM payment_transaction WHERE order_id=:o"),
                       {"o": order.id})
    db.session.commit()

    r = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()
    assert (r["data"]["diffCount"], r["data"]["matched"]) == (1, 0)
    d = r["data"]["diffs"][0]
    assert "缺支付流水" in d["reason"] and d["netPaid"] is None
    assert d["diffAmount"] == pytest.approx(float(bill.pay_amount))
    db.session.refresh(bill)
    assert bill.status == 4 and bill.reconciled_at is None


def test_reconcile_flags_overpaid(api, make_flight, user_ids):
    """多一笔成功支付（重复付款/补收没入账单）→ 金额不符差异，差额为负。"""
    order, bill = _done_flight(make_flight, user_ids)
    _txn(order, 10)

    d = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()["data"]["diffs"][0]
    assert "金额不符" in d["reason"]
    assert d["netPaid"] == pytest.approx(float(order.pay_amount) + 10)
    assert d["diffAmount"] == pytest.approx(-10)


def test_reconcile_flags_partial_refund(api, make_flight, user_ids):
    """已完成订单被售后部分退款、账单没冲减 → 有退款差异，净额与差额都要给出来。"""
    order, bill = _done_flight(make_flight, user_ids)
    _txn(order, 5, direction=2, status=4)

    d = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()["data"]["diffs"][0]
    assert "有退款" in d["reason"]
    assert d["netPaid"] == pytest.approx(float(order.pay_amount) - 5)
    assert d["diffAmount"] == pytest.approx(5)


def test_reconcile_unpaid_flow_is_not_money(api, make_flight, user_ids):
    """待支付/失败的流水不算到账：款项未到账要单独成一条差异原因，别和"缺流水"混在一起。"""
    order, bill = _done_flight(make_flight, user_ids)
    _kill_flow(order.id)

    r = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()
    d = r["data"]["diffs"][0]
    assert "款项未到账" in d["reason"] and d["netPaid"] == 0
    db.session.refresh(bill)
    assert bill.status == 4


def test_reconcile_can_be_fixed_and_rechecked(api, make_flight, user_ids):
    """差异单处理完（退款流水作废）再核一次应当平账 —— 财务闭环的关键一步。"""
    order, bill = _done_flight(make_flight, user_ids)
    refund = _txn(order, 5, direction=2, status=4)
    first = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()["data"]
    assert first["checked"] == 1 and first["diffCount"] == 1
    db.session.refresh(bill)
    assert bill.status == 4

    refund.deleted = 1
    db.session.commit()
    second = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()["data"]
    assert second["checked"] == 1 and second["matched"] == 1   # 状态 4 仍在可对账范围内
    db.session.refresh(bill)
    assert bill.status == 2


def test_reconcile_skips_settled_bills(api, make_flight, user_ids):
    """已结算的账单不重复进对账，否则财务一点就把整本账重跑一遍。"""
    _done_flight(make_flight, user_ids)
    api.as_("admin").post("/api/settlement/bills/reconcile", json={})
    r = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()
    assert r["code"] == 40900 and "没有待对账的账单" in r["message"]


# ---------------------------------------------------------------- 门禁与范围

def test_reconcile_requires_reconcile_perm(api, make_flight, user_ids):
    """商家管理员有 settlement:list，但没有 settlement:reconcile：能看账不能核账。"""
    _done_flight(make_flight, user_ids)
    r = api.as_("merchant").post("/api/settlement/bills/reconcile", json={}).get_json()
    assert r["code"] == 40300 and "settlement:reconcile" in r["message"]


def test_reconcile_ids_cannot_cross_merchant(api, make_flight, user_ids, second_merchant, fin):
    """显式勾选 ids 也必须走商家范围：不能靠传别人账单 id 把别家的账核掉。"""
    _, bill = _done_flight(make_flight, user_ids)
    bill.merchant_id = second_merchant
    db.session.commit()

    r = fin.post("/api/settlement/bills/reconcile", json={"ids": [bill.id]}).get_json()
    assert r["code"] == 40900                      # 本商家范围里没有可对账的账单
    db.session.refresh(bill)
    assert bill.status == 1


def test_bills_list_scope_and_filters(api, make_flight, user_ids, second_merchant, fin):
    """账单列表：商家只看本商家；onlyDiff 只挑差异单，汇总口径跟着筛选条件走。"""
    order, bill = _done_flight(make_flight, user_ids)                    # 商家 1，平账
    other_order, other = _done_flight(make_flight, user_ids)             # 挪给商家 2，做出差异
    other.merchant_id = second_merchant
    db.session.commit()
    _kill_flow(other_order.id)
    recon = api.as_("admin").post("/api/settlement/bills/reconcile", json={}).get_json()["data"]
    assert (recon["matched"], recon["diffCount"]) == (1, 1)

    mine = fin.get("/api/settlement/bills").get_json()["data"]
    assert mine["scope"] == "merchant"
    assert {row["merchantId"] for row in mine["list"]} == {1}
    assert bill.settlement_no in [row["settlementNo"] for row in mine["list"]]
    assert other.settlement_no not in [row["settlementNo"] for row in mine["list"]]

    only_diff = api.as_("admin").get("/api/settlement/bills?onlyDiff=1").get_json()["data"]
    assert [row["settlementNo"] for row in only_diff["list"]] == [other.settlement_no]
    assert only_diff["summary"] == {
        "count": 1, "totalFee": pytest.approx(float(other.pay_amount)),
        "pendingFee": 0, "diffCount": 1, "diffFee": pytest.approx(float(other.pay_amount))}

    kw = api.as_("admin").get(f"/api/settlement/bills?keyword={order.order_no}").get_json()["data"]
    assert [row["orderNo"] for row in kw["list"]] == [order.order_no]
    by_status = api.as_("admin").get("/api/settlement/bills?status=2").get_json()["data"]
    assert [row["settlementNo"] for row in by_status["list"]] == [bill.settlement_no]


# ---------------------------------------------------------------- 支付流水 M-73

def test_payments_scoped_by_order_merchant(api, make_flight, user_ids, second_merchant, fin):
    """支付流水是财务核差异的落点：客户侧接口只查本人，这里按订单归属收窄，不是按流水自己。"""
    order, _ = _done_flight(make_flight, user_ids)
    foreign, _ = _done_flight(make_flight, user_ids)
    db.session.execute(db.text("UPDATE orders SET merchant_id=:m WHERE id=:i"),
                       {"m": second_merchant, "i": foreign.id})
    db.session.commit()

    r = fin.get("/api/settlement/payments?direction=1").get_json()["data"]
    assert r["scope"] == "merchant"
    nos = [row["orderNo"] for row in r["list"]]
    assert order.order_no in nos and foreign.order_no not in nos
    assert r["summary"]["income"] == pytest.approx(float(order.pay_amount))
    assert r["summary"]["refund"] == 0

    full = api.as_("admin").get("/api/settlement/payments").get_json()["data"]
    assert full["scope"] == "platform" and len(full["list"]) == 2
    by_kw = api.as_("admin").get(f"/api/settlement/payments?keyword={order.order_no}").get_json()["data"]
    assert [row["orderNo"] for row in by_kw["list"]] == [order.order_no]
    by_status = api.as_("admin").get("/api/settlement/payments?status=1").get_json()["data"]
    assert by_status["list"] == [] and by_status["summary"]["net"] == 0


def test_payments_show_refund_rows(api, make_flight, user_ids):
    """退款单要能按方向筛出来，并和支付一起算出净收入。"""
    order, _ = _done_flight(make_flight, user_ids)
    _txn(order, 6, direction=2, status=4)

    refunds = api.as_("admin").get("/api/settlement/payments?direction=2").get_json()["data"]
    assert [(row["directionName"], row["statusName"], row["amount"]) for row in refunds["list"]] == [
        ("退款", "已退款", 6)]
    net = api.as_("admin").get("/api/settlement/payments").get_json()["data"]["summary"]
    assert net["income"] == pytest.approx(float(order.pay_amount))
    assert net["refund"] == 6 and net["net"] == pytest.approx(float(order.pay_amount) - 6)


# ---------------------------------------------------------------- 发票 M-74

def test_invoice_lifecycle(api, make_flight, user_ids, fin):
    """申请（商家/财务）→ 开具（对账权限）：状态机 0→1→2，重复动作与跳步都要拦。"""
    _, bill = _done_flight(make_flight, user_ids)
    # 未对账（待结算）不能开票：钱还没核实就开票，等于替一笔差异单背书
    blocked = fin.post(f"/api/settlement/bills/{bill.id}/invoice", json={}).get_json()
    assert blocked["code"] == 40900 and "先对账" in blocked["message"]

    api.as_("admin").post("/api/settlement/bills/reconcile", json={})
    applied = fin.post(f"/api/settlement/bills/{bill.id}/invoice",
                                    json={"title": "测试抬头", "taxNo": "91330000TEST"}).get_json()
    assert applied["code"] == 0 and applied["data"]["invoiceStatus"] == 1
    assert applied["data"]["invoiceTitle"] == "测试抬头"
    assert fin.post(f"/api/settlement/bills/{bill.id}/invoice", json={}).get_json()["code"] == 40900

    # 商家侧只有申请权，开具要 settlement:reconcile
    assert api.as_("merchant").post(
        f"/api/settlement/bills/{bill.id}/invoice/issue", json={}).get_json()["code"] == 40300

    issued = fin.post(f"/api/settlement/bills/{bill.id}/invoice/issue").get_json()
    assert issued["code"] == 0 and issued["data"]["invoiceStatus"] == 2
    db.session.refresh(bill)
    assert bill.invoice_status == 2
    assert fin.post(f"/api/settlement/bills/{bill.id}/invoice/issue").get_json()["code"] == 40900
    # 已开具不能再申请（否则会把 2 退回 1）
    assert fin.post(f"/api/settlement/bills/{bill.id}/invoice", json={}).get_json()["code"] == 40900


def test_invoice_cannot_be_issued_before_apply(api, make_flight, user_ids, fin):
    _, bill = _done_flight(make_flight, user_ids)
    bill.status = 2
    db.session.commit()
    r = fin.post(f"/api/settlement/bills/{bill.id}/invoice/issue").get_json()
    assert r["code"] == 40900 and "尚未申请" in r["message"]


def test_invoice_apply_defaults_to_merchant_name(api, make_flight, user_ids, fin):
    """不填抬头时默认用商户名称，别让财务再去猜开票对象。"""
    _, bill = _done_flight(make_flight, user_ids)
    bill.status = 2
    db.session.commit()
    fin.post(f"/api/settlement/bills/{bill.id}/invoice", json={})
    name = db.session.execute(db.text("SELECT merchant_name FROM merchants WHERE id=1")).scalar()
    db.session.refresh(bill)
    assert bill.invoice_title == name and bill.invoice_tax_no is None


def test_invoice_on_other_merchant_bill_forbidden(api, make_flight, user_ids, second_merchant, fin):
    _, bill = _done_flight(make_flight, user_ids)
    bill.status = 2
    bill.merchant_id = second_merchant
    db.session.commit()
    assert fin.post(
        f"/api/settlement/bills/{bill.id}/invoice", json={}).get_json()["code"] == 40300
    assert fin.post(
        f"/api/settlement/bills/{bill.id}/invoice/issue").get_json()["code"] == 40300


def test_invoice_on_missing_bill(api, fin):
    assert fin.post("/api/settlement/bills/999999/invoice", json={}).get_json()["code"] == 40400
    assert fin.post("/api/settlement/bills/999999/invoice/issue").get_json()["code"] == 40400


# ---------------------------------------------------------------- 账单出参与口径

def test_bill_rows_carry_net_and_diff_fields(api, make_flight, user_ids):
    """列表要能直接读出"账单 vs 流水"：净额、差额、差异原因、派生的商家实收。"""
    order, bill = _done_flight(make_flight, user_ids)
    row = api.as_("admin").get("/api/settlement/bills").get_json()["data"]["list"][0]
    assert row["settlementNo"] == bill.settlement_no
    assert row["netPaid"] == pytest.approx(float(order.pay_amount))
    assert row["diffReason"] is None and row["diffAmount"] == 0
    assert row["merchantIncome"] == pytest.approx(float(bill.pay_amount) - float(bill.platform_fee))
    assert row["baseFee"] + row["airspaceFee"] + row["urgentFee"] == pytest.approx(
        float(order.total_amount), 0.02)
    assert row["statusName"] == "待结算" and row["reconciledAt"] is None


def test_settlement_amount_is_customer_payment(api, make_flight, user_ids):
    """口径钉死：账单金额 = 客户实付，不是"扣完抽成的商家实收"。
    两者混用会让每笔账单都凭空差一笔抽成（种子数据就是这么写的，见迁移 p11）。"""
    order, bill = _done_flight(make_flight, user_ids)
    assert float(bill.pay_amount) == float(order.pay_amount)
    assert 0 < float(bill.platform_fee) < float(bill.pay_amount)
