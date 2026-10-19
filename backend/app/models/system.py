"""系统参数（业务阈值放数据库，不写死代码）。"""
from sqlalchemy import String
from sqlalchemy.dialects.mysql import TINYINT

from app.extensions import db
from .base import BaseModel


class SysConfig(BaseModel):
    __tablename__ = "sys_config"

    config_group = db.Column(String(50), nullable=False, default="common")
    config_key = db.Column(String(80), nullable=False, unique=True)
    config_value = db.Column(String(500))
    value_type = db.Column(String(20), nullable=False, default="string")
    remark = db.Column(String(255))
    is_system = db.Column(TINYINT(1), nullable=False, default=1)


_cache: dict[str, object] = {}


def get_config(key: str, default=None):
    """按 key 读参数，带进程内缓存（W6 接 Redis 后统一管理）。"""
    if key in _cache:
        return _cache[key]
    row = db.session.query(SysConfig).filter_by(config_key=key, deleted=0).first()
    if row is None:
        return default
    v = row.config_value
    if row.value_type == "number":
        try:
            v = float(v) if "." in str(v) else int(v)
        except (TypeError, ValueError):
            return default
    elif row.value_type == "boolean":
        v = str(v).lower() in ("1", "true", "yes", "on")
    _cache[key] = v
    return v
