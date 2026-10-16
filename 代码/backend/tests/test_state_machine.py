"""状态机回归（设计02 的核心承诺：非法跳转必须被挡住）。

这一层直接打 service 函数，不走 HTTP——门禁坏了会在 test_perms_matrix 里报，
这里只关心"业务规则本身是否还成立"，所以断言全部落在状态值与错误文案上。

覆盖两张表的合流：
  订单 0待支付 →1待接单 →2待调度 →3待审批 →4待起飞 →5飞行中 →6已送达 →7已完成（8已取消）
  计划 0待申报 →1待审批 →2已批准待执行 →3执行中 →4已完成（5已驳回 / 6已取消）
"""
import pytest

from app.common.errors import ApiError
from app.extensions import db
from app.models import Aircraft, Settlement, User, Vertiport
from app.services import dispatch_service, order_service


def _u(user_ids, name):
    return db.session.get(User, user_ids[name])


def _other_pilot(plan) -> User:
    """另一位持证机长（同商家）：用来验证"只有被指派的人能确认起降"。"""
    rows = (db.session.query(User)
            .filter(User.merchant_id == plan.merchant_id, User.deleted == 0, User.status == 1)
            .all())
    return next(u for u in rows if u.id != plan.pilot_id and "pilot" in u.role_codes)


def test_happy_path_order_and_plan_stay_in_lockstep(session, make_flight, user_ids, cleanup):
    """每一步订单状态与计划状态必须成对推进：只改一边就是状态机漂移。"""
    expected = {
        "draft": (0, None), "paid": (1, None), "accepted": (2, None),
        "planned": (2, 0), "submitted": (3, 1), "approved": (4, 2),
        "assigned": (4, 2), "flying": (5, 3), "landed": (6, 4),
    }
    for target, (order_status, plan_status) in expected.items():
        cleanup()        # 每档都从干净现场出发，避免上一档的在途任务把资源锁住
        order, plan = make_flight(target)
        assert order.status == order_status, f"{target}: 订单应为 {order_status}"
        if plan_status is None:
            assert plan is None
        else:
            assert plan.status == plan_status, f"{target}: 计划应为 {plan_status}"
        if target == "assigned":
            assert plan.assigned_at is not None
        if target == "flying":
            assert db.session.get(Aircraft, plan.aircraft_id).status == 2


def test_landing_returns_aircraft_to_parking_station(session, make_flight, user_ids):
    """降落必须一次性完成三件事：计划→4、飞机归位闲置并换场站、订单→6已送达。"""
    order, plan = make_flight("landed")
    aircraft = db.session.get(Aircraft, plan.aircraft_id)
    assert plan.status == 4 and plan.actual_landing_at is not None
    assert order.status == 6 and order.delivered_at is not None
    assert aircraft.status == 1
    assert aircraft.current_vertiport_id == plan.landing_vertiport_id
    assert aircraft.task_count == 1
    assert plan.current_altitude == 0 and plan.current_speed == 0


def test_takeoff_occupies_aircraft_and_records_initial_state(session, make_flight, user_ids):
    order, plan = make_flight("flying")
    aircraft = db.session.get(Aircraft, plan.aircraft_id)
    takeoff_vp = db.session.get(Vertiport, plan.takeoff_vertiport_id)
    assert aircraft.status == 2
    assert plan.actual_takeoff_at is not None
    assert round(float(plan.current_lng), 6) == round(float(takeoff_vp.lng), 6)
    assert plan.current_battery == pytest.approx(aircraft.battery, abs=1)


# ---------------------------------------------------------------- 订单侧非法跳转

def test_cannot_pay_same_order_twice(session, make_flight, user_ids):
    order, _ = make_flight("paid")
    with pytest.raises(ApiError) as e:
        order_service.pay_order(order, _u(user_ids, "customer"), pay_method=1)
    assert "不能支付" in e.value.message


def test_cannot_accept_unpaid_order(session, make_flight, user_ids):
    order, _ = make_flight("draft")
    with pytest.raises(ApiError) as e:
        order_service.staff_accept_order(order, _u(user_ids, "merchant"))
    assert "不能接单" in e.value.message


def test_customer_cannot_accept_own_order(session, make_flight, user_ids):
    """接单是运营方动作：客户即使知道订单号也不能自己接单。"""
    order, _ = make_flight("paid")
    with pytest.raises(ApiError) as e:
        order_service.staff_accept_order(order, _u(user_ids, "customer"))
    assert "只有运营方" in e.value.message


