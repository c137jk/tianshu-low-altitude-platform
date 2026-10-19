"""地理几何函数（GCJ-02 经纬度，全库统一坐标系）。

D2 下单试算、D5 空域合规、D6 飞行仿真三处共用，禁止各写一份。
- haversine_m：球面距离（米）
- point_in_polygon：射线法判断点是否在多边形内（边界算在内）
- bearing_deg：两帧之间的航向角（仿真器与真机上报都从这里取，别再各算一份 atan2）
- sample_segment：大圆近似的直线等距采样（小区域用线性插值足够）
- check_route_against_airspaces：航线采样点 vs 空域多边形，返回穿飞情况
- binding_ceiling：从若干空域上限里选出真正生效的那一个（申报准入与飞行中判定共用）
"""
import math
from typing import Iterable

EARTH_RADIUS_M = 6371000.0


def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """两点球面距离（米）。"""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def point_in_polygon(lng: float, lat: float, ring: list[list[float]]) -> bool:
    """射线法；ring 为 [[lng, lat], ...]，首点无需闭合。"""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        # 点落在顶点上
        if (lng == xi and lat == yi) or (lng == xj and lat == yj):
            return True
        intersects = ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def offset_meters(lng: float, lat: float, east_m: float, north_m: float) -> tuple[float, float]:
    """把位置沿东/北平移若干米（城市尺度的等距柱面近似，够演示用）。"""
    return (lng + east_m / (111320.0 * math.cos(math.radians(lat))),
            lat + north_m / 110540.0)


def bearing_deg(prev: tuple[float, float] | None, lng: float, lat: float) -> int:
    """两帧之间的航向角（0~359，正北为 0）。起点未知或原地不动都返回 0。"""
    if prev is None:
        return 0
    dlng, dlat = lng - prev[0], lat - prev[1]
    if abs(dlng) < 1e-12 and abs(dlat) < 1e-12:
        return 0
    return int((math.degrees(math.atan2(dlng, dlat)) + 360) % 360)


def sample_segment(p1: tuple[float, float], p2: tuple[float, float], samples: int = 40) -> list[tuple[float, float]]:
    """在寄收两点之间线性插值出采样点（含两端）。"""
    if samples < 2:
        samples = 2
    return [
        (
            p1[0] + (p2[0] - p1[0]) * i / (samples - 1),
            p1[1] + (p2[1] - p1[1]) * i / (samples - 1),
        )
        for i in range(samples)
    ]


def polygon_rings(geometry: dict) -> list[list[list[float]]]:
    """从 GeoJSON Geometry 取多边形外环列表（支持 Polygon / MultiPolygon）。"""
    if not geometry:
        return []
    t = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if t == "Polygon":
        return [coords[0]]
    if t == "MultiPolygon":
        return [poly[0] for poly in coords]
    return []


def check_route_against_airspaces(
    points: Iterable[tuple[float, float]],
    airspaces: list,
) -> dict:
    """航线采样点对一批 Airspace 对象做合规预检。

    返回：
      forbidden: 穿飞的禁飞/管制空域列表（任一命中即不可下单）
      limited:   穿飞的限飞空域列表（去重，计费/申报用）
      ceiled:    穿飞且设了高度上限的空域列表（去重，高度准入用）
                 限飞区和可飞区都带 altitude_max，只按类型 2 筛会把可飞区的上限整个漏掉
    Airspace 需有 airspace_type / airspace_name / geometry_json 属性。
    """
    pts = list(points)
    forbidden, limited, ceiled = [], [], []
    limited_seen, ceiled_seen = set(), set()
    for asp in airspaces:
        if asp.status != 1:
            continue
        rings = polygon_rings(asp.geometry_json or {})
        hit = any(point_in_polygon(lng, lat, ring) for lng, lat in pts for ring in rings)
        if not hit:
            continue
        if asp.airspace_type in (1, 4):  # 1 禁飞 4 临时管制
            forbidden.append(asp)
            continue
        if asp.airspace_type == 2 and asp.id not in limited_seen:
            limited_seen.add(asp.id)
            limited.append(asp)
        if asp.altitude_max is not None and asp.id not in ceiled_seen:
            ceiled_seen.add(asp.id)
            ceiled.append(asp)
    return {"forbidden": forbidden, "limited": limited, "ceiled": ceiled}


def binding_ceiling(candidates: Iterable[tuple[str, float | None]], altitude: float):
    """从若干 (显示名, 高度上限m) 里挑出真正卡住这个高度的那一个，返回 (显示名, 上限)。

    空域套叠时上限最低的那个才是约束。取"第一个命中"会写出「超过甲空域上限 60m」
    而 60 其实来自乙空域这种自相矛盾的结论，申报驳回和飞行中违规都出现过。
    上限为 None 表示该空域不限高；全部没超限时返回 None。
    """
    worst = None
    for label, ceiling in candidates:
        if ceiling is None or altitude <= float(ceiling):
            continue
        if worst is None or float(ceiling) < worst[1]:
            worst = (label, float(ceiling))
    return worst
