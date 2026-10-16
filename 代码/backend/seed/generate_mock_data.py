"""天枢平台 Mock 数据生成脚本（设计07 L2 层）。

用法（在 backend 目录，激活虚拟环境后）：
  python seed/generate_mock_data.py                 # 幂等补齐：客户/订单/流水/结算/在途计划
  python seed/generate_mock_data.py --force         # 先清空全部 Mock 数据再重新生成
  python seed/generate_mock_data.py --orders 60     # 指定基础订单数（默认30）
  python seed/generate_mock_data.py --customers 8   # 指定客户数（默认5）

幂等原则：真实演示数据（TS/PT/ST 前缀、admin 等账号）永不删除；
         --force 只清 Mock 前缀（TSM/PTM/PTMR/STM/FPM）与 Mock 飞机/机长。
W3 新增：2 个待调度订单 + 1 待审批 + 2 待起飞 + 1 飞行中 + 1 已送达，
         配套 5 张飞行计划、审批流水，并保证演示新单始终有空闲人机可推荐。
"""
import argparse
import random
from datetime import datetime, timedelta
from decimal import Decimal

from app import create_app
from app.adapters.weather import weather_adapter
from app.common.crypto import encrypt_phone, phone_digest
from app.extensions import db
from app.models import (
    User, Order, PaymentTransaction, Settlement,
    Aircraft, Vertiport, FlightPlan, ApprovalRecord,
)
from app.models.rbac import Role, user_role
from app.services import pricing_service, dispatch_service

random.seed(20260914)  # 固定种子，演示数据可复现

MID = 1  # 演示商家 id
# ------------------------------------------------------------------
# 语料：温州东部（瓯江口/龙湾/洞头/乐清，GCJ-02，坐标已经高德地理编码核对；
# 刻意避开龙湾机场净空禁飞框 120.83~120.88 / 27.90~27.93）
# 索引 0~4 为瓯江口核心区（短驳演示簇，落在可飞示范区 120.885~120.975/27.915~27.975 内）
# ------------------------------------------------------------------
PLACES = [
    ("瓯江口产业集聚区管委会", 120.9190, 27.9349),
    ("瓯江口新月公园", 120.9284, 27.9522),
    # "温州湾未来科技城"不是高德真实 POI（geocode 会把"温州湾"解析成海湾、定位到海里），
    # 换成地图上真实存在、regeo 在陆地上的"温州瓯江口规划展示馆"（inputtips 核准坐标）
    ("温州瓯江口规划展示馆", 120.9202, 27.9272),
    ("灵昆街道便民服务中心", 120.8886, 27.9635),
    ("温医大附二院瓯江口院区", 120.9261, 27.9325),
    ("霓屿街道办事处", 121.0630, 27.8526),
    ("元觉街道办事处", 121.1412, 27.8826),
    ("洞头区行政服务中心", 121.1444, 27.8444),
    ("洞头新城客运站", 121.1581, 27.8477),
    ("大门镇便民服务点", 121.0909, 27.9622),
    ("乐清柳市电器城", 120.8984, 28.0461),
    ("乐清经济开发区管委会", 120.9900, 28.0663),
]
GOODS = [
    ("生鲜食材", "冷链海鲜礼盒", 3.5), ("文件票据", "合同文件", 0.4),
    ("医药用品", "急救药品", 1.2), ("电子配件", "工业传感器", 2.0),
    ("电商小件", "日用百货", 1.8), ("机加工件", "精密零件", 8.0),
    ("生鲜食材", "时令水果", 5.5), ("应急物资", "抢修工具包", 6.5),
]
NAMES = ["王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴", "徐", "孙"]
TAIL = ["明", "华", "强", "伟", "芳", "娜", "敏", "静", "磊", "军", "杰", "娟"]

# 基础订单的状态分布（不含待支付/待接单——那两类由演示动作现场产生）
STATUS_POOL = [7] * 12 + [8] * 6 + [1] * 4 + [6] * 3 + [2] * 3 + [0] * 2
# W3 在途订单：(订单状态, 计划状态)；计划状态 None = 不建计划
INFLIGHT = [
    (2, None), (2, None),   # 待调度 ×2，进调度台订单池
    (3, 1),                 # 待审批 ×1，计划待审批
    (4, 2),                 # 待起飞 ×1，计划已批准未下发
    (4, 2),                 # 待起飞 ×1，计划已下发机长
    (5, 3),                 # 飞行中 ×1
    (6, 4),                 # 已送达 ×1
]


