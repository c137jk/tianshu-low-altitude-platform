"""气象服务 Mock（设计07 L4；对应外部 U-Weather 接口）。

不真接气象局，按经纬度网格 + 当前小时做确定性伪随机：
同一位置同一小时内多次查询结果一致（可复现、可演示），绝大多数时间适飞。
真实对接时替换为气象 API 调用，service 层只认 current() 的返回结构。

预警等级 warning_level：0 无 / 1 蓝 / 2 黄 / 3 橙 / 4 红；
可飞判定在 dispatch_service：橙红预警、大风、低能见度会阻断申报。
"""
import hashlib
from datetime import datetime

DESC_POOL = ["晴", "多云", "阴", "小雨"]
WIND_DIRS = ["北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风"]


def _grid_seed(lng: float, lat: float, hour_key: str) -> int:
    raw = f"{round(float(lng), 3)},{round(float(lat), 3)},{hour_key}"
    return int(hashlib.md5(raw.encode("utf-8")).hexdigest(), 16)


class MockWeatherAdapter:
    def current(self, lng: float, lat: float, at: datetime | None = None) -> dict:
        at = at or datetime.now()
        hour_key = at.strftime("%Y%m%d%H")
        seed = _grid_seed(lng, lat, hour_key)
        # 90% 适飞好天气；约 10% 出现蓝色提示（不阻断）；橙红仅在显式构造的测试场景出现
        roll = seed % 100
        if roll < 55:
            wind, vis, desc, level, temp = round(2 + (seed % 10) / 10, 1), 15000, "晴", 0, 26.0
        elif roll < 90:
            wind, vis, desc, level, temp = round(3.2 + (seed % 15) / 10, 1), 12000, "多云", 0, 24.5
        elif roll < 97:
            wind, vis, desc, level, temp = round(5.5 + (seed % 12) / 10, 1), 8000, "阴", 1, 23.0
        else:
            wind, vis, desc, level, temp = round(7.5 + (seed % 10) / 10, 1), 6000, "小雨", 1, 21.5
        direction = (seed % 8) * 45
        return {
            "lng": round(float(lng), 6), "lat": round(float(lat), 6),
            "windSpeed": wind,                       # m/s
            "windDirection": direction,             # 度
            "windDirectionName": WIND_DIRS[seed % 8],
            "visibilityM": vis,
            "temperature": temp,
            "weatherDesc": desc,
            "warningLevel": level,
            "warningLevelName": ["无", "蓝色", "黄色", "橙色", "红色"][level],
            "fetchedAt": at.strftime("%Y-%m-%d %H:%M:%S"),
            "flyable": wind < 10 and vis >= 3000 and level < 3,
            "mock": True,
        }


weather_adapter = MockWeatherAdapter()
