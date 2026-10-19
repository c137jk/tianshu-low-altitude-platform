"""D3 调度与飞行任务模型：飞行计划（一行走完一生）+ 多轮审批流水。

注意 approval_record 是流水窄表（DDL 无 updated_at/deleted），不继承 BaseModel。
"""
from sqlalchemy import String, Integer, DateTime, Numeric, text
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, JSON

from app.extensions import db
from .base import BaseModel


class FlightPlan(BaseModel):
    __tablename__ = "flight_plan"

    plan_no = db.Column(String(32), nullable=False, unique=True)
    order_id = db.Column(BIGINT(unsigned=True), nullable=False, unique=True)
    merchant_id = db.Column(BIGINT(unsigned=True), nullable=False)
    status = db.Column(TINYINT, nullable=False, default=0,
                       comment="0待申报1待审批2已批准待执行3执行中4已完成5已驳回6已取消")
    # 调度方案
    aircraft_id = db.Column(BIGINT(unsigned=True))
    pilot_id = db.Column(BIGINT(unsigned=True))
    takeoff_vertiport_id = db.Column(BIGINT(unsigned=True))
    landing_vertiport_id = db.Column(BIGINT(unsigned=True))
    route_json = db.Column(JSON, comment="有序航点[{lng,lat}]GCJ-02")
    route_distance_m = db.Column(Integer)
    est_duration_sec = db.Column(Integer)
    dispatch_by = db.Column(BIGINT(unsigned=True))
    dispatched_at = db.Column(DateTime)
    # 飞行要素
    task_name = db.Column(String(100))
    purpose = db.Column(String(255))
    plan_start_at = db.Column(DateTime)
    plan_end_at = db.Column(DateTime)
    altitude_m = db.Column(Integer, comment="申报高度")
    cruise_speed = db.Column(Numeric(5, 1), comment="巡航速度m/s")
    weather_json = db.Column(JSON, comment="申报时气象快照")
    # 审批当前结果
    submitted_at = db.Column(DateTime)
    approve_result = db.Column(TINYINT, comment="1通过2驳回")
    approved_by = db.Column(BIGINT(unsigned=True))
    approved_at = db.Column(DateTime)
    approve_opinion = db.Column(String(500))
    reject_reason = db.Column(String(500))
    # 执行热快照
    assigned_at = db.Column(DateTime, comment="下发机长时间")
    actual_takeoff_at = db.Column(DateTime)
    actual_landing_at = db.Column(DateTime)
    current_lng = db.Column(Numeric(10, 6))
    current_lat = db.Column(Numeric(9, 6))
    current_altitude = db.Column(Numeric(7, 2))
    current_speed = db.Column(Numeric(5, 2))
    current_battery = db.Column(TINYINT)
    last_report_at = db.Column(DateTime)
    remark = db.Column(String(255))


class ApprovalRecord(db.Model):
    """审批流水：每轮申报/审批一条，只增不改。"""
    __tablename__ = "approval_record"

    id = db.Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    plan_id = db.Column(BIGINT(unsigned=True), nullable=False, index=True)
    approver_id = db.Column(BIGINT(unsigned=True), nullable=False)
    result = db.Column(TINYINT, nullable=False, comment="1通过2驳回")
    opinion = db.Column(String(500))
    created_at = db.Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
