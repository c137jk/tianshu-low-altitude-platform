"""模型公共基类。

对应设计04 全局约定：每张业务表都有四件套
    id BIGINT UNSIGNED 主键自增 / created_at / updated_at（ON UPDATE 自动刷新）/ deleted 逻辑删除
所有业务模型继承 BaseModel 即可，不再重复写这四列。
桥梁表（user_role / role_permission）结构特殊，单独定义，不继承。
"""
from sqlalchemy import DateTime, text
from sqlalchemy.dialects.mysql import BIGINT, TINYINT

from app.extensions import db


class TimestampMixin:
    created_at = db.Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = db.Column(
        DateTime, nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )


class SoftDeleteMixin:
    # 0 正常 1 已删除；查询默认要带 deleted == 0（后续在统一查询层封装）
    deleted = db.Column(TINYINT, nullable=False, default=0, server_default="0")


class BaseModel(db.Model, TimestampMixin, SoftDeleteMixin):
    __abstract__ = True

    id = db.Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.id}>"
