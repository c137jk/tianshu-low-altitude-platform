"""D2 结算单与支付流水（Mock）。"""
from sqlalchemy import String, DateTime, Numeric
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, JSON

from app.extensions import db
from .base import BaseModel


class Settlement(BaseModel):
    __tablename__ = "settlement"

    settlement_no = db.Column(String(32), nullable=False, unique=True)
    order_id = db.Column(BIGINT(unsigned=True), nullable=False, unique=True)
    merchant_id = db.Column(BIGINT(unsigned=True), nullable=False, index=True)
    customer_id = db.Column(BIGINT(unsigned=True), nullable=False)
    base_fee = db.Column(Numeric(10, 2), nullable=False, default=0)
    airspace_fee = db.Column(Numeric(10, 2), nullable=False, default=0)
    urgent_fee = db.Column(Numeric(10, 2), nullable=False, default=0)
    platform_fee = db.Column(Numeric(10, 2), nullable=False, default=0, comment="平台抽佣")
    pay_amount = db.Column(Numeric(10, 2), nullable=False, default=0)
    status = db.Column(TINYINT, nullable=False, default=1, comment="1待结算2已结算3对账中4有差异")
    reconciled_at = db.Column(DateTime)
    invoice_status = db.Column(TINYINT, nullable=False, default=0, comment="0未申请1已申请2已开具")
    invoice_title = db.Column(String(120), comment="发票抬头")
    invoice_tax_no = db.Column(String(40), comment="纳税人识别号")
    invoice_applied_at = db.Column(DateTime, comment="发票申请时间")


class PaymentTransaction(BaseModel):
    __tablename__ = "payment_transaction"

    txn_no = db.Column(String(32), nullable=False, unique=True)
    order_id = db.Column(BIGINT(unsigned=True), nullable=False, index=True)
    user_id = db.Column(BIGINT(unsigned=True), nullable=False)
    amount = db.Column(Numeric(10, 2), nullable=False)
    direction = db.Column(TINYINT, nullable=False, comment="1支付2退款")
    pay_method = db.Column(TINYINT, nullable=False, comment="1微信2支付宝3对公")
    channel_txn_no = db.Column(String(64), comment="三方流水(Mock生成)")
    status = db.Column(TINYINT, nullable=False, default=1, comment="1待支付2成功3失败4已退款")
    paid_at = db.Column(DateTime)
    raw_resp = db.Column(JSON, comment="渠道回调留痕(Mock)")
