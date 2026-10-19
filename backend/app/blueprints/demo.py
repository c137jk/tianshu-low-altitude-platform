"""一键演示（M-90）：真实走通 下单→支付→接单→推荐→方案→申报→审批→下发→起飞→在空飞行。

权限：demo:run（平台管理员 / 运维）。这条链路会真写订单、支付流水、飞行计划与轨迹，
     所以必须收口，不能任何登录账号都能触发。

两个关键口径（都是对着差距分析改的）：
  1. 机/长/场由 D3 recommend() 决定，但下单前先在内存里预演一遍航线（不落库的草稿 Order
     + build_route + route_compliance），挑一组「不会被禁飞区拦、电量够、载重够」的场站组合。
     旧实现是"抓第一架 status=1 的飞机、强行把电量改成 100"，既和在途任务抢机（误导性 409），
     也会污染在飞飞机的真实状态。
  2. 默认让计划停在「执行中」，由飞行仿真器按帧推进（大屏看得见动画）；
     传 {"finish": true} 才立即降落送达——冒烟/回归用它，不留在途任务。
"""
import math
from decimal import Decimal

from flask import Blueprint, request

from app.common.decorators import current_user, perm_required
from app.common.errors import ApiError
from app.common.response import BizCode, success
from app.extensions import db
from app.models import Aircraft, Order, User, Vertiport
from app.services import dispatch_service, geo, order_service, pricing_service

bp = Blueprint("demo", __name__, url_prefix="/api/demo")

DEMO_GOODS = "文件"
DEMO_WEIGHT_KG = Decimal("1.0")
SENDER_OFFSET = (60.0, 40.0)     # 取货点离场站约 70m：保证 recommend 就近算到的就是这场站
RECEIVER_OFFSET = (120.0, -80.0)


def _customer() -> User:
    c = (db.session.query(User)
         .filter(User.user_type.in_((1, 2)), User.deleted == 0, User.status == 1)
         .order_by(User.id.asc()).first())
    if c is None:
        raise ApiError(BizCode.BIZ_ERROR, "演示数据里没有可用客户账号")
    return c


def _approver(operator: User) -> User:
    """审批人优先取真实空管账号（user_type=5），没有就用当前操作人。"""
    atc = (db.session.query(User).filter_by(user_type=5, deleted=0, status=1)
           .order_by(User.id.asc()).first())
    return atc or operator


def _draft_order(sender, receiver) -> Order:
    """只为复用 build_route 的内存草稿，绝不入库。"""
    return Order(sender_lng=Decimal(str(sender[0])), sender_lat=Decimal(str(sender[1])),
                 receiver_lng=Decimal(str(receiver[0])), receiver_lat=Decimal(str(receiver[1])))


def _pick_combo(mid: int) -> dict:
    """在可立即调度的飞机 × 开放场站里，挑第一组能飞过合规预检的组合。"""
    idle = sorted(dispatch_service.idle_aircraft(mid),
                  key=lambda a: -(a.battery or 0))
    if not idle:
        raise ApiError(BizCode.CONFLICT,
                       "当前没有可立即调度的飞行器（都在执行任务、未在线或未停放场站）。"
                       "可等在飞任务落地，或到资源台账把飞机置为闲置", 409)
    stations = (db.session.query(Vertiport)
                .filter(db.or_(Vertiport.merchant_id == mid, Vertiport.merchant_id.is_(None)),
                        Vertiport.status == 1, Vertiport.deleted == 0).all())
    by_id = {v.id: v for v in stations}
    tried = []
    for ac in idle:
        takeoff = by_id.get(ac.current_vertiport_id)
        if takeoff is None:
            continue
        if float(ac.max_load_kg or 0) < float(DEMO_WEIGHT_KG):
            tried.append(f"{ac.aircraft_no} 载重不足")
            continue
        others = sorted([v for v in stations if v.id != takeoff.id],
                        key=lambda v: geo.haversine_m(float(takeoff.lng), float(takeoff.lat),
                                                      float(v.lng), float(v.lat)))
        for landing in others[:3] + [takeoff]:      # 先试异场站配送，再退回同场站短程
            sender = geo.offset_meters(float(takeoff.lng), float(takeoff.lat), *SENDER_OFFSET)
            receiver = geo.offset_meters(float(landing.lng), float(landing.lat), *RECEIVER_OFFSET)
            route = dispatch_service.build_route(_draft_order(sender, receiver), takeoff, landing)
            need_min = route["durationSec"] / 60 * dispatch_service.RESERVE_RATIO
            need_battery = math.ceil(need_min / max(ac.endurance_min or 1, 1) * 100)
            if (ac.battery or 0) < need_battery:
                tried.append(f"{ac.aircraft_no} 到 {landing.station_name} 需 {need_battery}% 电量")
                continue
            if dispatch_service.route_compliance(route["route"])["forbidden"]:
                tried.append(f"{takeoff.station_name}→{landing.station_name} 航线穿禁飞区")
                continue
            return {"aircraft": ac, "takeoff": takeoff, "landing": landing,
                    "sender": sender, "receiver": receiver, "route": route,
                    "needBattery": need_battery}
    raise ApiError(BizCode.CONFLICT,
                   "演示链路找不到可用航线组合：" + "；".join(dict.fromkeys(tried))[:400], 409)


