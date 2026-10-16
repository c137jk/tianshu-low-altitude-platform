"""健康检查蓝图：不依赖数据库，用来验证骨架是否跑起来。"""
from flask import Blueprint
from datetime import datetime

from app.common.response import success

bp = Blueprint("health", __name__, url_prefix="/api")


@bp.get("/health")
def health():
    return success({
        "service": "tianshu-utms-backend",
        "status": "UP",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