def test_other_merchant_cannot_take_the_order(session, make_flight, user_ids, fresh_user,
                                              second_merchant):
    order, _ = make_flight("paid")
    rival = fresh_user("rival_staff", role_code="dispatcher", merchant_id=second_merchant)
    with pytest.raises(ApiError) as e:
        order_service.staff_accept_order(order, rival)
    assert "其他商家" in e.value.message


def test_reject_requires_reason_then_refunds(session, make_flight, user_ids):
    from app.models import PaymentTransaction
    order, _ = make_flight("paid")
    with pytest.raises(ApiError) as e:
        order_service.staff_reject_order(order, _u(user_ids, "merchant"), "   ")
    assert "必须填写原因" in e.value.message

    order_service.staff_reject_order(order, _u(user_ids, "merchant"), "货类不承接")
    assert order.status == 8 and order.cancel_reason.startswith("商家拒单")
    refunds = (db.session.query(PaymentTransaction)
               .filter_by(order_id=order.id, direction=2).all())
    assert len(refunds) == 1 and refunds[0].status == 4


def test_customer_can_only_self_cancel_before_dispatch(session, make_flight, user_ids):
    """0/1 能自助取消；进入调度（2）之后只能走客服。"""
    order, _ = make_flight("accepted")
    with pytest.raises(ApiError) as e:
        order_service.cancel_order(order, _u(user_ids, "customer"), "不想要了")
    assert "不能自助取消" in e.value.message

    cancellable, _ = make_flight("paid")
    order_service.cancel_order(cancellable, _u(user_ids, "customer"), "不想要了")
    assert cancellable.status == 8


def test_cancel_by_other_customer_forbidden(session, make_flight, user_ids, fresh_user):
    order, _ = make_flight("draft")
    stranger = fresh_user("other_cust", user_type=1, role_code="customer", merchant_id=None)
    with pytest.raises(ApiError) as e:
        order_service.cancel_order(order, stranger, "偷取消")
    assert "只能取消自己的订单" in e.value.message


def test_confirm_receipt_only_after_delivered_and_creates_settlement(session, make_flight, user_ids):
    order, _ = make_flight("flying")
    with pytest.raises(ApiError) as e:
        order_service.confirm_receipt(order, _u(user_ids, "customer"))
    assert "尚未送达" in e.value.message

    landed, _ = make_flight("landed")
    order_service.confirm_receipt(landed, _u(user_ids, "customer"))
    assert landed.status == 7 and landed.finished_at is not None
    st = db.session.query(Settlement).filter_by(order_id=landed.id, deleted=0).first()
    assert st is not None and float(st.pay_amount) == float(landed.pay_amount)


# ---------------------------------------------------------------- 计划侧非法跳转

def test_plan_needs_order_in_dispatch_pool(session, make_flight, user_ids):
    order, _ = make_flight("paid")        # 1待接单
    with pytest.raises(ApiError) as e:
        dispatch_service.create_plan(_u(user_ids, "merchant"), {"orderId": order.id})
    assert "只有待调度订单" in e.value.message


def test_submit_and_approve_must_follow_order(session, make_flight, user_ids):
    _, plan = make_flight("planned")
    with pytest.raises(ApiError) as e:            # 还没申报就审批
        dispatch_service.approve_plan(_u(user_ids, "admin"), plan, "太急了")
    assert "只有待审批方案能审批" in e.value.message

    dispatch_service.submit_plan(_u(user_ids, "merchant"), plan)
    with pytest.raises(ApiError) as e:            # 重复申报
        dispatch_service.submit_plan(_u(user_ids, "merchant"), plan)
    assert "只有待申报方案能提交申报" in e.value.message

    dispatch_service.approve_plan(_u(user_ids, "admin"), plan, "同意")
    with pytest.raises(ApiError) as e:            # 批完再批
        dispatch_service.approve_plan(_u(user_ids, "admin"), plan, "再批一次")
    assert "只有待审批方案能审批" in e.value.message


