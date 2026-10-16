"""商家企业表（users.merchant_id 指向它）。"""
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, JSON

from app.extensions import db
from .base import BaseModel


class Merchant(BaseModel):
    __tablename__ = "merchants"

    merchant_no = db.Column(String(32), nullable=False, unique=True)
    merchant_name = db.Column(String(100), nullable=False)
    unified_credit_code = db.Column(String(18), unique=True)
    contact_user_id = db.Column(BIGINT(unsigned=True))
    legal_person = db.Column(String(50))
    contact_phone_cipher = db.Column(String(100))
    service_area_json = db.Column(JSON)
    pricing_json = db.Column(JSON)
    audit_status = db.Column(TINYINT, nullable=False, default=0,
                             comment="0待提交1审核中2通过3驳回4停用注销")
    audit_opinion = db.Column(String(500))
    audited_by = db.Column(BIGINT(unsigned=True))
    audited_at = db.Column(DateTime)
    expire_at = db.Column(DateTime)
    status = db.Column(TINYINT, nullable=False, default=1, comment="1正常2暂停3注销")