def rand_name():
    return random.choice(NAMES) + random.choice(TAIL)


def rand_phone():
    return f"139{random.randint(10000000, 99999999)}"


def gen_no(prefix):
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"


# ------------------------------------------------------------------
# 1. 幂等补齐：机长账号与 dispatch:execute 权限（DDL 升级，不重跑建库）
# ------------------------------------------------------------------
def ensure_staff():
    """老库补齐：权限33、机长授权、陈/林两位机长；幂等。"""
    # 权限点 33 与机长角色授权（INSERT IGNORE，重复执行不报错）
    db.session.execute(db.text(
        "INSERT IGNORE INTO permission (id,parent_id,perm_code,perm_name,perm_type,path,sort) "
        "VALUES (33,3,'dispatch:execute','起飞/降落确认',3,NULL,5)"))
    db.session.execute(db.text(
        "INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (7, 33)"))
    # 复用 admin 的密码哈希，保证 Mock 账号同为 Admin@123
    admin = db.session.get(User, 1)
    pw_hash = admin.password_hash
    pilot_role = db.session.query(Role).filter_by(role_code="pilot").first()
    for username, real_name, phone in (
            ("pilot", "陈机长", "13800000004"),
            ("pilot02", "林机长", "13800000005")):
        u = db.session.query(User).filter_by(username=username).first()
        if u is None:
            u = User(username=username, password_hash=pw_hash, user_type=3,
                     real_name=real_name, phone_cipher=encrypt_phone(phone),
                     phone_digest=phone_digest(phone), realname_status=2, gender=1,
                     merchant_id=MID, status=1)
            db.session.add(u)
            db.session.flush()
        exists = db.session.query(user_role).filter_by(
            user_id=u.id, role_id=pilot_role.id).first()
        if not exists:
            db.session.execute(user_role.insert().values(
                user_id=u.id, role_id=pilot_role.id, granted_by=1))
    db.session.commit()
    print("  · 机长账号/权限已就绪：pilot 陈机长、pilot02 林机长（密码 Admin@123）")


# ------------------------------------------------------------------
# 2. 客户
# ------------------------------------------------------------------
def ensure_customers(n=5):
    users = []
    for i in range(1, n + 1):
        username = f"cust{i:02d}"
        u = db.session.query(User).filter_by(username=username).first()
        phone = f"137{10000000 + i * 137:08d}"
        if u is None:
            admin = db.session.get(User, 1)
            u = User(username=username, password_hash=admin.password_hash,
                     user_type=1, real_name=f"演示客户{i}",
                     phone_cipher=encrypt_phone(phone), phone_digest=phone_digest(phone),
                     realname_status=2, gender=random.choice([1, 2]), status=1)
            db.session.add(u)
            db.session.flush()
            role_customer = db.session.query(Role).filter_by(role_code="customer").first()
            db.session.execute(user_role.insert().values(
                user_id=u.id, role_id=role_customer.id, granted_by=1))
            db.session.execute(db.text(
                "INSERT INTO customer_profile (user_id, customer_type, verified_at) "
                "VALUES (:uid, 1, NOW())"), {"uid": u.id})
        users.append(u)
    db.session.commit()
    return users