def test_rejected_plan_can_be_fixed_and_resubmitted(session, make_flight, user_ids):
    """驳回不是死路：订单回 2待调度、计划回 5，方案可重建并重新申报。"""
    order, plan = make_flight("submitted")
    dispatch_service.reject_plan(_u(user_ids, "admin"), plan, "气象窗口偏窄")
    assert plan.status == 5 and order.status == 2
    assert plan.reject_reason == "气象窗口偏窄"

    rebuilt = dispatch_service.create_plan(_u(user_ids, "merchant"), {
        "orderId": order.id, "aircraftId": plan.aircraft_id, "pilotId": plan.pilot_id,
        "takeoffVertiportId": plan.takeoff_vertiport_id,
        "landingVertiportId": plan.landing_vertiport_id,
        "altitudeM": 90, "cruiseSpeed": 12, "taskName": "pytest-改后再报"})
    assert rebuilt.id == plan.id and rebuilt.status == 0
    dispatch_service.submit_plan(_u(user_ids, "merchant"), rebuilt)
    assert rebuilt.status == 1 and order.status == 3


def test_cannot_takeoff_before_assign(session, make_flight, user_ids):
    _, plan = make_flight("approved")     # 状态 2 但 assigned_at 为空
    assert plan.assigned_at is None
    with pytest.raises(ApiError) as e:
        dispatch_service.takeoff(_u(user_ids, "pilot"), plan)
    assert "已下发机长后才能起飞" in e.value.message


def test_cannot_land_a_plan_that_is_not_flying(session, make_flight, user_ids):
    _, plan = make_flight("approved")
    with pytest.raises(ApiError) as e:
        dispatch_service.land(_u(user_ids, "pilot"), plan)
    assert "只有执行中的计划能确认降落" in e.value.message


def test_only_assigned_pilot_can_takeoff_and_land(session, make_flight, user_ids):
    _, plan = make_flight("assigned")
    other = _other_pilot(plan)
    assert other.id != plan.pilot_id and "pilot" in other.role_codes
    with pytest.raises(ApiError) as e:
        dispatch_service.takeoff(other, plan)
    assert "只有被指派的机长能确认起飞" in e.value.message

    dispatch_service.takeoff(db.session.get(User, plan.pilot_id), plan)
    with pytest.raises(ApiError) as e:
        dispatch_service.land(other, plan)
    assert "只有被指派的机长能确认降落" in e.value.message


def test_running_plan_cannot_be_cancelled(session, make_flight, user_ids):
    """起飞之后只能走应急流程，取消口子必须关闭（否则飞机状态无处安放）。"""
    _, plan = make_flight("flying")
    with pytest.raises(ApiError) as e:
        dispatch_service.cancel_plan(_u(user_ids, "merchant"), plan, "临时不飞了")
    assert "不能取消" in e.value.message


def test_cancel_before_takeoff_returns_order_to_pool(session, make_flight, user_ids):
    order, plan = make_flight("approved")
    dispatch_service.cancel_plan(_u(user_ids, "merchant"), plan, "客户改期")
    assert plan.status == 6 and order.status == 2
    assert "客户改期" in plan.remark


# ---------------------------------------------------------------- 资源与合规闸口

def test_aircraft_busy_while_another_plan_is_running(session, make_flight, user_ids):
    """在途任务锁机：第二张订单不能把同一架飞机再排一次。"""
    flying_order, plan = make_flight("flying")
    ac = db.session.get(Aircraft, plan.aircraft_id)
    second, _ = make_flight("accepted")
    with pytest.raises(ApiError) as e:
        dispatch_service.create_plan(_u(user_ids, "merchant"), {
            "orderId": second.id, "aircraftId": ac.id, "pilotId": plan.pilot_id,
            "takeoffVertiportId": ac.current_vertiport_id, "landingVertiportId": ac.current_vertiport_id,
            "altitudeM": 80, "cruiseSpeed": 12})
    assert "已有在途任务" in e.value.message


def test_idle_plan_does_not_lock_resources(session, make_flight, user_ids):
    """未下发的"已批准"方案不占资源——设计里资源占用只算执行中/已下发待执行。"""
    _, first = make_flight("approved")
    occupied = lambda: {p.id for p in dispatch_service._occupied_plan_query().all()}  # noqa: E731
    assert first.id not in occupied()

    _, second = make_flight("assigned")
    now_occupied = occupied()
    assert second.id in now_occupied and first.id not in now_occupied
    assert second.aircraft_id in dispatch_service._aircraft_busy_ids()
    assert second.pilot_id in dispatch_service._pilot_busy_ids()


