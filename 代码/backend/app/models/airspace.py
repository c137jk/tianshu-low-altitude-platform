"""空域与电子围栏模型（D5）。

airspace 是参考数据（空管同步 + 平台自绘），过去只有 status 一列，
"谁批准生效的"没有任何落点，所以 M-42 审核流做不出来；现在补 audit_* 四列。
geofence 是运营侧自定义围栏（需求 4.2.4 第 4 条），merchant_id 为空表示平台级、
对全部商家生效；围栏参与飞行中合规判定（见 compliance_monitor.findings）。
"""
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.dialects.mysql import BIGINT, TINYINT, JSON

from app.extensions import db
from .base import BaseModel

# 台账口径的中文名放在模型层：空域蓝图（列表/审核）、监控大屏和 D8 报表都要读同一套，
# 各自复制一份迟早会漂成"同一个数字两种叫法"。
AIRSPACE_TYPE_NAME = {1: "禁飞区", 2: "限飞区", 3: "可飞区", 4: "临时管制"}
AIRSPACE_SOURCE_NAME = {1: "空管同步", 2: "平台自绘"}
AUDIT_STATUS_NAME = {0: "待审核", 1: "已通过", 2: "已驳回"}


class Airspace(BaseModel):
    __tablename__ = "airspace"

    airspace_no = db.Column(String(32), nullable=False, unique=True)
    airspace_name = db.Column(String(80), nullable=False)
    airspace_type = db.Column(TINYINT, nullable=False, comment="1禁飞2限飞3可飞4临时管制")
    source = db.Column(TINYINT, nullable=False, default=1, comment="1空管同步(Mock)2平台自绘")
    geometry_json = db.Column(JSON, nullable=False, comment="GeoJSON 多边形/圆 GCJ-02")
    altitude_min = db.Column(Integer)
    altitude_max = db.Column(Integer)
    control_start_at = db.Column(DateTime)
    control_end_at = db.Column(DateTime)
    control_reason = db.Column(String(255))
    region_name = db.Column(String(80))
    status = db.Column(TINYINT, nullable=False, default=1, comment="1生效2失效")
    # 审核留痕（迁移 p8）：只有 audit_status=1 的空域参与合规判定
    audit_status = db.Column(TINYINT, nullable=False, default=1, comment="0待审核1通过2驳回")
    audited_by = db.Column(BIGINT(unsigned=True))
    audited_at = db.Column(DateTime)
    audit_opinion = db.Column(String(500))


class Geofence(BaseModel):
    __tablename__ = "geofence"

    fence_name = db.Column(String(80), nullable=False)
    merchant_id = db.Column(BIGINT(unsigned=True), comment="设置方，空=平台")
    geometry_json = db.Column(JSON, nullable=False)
    fence_type = db.Column(TINYINT, nullable=False, comment="1限入2限出3接近告警")
    altitude_limit = db.Column(Integer)
    warn_distance_m = db.Column(Integer)
    status = db.Column(TINYINT, nullable=False, default=1, comment="1启用2停用")