# ------------------------------------------------------------------
# 3. 订单（含支付/退款流水；状态6/7 再生成结算单）
# ------------------------------------------------------------------
def _make_order(seq, customer, status, now, base_dt, span_days=26, short_haul=False):
    if short_haul:
        # 在途/调度台演示单：只用瓯江口核心区相邻点（1.5~5km，全在可飞示范区内），
        # 保证调度台一键推荐时航程、电量/续航校验必然通过
        cluster = [PLACES[i] for i in (0, 1, 2, 3, 4)]
        sp = random.choice(cluster)
        rp = random.choice([p for p in cluster if p != sp])
    else:
        sp = random.choice(PLACES)
        candidates = [p for p in PLACES if p != sp]
        rp = random.choice(candidates)
    goods_type, goods_name, weight = random.choice(GOODS)
    urgent = random.random() < 0.18
    delivery_type = 2 if urgent else 1
    pricing = pricing_service.estimate(
        sender_lng=sp[1], sender_lat=sp[2], receiver_lng=rp[1], receiver_lat=rp[2],
        weight_kg=weight, goods_type=goods_type, goods_name=goods_name,
        delivery_type=delivery_type)
    pay_fee = round(pricing["totalAmount"], 2)
    merchant_income = round(pay_fee * (1 - float(pricing_service.PLATFORM_RATE)), 2)
    created = base_dt + timedelta(
        minutes=random.randint(0, 60 * 24 * span_days))  # 在 base_dt 之后的跨度内随机下单
    sender_name, receiver_name = rand_name(), rand_name()
    order = Order(
        order_no=f"TSM{created.strftime('%Y%m%d')}{seq:04d}",
        customer_id=customer.id, merchant_id=MID, order_type=1, status=status,
        sender_name=sender_name, sender_phone=encrypt_phone(rand_phone()),
        sender_address=sp[0], sender_lng=sp[1], sender_lat=sp[2],
        receiver_name=receiver_name, receiver_phone=encrypt_phone(rand_phone()),
        receiver_address=rp[0], receiver_lng=rp[1], receiver_lat=rp[2],
        goods_type=goods_type, goods_name=goods_name,
        weight_kg=Decimal(str(weight)), quantity=1, delivery_type=delivery_type,
        base_fee=pricing["baseFee"], airspace_fee=pricing["airspaceFee"],
        urgent_fee=pricing["urgentFee"], total_amount=pricing["totalAmount"],
        pay_amount=pay_fee, pay_method=random.choice([1, 2]),
        created_at=created, updated_at=created)
    db.session.add(order)
    db.session.flush()
    # 2~7 都是已支付后的状态
    if status in (1, 2, 3, 4, 5, 6, 7) or (status == 8 and random.random() < 0.7):
        order.paid, order.paid_at = 1, created + timedelta(minutes=2)
        txn = PaymentTransaction(
            txn_no=f"PTM{created.strftime('%Y%m%d')}{seq:04d}", order_id=order.id,
            user_id=customer.id, amount=pay_fee, direction=1,
            pay_method=order.pay_method, channel_txn_no=f"MOCK{random.randint(10**11, 10**12-1)}",
            status=2, paid_at=order.paid_at,
            raw_resp={"channel": "mock", "result": "SUCCESS"}, created_at=order.paid_at)
        db.session.add(txn)
    if status == 8:
        order.cancel_reason = random.choice(["客户临时取消", "地址有误重新下单", "超时未支付自动取消"])
        order.updated_at = created + timedelta(minutes=15)
        if order.paid:
            rf = PaymentTransaction(
                txn_no=f"PTMR{created.strftime('%Y%m%d')}{seq:04d}", order_id=order.id,
                user_id=customer.id, amount=pay_fee, direction=2,
                pay_method=order.pay_method, channel_txn_no=f"MOCKR{random.randint(10**11, 10**12-1)}",
                status=4, paid_at=order.updated_at,
                raw_resp={"channel": "mock", "refund": "SUCCESS"},
                created_at=order.updated_at)
            db.session.add(rf)
    if status == 6:
        order.delivered_at = created + timedelta(minutes=random.randint(20, 50))
        order.updated_at = order.delivered_at
    if status == 7:
        delivered = created + timedelta(minutes=random.randint(20, 50))
        finished = delivered + timedelta(hours=random.randint(1, 20))
        order.delivered_at, order.finished_at, order.updated_at = delivered, finished, finished
        # 账单口径：pay_amount = 客户实付（与 payment_transaction 净额一一对应，对账才比得出真伪）。
        # 商家实收是派生值 = pay_amount - platform_fee，不能塞进 pay_amount，否则每笔账单都像差一笔抽成。
        # 近 7 天完成的订单留成 待结算：结算页一进来就有活可对账，不然全库已结算，对账按钮永远点不动。
        recent = finished > now - timedelta(days=7)
        st = Settlement(
            settlement_no=f"STM{created.strftime('%Y%m%d')}{seq:04d}", order_id=order.id,
            merchant_id=MID, customer_id=customer.id, base_fee=pricing["baseFee"],
            airspace_fee=pricing["airspaceFee"], urgent_fee=pricing["urgentFee"],
            platform_fee=round(pay_fee - merchant_income, 2), pay_amount=pay_fee,
            status=1 if recent else 2,
            reconciled_at=None if recent else finished + timedelta(days=1), created_at=finished,
            updated_at=finished + timedelta(days=1))
        db.session.add(st)
    return order


def make_orders(customers, n=30):
    now = datetime.now()
    base_dt = now - timedelta(days=27)
    orders = []
    for i in range(n):
        status = STATUS_POOL[i % len(STATUS_POOL)]
        orders.append(_make_order(i + 1, random.choice(customers), status, now, base_dt))
    db.session.commit()
    return orders


