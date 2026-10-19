"""契约 D6 / 设计07 的"统一上报入口"：POST /tracks/batch 与 POST /devices/heartbeat。

这两句话在文档里挂了很久，但全仓只有仿真器一段 ORM 直写（source 硬编码 1），真机数据
根本没有落点。端点补上之后，断言就盯着四条口径看，因为哪一条漂移了，接进来的真机数据
都会静默变味：
  · source 由服务端定死，请求体里自报 1 也照样落 2 —— 否则"这条轨迹该不该信"失去意义；
  · 一帧脏数据不许拖垮整批：越界的丢掉并回报原因，其余照收（全批不可用才是 400）；
  · 合规判定只有 compliance_monitor 一个出口：设备自报的 violation 不认，偏航帧入库就进台账；
  · 上报只写热快照与链路状态，绝不推状态机：plan.status / aircraft.status 不是遥测能动的。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.extensions import db
from app.models import Aircraft, FlightTrack, Vertiport
from app.services import compliance_monitor, geo, track_service

BATCH_URL = "/api/monitor/tracks/batch"
HB_URL = "/api/monitor/devices/heartbeat"


# ------------------------------------------------------------------ 工具
def _frames(plan, n: int = 3, *, step: int = 5, t0: datetime | None = None,
            speed: float = 12.0, battery: int = 90) -> list[dict]:
    """沿计划航线取 n 个点当上报帧：点都落在线段上，偏航判定不会误报。

    t 给 datetime 而不是字符串：内部调用方（仿真器、persist_frames）的约定就是 datetime，
    字符串只在过 HTTP 那一跳由 _post 转成报文格式。
    """
    route = plan.route_json or []
    idxs = [len(route) // 2] if n == 1 else [round(i * (len(route) - 1) / (n - 1)) for i in range(n)]
    base = t0 or datetime.now().replace(microsecond=0) - timedelta(seconds=step * n)
    return [{
        "t": base + timedelta(seconds=step * k),
        "lng": route[i]["lng"], "lat": route[i]["lat"],
        "altitude": float(plan.altitude_m or 80), "speed": speed,
        "battery": battery - k,
    } for k, i in enumerate(idxs)]


def _off_route_frame(plan) -> dict:
    """造一帧"确实偏航、但没闯禁飞"的位置。

    航线本身是折返的（起飞场→寄件→收件→返场），固定往一个方向偏 300m 可能正好贴着
    另一段航迹，偏航判定反而不触发；所以按"由近到远、四个方向"试到越界为止，
    并且要绕开禁飞/管制区——否则报的是闯禁飞，就不是在测偏航这一路了。
    """
    base = _frames(plan, 1)[0]
    limit = compliance_monitor.sys_cfg_num("route_deviation_limit_m",
                                           compliance_monitor.DEVIATION_LIMIT_M, int)
    for meters in (limit + 50, limit * 3, limit * 8):
        for east, north in ((0, meters), (meters, 0), (0, -meters), (-meters, 0)):
            lng, lat = (round(v, 6) for v in geo.offset_meters(base["lng"], base["lat"],
                                                               east, north))
            if (compliance_monitor.deviation_m(plan.route_json, lng, lat) or 0) > limit \
                    and not compliance_monitor.hit_airspaces(lng, lat, (1, 4)):
                return dict(base, lng=lng, lat=lat)
    raise AssertionError("造不出「偏航且不闯禁飞」的位置，测试现场的航线/空域布局变了")


def _wire(frames: list[dict]) -> list[dict]:
    """过 HTTP 那一跳：时间戳序列化成报文里常见的 "YYYY-mm-dd HH:MM:SS"。"""
    return [dict(f, t=f["t"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(f.get("t"), datetime)
                 else f.get("t")) for f in frames]


def _post(api, uid: int, plan_id: int, frames, **extra) -> dict:
    return api.as_id(uid).post(BATCH_URL,
                               json={"planId": plan_id, "frames": _wire(frames), **extra}).get_json()


def _track_rows(plan_id: int) -> list[FlightTrack]:
    return (db.session.query(FlightTrack).filter(FlightTrack.plan_id == plan_id)
            .order_by(FlightTrack.track_time).all())


def _backdate(table: str, row_id: int, seconds: int, column: str = "updated_at") -> None:
    """显式赋值时间列：MySQL 的 ON UPDATE 只在没被 SET 的列上才自动刷新。"""
    db.session.execute(text(f"UPDATE {table} SET {column}=:t WHERE id=:i"),
                       {"t": datetime.now() - timedelta(seconds=seconds), "i": row_id})
    db.session.commit()


def _devices(api, who: str = "admin") -> dict:
    return api.as_(who).get("/api/monitor/devices").get_json()["data"]


def _row(data: dict, ttype: int, did: int) -> dict:
    return next(x for x in data["list"] if x["terminalType"] == ttype and x["id"] == did)


# ---------------------------------------------------------- POST /tracks/batch
def test_batch_frames_land_with_server_stamped_source(api, session, make_flight):
    """真机走的就是仿真器那同一个落库函数，唯一区别是 source —— 而且这个值不由上报方填。"""
    _, plan = make_flight("flying")
    body = _post(api, plan.pilot_id, plan.id, _frames(plan, 3),
                 source=1, violation="route_deviation")
    assert body["code"] == 0 and body["data"]["accepted"] == 3
    rows = _track_rows(plan.id)
    assert len(rows) == 3 and {r.source for r in rows} == {2}
    # 设备自报"我偏航了"不算证据，服务端沿航线判出来没偏就是没偏
    assert all(r.is_violation == 0 and r.violation_type is None for r in rows)
    assert rows[0].direction is not None            # 相邻帧推出来的航向，不靠上报方自报


def test_batch_writes_the_hot_snapshot(api, session, make_flight):
    _, plan = make_flight("flying")
    last = _frames(plan, 3)[-1]
    _post(api, plan.pilot_id, plan.id, _frames(plan, 3))
    db.session.expire_all()
    fresh = db.session.get(type(plan), plan.id)
    assert fresh.last_report_at is not None
    assert float(fresh.current_lng) == pytest.approx(last["lng"], abs=1e-6)
    assert fresh.current_battery == last["battery"]
    assert db.session.get(Aircraft, plan.aircraft_id).online == 1


def test_dirty_frames_are_dropped_with_reasons(api, session, make_flight):
    """网络抖动送来一批帧、其中三帧坏掉：正确结果收下能用的，并回报坏的那几帧为什么被丢。"""
    _, plan = make_flight("flying")
    good = _frames(plan, 1)[0]
    swapped = dict(good, lng=good["lat"], lat=good["lng"])          # 经纬度写反：接入最常犯的手误
    no_alt = {k: v for k, v in good.items() if k != "altitude"}
    future = dict(good, t=(datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"))
    stale = dict(good, t=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"))

    d = _post(api, plan.pilot_id, plan.id, [good, swapped, no_alt, future, stale])["data"]
    assert d["accepted"] == 1
    assert d["rejectedCount"] == 4
    reasons = "；".join(x["reason"] for x in d["rejected"])
    assert "超出" in reasons and "缺失" in reasons
    assert "晚于服务器时间" in reasons and "补报窗口" in reasons


def test_all_frames_bad_is_400_and_writes_nothing(api, session, make_flight):
    """整批都用不了才 400：这时候给 200 会让上报方以为链路通了、只是数据怪。"""
    _, plan = make_flight("flying")
    bad = [dict(f, lng=999.0) for f in _frames(plan, 2)]
    body = _post(api, plan.pilot_id, plan.id, bad)
    assert body["code"] == 40000 and "没有可用帧" in body["message"]
    assert _track_rows(plan.id) == []


def test_request_shape_and_missing_plan_are_rejected(api, session, make_flight):
    """闸门顺序：先认计划（不存在就是 404），再认报文形状（frames 不是数组就是 400）。"""
    _, plan = make_flight("flying")
    uid = plan.pilot_id
    body = api.as_id(uid).post(BATCH_URL, json={"frames": _wire(_frames(plan, 1))}).get_json()
    assert body["code"] == 40000 and "planId" in body["message"]        # 缺 planId
    # frames 是空数组也不该先报"格式错"：先认计划，才知道这是不是你的计划
    missing = api.as_id(uid).post(BATCH_URL, json={"planId": 999999, "frames": []}).get_json()
    assert missing["code"] == 40400 and missing["message"] == "飞行计划不存在"
    assert api.as_id(uid).post(BATCH_URL,
                               json={"planId": plan.id, "frames": "不是数组"}
                               ).get_json()["code"] == 40000
    assert api.as_id(uid).post(BATCH_URL, json={"planId": plan.id, "frames": []}
                               ).get_json()["code"] == 40000
    assert _track_rows(plan.id) == []


def test_retransmitted_frames_are_not_written_twice(api, session, make_flight):
    """断链重传是上报侧的常态：同一段时间再发一遍，不该把同一段航迹存两份。"""
    _, plan = make_flight("flying")
    frames = _frames(plan, 3)
    assert _post(api, plan.pilot_id, plan.id, frames)["data"]["accepted"] == 3
    again = _post(api, plan.pilot_id, plan.id, frames)["data"]
    assert again["accepted"] == 0 and again["duplicated"] == 3
    assert len(_track_rows(plan.id)) == 3


def test_off_route_frame_enters_the_violation_ledger(api, session, make_flight):
    """入库的帧照旧逐帧过 compliance_monitor：上报入口不是绕过合规的后门。"""
    _, plan = make_flight("flying")
    d = _post(api, plan.pilot_id, plan.id, [_off_route_frame(plan)])["data"]
    assert d["accepted"] == 1 and d["violations"] >= 1
    assert 2 in d["newTypes"] and d["newTypes"][0] == 2
    row = _track_rows(plan.id)[0]
    assert row.is_violation == 1 and row.violation_type == "route_deviation"
    assert db.session.execute(
        text("SELECT COUNT(*) FROM violation_record WHERE plan_id=:p AND violation_type=2"),
        {"p": plan.id}).scalar() == 1


def test_ingest_never_moves_the_flight_state_machine(api, session, make_flight):
    """遥测不是审批也不是降落指令：状态机唯一入口是 D3 的 dispatch_service。"""
    _, plan = make_flight("flying")
    before = (plan.status, db.session.get(Aircraft, plan.aircraft_id).status)
    _post(api, plan.pilot_id, plan.id, _frames(plan, 3))
    db.session.expire_all()
    plan2 = db.session.get(type(plan), plan.id)
    assert (plan2.status, db.session.get(Aircraft, plan2.aircraft_id).status) == before


def test_only_the_assigned_pilot_or_platform_may_report(api, session, make_flight, anon):
    """持有 monitor:track 只等于能看回放，不等于能往别人的计划里灌轨迹数据。"""
    _, plan = make_flight("flying")
    frames = _wire(_frames(plan, 1))
    assert anon.post(BATCH_URL, json={"planId": plan.id, "frames": frames}).status_code == 401
    assert api.as_("customer").post(BATCH_URL, json={"planId": plan.id, "frames": frames}
                                    ).get_json()["code"] == 40300
    # merchant 有 monitor:track，但不是这班的机长
    assert api.as_("merchant").post(BATCH_URL, json={"planId": plan.id, "frames": frames}
                                    ).get_json()["code"] == 40300
    assert api.as_id(plan.pilot_id).post(BATCH_URL,
                                         json={"planId": plan.id, "frames": frames}
                                         ).get_json()["code"] == 0
    assert api.as_("admin").post(BATCH_URL, json={"planId": plan.id, "frames": frames}
                                 ).get_json()["code"] == 0        # 平台侧（空管/运维）可补报


def test_only_executing_or_recently_landed_plans_accept_frames(api, session, make_flight):
    _, pending = make_flight("approved")
    body = _post(api, pending.pilot_id, pending.id, _frames(pending, 1))
    assert body["code"] == 40900 and "不接受轨迹上报" in body["message"]

    _, landed = make_flight("landed")
    assert _post(api, landed.pilot_id, landed.id, _frames(landed, 1))["code"] == 0
    _backdate("flight_plan", landed.id, 20 * 60, "actual_landing_at")
    late = _post(api, landed.pilot_id, landed.id,
                 _frames(landed, 1, t0=datetime.now() - timedelta(minutes=3)))
    assert late["code"] == 40900 and "分钟" in late["message"]


# ------------------------------------------------------ POST /devices/heartbeat
def test_heartbeat_brings_a_silent_aircraft_back_online(api, session, make_flight):
    """没有这个端点，接上真机也永远显示离线：M-63 的在线全靠"最近一次真实写入时间"。"""
    _, plan = make_flight("flying")
    did = plan.aircraft_id
    db.session.execute(text("UPDATE aircraft SET online=0, current_lng=NULL, current_lat=NULL "
                             "WHERE id=:i"), {"i": did})
    db.session.commit()
    _backdate("aircraft", did, 600)
    _backdate("flight_plan", plan.id, 600, "last_report_at")
    assert _row(_devices(api), 1, did)["state"] == "lost"

    pos = plan.route_json[3]
    r = api.as_id(plan.pilot_id).post(HB_URL, json={
        "terminalType": 1, "deviceId": did, "lng": pos["lng"], "lat": pos["lat"],
        "altitude": 78, "speed": 11.5, "battery": 71}).get_json()
    assert r["code"] == 0 and r["data"]["state"] == "online"

    row = _row(_devices(api), 1, did)
    assert row["state"] == "online" and row["battery"] == 71
    assert row["lng"] == pytest.approx(float(pos["lng"]), abs=1e-6)
    db.session.expire_all()
    assert db.session.get(Aircraft, did).status == 2        # 心跳不动状态机


def test_heartbeat_without_usable_position_still_counts(api, session, make_flight):
    """缺一副经纬度不该让整条心跳作废——心跳本身回答的是"听到了没有"。"""
    _, plan = make_flight("flying")
    did = plan.aircraft_id
    _backdate("aircraft", did, 600)
    before = db.session.get(Aircraft, did).current_lng
    r = api.as_id(plan.pilot_id).post(HB_URL, json={
        "terminalType": 1, "deviceId": did, "lng": 999.0, "lat": 30.0, "battery": 55}).get_json()
    assert r["code"] == 0
    db.session.expire_all()
    ac = db.session.get(Aircraft, did)
    assert ac.battery == 55 and ac.current_lng == before          # 越界坐标当没报，位置不许污染
    assert _row(_devices(api), 1, did)["state"] == "online"


def test_heartbeat_does_not_clear_a_manual_lost_mark(api, session, make_flight):
    """运维标了失联、链路一跳就自己变回在线，那这个标记就成了摆设。"""
    _, plan = make_flight("flying")
    did = plan.aircraft_id
    api.as_("admin").post("/api/monitor/devices/lost-mark",
                          json={"terminalType": 1, "deviceId": did, "reason": "图传丢失"})
    r = api.as_id(plan.pilot_id).post(HB_URL, json={
        "terminalType": 1, "deviceId": did, "battery": 88}).get_json()
    assert r["data"]["lostMarked"] is True and r["data"]["state"] == "lost"
    assert _row(_devices(api), 1, did)["state"] == "lost"


def test_ground_station_heartbeat_refreshes_record_and_charger_count(api):
    vp = db.session.get(Vertiport, 1)
    target = max(0, (vp.charger_total or 0) - 1)
    # 先从"从没报过心跳"的起点开始，否则 before/after 两个年龄可能同为 0 秒
    db.session.execute(text("UPDATE vertiport SET last_heartbeat_at=NULL WHERE id=:i"), {"i": vp.id})
    _backdate("vertiport", vp.id, 3600)
    before = _row(_devices(api), 2, vp.id)["heartbeatAgeSec"]

    r = api.as_("admin").post(HB_URL, json={"terminalType": 2, "deviceId": vp.id,
                                            "chargerAvailable": target}).get_json()
    assert r["code"] == 0 and r["data"]["state"] == "online"
    db.session.expire_all()
    assert db.session.get(Vertiport, vp.id).charger_available == target

    row = _row(_devices(api), 2, vp.id)
    assert row["heartbeatAgeSec"] < before
    # 报过心跳之后这一栏就是真心跳，页面不能再挂"· 资料更新"的后缀
    assert row["heartbeatKind"] == "telemetry"
    db.session.expire_all()
    assert db.session.get(Vertiport, vp.id).last_heartbeat_at is not None
    # 桩位数超出总桩数就当没报：宁可保留上一次的读数，也不把一个坏报文写进台账
    api.as_("admin").post(HB_URL, json={"terminalType": 2, "deviceId": vp.id,
                                        "chargerAvailable": (vp.charger_total or 0) + 9}).get_json()
    db.session.expire_all()
    assert db.session.get(Vertiport, vp.id).charger_available == target


def test_station_goes_offline_once_its_heartbeat_goes_stale(api):
    """场站"开放"和场站"还在报"是两件事，混成一列就会有一件被另一件盖掉。"""
    vp = db.session.get(Vertiport, 1)
    db.session.execute(text("UPDATE vertiport SET status=1, last_heartbeat_at=NULL WHERE id=:i"),
                       {"i": vp.id})
    db.session.commit()

    never = _row(_devices(api), 2, vp.id)
    assert never["state"] == "online" and never["heartbeatKind"] == "record"

    assert api.as_("admin").post(HB_URL, json={"terminalType": 2, "deviceId": vp.id}
                                 ).get_json()["code"] == 0
    assert _row(_devices(api), 2, vp.id)["heartbeatKind"] == "telemetry"

    # 报过之后再静默：运营状态仍是"开放"，但设备停了口，页面得跟着掉线
    _backdate("vertiport", vp.id, 3600, "last_heartbeat_at")
    stale = _row(_devices(api), 2, vp.id)
    assert stale["state"] == "offline" and stale["heartbeatKind"] == "telemetry"

    # 人工失联始终压过推导
    api.as_("admin").post("/api/monitor/devices/lost-mark",
                          json={"terminalType": 2, "deviceId": vp.id, "reason": "桩机电源中断"})
    assert _row(_devices(api), 2, vp.id)["state"] == "lost"


def test_heartbeat_is_scoped_to_the_device_owner(api, session, make_flight,
                                                 fresh_user, second_merchant, anon):
    _, plan = make_flight("flying")
    did = plan.aircraft_id
    payload = {"terminalType": 1, "deviceId": did, "battery": 60}
    assert anon.post(HB_URL, json=payload).status_code == 401
    outsider = fresh_user("m2maintainer", role_code="merchant_maintainer", merchant_id=2)
    assert api.as_id(outsider.id).post(HB_URL, json=payload).get_json()["code"] == 40300
    owner = fresh_user("m1maintainer", role_code="merchant_maintainer")
    assert api.as_id(owner.id).post(HB_URL, json=payload).get_json()["code"] == 0
    assert api.as_("customer").post(HB_URL, json=payload).get_json()["code"] == 40300


def test_heartbeat_validates_input(api, session, make_flight):
    _, plan = make_flight("flying")
    bad_type = api.as_("admin").post(HB_URL, json={"terminalType": 9, "deviceId": 1}).get_json()
    assert bad_type["code"] == 40000 and "终端类别" in bad_type["message"]
    missing_id = api.as_("admin").post(HB_URL, json={"terminalType": 1}).get_json()
    assert missing_id["code"] == 40000
    ghost = api.as_("admin").post(HB_URL, json={"terminalType": 1, "deviceId": 999999}).get_json()
    assert ghost["code"] == 40400


# -------------------------------------------------------------- 兼容与共用
def test_simulator_and_gcs_frames_coexist_in_one_table(api, session, make_flight):
    """source 是"这条轨迹该不该信"的依据：同一个入口、两路数据、各带各的来源标记。"""
    _, plan = make_flight("flying")
    sim = _frames(plan, 2, t0=datetime.now().replace(microsecond=0) - timedelta(minutes=6))
    assert track_service.persist_frames(plan, sim, source=1)["accepted"] == 2
    db.session.commit()

    real = _frames(plan, 2)
    assert _post(api, plan.pilot_id, plan.id, real)["data"]["accepted"] == 2
    rows = _track_rows(plan.id)
    assert len(rows) == 4 and {r.source for r in rows} == {1, 2}
    assert [r.plan_id for r in rows] == [plan.id] * 4
