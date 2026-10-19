"""D4 低空资源模型：起降/换电场站、飞行器台账（W3 只读使用，管理页 W5 补 CRUD）。"""
from sqlalchemy import String, Integer, Date, DateTime, Numeric
from sqlalchemy.dialects.mysql import BIGINT, TINYINT

from app.extensions import db
from .base import BaseModel


class Vertiport(BaseModel):
    __tablename__ = "vertiport"

    station_no = db.Column(String(32), nullable=False, unique=True)
    station_name = db.Column(String(80), nullable=False)
    station_type = db.Column(TINYINT, nullable=False, comment="1起降场2换电站3综合站")
    lng = db.Column(Numeric(10, 6), nullable=False)
    lat = db.Column(Numeric(9, 6), nullable=False)
    address = db.Column(String(255))
    merchant_id = db.Column(BIGINT(unsigned=True), comment="空=公共场站")
    capacity = db.Column(Integer, nullable=False, default=0)
    charger_total = db.Column(Integer, nullable=False, default=0)
    charger_available = db.Column(Integer, nullable=False, default=0)
    # 历史快照列：没有写入方（心跳只报桩位），对外展示的占用数一律走
    # dispatch_service.station_occupancy()，别读这一列。
    occupied_count = db.Column(Integer, nullable=False, default=0)
    service_aircraft_types = db.Column(String(200))
    open_time = db.Column(DateTime)
    close_time = db.Column(DateTime)
    status = db.Column(TINYINT, nullable=False, default=1, comment="1开放2维护中3关闭")
    # NULL 表示"这台场站从没报过心跳"，与"报过但超时"是两件事，不能拿 updated_at 凑
    last_heartbeat_at = db.Column(DateTime, comment="最近一次设备心跳")


class Aircraft(BaseModel):
    __tablename__ = "aircraft"

    aircraft_no = db.Column(String(32), nullable=False, unique=True)
    merchant_id = db.Column(BIGINT(unsigned=True), nullable=False)
    brand = db.Column(String(60))
    model = db.Column(String(60))
    serial_no = db.Column(String(64))
    endurance_min = db.Column(Integer, comment="续航分钟")
    max_load_kg = db.Column(Numeric(8, 2), comment="最大载重kg")
    max_speed = db.Column(Numeric(5, 1), comment="最大速度m/s")
    max_altitude = db.Column(Integer, comment="最大飞行高度m")
    purchase_at = db.Column(Date)
    current_vertiport_id = db.Column(BIGINT(unsigned=True))
    status = db.Column(TINYINT, nullable=False, default=1,
                       comment="1闲置2执行中3充换电4维保中5故障离线")
    health_score = db.Column(TINYINT, nullable=False, default=100)
    total_flight_min = db.Column(Integer, nullable=False, default=0)
    task_count = db.Column(Integer, nullable=False, default=0)
    fault_count = db.Column(Integer, nullable=False, default=0)
    last_maintain_at = db.Column(DateTime)
    next_maintain_at = db.Column(DateTime)
    current_lng = db.Column(Numeric(10, 6))
    current_lat = db.Column(Numeric(9, 6))
    battery = db.Column(TINYINT, comment="当前电量%")
    online = db.Column(TINYINT(1), nullable=False, default=0)


class StationReservation(BaseModel):
    """场站预约（M-55）：商家申请在某时段占用泊位或充电桩，平台/空管审核。

    容量口径：purpose=1（起降）占 capacity，purpose=2/3（换电/充电）占 charger_total。
    待审(1)与通过(2)都算占位——放行时才做最终冲突裁决的话，两个商家各自排队申请
    都能通过，一起通过就超容了；所以申请时按"待审+通过"预占，审核时再核一次。
    """
    __tablename__ = "station_reservation"

    reservation_no = db.Column(String(32), nullable=False, unique=True)
    station_id = db.Column(BIGINT(unsigned=True), nullable=False)
    merchant_id = db.Column(BIGINT(unsigned=True), nullable=False)
    user_id = db.Column(BIGINT(unsigned=True), nullable=False)
    aircraft_id = db.Column(BIGINT(unsigned=True))
    purpose = db.Column(TINYINT, nullable=False, comment="1起降2换电3充电")
    start_at = db.Column(DateTime, nullable=False)
    end_at = db.Column(DateTime, nullable=False)
    status = db.Column(TINYINT, nullable=False, default=1,
                       comment="1待审2通过3驳回4完成5取消")
    audit_opinion = db.Column(String(255))
