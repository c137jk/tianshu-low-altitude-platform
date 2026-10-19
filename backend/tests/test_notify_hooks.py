"""消息通知写入 hook（差距分析 §7：message_notification 过去只有读、没有任何写入）。

三个 hook 各钉一侧，缺任何一个都会留下"系统里发生了事、当事人却不知道"的洞：
  · 订单状态变更——客户付款后要有人接单（通知商家侧），商家接单/拒单后客户要知道。
  · 审批结果——空管驳回会把订单退回待调度，商家不刷新调度台就不知道要重新申报。
  · 设备告警——运维人工标记失联，除了点按钮的那个人之外谁都不该被蒙在鼓里。

顺带钉住两条范围口径：
  · 跨商家不串门：A 商家的新单/告警不能出现在 B 商家的铃铛里（标题带着品类和单量）。
  · 重复动作不重复轰炸：同一台设备连点两次"标记失联"只留一条告警。
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.extensions import db
from app.models import User, Vertiport
from app.services import dispatch_service, geo, notify, order_service


def _u(user_ids, name) -> User:
    return db.session.get(User, user_ids[name])


def _rows(biz_type, user_id=None):
    db.session.expire_all()
    q = "SELECT user_id,title,content,biz_id FROM message_notification WHERE biz_type=:b"
    if user_id is None:
        return db.session.execute(text(q), {"b": biz_type}).mappings().all()
    return db.session.execute(text(q + " AND user_id=:u"), {"b": biz_type, "u": user_id}).mappings().all()


def _count(biz_type) -> int:
    db.session.expire_all()
    return int(db.session.execute(
        text("SELECT COUNT(*) FROM message_notification WHERE biz_type=:b"),
        {"b": biz_type}).scalar() or 0)


def _payload(prefix="通知"):
    vp1, vp2 = db.session.get(Vertiport, 1), db.session.get(Vertiport, 2)
    s = geo.offset_meters(float(vp1.lng), float(vp1.lat), 60.0, 40.0)
    r = geo.offset_meters(float(vp2.lng), float(vp2.lat), 120.0, -80.0)
    return {
        "senderName": f"{prefix}·寄件", "senderPhone": "13900003333",
        "senderAddress": "通知测试取货点",
        "senderLng": Decimal(str(round(s[0], 6))), "senderLat": Decimal(str(round(s[1], 6))),
        "receiverName": f"{prefix}·收件", "receiverPhone": "13900004444",
        "receiverAddress": "通知测试收货点",
        "receiverLng": Decimal(str(round(r[0], 6))), "receiverLat": Decimal(str(round(r[1], 6))),
        "goodsType": "文件", "goodsName": "通知测试件", "weightKg": Decimal("1.0"),
        "deliveryType": 1, "orderType": 1,
    }


# ------------------------------------------------------------------ 订单状态变更
def test_pay_notifies_own_merchant_only(make_flight, user_ids, fresh_user, second_merchant):
    """付款 = 一张真单子。通知商家侧能接单的人，别家商家一条都不该收到。"""
    rival = fresh_user("notify_rival_admin", role_code="merchant_admin", merchant_id=2)
    order, _ = make_flight("paid")
    rows = _rows("order_new", user_ids["merchant"])
    assert rows and order.order_no in rows[0]["title"]
    assert _count("order_new") == len(rows), "只有本商家的 order:list 持有者能收到新单"
    assert not _rows("order_new", rival.id)
    # 内容要够对方做出动作：品类、重量、起讫、金额
    body = rows[0]["content"]
    assert order.goods_type in body and str(order.sender_address) in body


def test_draft_order_does_not_disturb_merchant(make_flight, user_ids):
    """待支付的单子不进商家工作台：否则运营会练出"看到通知也不用管"的坏习惯。"""
    make_flight("draft")
    assert _count("order_new") == 0


def test_accept_notifies_customer_not_merchant(make_flight, user_ids):
    order, _ = make_flight("accepted")
    rows = _rows("order_accept", order.customer_id)
    assert rows and "待调度" in rows[0]["content"]
    assert not _rows("order_accept", user_ids["merchant"])


def test_reject_notifies_customer_with_reason(make_flight, user_ids):
    order, _ = make_flight("paid")
    order_service.staff_reject_order(order, _u(user_ids, "merchant"), "运力不足")
    rows = _rows("order_reject", order.customer_id)
    assert rows and "运力不足" in rows[0]["content"]


def test_cancel_notifies_merchant_side(make_flight, user_ids):
    order, _ = make_flight("paid")
    order_service.cancel_order(order, _u(user_ids, "customer"), "临时不寄了")
    rows = _rows("order_cancel", user_ids["merchant"])
    assert rows and "临时不寄了" in rows[0]["content"]


def test_confirm_receipt_notifies_merchant(make_flight, user_ids):
    """确认收货同时生成结算单，商家侧要知道这单可以开票对账了。"""
    order, _ = make_flight("landed")
    order_service.confirm_receipt(order, _u(user_ids, "customer"))
    rows = _rows("order_finish", user_ids["merchant"])
    assert rows and "结算单" in rows[0]["content"]


def test_notify_failure_does_not_break_the_flow(app_ctx, user_ids, monkeypatch):
    """notify() 里那段 SAVEPOINT 存在的意义：站内信写失败不能让客户下不出单。"""
    monkeypatch.setattr(notify, "_INSERT", text("INSERT INTO no_such_table (a) VALUES (:u)"))
    order = order_service.create_order(_u(user_ids, "customer"), _payload())
    order_service.pay_order(order, _u(user_ids, "customer"), pay_method=1)
    assert order.status == 1 and order.paid == 1


# ------------------------------------------------------------------ 审批结果
def test_approve_notifies_dispatcher_and_customer(make_flight, user_ids):
    order, plan = make_flight("submitted")
    dispatch_service.approve_plan(_u(user_ids, "admin"), plan, "同意，注意避让航道")
    staff_rows = _rows("plan_approve", plan.dispatch_by)
    assert staff_rows and "下发机长" in staff_rows[0]["content"]
    cust_rows = _rows("plan_approve", order.customer_id)
    assert cust_rows and "通过空管审批" in cust_rows[0]["content"]


def test_reject_returns_order_and_notifies(make_flight, user_ids):
    """驳回这条最容易漏：订单退回待调度，商家不知道就不会重新申报。"""
    order, plan = make_flight("submitted")
    dispatch_service.reject_plan(_u(user_ids, "admin"), plan, "航线穿越管制区")
    assert order.status == 2
    assert _rows("plan_reject", plan.dispatch_by)[0]["content"].count("航线穿越管制区") == 1
    assert "无需您重复下单" in _rows("plan_reject", order.customer_id)[0]["content"]


def test_batch_approve_notifies_each_plan(make_flight, user_ids):
    o1, p1 = make_flight("submitted")
    o2, p2 = make_flight("submitted")
    out = dispatch_service.batch_approve(_u(user_ids, "admin"), [p1.id, p2.id], "批量演练")
    assert out["count"] == 2
    # 批量最怕"只通知了勾到的第一单"：两份方案各自都要有商家侧与客户侧的站内信。
    # 两条订单同属种子 customer，所以按方案号点名，不能取第一条。
    assert _count("plan_approve") == 4
    titles = [r["title"] for r in _rows("plan_approve", o1.customer_id)]
    assert any(p1.plan_no in t for t in titles) and any(p2.plan_no in t for t in titles)
    staff_titles = [r["title"] for r in _rows("plan_approve", p1.dispatch_by)]
    assert any(p1.plan_no in t for t in staff_titles)


# ------------------------------------------------------------------ 设备失联告警
def test_lost_mark_alerts_once_and_recovery_paired(api):
    """重复标记只留一条告警；解除时补一条恢复，且"本来没标记"不会编造恢复告警。"""
    api.as_("admin").post("/api/monitor/devices/lost-mark",
                          json={"terminalType": 1, "deviceId": 1, "reason": "图传无信号"})
    first = len(_rows("device_lost"))
    assert first and "TS-UAV-001" in _rows("device_lost")[0]["title"]
    api.as_("admin").post("/api/monitor/devices/lost-mark",
                          json={"terminalType": 1, "deviceId": 1, "reason": "再说一遍换个原因"})
    assert len(_rows("device_lost")) == first, "重复标记不该再刷一条告警"

    api.as_("admin").delete("/api/monitor/devices/lost-mark?terminalType=1&deviceId=1")
    assert len(_rows("device_recover")) == first
    api.as_("admin").delete("/api/monitor/devices/lost-mark?terminalType=1&deviceId=1")
    assert len(_rows("device_recover")) == first, "没有标记可解除时不编造恢复告警"


def test_lost_mark_alerts_own_maintainer_not_rival(api, fresh_user, second_merchant):
    """跨商家的设备运维员收不到别家飞机失联：机号出现在别家铃铛里就是一次信息泄露。"""
    rival = fresh_user("notify_rival_maintainer", role_code="merchant_maintainer", merchant_id=2)
    api.as_("admin").post("/api/monitor/devices/lost-mark",
                          json={"terminalType": 1, "deviceId": 1, "reason": "遥测中断"})
    assert _rows("device_lost", rival.id) == []
    # 平台运维（role 3，merchant_id 为空）必须在收件人里
    ops = db.session.execute(text(
        "SELECT u.id FROM users u JOIN user_role ur ON ur.user_id=u.id"
        " JOIN role r ON r.id=ur.role_id WHERE r.role_code='ops_maintainer'"
        " AND u.deleted=0 AND u.status=1 LIMIT 1")).scalar()
    if ops:
        assert _rows("device_lost", ops)


# ------------------------------------------------------------------ 广播的边界
def test_broadcast_is_not_a_notification_channel(app_ctx):
    """user_id=0 是广播，但 /aux/messages 按 user_id 精确匹配、谁都读不到。

    这条钉住 compliance_monitor 里"广播给平台侧"那种写法的边界：大屏能看见，
    但不能当成"已经通知到某个具体的人"。要通知人，就按权限点把人查出来。
    """
    assert notify.notify(None, "占位标题", "占位内容", biz_type="probe") is True
    assert _rows("probe")[0]["user_id"] == 0
