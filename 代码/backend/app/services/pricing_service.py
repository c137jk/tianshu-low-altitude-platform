"""费用试算与下单合规预检（D2）。

定价口径（演示版，来自 DDL 商家 pricing_json，未在设计件中细化的部分在此显式约定）：
- base_fee   = 距离km × basePerKm + 重量kg × perKg
- airspace_fee = 每穿越 1 个限飞区 10.00 元（禁飞区/临时管制直接拒单）
- urgent_fee = 加急单 base_fee × (urgentRate - 1)
- total = base + airspace + urgent - discount
试算只用于展示；下单时服务端用同一函数重算并冻结，前端传的费用不作数。
"""
from decimal import Decimal, ROUND_HALF_UP

from app.common.errors import ApiError
from app.common.response import BizCode
from app.extensions import db
from app.models import Airspace, Merchant
from app.services import geo

# 禁运品关键词（演示口径，真实项目走字典表/品名知识库）
FORBIDDEN_GOODS = ["枪", "弹药", "易燃", "易爆", "雷管", "酒精", "汽油", "柴油",
                   "危化", "腐蚀", "剧毒", "毒品", "管制刀具", "活体", "动物", "现金"]
LIMITED_ZONE_FEE = Decimal("10.00")   # 每个限飞区加收
PLATFORM_RATE = Decimal("0.10")       # 平台抽佣 10%（结算演示口径）
CRUISE_SPEED_MPS = Decimal("12.0")    # 演示巡航速度 m/s


def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_demo_merchant() -> Merchant:
    """演示期只有一家审核通过商家，下单自动承接。"""
    merchant = (
        db.session.query(Merchant)
        .filter_by(audit_status=2, status=1, deleted=0)
        .order_by(Merchant.id.asc())
        .first()
    )
    if merchant is None:
        raise ApiError(BizCode.BIZ_ERROR, "当前没有可承接订单的运营商家")
    return merchant


def check_goods(goods_type: str, goods_name: str | None) -> None:
    text = f"{goods_type or ''}{goods_name or ''}"
    hit = [w for w in FORBIDDEN_GOODS if w in text]
    if hit:
        raise ApiError(BizCode.PARAM_ERROR, f"物品含禁运关键词（{ '、'.join(sorted(set(hit))) }），无法下单")


def service_area(merchant: Merchant) -> dict:
    """商家服务空域 → {"districts": [区域名], "rings": [[[lng,lat]...]]}。

    service_area_json 有两种形态，都得认：
      旧：JSON_ARRAY('瓯江口新区','龙湾区')  —— 只有区域名，没有几何，判不了坐标；
      新：{"districts": [...], "geometry": {GeoJSON Polygon}}（迁移 20260929_p12 灌的演示值）。
    没有几何时一律"不拦"：服务范围是商家自己配的资产，配漏了不能把全平台下单堵死。
    """
    raw = merchant.service_area_json or {}
    if isinstance(raw, list):                      # 旧形态：只有名字
        return {"districts": [str(x) for x in raw], "geometry": None, "rings": []}
    geometry = raw.get("geometry")
    return {"districts": [str(x) for x in (raw.get("districts") or [])],
            "geometry": geometry, "rings": geo.polygon_rings(geometry or {})}


def check_service_area(points: list[tuple[str, float, float]], sa: dict, merchant: Merchant) -> None:
    """寄/收点必须落在商家服务空域内（需求 4.2.2-1）。"""
    if not sa["rings"]:
        return
    for label, lng, lat in points:
        if not any(geo.point_in_polygon(lng, lat, ring) for ring in sa["rings"]):
            raise ApiError(
                BizCode.CONFLICT,
                f"{label}点不在商家「{merchant.merchant_name}」服务空域内"
                f"（当前覆盖：{districts_hint(sa)}），请更换{label}点", 409)


def districts_hint(sa: dict) -> str:
    return "、".join(sa["districts"]) or "未命名区域"


def estimate(*, sender_lng, sender_lat, receiver_lng, receiver_lat,
             weight_kg, delivery_type=1, goods_type="文件", goods_name=None,
             merchant: Merchant | None = None) -> dict:
    """服务端权威试算（禁运品 + 服务空域 + 禁飞区 三道闸，下单与批量导入共用）。"""
    check_goods(str(goods_type or ""), goods_name)
    # 1. 坐标基本合法性（中国大致范围，GCJ-02）
    for lng, lat in [(sender_lng, sender_lat), (receiver_lng, receiver_lat)]:
        if not (73 <= float(lng) <= 135 and 18 <= float(lat) <= 54):
            raise ApiError(BizCode.PARAM_ERROR, "寄/收点坐标不合法（应为 GCJ-02 经纬度）")

    # 2. 服务空域准入：不在商家覆盖范围内的单子，后面再合规也没人接
    merchant = merchant or get_demo_merchant()
    sa = service_area(merchant)
    check_service_area([("寄件", float(sender_lng), float(sender_lat)),
                        ("收件", float(receiver_lng), float(receiver_lat))], sa, merchant)

    weight = Decimal(str(weight_kg))
    if weight <= 0:
        raise ApiError(BizCode.PARAM_ERROR, "重量必须大于 0")

    # 3. 距离与航线采样穿空域检查
    distance_m = geo.haversine_m(float(sender_lng), float(sender_lat),
                                 float(receiver_lng), float(receiver_lat))
    if distance_m < 50:
        raise ApiError(BizCode.PARAM_ERROR, "寄收点距离过近（不足 50 米），无需无人机配送")
    samples = geo.sample_segment((float(sender_lng), float(sender_lat)),
                                 (float(receiver_lng), float(receiver_lat)), 48)
    airspaces = db.session.query(Airspace).filter_by(status=1, deleted=0).all()
    cross = geo.check_route_against_airspaces(samples, airspaces)
    if cross["forbidden"]:
        names = "、".join(a.airspace_name for a in cross["forbidden"])
        raise ApiError(BizCode.CONFLICT, f"航线经过禁飞/管制空域（{names}），无法下单", 409)

    # 4. 计费
    pricing = merchant.pricing_json or {}
    base_per_km = Decimal(str(pricing.get("basePerKm", 3.5)))
    per_kg = Decimal(str(pricing.get("perKg", 1.2)))
    urgent_rate = Decimal(str(pricing.get("urgentRate", 1.5)))
    distance_km = Decimal(str(round(distance_m / 1000, 3)))
    base_fee = _q2(distance_km * base_per_km + weight * per_kg)
    airspace_fee = _q2(LIMITED_ZONE_FEE * len(cross["limited"]))
    urgent_fee = _q2(base_fee * (urgent_rate - 1)) if int(delivery_type) == 2 else Decimal("0.00")
    total = _q2(base_fee + airspace_fee + urgent_fee)
    duration_sec = int(Decimal(str(distance_m)) / CRUISE_SPEED_MPS)

    return {
        "merchantId": merchant.id,
        "merchantName": merchant.merchant_name,
        "distanceM": int(round(distance_m)),
        "estDurationSec": duration_sec,
        "crossLimitedZones": [a.airspace_name for a in cross["limited"]],
        "baseFee": float(base_fee),
        "airspaceFee": float(airspace_fee),
        "urgentFee": float(urgent_fee),
        "discountAmount": 0.0,
        "totalAmount": float(total),
    }
