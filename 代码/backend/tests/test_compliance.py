"""飞行中合规监测（设计05/07）：四类判定、去重入账、轨迹打标、应急联动。

这块是差距分析里最硬的一条——"违规记录只能人工录入，自动检测没有实现"。所以用例不只测
"能不能判出来"，更测"判出来之后有没有变成台账、站内信和应急事件"，以及"60 倍速连续
判定下会不会把台账刷成垃圾数据"。判定阈值一律走 sys_config，用例里会改参数并清缓存，
证明它真是配置驱动而不是写死在代码里。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.common.scope import invalidate_cfg, sys_cfg_num
from app.extensions import db
from app.models import EmergencyEvent, FlightTrack, ViolationRecord
from app.services import compliance_monitor

NO_FLY_POINT = (120.8550, 27.9150)      # 龙湾机场净空禁飞区（120.83~120.88 / 27.90~27.93）内部
LIMITED_POINT = (120.6300, 28.0000)     # 瓯海中心限飞区（120.60~120.66 / 27.98~28.03，限高 60m）


def _types(hits):
    return [h["type"] for h in hits]


def _notif_count(biz_type, biz_id=None):
    sql = "SELECT COUNT(*) FROM message_notification WHERE biz_type=:bt"
    params = {"bt": biz_type}
    if biz_id is not None:
        sql += " AND biz_id=:bi"
        params["bi"] = biz_id
    return db.session.execute(text(sql), params).scalar()


# ---------------------------------------------------------------- 四类判定

def test_forbidden_zone_intrusion_is_detected(session, make_flight):
    _, plan = make_flight("flying")
    hits = compliance_monitor.findings(plan, *NO_FLY_POINT, 50)
    assert 1 in _types(hits)
    hit = next(h for h in hits if h["type"] == 1)
    assert "龙湾机场净空禁飞区" in hit["desc"] and hit["tag"] == "no_fly_intrusion"


def test_on_route_point_is_clean(session, make_flight):
    """航线上、限高内、时段内的位置不该产生任何违规——否则大屏会满屏假告警。"""
    _, plan = make_flight("flying")
    mid = plan.route_json[len(plan.route_json) // 2]
    hits = compliance_monitor.findings(plan, float(mid["lng"]), float(mid["lat"]),
                                       float(plan.altitude_m or 80))
    assert hits == []


def test_deviation_beyond_config_limit_is_detected(session, make_flight):
    _, plan = make_flight("flying")
    mid = plan.route_json[len(plan.route_json) // 2]
    from app.services.geo import offset_meters
    off = offset_meters(float(mid["lng"]), float(mid["lat"]), 160.0, 160.0)  # 约 226m
    assert _types(compliance_monitor.findings(plan, off[0], off[1], 80)) == [2]

    # 阈值调到 400m 后同一位置就不该再判偏航：证明读的是 sys_config 而不是常量
    db.session.execute(text("UPDATE sys_config SET config_value='400' "
                            "WHERE config_key='route_deviation_limit_m'"))
    db.session.commit()
    invalidate_cfg("route_deviation_limit_m")
    try:
        assert sys_cfg_num("route_deviation_limit_m", 100, int) == 400
        assert compliance_monitor.findings(plan, off[0], off[1], 80) == []
    finally:
        db.session.execute(text("UPDATE sys_config SET config_value='100' "
                                "WHERE config_key='route_deviation_limit_m'"))
        db.session.commit()
        invalidate_cfg("route_deviation_limit_m")


def test_over_schedule_is_detected_after_tolerance(session, make_flight):
    _, plan = make_flight("flying")
    late = plan.plan_end_at + timedelta(minutes=30)
    hits = compliance_monitor.findings(plan, float(plan.current_lng), float(plan.current_lat),
                                       80, now=late)
    assert 3 in _types(hits)
    # 容忍窗口内（默认 10 分钟）不算超时
    on_time = plan.plan_end_at + timedelta(minutes=5)
    assert 3 not in _types(compliance_monitor.findings(
        plan, float(plan.current_lng), float(plan.current_lat), 80, now=on_time))


def test_altitude_exceeds_global_and_local_limits(session, make_flight):
    _, plan = make_flight("flying")
    mid = plan.route_json[len(plan.route_json) // 2]
    hits = compliance_monitor.findings(plan, float(mid["lng"]), float(mid["lat"]), 200)
    over = next(h for h in hits if h["type"] == 4)
    assert "全局限高" in over["desc"]

    limited = compliance_monitor.findings(plan, *LIMITED_POINT, 80)
    hits4 = [h for h in limited if h["type"] == 4]
    assert hits4 and "瓯海中心限飞区" in hits4[0]["desc"]


# ---------------------------------------------------------------- 台账与去重

def test_violation_record_created_then_deduplicated(session, make_flight):
    """同计划同类型未结案只入账一次：60 倍速每帧都判，台账不能被刷爆。"""
    _, plan = make_flight("flying")
    finding = {"type": 1, "desc": "测试闯禁飞"}
    first = compliance_monitor.record_violation(plan, finding, *NO_FLY_POINT)
    assert first is not None and first.status == 1
    assert compliance_monitor.record_violation(plan, finding, *NO_FLY_POINT) is None
    assert db.session.query(ViolationRecord).filter_by(plan_id=plan.id).count() == 1

    # 另一种类型不受影响，仍然入账
    assert compliance_monitor.record_violation(plan, {"type": 2, "desc": "测试偏航"},
                                               *NO_FLY_POINT) is not None
    # 结案（归档）后同类可以再入账
    first.status = 3
    db.session.commit()
    assert compliance_monitor.record_violation(plan, finding, *NO_FLY_POINT) is not None


def test_violation_notifies_pilot_and_platform(session, make_flight):
    """违规必须有人被通知到：机长单点 + 严重违规（闯禁飞/超高）平台广播。"""
    _, plan = make_flight("flying")
    rec = compliance_monitor.record_violation(plan, {"type": 1, "desc": "闯禁飞"}, *NO_FLY_POINT)
    assert _notif_count("violation", rec.id) == 2      # pilot_id + user_id=0 广播
    rows = db.session.execute(
        text("SELECT user_id FROM message_notification WHERE biz_type='violation' AND biz_id=:i"),
        {"i": rec.id}).all()
    assert {r[0] for r in rows} == {plan.pilot_id, 0}

    light = compliance_monitor.record_violation(plan, {"type": 2, "desc": "偏航"}, *NO_FLY_POINT)
    assert _notif_count("violation", light.id) == 1    # 非严重类型不广播


def test_track_frame_is_annotated_before_insert(session, make_flight):
    """轨迹帧的违规标记必须在 INSERT 之前写好。

    flight_track 是 (id, track_time) 复合主键的分区表，插完再补 UPDATE 会因毫秒值对不上
    触发 StaleDataError 整帧回滚——这条用例就是把那个坑钉住。
    """
    _, plan = make_flight("flying")
    frame = FlightTrack(plan_id=plan.id, aircraft_id=plan.aircraft_id, order_id=plan.order_id,
                        lng=NO_FLY_POINT[0], lat=NO_FLY_POINT[1], altitude=60, speed=12,
                        direction=90, battery=88,
                        track_time=datetime.now().replace(microsecond=0), source=1)
    hits = compliance_monitor.annotate(plan, frame)
    db.session.add(frame)
    db.session.flush()                 # 真往分区表插一帧，不报错才算过
    assert frame.is_violation == 1 and frame.violation_type == "no_fly_intrusion"
    assert 1 in [h["type"] for h in hits]
    db.session.rollback()


def test_commit_findings_writes_ledger_and_emergency(session, make_flight):
    """闯禁飞除了台账，还要立刻立一条"闯入禁飞区"应急事件（设计07 的闭环）。"""
    _, plan = make_flight("flying")
    frame = FlightTrack(plan_id=plan.id, aircraft_id=plan.aircraft_id, order_id=plan.order_id,
                        lng=NO_FLY_POINT[0], lat=NO_FLY_POINT[1], altitude=60, speed=12,
                        track_time=datetime.now().replace(microsecond=0), source=1)
    db.session.add(frame)
    db.session.flush()
    recorded = compliance_monitor.commit_findings(
        plan, frame, [{"type": 1, "desc": "闯入禁飞区", "tag": "no_fly_intrusion"}])
    assert recorded == [1]
    ev = (db.session.query(EmergencyEvent)
          .filter_by(plan_id=plan.id, event_type=2).first())
    assert ev is not None and ev.status == 1 and ev.level == 2
    assert ev.description == "闯入禁飞区"
    assert frame.id in [r.track_id for r in
                        db.session.query(ViolationRecord).filter_by(plan_id=plan.id).all()]

    # 同一帧位置再判一次：台账去重，应急事件也不重复
    again = compliance_monitor.commit_findings(
        plan, frame, [{"type": 1, "desc": "闯入禁飞区", "tag": "no_fly_intrusion"}])
    assert again == []
    assert db.session.query(EmergencyEvent).filter_by(plan_id=plan.id).count() == 1


def test_emergencies_resolve_on_landing(session, make_flight):
    _, plan = make_flight("flying")
    compliance_monitor.raise_emergency(plan, 1, 3, "失联")
    compliance_monitor.raise_emergency(plan, 3, 1, "低电量")
    assert compliance_monitor.raise_emergency(plan, 1, 3, "失联") is None   # 同类只保留一条

    resolved = compliance_monitor.resolve_emergencies(plan, measure="安全降落")
    assert resolved == 2
    rows = db.session.query(EmergencyEvent).filter_by(plan_id=plan.id).all()
    assert all(r.status == 3 and r.closed_at is not None for r in rows)
    assert all("安全降落" in (r.handle_measure or "") for r in rows)


def test_summarize_counts_open_violations_by_type(session, make_flight):
    _, plan = make_flight("flying")
    compliance_monitor.record_violation(plan, {"type": 1, "desc": "a"}, *NO_FLY_POINT)
    compliance_monitor.record_violation(plan, {"type": 2, "desc": "b"}, *NO_FLY_POINT)
    done = compliance_monitor.record_violation(plan, {"type": 4, "desc": "c"}, *NO_FLY_POINT)
    done.status = 3
    db.session.commit()
    assert compliance_monitor.summarize([plan.id]) == {"闯禁飞区": 1, "偏航": 1}
    assert compliance_monitor.summarize([]) == {}


def test_deviation_math_is_metric(session):
    """deviation_m 用的是等距近似，误差在城市尺度可接受；这里锁住量级别退化成"度"。"""
    route = [{"lng": 120.9000, "lat": 27.9500}, {"lng": 120.9100, "lat": 27.9500}]
    assert compliance_monitor.deviation_m(route, 120.9000, 27.9500) == pytest.approx(0, abs=1)
    assert compliance_monitor.deviation_m(route, 120.9050, 27.9510) == pytest.approx(110, abs=25)
    assert compliance_monitor.deviation_m(None, 120.9, 27.95) is None
    assert compliance_monitor.deviation_m(route, 120.9500, 27.9500) > 1000
