"""用户主表：全角色统一登录（D-13）。

敏感字段（phone_cipher/email_cipher/id_card_cipher/password_hash）永不出现在接口出参里；
对外序列化统一走 to_login_dict()。
"""
import bcrypt
from sqlalchemy import String, DateTime
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, CHAR

from app.extensions import db
from .base import BaseModel
from .rbac import user_role, Role


class User(BaseModel):
    __tablename__ = "users"

    username = db.Column(String(50), nullable=False, unique=True)
    password_hash = db.Column(String(100), nullable=False, comment="BCrypt 哈希")
    user_type = db.Column(TINYINT, nullable=False,
                          comment="1个人客户2企业客户3商家员工4平台管理员5空管6运维")
    real_name = db.Column(String(50))
    phone_cipher = db.Column(String(100), nullable=False)
    phone_digest = db.Column(CHAR(64), nullable=False, unique=True)
    email_cipher = db.Column(String(100))
    id_card_cipher = db.Column(String(100))
    realname_status = db.Column(TINYINT, nullable=False, default=0,
                                comment="0未认证1审核中2已认证3驳回")
    gender = db.Column(TINYINT, nullable=False, default=0)
    avatar_url = db.Column(String(255))
    merchant_id = db.Column(BIGINT(unsigned=True), db.ForeignKey("merchants.id"))
    status = db.Column(TINYINT, nullable=False, default=1, comment="1正常2冻结3注销")
    frozen_reason = db.Column(String(255))
    last_login_at = db.Column(DateTime)
    last_login_ip = db.Column(String(45))

    roles = db.relationship("Role", secondary=user_role, lazy="selectin", viewonly=True)
    merchant = db.relationship("Merchant", foreign_keys=[merchant_id], lazy="joined", viewonly=True)

    # ---- 业务方法 ----
    def check_password(self, raw: str) -> bool:
        """校验明文密码；哈希来自 DDL 种子（cost=10）。"""
        try:
            return bcrypt.checkpw(raw.encode("utf-8"), self.password_hash.encode("utf-8"))
        except (ValueError, TypeError):
            return False

    @property
    def role_codes(self) -> list[str]:
        return [r.role_code for r in self.roles]

    @property
    def perm_codes(self) -> list[str]:
        """该用户经所有角色汇总后的权限点编码（去重）。"""
        codes = set()
        for r in self.roles:
            codes.update(p.perm_code for p in r.permissions)
        return sorted(codes)

    def has_perm(self, perm_code: str) -> bool:
        # platform_admin 在种子里已铺满全部权限，这里仍保留通配兜底
        return "platform_admin" in self.role_codes or perm_code in self.perm_codes

    def to_login_dict(self) -> dict:
        """登录/当前用户接口的安全出参（不含任何密文与哈希）。"""
        return {
            "id": self.id,
            "username": self.username,
            "userType": self.user_type,
            "realName": self.real_name,
            "avatarUrl": self.avatar_url,
            "realnameStatus": self.realname_status,
            "merchantId": self.merchant_id,
            "merchantName": self.merchant.merchant_name if self.merchant else None,
            "roles": self.role_codes,
            "perms": self.perm_codes,
        }
