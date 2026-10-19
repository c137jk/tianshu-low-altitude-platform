"""D2 订单模型。费用在下单时冻结为快照，后续商家改价不影响历史单。"""
from sqlalchemy import String, Integer, DateTime, Numeric
from sqlalchemy.dialects.mysql import BIGINT, TINYINT

from app.extensions import db
from .base import BaseModel


class Order(BaseModel):
    __tablename__ = "orders"

    order_no = db.Column(String(32), nullable=False, unique=True)
    customer_id = db.Column(BIGINT(unsigned=True), nullable=False, index=True)
    merchant_id = db.Column(BIGINT(unsigned=True), index=True)
    order_type = db.Column(TINYINT, nullable=False, default=1, comment="1普通2应急3预留")
    status = db.Column(TINYINT, nullable=False, default=0,
                       comment="0待支付1待接单2待调度3待审批4待起飞5飞行中6已送达7已完成8已取消")
    # 寄件快照
    sender_name = db.Column(String(50), nullable=False)
    sender_phone = db.Column(String(100), nullable=False, comment="AES密文")
    # 密文列每次加密 nonce 不同 → 无法 LIKE，检索一律走下面这组摘要列
    sender_phone_digest = db.Column(String(64), index=True, comment="寄件手机号HMAC摘要（整号检索）")
    sender_phone_tail = db.Column(String(64), comment="寄件手机号后4位摘要（尾号检索）")
    sender_address = db.Column(String(255), nullable=False)
    sender_lng = db.Column(Numeric(10, 6), nullable=False)
    sender_lat = db.Column(Numeric(9, 6), nullable=False)
    # 收件快照
    receiver_name = db.Column(String(50), nullable=False)
    receiver_phone = db.Column(String(100), nullable=False, comment="AES密文")
    receiver_phone_digest = db.Column(String(64), index=True, comment="收件手机号HMAC摘要（整号检索）")
    receiver_phone_tail = db.Column(String(64), comment="收件手机号后4位摘要（尾号检索）")
    receiver_address = db.Column(String(255), nullable=False)
    receiver_lng = db.Column(Numeric(10, 6), nullable=False)
    receiver_lat = db.Column(Numeric(9, 6), nullable=False)
    # 物品时效
    goods_type = db.Column(String(40), nullable=False)
    goods_name = db.Column(String(100))
    weight_kg = db.Column(Numeric(8, 2), nullable=False)
    volume_m3 = db.Column(Numeric(8, 3))
    quantity = db.Column(Integer, nullable=False, default=1)
    goods_cert_url = db.Column(String(255))
    delivery_type = db.Column(TINYINT, nullable=False, default=1, comment="1普通2加急")
    expect_at = db.Column(DateTime, comment="预约时间，空=即时单")
    # 费用快照
    base_fee = db.Column(Numeric(10, 2), nullable=False, default=0)
    airspace_fee = db.Column(Numeric(10, 2), nullable=False, default=0)
    urgent_fee = db.Column(Numeric(10, 2), nullable=False, default=0)
    discount_amount = db.Column(Numeric(10, 2), nullable=False, default=0)
    total_amount = db.Column(Numeric(10, 2), nullable=False)
    pay_amount = db.Column(Numeric(10, 2), nullable=False, default=0)
    pay_method = db.Column(TINYINT, comment="1微信2支付宝3对公")
    paid = db.Column(TINYINT(1), nullable=False, default=0)
    paid_at = db.Column(DateTime)
    delivered_at = db.Column(DateTime, comment="实际送达时刻（状态转 6 已送达）")
    finished_at = db.Column(DateTime, comment="客户确认完成时刻（状态转 7 已完成）")
    cancel_reason = db.Column(String(255))
    remark = db.Column(String(255))

    def fee_dict(self) -> dict:
        return {
            "baseFee": float(self.base_fee or 0),
            "airspaceFee": float(self.airspace_fee or 0),
            "urgentFee": float(self.urgent_fee or 0),
            "discountAmount": float(self.discount_amount or 0),
            "totalAmount": float(self.total_amount or 0),
            "payAmount": float(self.pay_amount or 0),
        }