@bp.post("/run")
@perm_required("demo:run")
def run():
    user = current_user()
    body = request.get_json(silent=True) or {}
    finish = bool(body.get("finish"))
    steps: list[dict] = []

    customer = _customer()
    merchant = pricing_service.get_demo_merchant()
    combo = _pick_combo(merchant.id)
    ac, takeoff, landing = combo["aircraft"], combo["takeoff"], combo["landing"]

    order = order_service.create_order(customer, {
        "senderName": "演示·市民中心", "senderPhone": "13900000001",
        "senderAddress": f"{takeoff.station_name}取货点",
        "senderLng": Decimal(str(round(combo["sender"][0], 6))),
        "senderLat": Decimal(str(round(combo["sender"][1], 6))),
        "receiverName": "演示·社区驿站", "receiverPhone": "13900000002",
        "receiverAddress": f"{landing.station_name}收货点",
        "receiverLng": Decimal(str(round(combo["receiver"][0], 6))),
        "receiverLat": Decimal(str(round(combo["receiver"][1], 6))),
        "goodsType": DEMO_GOODS, "goodsName": "同城急件",
        "weightKg": DEMO_WEIGHT_KG, "deliveryType": 1, "orderType": 1,
        "remark": "一键演示订单",
    })
    steps.append({"step": "1. 客户下单", "detail": f"{order.order_no}｜承接：{merchant.merchant_name}"})

    order_service.pay_order(order, customer, pay_method=1)
    steps.append({"step": "2. 支付成功", "detail": f"￥{order.pay_amount}（Mock 微信）"})

    order_service.staff_accept_order(order, user)
    steps.append({"step": "3. 运营接单", "detail": "订单进入待调度"})

    rec = dispatch_service.recommend(user, order.id)
    if rec["blocked"]:
        raise ApiError(BizCode.CONFLICT,
                       "演示链路无法自动完成调度：" + "；".join(rec["reasons"]), 409)
    r = rec["recommended"]
    ac = db.session.get(Aircraft, r["aircraftId"])          # 以 D3 推荐结果为准
    pilot = db.session.get(User, r["pilotId"])
    if ac is None or pilot is None:
        raise ApiError(BizCode.CONFLICT, "推荐结果缺少飞行器或机长，请检查资源台账", 409)
    takeoff = db.session.get(Vertiport, r["takeoffVertiportId"])
    landing = db.session.get(Vertiport, r["landingVertiportId"])
    steps.append({"step": "4. 智能推荐",
                  "detail": f"{ac.aircraft_no}／机长{pilot.real_name}｜"
                            f"航程{r['distanceM']}m｜预计{r['durationSec']}s｜"
                            f"气象{r['weather']['weatherDesc']} 风{r['weather']['windSpeed']}m/s"})

    plan = dispatch_service.create_plan(user, {
        "orderId": order.id, "aircraftId": r["aircraftId"], "pilotId": r["pilotId"],
        "takeoffVertiportId": r["takeoffVertiportId"], "landingVertiportId": r["landingVertiportId"],
        "altitudeM": r["altitudeM"], "cruiseSpeed": r["cruiseSpeed"],
        "taskName": f"一键演示-{takeoff.station_name}→{landing.station_name}",
        "purpose": "全流程演示（下单到送达）",
    })
    steps.append({"step": "5. 生成调度方案", "detail": plan.plan_no})

    dispatch_service.submit_plan(user, plan)
    steps.append({"step": "6. 提交空管申报", "detail": "UTM Mock 已签收，等待审批"})

    approver = _approver(user)
    dispatch_service.approve_plan(approver, plan, "演示链路自动审批通过")
    steps.append({"step": "7. 空管审批通过",
                  "detail": f"审批人 {approver.real_name or approver.username}"})

    dispatch_service.assign_plan(user, plan)
    steps.append({"step": "8. 下发机长", "detail": "地面站 ACK"})

    dispatch_service.takeoff(user, plan)
    steps.append({"step": "9. 已起飞", "detail": f"{ac.aircraft_no} 离开 {takeoff.station_name}，"
                                                f"仿真器按帧推进（{r['durationSec']}s 航时）"})

    landed = None
    if finish:
        dispatch_service.land(user, plan)
        to_vp = db.session.get(Vertiport, plan.landing_vertiport_id)
        landed = to_vp.station_name if to_vp else None
        steps.append({"step": "10. 已降落送达", "detail": f"{ac.aircraft_no} 归位 {landed}"})
    else:
        steps.append({"step": "10. 巡航中",
                      "detail": "大屏实时飞行页可见飞机沿航线移动；传 {\"finish\": true} 立即送达"})

    return success({
        "steps": steps, "orderNo": order.order_no, "planNo": plan.plan_no,
        "planId": plan.id, "status": plan.status, "aircraftNo": ac.aircraft_no,
        "pilot": pilot.real_name, "takeoffStation": takeoff.station_name,
        "landingStation": landing.station_name,
        "distanceM": r["distanceM"], "durationSec": r["durationSec"],
        "warnings": rec["warnings"], "finished": finish, "landedAt": landed,
    }, "演示链路已跑通")
