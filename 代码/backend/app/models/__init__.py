"""模型导出中心：新增领域模型后在这里 import 一次，保证关系字符串能解析。"""
from .base import BaseModel
from .rbac import Role, Permission, user_role, role_permission
from .organization import Merchant
from .user import User
from .airspace import Airspace, Geofence
from .resource import Vertiport, Aircraft, StationReservation
from .order import Order
from .dispatch import FlightPlan, ApprovalRecord
from .settlement import Settlement, PaymentTransaction
from .system import SysConfig
from .track import FlightTrack
from .qualification import Qualification
from .compliance import ViolationRecord, EmergencyEvent

__all__ = [
    "BaseModel", "Role", "Permission", "user_role", "role_permission",
    "Merchant", "User", "Airspace", "Geofence", "Vertiport", "Aircraft",
    "StationReservation",
    "Order", "FlightPlan", "ApprovalRecord", "Settlement",
    "PaymentTransaction", "SysConfig", "FlightTrack",
    "ViolationRecord", "EmergencyEvent",
]
