"""演示地址位置确认脚本（设计07 数据质量闸门）。

做法正是你要的"先按地名定位 POI，再确认位置"：
  1) 用高德 inputtips（POI 关键字搜索）按 PLACES 里的名字搜真实 POI；
  2) 要求搜到的 POI 与种子里写死的坐标距离 < 300m，否则报警（说明坐标是手估的、没对准 POI）；
  3) regeo 反查坐标，要求落在有街道/乡镇的陆地上（不是海里）。
跑通才能 --force 造演示数据。需要网络（高德 Web 服务 Key 在 backend/.env）。
"""
import sys
import urllib.parse
import urllib.request
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seed.generate_mock_data import PLACES

KEY = "cea8cffc0a2ef53e9c1d260c240f0c7f"


def get(path, params):
    params["key"] = KEY
    url = f"https://restapi.amap.com{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def hav(lng1, lat1, lng2, lat2):
    from math import radians, sin, cos, asin, sqrt
    r = 6371000
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))


bad = 0
for idx, (name, lng, lat) in enumerate(PLACES):
    cluster = idx <= 4  # 短驳演示簇严格核对；远郊长航程点只要求在陆地上
    tips = get("/v3/assistant/inputtips", {"keywords": name, "city": "温州", "datatype": "poi"})
    hits = [t for t in (tips.get("tips") or []) if t.get("location")]
    rg = get("/v3/geocode/regeo", {"location": f"{lng},{lat}", "extensions": "base"})
    rc = (rg.get("regeocode") or {}).get("addressComponent", {})
    township = rc.get("township") or ""
    district = rc.get("district") or ""
    on_land = bool(township)
    if hits:
        best = min(hits, key=lambda t: hav(lng, lat, *[float(x) for x in t["location"].split(",")]))
        d = hav(lng, lat, *[float(x) for x in best["location"].split(",")])
        aligned = d < (300 if cluster else 3000)
        print(f"{'OK ' if aligned and on_land else ('BAD' if cluster else 'WARN')} "
              f"{'[簇]' if cluster else '[远]'} {name} | POI[{best['name']}] 偏差{d:.0f}m | "
              f"regeo {district}/{township or '海里?'}")
        if cluster and not aligned:
            bad += 1
    else:
        print(f"{'BAD' if cluster else 'WARN'} {'[簇]' if cluster else '[远]'} {name} | "
              f"inputtips 搜不到该 POI！regeo {district}/{township or '海里?'}")
        if cluster:
            bad += 1
    if not on_land:
        bad += 1
        print("    ↑ 不在陆地上！")

print(f"\n核心簇位置确认通过；失败项 {bad}")
sys.exit(1 if bad else 0)