def test_low_battery_blocks_plan_creation(session, make_flight, user_ids):
    """电量要满足航时×1.3 预留，否则申报直接被拒（不是"先批了再说"）。"""
    order, _ = make_flight("accepted")
    ac = db.session.get(Aircraft, 1)
    ac.battery = 1
    db.session.commit()
    with pytest.raises(ApiError) as e:
        dispatch_service.create_plan(_u(user_ids, "merchant"), {
            "orderId": order.id, "aircraftId": ac.id, "pilotId": user_ids["pilot"],
            "takeoffVertiportId": ac.current_vertiport_id,
            "landingVertiportId": ac.current_vertiport_id,
            "altitudeM": 80, "cruiseSpeed": 12})
    assert "电量" in e.value.message


def test_overweight_cargo_blocks_plan_creation(session, make_flight, user_ids):
    order, _ = make_flight("accepted")
    order.weight_kg = 999
    ac = db.session.get(Aircraft, 1)
    db.session.commit()
    with pytest.raises(ApiError) as e:
        dispatch_service.create_plan(_u(user_ids, "merchant"), {
            "orderId": order.id, "aircraftId": ac.id, "pilotId": user_ids["pilot"],
            "takeoffVertiportId": ac.current_vertiport_id,
            "landingVertiportId": ac.current_vertiport_id,
            "altitudeM": 80, "cruiseSpeed": 12})
    assert "载重" in e.value.message


def test_route_crossing_no_fly_zone_is_blocked(session, make_flight, user_ids):
    """禁飞区在申报前就拦下（设计03：合规预检是闸口而不是提示）。"""
    order, _ = make_flight("accepted")
    ac = db.session.get(Aircraft, 1)
    # 龙湾机场净空禁飞区：120.83~120.88 / 27.90~27.93，把收件点挪进区内
    order.receiver_lng = 120.8550
    order.receiver_lat = 27.9150
    db.session.commit()
    with pytest.raises(ApiError) as e:
        dispatch_service.create_plan(_u(user_ids, "merchant"), {
            "orderId": order.id, "aircraftId": ac.id, "pilotId": user_ids["pilot"],
            "takeoffVertiportId": ac.current_vertiport_id, "landingVertiportId": 2,
            "altitudeM": 80, "cruiseSpeed": 12})
    assert "禁飞" in e.value.message


def test_closed_vertiport_blocks_plan(session, make_flight, user_ids):
    order, _ = make_flight("accepted")
    ac = db.session.get(Aircraft, 1)
    landing = db.session.get(Vertiport, 2)
    landing.status = 3          # 关闭
    db.session.commit()
    with pytest.raises(ApiError) as e:
        dispatch_service.create_plan(_u(user_ids, "merchant"), {
            "orderId": order.id, "aircraftId": ac.id, "pilotId": user_ids["pilot"],
            "takeoffVertiportId": ac.current_vertiport_id, "landingVertiportId": landing.id,
            "altitudeM": 80, "cruiseSpeed": 12})
    assert "开放状态" in e.value.message


# ---------------------------------------------------------------- 查询侧数据范围

def test_order_list_is_scoped_by_identity(session, make_flight, user_ids, fresh_user):
    order, _ = make_flight("paid")
    mine = order_service.list_orders(_u(user_ids, "customer"))
    assert mine["total"] >= 1 and all(o["customerId"] == user_ids["customer"] for o in mine["list"])

    rival = fresh_user("rival_cust", user_type=1, role_code="customer", merchant_id=None)
    assert order_service.list_orders(rival)["total"] == 0
    with pytest.raises(ApiError) as e:
        order_service.get_visible_order(order.id, rival)
    assert "无权查看该订单" in e.value.message


def test_pilot_only_sees_own_plans(session, make_flight, user_ids):
    _, plan = make_flight("assigned")
    other = _other_pilot(plan)
    with pytest.raises(ApiError) as e:
        dispatch_service._get_plan_for_user(plan.id, other)
    assert "分配给自己" in e.value.message
    assert dispatch_service._get_plan_for_user(plan.id, _u(user_ids, "admin")).id == plan.id
    assert dispatch_service._get_plan_for_user(plan.id, db.session.get(User, plan.pilot_id)).id == plan.id
