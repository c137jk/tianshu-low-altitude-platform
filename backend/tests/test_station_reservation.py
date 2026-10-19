"""场站预约（M-55）：机位预占、审核放行、状态流转与数据范围。

这里钉住的是四条容易被写歪的规则，而不只是"接口能通"：
  · 机位口径——待审(1)与通过(2)都占位。只算"已通过"的话，两个商家各自排队申请都能通过，
    一起放下来就超容；所以申请时预占、审核时再核一次，两边共用 _check_slot。
  · 用途分池——起降看泊位 capacity，换电/充电共用充电桩 charger_total，两类互不挤占。
  · 数据范围——商家只能为本商家申请、取消、回执；审核权只在平台侧（运维/平台管理员）。
  · 时段流转——已开始的通过单不给直接取消，未结束的通过单不给提前回执完成。

预约时间闸门读的是 datetime.now()，用例一律按"明天整点"造时段，避免贴着当前时间抖。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.extensions import db

STATION_NO = "VP-PYTEST-RSV"


def _slot(day_offset=1, hour=9, duration_min=60):
    st = (datetime.now() + timedelta(days=day_offset)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    return st, st + timedelta(minutes=duration_min)


def _payload(station_id, purpose=1, day_offset=1, hour=9, duration_min=60, **kw):
    st, en = _slot(day_offset, hour, duration_min)
    body = {"stationId": station_id, "purpose": purpose,
            "startAt": st.isoformat(), "endAt": en.isoformat()}
    body.update(kw)
    return body


STATION_SEQ = iter(range(1, 999))


def _mk_station(capacity=1, charger_total=0, status=1) -> int:
    """现造一台小场站：泊位/充电桩设成 1 才能用两条预约把冲突场景撑出来。

    种子场站有 8 个泊位，真要占满得插 8 条数据；conftest 的清场会把
    id 不在 (1,2) 的场站删掉，所以这里造的站不用自己收拾。
    同一个用例里可能造两台（开放站 + 维护中站），场站编号必须唯一。
    """
    seq = next(STATION_SEQ)
    no = f"{STATION_NO}-{seq}"
    db.session.execute(text(
        "INSERT INTO vertiport (station_no, station_name, station_type, lng, lat, address,"
        " merchant_id, capacity, charger_total, charger_available, occupied_count, status, deleted)"
        " VALUES (:no,:nm,3,120.65,28.00,'pytest 测试场站',1,:cap,:ct,:cap,0,:st,0)"),
        {"no": no, "nm": f"pytest 场站预约测试站{seq}", "cap": capacity,
         "ct": charger_total, "st": status})
    db.session.commit()
    return int(db.session.execute(
        text("SELECT id FROM vertiport WHERE station_no=:no"), {"no": no}).scalar())


def _row(rid) -> dict:
    db.session.expire_all()
    return db.session.execute(text(
        "SELECT status, merchant_id, aircraft_id, audit_opinion, reservation_no,"
        " station_id, purpose FROM station_reservation WHERE id=:i"), {"i": rid}).mappings().first()


def _id_of(body) -> int:
    assert body["code"] == 0, body
    return body["data"]["id"]


# ---------------------------------------------------------------- 申请与机位预占

def test_create_pends_audit_and_holds_the_slot(api):
    """申请即预占：状态是待审，但机位已经被吃掉——不是等审核通过才开始排队。"""
    sid = _mk_station(capacity=1)
    body = api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid)).get_json()
    assert body["code"] == 0
    assert body["data"]["reservationNo"].startswith("SR")
    r = _row(body["data"]["id"])
    assert r["status"] == 1 and r["merchant_id"] == 1
    assert r["reservation_no"] == body["data"]["reservationNo"]


def test_second_merchant_cannot_take_the_last_slot(api):
    """capacity=1 时重叠时段的第二条申请必须撞墙，报错要说清时段、机位类型和余量。

    用 9~11 与 10~11 这两个相交但不相等的区间，顺带钉住"相交判定"而不是"整点相同判定"。
    """
    sid = _mk_station(capacity=1)
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, hour=9, duration_min=120)).get_json()["code"] == 0
    resp = api.as_("merchant").post("/api/resource/reservations", json=_payload(sid, hour=10))
    body = resp.get_json()
    assert resp.status_code == 409 and "已被占满（1/1）" in body["message"]
    assert "10:00~11:00" in body["message"] and "泊位" in body["message"]
    assert int(db.session.execute(
        text("SELECT COUNT(*) FROM station_reservation")).scalar()) == 1, \
        "被拒的申请不能留在台账里占位"


def test_cancel_releases_the_slot_for_the_next_applicant(api):
    sid = _mk_station(capacity=1)
    first = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                            json=_payload(sid)).get_json())
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid)).get_json()["code"] != 0
    assert api.as_("merchant").post(f"/api/resource/reservations/{first}/cancel").get_json()["code"] == 0
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid)).get_json()["code"] == 0


def test_takeoff_and_charging_use_separate_pools(api):
    """起降吃 capacity、换电/充电吃 charger_total：把两类混在一个池子里算，实际不冲突的也会误拒。"""
    sid = _mk_station(capacity=1, charger_total=1)
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, purpose=1)).get_json()["code"] == 0
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, purpose=2)).get_json()["code"] == 0
    # 换电与充电共用同一批桩，第三条无论哪一类都进不来
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, purpose=3)).get_json()["code"] != 0


def test_non_overlapping_windows_share_one_slot(api):
    """时段判定是区间相交：9~10 与 10~11 不重叠，一个泊位就该能连约两次。"""
    sid = _mk_station(capacity=1)
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, hour=9)).get_json()["code"] == 0
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, hour=10)).get_json()["code"] == 0


def test_completed_or_rejected_reservations_stop_holding(api):
    """终态（驳回/完成/取消）不再占位，否则一次误约就把机位锁到天荒地老。"""
    sid = _mk_station(capacity=1)
    rid = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                         json=_payload(sid)).get_json())
    api.as_("admin").post(f"/api/resource/reservations/{rid}/audit",
                          json={"result": 3, "opinion": "时段与检修冲突"})
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid)).get_json()["code"] == 0


# ---------------------------------------------------------------- 审核

def test_audit_approve_then_notify_applicant(api):
    sid = _mk_station(capacity=2)
    rid = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                         json=_payload(sid)).get_json())
    body = api.as_("admin").post(f"/api/resource/reservations/{rid}/audit",
                                 json={"result": 2, "opinion": "同意"}).get_json()
    assert body["code"] == 0
    assert _row(rid)["status"] == 2
    msg = db.session.execute(text(
        "SELECT title, biz_type FROM message_notification WHERE biz_type='reservation'")).mappings().all()
    assert len(msg) == 1 and _row(rid)["reservation_no"] in msg[0]["title"]


def test_audit_rechecks_capacity_at_pass_time(api):
    """申请后场站改造收窄了机位，放行必须再判一次——否则超容是审核点出来的。"""
    sid = _mk_station(capacity=2)
    window = _payload(sid, hour=9, duration_min=120)
    first = _id_of(api.as_("merchant").post("/api/resource/reservations", json=window).get_json())
    second = _id_of(api.as_("merchant").post("/api/resource/reservations", json=window).get_json())
    assert api.as_("admin").post(f"/api/resource/reservations/{first}/audit",
                                 json={"result": 2}).get_json()["code"] == 0
    db.session.execute(text("UPDATE vertiport SET capacity=1 WHERE id=:s"), {"s": sid})
    db.session.commit()
    resp = api.as_("admin").post(f"/api/resource/reservations/{second}/audit",
                                 json={"result": 2}).get_json()
    assert "已被占满（1/1）" in resp["message"]
    assert _row(second)["status"] == 1, "放行失败不能把待审单顺手改成已通过"


def test_audit_reject_requires_an_opinion_and_cannot_repeat(api):
    sid = _mk_station(capacity=1)
    rid = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                         json=_payload(sid)).get_json())
    assert api.as_("admin").post(f"/api/resource/reservations/{rid}/audit",
                                 json={"result": 3}).status_code == 400
    assert api.as_("admin").post(f"/api/resource/reservations/{rid}/audit",
                                 json={"result": 9}).status_code == 400
    assert api.as_("admin").post(f"/api/resource/reservations/{rid}/audit",
                                 json={"result": 3, "opinion": "机位已满"}).get_json()["code"] == 0
    assert _row(rid)["audit_opinion"] == "机位已满"
    resp = api.as_("admin").post(f"/api/resource/reservations/{rid}/audit",
                                 json={"result": 2}).get_json()
    assert "已是「已驳回」" in resp["message"]


def test_audit_is_platform_side_only(api, fresh_user, second_merchant):
    """商家不能自己放行（本商家不行，别家更不行）；运维是场站运营方，可以审。"""
    sid = _mk_station(capacity=1)
    rid = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                         json=_payload(sid)).get_json())
    assert api.as_("merchant").post(f"/api/resource/reservations/{rid}/audit",
                                    json={"result": 2}).get_json()["code"] == 40300
    other = fresh_user("t_rsv_other_m", role_code="merchant_admin", merchant_id=second_merchant)
    assert api.as_id(other.id).post(f"/api/resource/reservations/{rid}/audit",
                                   json={"result": 2}).get_json()["code"] == 40300
    ops = fresh_user("t_rsv_ops", role_code="ops_maintainer", user_type=5, merchant_id=None)
    assert api.as_id(ops.id).post(f"/api/resource/reservations/{rid}/audit",
                                 json={"result": 2}).get_json()["code"] == 0


# ---------------------------------------------------------------- 取消 / 回执

def test_cancel_is_scoped_and_state_gated(api, fresh_user, second_merchant):
    sid = _mk_station(capacity=1)
    rid = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                         json=_payload(sid)).get_json())
    other = fresh_user("t_rsv_cancel_m", role_code="merchant_admin", merchant_id=second_merchant)
    assert api.as_id(other.id).post(f"/api/resource/reservations/{rid}/cancel").get_json()["code"] == 40300
    assert _row(rid)["status"] == 1, "越权被拒不能顺手改别人的状态"
    assert api.as_("admin").post(f"/api/resource/reservations/{rid}/cancel").get_json()["code"] == 0
    assert api.as_("admin").post(f"/api/resource/reservations/{rid}/cancel").status_code == 409


def test_approved_slot_that_already_started_cannot_be_cancelled(api):
    sid = _mk_station(capacity=1)
    rid = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                         json=_payload(sid)).get_json())
    api.as_("admin").post(f"/api/resource/reservations/{rid}/audit", json={"result": 2})
    past = datetime.now() - timedelta(minutes=30)
    db.session.execute(text("UPDATE station_reservation SET start_at=:s WHERE id=:i"),
                       {"s": past, "i": rid})
    db.session.commit()
    resp = api.as_("merchant").post(f"/api/resource/reservations/{rid}/cancel").get_json()
    assert "时段已开始" in resp["message"] and _row(rid)["status"] == 2


def test_finish_only_after_the_window_ends(api):
    sid = _mk_station(capacity=1)
    rid = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                         json=_payload(sid)).get_json())
    assert api.as_("merchant").post(f"/api/resource/reservations/{rid}/finish").status_code == 409
    api.as_("admin").post(f"/api/resource/reservations/{rid}/audit", json={"result": 2})
    assert api.as_("merchant").post(f"/api/resource/reservations/{rid}/finish").status_code == 409

    end = datetime.now() - timedelta(minutes=5)
    db.session.execute(text("UPDATE station_reservation SET end_at=:e WHERE id=:i"),
                       {"e": end, "i": rid})
    db.session.commit()
    assert api.as_("merchant").post(f"/api/resource/reservations/{rid}/finish").get_json()["code"] == 0
    assert _row(rid)["status"] == 4


# ---------------------------------------------------------------- 入参校验与数据范围

def test_create_validates_the_window_and_the_station(api):
    sid = _mk_station(capacity=2)
    closed = _mk_station(status=2)
    st, _ = _slot()

    bad = dict(_payload(sid), purpose=7)
    assert api.as_("merchant").post("/api/resource/reservations", json=bad).status_code == 400

    assert api.as_("merchant").post("/api/resource/reservations", json=dict(
        _payload(sid), startAt=st.isoformat(), endAt=st.isoformat())).status_code == 400
    assert api.as_("merchant").post("/api/resource/reservations", json=_payload(
        sid, day_offset=-1)).status_code == 400
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, duration_min=600)).status_code == 400
    assert "不接受预约" in api.as_("merchant").post(
        "/api/resource/reservations", json=_payload(closed)).get_json()["message"]
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(999999)).get_json()["code"] == 40400


def test_merchant_id_comes_from_login_not_the_payload(api, second_merchant):
    """前端传别家 merchantId 也不能把预约挂到别人名下——身份只认登录态。"""
    sid = _mk_station(capacity=1)
    rid = _id_of(api.as_("merchant").post(
        "/api/resource/reservations", json=_payload(sid, merchantId=second_merchant)).get_json())
    assert _row(rid)["merchant_id"] == 1
    # 平台侧代商家申请是显式能力，不给 merchantId 就报错，不能悄悄挂到自己头上
    assert api.as_("admin").post("/api/resource/reservations",
                                 json=_payload(sid, merchantId=None)).status_code == 400


def test_aircraft_must_belong_to_the_applying_merchant(api, fresh_user, second_merchant):
    """机号会显示在自己的台账上，随手填别家机号等于抄对方的编号。"""
    other = fresh_user("t_rsv_ac_m", role_code="merchant_admin", merchant_id=second_merchant)
    sid = _mk_station(capacity=4)
    resp = api.as_id(other.id).post("/api/resource/reservations",
                                    json=_payload(sid, aircraftId=1)).get_json()
    assert resp["code"] == 40300
    assert api.as_("merchant").post("/api/resource/reservations",
                                   json=_payload(sid, aircraftId=1)).get_json()["code"] == 0
    assert api.as_("merchant").post("/api/resource/reservations",
                                    json=_payload(sid, aircraftId=999999)).get_json()["code"] == 40400


def test_list_is_scoped_by_merchant_and_filterable(api, second_merchant):
    sid = _mk_station(capacity=4)
    mine = _id_of(api.as_("merchant").post("/api/resource/reservations",
                                          json=_payload(sid)).get_json())
    # 别家那条由平台代约（merchantId=2）：列表收窄看的是 r.merchant_id，与谁点的提交无关，
    # 这样就不用为了造数据在 users 里留一个临时账号（它的预约会钉住外键删不掉）。
    theirs = _id_of(api.as_("admin").post(
        "/api/resource/reservations", json=_payload(sid, merchantId=second_merchant)).get_json())

    def ids(username, **query):
        body = api.as_(username).get("/api/resource/reservations", query_string=query).get_json()
        assert body["code"] == 0, body
        return {x["id"] for x in body["data"]["list"]}, body["data"]["list"]

    seen, _ = ids("merchant")
    assert mine in seen and theirs not in seen
    seen_all, rows = ids("admin")
    assert {mine, theirs} <= seen_all
    row = next(x for x in rows if x["id"] == mine)
    assert row["statusName"] == "待审" and row["purposeName"] == "起降"
    assert row["stationName"].startswith("pytest 场站预约测试站") and row["userName"] == "王调度"
    assert row["merchantName"] == "温州天枢示范运营有限公司"
    theirs_row = next(x for x in rows if x["id"] == theirs)
    assert theirs_row["merchantName"] == "测试·隔壁市运营公司"

    filtered, _ = ids("admin", stationId=sid, status=1)
    assert filtered == {mine, theirs}
    none, _ = ids("admin", date="2000-01-01")
    assert none == set()


def test_reservation_gate_blocks_the_unprivileged(api, fresh_user):
    """客户与财务都不碰场站机位；机长只读台账（读口靠 dispatch/monitor 权限放行）。"""
    sid = _mk_station(capacity=1)
    assert api.as_("customer").post("/api/resource/reservations",
                                    json=_payload(sid)).get_json()["code"] == 40300
    fin = fresh_user("t_rsv_fin", role_code="finance")
    assert api.as_id(fin.id).post("/api/resource/reservations",
                                  json=_payload(sid)).get_json()["code"] == 40300
    assert api.as_("pilot").get("/api/resource/reservations").get_json()["code"] == 40300


def test_anon_cannot_touch_reservations(anon):
    assert anon.get("/api/resource/reservations").status_code == 401
    assert anon.post("/api/resource/reservations", json={}).status_code == 401
