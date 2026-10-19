"""D5 违规台账 + D8 应急事件模型。

这两张表过去只有 DDL 和散在各蓝图里的 raw SQL；仿真器要每帧自动产生违规，
没有 ORM 就得再抄一遍列名（列序漂移只能在运行时才发现），所以补上模型。
注意：flight_track 的 violation_type 是字符串打标（如 no_fly_intrusion），
      violation_record.violation_type 是台账口径的数字类型，两者含义不同。
"""
from sqlalchemy import String, Integer, DateTime, Numeric
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, JSON

from app.extensions import db
from .base import BaseModel


class ViolationRecord(BaseModel):
    __tablename__ = "violation_record"

    violation_no = db.Column(String(32), nullable=False, unique=True)
    merchant_id = db.Column(BIGINT(unsigned=True), nullable=False)
    order_id = db.Column(BIGINT(unsigned=True))
    plan_id = db.Column(BIGINT(unsigned=True))
    aircraft_id = db.Column(BIGINT(unsigned=True))
    pilot_id = db.Column(BIGINT(unsigned=True))
    violation_type = db.Column(TINYINT, nullable=False,
                               comment="1闯禁飞2偏航3超时4超高5无证6其他")
    lng = db.Column(Numeric(10, 6))
    lat = db.Column(Numeric(9, 6))
    track_id = db.Column(BIGINT(unsigned=True), comment="对应轨迹帧（分区表不建 FK）")
    evidence_urls = db.Column(JSON)
    violation_time = db.Column(DateTime, nullable=False)
    description = db.Column(String(500))
    penalty_type = db.Column(TINYINT, comment="0未处罚1警告2罚款3暂停服务4永久封禁")
    penalty_amount = db.Column(Numeric(10, 2))
    penalty_days = db.Column(Integer)
    penalty_reason = db.Column(String(500))
    penalty_by = db.Column(BIGINT(unsigned=True))
    penalty_at = db.Column(DateTime)
    notice_url = db.Column(String(255))
    status = db.Column(TINYINT, nullable=False, default=1, comment="1未处理2处理中3已归档")


class EmergencyEvent(BaseModel):
    __tablename__ = "emergency_event"

    event_no = db.Column(String(32), nullable=False, unique=True)
    order_id = db.Column(BIGINT(unsigned=True))
    plan_id = db.Column(BIGINT(unsigned=True))
    aircraft_id = db.Column(BIGINT(unsigned=True))
    event_type = db.Column(TINYINT, nullable=False, comment="1失联2闯禁飞3低电量4恶劣天气5其他")
    level = db.Column(TINYINT, nullable=False, default=1, comment="1一般2较大3重大")
    lng = db.Column(Numeric(10, 6))
    lat = db.Column(Numeric(9, 6))
    description = db.Column(String(500))
    handle_measure = db.Column(String(500))
    handler_id = db.Column(BIGINT(unsigned=True))
    started_at = db.Column(DateTime)
    closed_at = db.Column(DateTime)
    status = db.Column(TINYINT, nullable=False, default=1, comment="1发生中2处置中3已解除")


VIOLATION_TYPE = {1: "闯禁飞区", 2: "偏航", 3: "超时飞行", 4: "超高", 5: "无证飞行", 6: "其他"}
VIOLATION_TAG = {1: "no_fly_intrusion", 2: "route_deviation", 3: "over_schedule",
                 4: "altitude_exceed", 5: "unlicensed", 6: "other"}
EMERGENCY_TYPE = {1: "链路失联", 2: "闯入禁飞区", 3: "低电量", 4: "恶劣天气", 5: "其他"}
EMERGENCY_LEVEL = {1: "一般", 2: "较大", 3: "重大"}
