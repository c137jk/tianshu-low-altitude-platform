"""资质文件模型（W6 机长资质审核）。"""
from sqlalchemy import String, DateTime
from sqlalchemy.dialects.mysql import BIGINT, TINYINT
from app.extensions import db


class Qualification(db.Model):
    __tablename__ = "qualification"

    id = db.Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    subject_type = db.Column(TINYINT, nullable=False, comment="1商家2人员3飞行器")
    subject_id = db.Column(BIGINT(unsigned=True), nullable=False)
    qual_type = db.Column(String(30), nullable=False)
    qual_name = db.Column(String(100), nullable=False)
    file_url = db.Column(String(255))
    issue_at = db.Column(DateTime)
    expire_at = db.Column(DateTime)
    status = db.Column(TINYINT, default=0, comment="0待审核1有效2即将到期3已过期4驳回")
    audited_by = db.Column(BIGINT(unsigned=True))
    audited_at = db.Column(DateTime)
    audit_opinion = db.Column(String(500))
    deleted = db.Column(TINYINT, default=0)
