"""RBAC：角色、权限点，以及 user_role / role_permission 两张 N:N 桥梁表。

表结构以 sql/tianshu-ddl-v1.sql 为唯一权威，ORM 不负责建表（不调用 db.create_all）。
"""
from sqlalchemy import UniqueConstraint, DateTime, String, Integer, text
from sqlalchemy.dialects.mysql import BIGINT, TINYINT

from app.extensions import db
from .base import BaseModel


# ---- 桥梁表（只有 id/关联列/创建时间，单独用 Core Table 定义）----
user_role = db.Table(
    "user_role",
    db.Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
    db.Column("user_id", BIGINT(unsigned=True),
              db.ForeignKey("users.id", name="fk_ur_user"), nullable=False),
    db.Column("role_id", BIGINT(unsigned=True),
              db.ForeignKey("role.id", name="fk_ur_role"), nullable=False),
    db.Column("granted_by", BIGINT(unsigned=True), comment="授权人"),
    db.Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("user_id", "role_id", name="uk_user_role"),
)

role_permission = db.Table(
    "role_permission",
    db.Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
    db.Column("role_id", BIGINT(unsigned=True),
              db.ForeignKey("role.id", name="fk_rp_role"), nullable=False),
    db.Column("permission_id", BIGINT(unsigned=True),
              db.ForeignKey("permission.id", name="fk_rp_perm"), nullable=False),
    db.Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("role_id", "permission_id", name="uk_role_perm"),
)


class Role(BaseModel):
    __tablename__ = "role"

    role_code = db.Column(String(50), nullable=False, unique=True, comment="角色编码，程序判断用")
    role_name = db.Column(String(50), nullable=False, comment="角色名称，界面显示")
    role_scope = db.Column(TINYINT, nullable=False, default=1, comment="数据范围 1平台 2商家 3客户")
    built_in = db.Column(TINYINT(1), nullable=False, default=0, comment="内置不可删 0否1是")
    sort = db.Column(Integer, nullable=False, default=0)
    remark = db.Column(String(255))

    permissions = db.relationship(
        "Permission", secondary=role_permission, lazy="selectin", viewonly=True,
        order_by="Permission.sort",
    )


class Permission(BaseModel):
    __tablename__ = "permission"

    parent_id = db.Column(BIGINT(unsigned=True), nullable=False, default=0, comment="0=根节点")
    perm_code = db.Column(String(80), nullable=False, unique=True, comment="如 order:create")
    perm_name = db.Column(String(80), nullable=False)
    perm_type = db.Column(TINYINT, nullable=False, default=1, comment="1菜单 2按钮 3接口")
    path = db.Column(String(120), comment="前端路由/接口路径")
    icon = db.Column(String(60))
    sort = db.Column(Integer, nullable=False, default=0)
