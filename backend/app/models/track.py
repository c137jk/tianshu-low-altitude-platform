"""飞行轨迹模型（W6 轨迹回放）。"""
from sqlalchemy import String, DateTime, Numeric, Integer, SmallInteger
from sqlalchemy.dialects.mysql import BIGINT, TINYINT
from app.extensions import db


class FlightTrack(db.Model):
    __tablename__ = "flight_track"

    id = db.Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    plan_id = db.Column(BIGINT(unsigned=True), nullable=False)
    aircraft_id = db.Column(BIGINT(unsigned=True), nullable=False)
    order_id = db.Column(BIGINT(unsigned=True), nullable=False)
    lng = db.Column(Numeric(10, 6), nullable=False)
    lat = db.Column(Numeric(9, 6), nullable=False)
    altitude = db.Column(Numeric(7, 2), default=0)
    speed = db.Column(Numeric(5, 2), default=0)
    direction = db.Column(SmallInteger, default=0)
    battery = db.Column(TINYINT(unsigned=True), default=100)
    track_time = db.Column(DateTime, primary_key=True, nullable=False)
    is_violation = db.Column(TINYINT(1), default=0)
    violation_type = db.Column(String(40), comment="违规类型打标，如 no_fly_intrusion")
    source = db.Column(TINYINT, default=1)