# ------------------------------------------------------------------
# 4. W3 在途订单 + 飞行计划（调度/审批/机长台演示数据）
# ------------------------------------------------------------------
def _nearest_station(lng, lat, stations):
    return min(stations, key=lambda v: dispatch_service.haversine_m(
        lng, lat, float(v.lng), float(v.lat)))


def make_inflight_plans(customers, seq_start):
    """生成 7 个在途订单与 5 张计划；保证陈机长(pilot4)与 2 号机演示后仍空闲。"""
    now = datetime.now()
    base_dt = now - timedelta(hours=20)
    stations = db.session.query(Vertiport).filter(Vertiport.deleted == 0).all()
    pilot4 = db.session.query(User).filter_by(username="pilot").first()
    pilot5 = db.session.query(User).filter_by(username="pilot02").first()
    ac1 = db.session.get(Aircraft, 1)
    ac2 = db.session.get(Aircraft, 2)

    # Mock 三号机：专门用于"飞行中"演示，结束后随 --force 清除
    ac3 = db.session.query(Aircraft).filter_by(aircraft_no="TS-UAV-M03").first()
    if ac3 is None:
        ac3 = Aircraft(aircraft_no="TS-UAV-M03", merchant_id=MID, brand="大疆", model="FlyCart 30",
                       serial_no="SNM03", endurance_min=36, max_load_kg=Decimal("30.00"),
                       max_speed=Decimal("18.0"), max_altitude=120,
                       purchase_at=datetime(2026, 5, 1).date(),
                       current_vertiport_id=1, status=1, health_score=90,
                       battery=100, online=1)
        db.session.add(ac3)
        db.session.flush()

    # Mock 三号机长：专门执飞"飞行中"那单，保持陈/林机长有空闲
    pilot03 = db.session.query(User).filter_by(username="pilot03").first()
    if pilot03 is None:
        admin = db.session.get(User, 1)
        phone = "13800000006"
        pilot03 = User(username="pilot03", password_hash=admin.password_hash, user_type=3,
                       real_name="赵机长", phone_cipher=encrypt_phone(phone),
                       phone_digest=phone_digest(phone), realname_status=2, gender=1,
                       merchant_id=MID, status=1)
        db.session.add(pilot03)
        db.session.flush()
        role_pilot = db.session.query(Role).filter_by(role_code="pilot").first()
        db.session.execute(user_role.insert().values(
            user_id=pilot03.id, role_id=role_pilot.id, granted_by=1))

    seq = seq_start
    plan_seq = 1
    for order_status, plan_status in INFLIGHT:
        order = _make_order(seq, random.choice(customers), order_status, now, base_dt,
                            span_days=0, short_haul=True)
        db.session.flush()
        seq += 1
        if plan_status is None:
            continue
        takeoff = _nearest_station(float(order.sender_lng), float(order.sender_lat), stations)
        landing = _nearest_station(float(order.receiver_lng), float(order.receiver_lat), stations)
        route = dispatch_service.build_route(order, takeoff, landing)
        weather = weather_adapter.current(
            (float(order.sender_lng) + float(order.receiver_lng)) / 2,
            (float(order.sender_lat) + float(order.receiver_lat)) / 2,
            at=now)
        # 人机分配：待审批/未下发→陈机长+1号机；已下发→林机长+1号机；
        # 飞行中→赵机长+3号机；已送达→赵机长+2号机（已归位空闲）
        if plan_status == 3:
            aircraft, pilot = ac3, pilot03
        elif plan_status == 4:
            aircraft, pilot = ac2, pilot03
        elif order_status == 4 and plan_seq == 3:
            aircraft, pilot = ac1, pilot5   # 已下发那张
        else:
            aircraft, pilot = ac1, pilot4
        start_at = now - timedelta(minutes=30)
        end_at = start_at + timedelta(seconds=route["durationSec"])
        plan = FlightPlan(
            plan_no=f"FPM{now.strftime('%Y%m%d')}{plan_seq:04d}", order_id=order.id,
            merchant_id=MID, status=plan_status, aircraft_id=aircraft.id, pilot_id=pilot.id,
            takeoff_vertiport_id=takeoff.id, landing_vertiport_id=landing.id,
            route_json=route["route"], route_distance_m=route["distanceM"],
            est_duration_sec=route["durationSec"], dispatch_by=2,
            dispatched_at=now - timedelta(minutes=25),
            task_name=f"{order.goods_type}配送-{order.order_no[-6:]}",
            purpose="城市低空物流配送", plan_start_at=start_at, plan_end_at=end_at,
            altitude_m=100, cruise_speed=Decimal("12.0"), weather_json=weather)
        db.session.add(plan)
        db.session.flush()

        if plan_status == 1:  # 待审批：已提交空管
            plan.submitted_at = now - timedelta(minutes=12)
        elif plan_status == 2:  # 已批准
            plan.submitted_at = now - timedelta(minutes=40)
            plan.approve_result, plan.approved_by, plan.approved_at = 1, 1, now - timedelta(minutes=30)
            plan.approve_opinion = "航线合规，气象适飞，同意。"
            db.session.add(ApprovalRecord(plan_id=plan.id, approver_id=1, result=1,
                                          opinion="航线合规，气象适飞，同意。",
                                          created_at=plan.approved_at))
            if order_status == 4 and plan_seq == 3:
                plan.assigned_at = now - timedelta(minutes=18)   # 第二张待起飞：已下发
        elif plan_status == 3:  # 执行中
            plan.submitted_at = now - timedelta(hours=2)
            plan.approve_result, plan.approved_by, plan.approved_at = 1, 1, now - timedelta(hours=1, minutes=45)
            plan.approve_opinion = "同意，注意风速变化。"
            plan.assigned_at = now - timedelta(hours=1, minutes=30)
            plan.actual_takeoff_at = now - timedelta(minutes=12)
            mid_pt = route["route"][len(route["route"]) // 2]
            plan.current_lng, plan.current_lat = mid_pt["lng"], mid_pt["lat"]
            plan.current_altitude, plan.current_speed, plan.current_battery = 95, Decimal("11.6"), 68
            plan.last_report_at = now
            db.session.add(ApprovalRecord(plan_id=plan.id, approver_id=1, result=1,
                                          opinion="同意，注意风速变化。",
                                          created_at=plan.approved_at))
            aircraft.status = 2
            aircraft.current_lng, aircraft.current_lat = mid_pt["lng"], mid_pt["lat"]
            aircraft.battery = 68
        elif plan_status == 4:  # 已完成
            takeoff_at = now - timedelta(hours=6)
            land_at = takeoff_at + timedelta(seconds=route["durationSec"])
            plan.submitted_at = takeoff_at - timedelta(minutes=20)
            plan.approve_result, plan.approved_by, plan.approved_at = 1, 1, takeoff_at - timedelta(minutes=12)
            plan.approve_opinion = "同意。"
            plan.assigned_at = takeoff_at - timedelta(minutes=8)
            plan.actual_takeoff_at = takeoff_at
            plan.actual_landing_at = land_at
            plan.current_lng, plan.current_lat = landing.lng, landing.lat
            plan.current_altitude, plan.current_speed = 0, 0
            plan.last_report_at = land_at
            db.session.add(ApprovalRecord(plan_id=plan.id, approver_id=1, result=1,
                                          opinion="同意。", created_at=plan.approved_at))
            aircraft.status = 1
            aircraft.current_vertiport_id = landing.id
            aircraft.current_lng, aircraft.current_lat = landing.lng, landing.lat
            aircraft.battery = 88
            aircraft.task_count = (aircraft.task_count or 0) + 1
            aircraft.total_flight_min = (aircraft.total_flight_min or 0) + max(
                1, int(route["durationSec"] / 60))
            order.delivered_at = land_at
            order.updated_at = land_at
        plan_seq += 1
    db.session.commit()


# ------------------------------------------------------------------
# 5. 清空 Mock 数据（--force）
# ------------------------------------------------------------------
def wipe_mock():
    print("  · 清空旧 Mock 数据（TSM/FPM/FP/TS/M03/pilot03）...")
    # 浏览器联调中通过界面产生的飞行计划（FP 前缀）一并清除
    fp_ids = [r[0] for r in db.session.query(FlightPlan.id).filter(
        FlightPlan.plan_no.like("FP%")).all()]
    if fp_ids:
        ApprovalRecord.query.filter(ApprovalRecord.plan_id.in_(fp_ids)).delete(
            synchronize_session=False)
    FlightPlan.query.filter(FlightPlan.plan_no.like("FP%")).delete(synchronize_session=False)
    plan_ids = [r[0] for r in db.session.query(FlightPlan.id).filter(
        FlightPlan.plan_no.like("FPM%")).all()]
    if plan_ids:
        ApprovalRecord.query.filter(ApprovalRecord.plan_id.in_(plan_ids)).delete(
            synchronize_session=False)
    FlightPlan.query.filter(FlightPlan.plan_no.like("FPM%")).delete(synchronize_session=False)
    # 浏览器联调中通过界面产生的订单（TS 前缀）及其流水/结算
    ui_order_ids = [r[0] for r in db.session.query(Order.id).filter(
        Order.order_no.like("TS%")).all()]
    if ui_order_ids:
        PaymentTransaction.query.filter(
            PaymentTransaction.order_id.in_(ui_order_ids)).delete(synchronize_session=False)
        Settlement.query.filter(Settlement.order_id.in_(ui_order_ids)).delete(
            synchronize_session=False)
    Order.query.filter(Order.order_no.like("TS%")).delete(synchronize_session=False)
    mock_order_ids = [r[0] for r in db.session.query(Order.id).filter(
        Order.order_no.like("TSM%")).all()]
    if mock_order_ids:
        PaymentTransaction.query.filter(
            PaymentTransaction.order_id.in_(mock_order_ids)).delete(synchronize_session=False)
        Settlement.query.filter(Settlement.order_id.in_(mock_order_ids)).delete(
            synchronize_session=False)
    Order.query.filter(Order.order_no.like("TSM%")).delete(synchronize_session=False)
    # Mock 三号机长
    p03 = db.session.query(User).filter_by(username="pilot03").first()
    if p03:
        db.session.query(user_role).filter_by(user_id=p03.id).delete()
        db.session.delete(p03)
    # Mock 三号机
    Aircraft.query.filter_by(aircraft_no="TS-UAV-M03").delete(synchronize_session=False)
    # 演示飞机归位（清掉在途快照；坐标与 DDL 7.6 场站保持一致）
    for ac in db.session.query(Aircraft).filter(Aircraft.id.in_([1, 2])).all():
        if ac.id == 1:
            ac.status, ac.battery, ac.current_vertiport_id = 1, 100, 1
            ac.current_lng, ac.current_lat = 120.9295, 27.9555
        else:
            ac.status, ac.battery, ac.current_vertiport_id = 1, 96, 2
            ac.current_lng, ac.current_lat = 120.815, 27.945
    # Mock 客户
    # Mock 客户（正则排除演示账号 customer）
    mock_users = db.session.query(User).filter(
        User.username.op("REGEXP")("^cust[0-9]+$")).all()
    for u in mock_users:
        db.session.query(user_role).filter_by(user_id=u.id).delete()
        db.session.execute(db.text("DELETE FROM customer_profile WHERE user_id=:uid"),
                           {"uid": u.id})
        db.session.delete(u)
    db.session.commit()


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="清空 Mock 数据后重建")
    parser.add_argument("--orders", type=int, default=30, help="基础 Mock 订单数")
    parser.add_argument("--customers", type=int, default=5, help="Mock 客户数")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        print("=== 天枢 Mock 数据生成 ===")
        if args.force:
            wipe_mock()
        ensure_staff()
        customers = ensure_customers(args.customers)
        print(f"  · 客户就绪：{len(customers)} 个 cust01~cust{len(customers):02d}")

        existing = Order.query.filter(Order.order_no.like("TSM%")).count()
        if existing == 0:
            orders = make_orders(customers, args.orders)
            print(f"  · 生成基础订单 {len(orders)} 单（含支付/退款流水、结算单）")
        else:
            print(f"  · 已存在 {existing} 个 Mock 订单，跳过基础订单（--force 可重建）")

        plan_cnt = FlightPlan.query.filter(FlightPlan.plan_no.like("FPM%")).count()
        if plan_cnt == 0:
            make_inflight_plans(customers, seq_start=args.orders + 1)
            print("  · 生成在途订单 7 单 + 飞行计划 5 张（待审批/待起飞/飞行中/已送达）")
        else:
            print(f"  · 已存在 {plan_cnt} 张 Mock 飞行计划，跳过（--force 可重建）")

        # 汇总
        from sqlalchemy import func as sqlfunc
        oc = db.session.query(Order.status, sqlfunc.count(Order.id)).group_by(Order.status).all()
        pc = db.session.query(FlightPlan.status, sqlfunc.count(FlightPlan.id)).group_by(
            FlightPlan.status).all()
        print("=== 完成 ===")
        print("  订单状态分布：", dict(sorted(oc)))
        print("  计划状态分布：", dict(sorted(pc)))
        print("  演示账号：admin / merchant / customer / pilot / pilot02 / pilot03，密码 Admin@123")


if __name__ == "__main__":
    main()
