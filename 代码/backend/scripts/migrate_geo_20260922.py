"""一次性迁移（幂等）：场站坐标/地址、可飞示范区多边形与高德真实地理对齐。

背景：初版 DDL/造数坐标为手估，导致地图标记与地名不符。
本脚本把 DDL 7.6/7.8 的修正同步到已建好的现网库 tianshu_utms。
可重复执行。
"""
from app import create_app
from app.extensions import db
from app.models import Vertiport, Airspace

VP1 = (120.929500, 27.955500, "温州市洞头区瓯江口产业集聚区灵昆岛")
VP2 = (120.815000, 27.945000, "温州市龙湾区温州湾城市配送点")
# 瓯江口低空可飞示范区：覆盖灵昆岛瓯江口核心区，与机场禁飞框（东界 120.880）留 500m 缝
GREEN_POLY = [[
    [120.885, 27.915], [120.975, 27.915], [120.975, 27.975],
    [120.885, 27.975], [120.885, 27.915],
]]

app = create_app()
with app.app_context():
    vp1 = db.session.query(Vertiport).filter_by(station_no="VP-WZ-001").first()
    if vp1:
        vp1.lng, vp1.lat, vp1.address = VP1
        print("VP-WZ-001 已更新为", VP1[:2])
    vp2 = db.session.query(Vertiport).filter_by(station_no="VP-WZ-002").first()
    if vp2:
        vp2.lng, vp2.lat, vp2.address = VP2
        print("VP-WZ-002 已更新为", VP2[:2])
    green = db.session.query(Airspace).filter_by(airspace_no="AS-WZ-003").first()
    if green:
        green.geometry_json = {"type": "Polygon", "coordinates": GREEN_POLY}
        print("AS-WZ-003 可飞示范区多边形已更新")
    db.session.commit()
    print("迁移完成")
