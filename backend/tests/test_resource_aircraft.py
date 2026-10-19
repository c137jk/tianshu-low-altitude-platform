"""飞行器详情（M-52）与维保↔状态联动。

详情接口是台账/调度台/监控台共用的读入口，所以三件事必须钉住：
  · 数据范围——商家账号不能借 detail 窥视别家的机（列表已经收窄，详情页更要收窄）；
  · 履历口径——未落地的计划给的是申报时长，不能和实际起降差值混在同一个数字里；
  · 联动语义——登记哪一类维保会把机锁出派单池、完结到哪一条才放开，
    这两条决定调度台会不会把一台还在修的机器推荐出去。

maintenance_record 不在 conftest 的清场名单里（它是台账不是流水），
所以本模块自己负责"造了几条就删几条"，并把 aircraft 的维保时间还回去。
"""
import pytest
from sqlalchemy import text

from app.extensions import db


@pytest.fixture()
def ac_env(app_ctx):
    """种子飞行器复位 + 维保表按用例增删的账本。返回本用例可操作的机号 id=1。"""
    before = db.session.execute(text("SELECT COALESCE(MAX(id),0) FROM maintenance_record")).scalar()
    snap = db.session.execute(text(
        "SELECT status, battery, last_maintain_at, next_maintain_at FROM aircraft WHERE id=1"
    )).mappings().first()
    yield 1
    db.session.execute(text("DELETE FROM maintenance_record WHERE id > :b"), {"b": before})
    db.session.execute(text(
        "UPDATE aircraft SET status=:s, battery=:b, last_maintain_at=:l, next_maintain_at=:n "
        "WHERE id=1"),
        {"s": snap["status"], "b": snap["battery"],
         "l": snap["last_maintain_at"], "n": snap["next_maintain_at"]})
    db.session.commit()


def _add_maintenance(aid: int, record_type: int, fault: str = "动力系统") -> int:
    db.session.execute(text(
        "INSERT INTO maintenance_record (aircraft_id, record_type, fault_type, description,"
        " started_at, status, deleted) VALUES (:a,:rt,:f,'测试用例登记',NOW(),1,0)"),
        {"a": aid, "rt": record_type, "f": fault})
    db.session.commit()
    return int(db.session.execute(text("SELECT MAX(id) FROM maintenance_record")).scalar())


def _status(aid: int) -> int:
    db.session.expire_all()
    return int(db.session.execute(
        text("SELECT status FROM aircraft WHERE id=:a"), {"a": aid}).scalar())


# ---------------------------------------------------------------- 详情接口

def test_detail_returns_ledger_history_and_stats(api, ac_env, make_flight):
    """详情页要一次给全：台账、维保、飞行履历、汇总数——缺哪块前端就得再开一个接口。"""
    aid = ac_env
    _add_maintenance(aid, 2, "电机异响")
    order, plan = make_flight("landed")
    db.session.execute(text("UPDATE flight_plan SET aircraft_id=:a WHERE id=:p"),
                       {"a": aid, "p": plan.id})
    db.session.execute(text(
        "INSERT INTO violation_record (violation_no, merchant_id, plan_id, aircraft_id,"
        " violation_type, violation_time, status, deleted)"
        " VALUES ('V-T-1',1,:p,:a,2,NOW(),1,0)"),
        {"p": plan.id, "a": aid})
    db.session.commit()

    body = api.as_("admin").get(f"/api/resource/aircraft/{aid}").get_json()["data"]
    assert body["aircraft"]["id"] == aid
    assert body["aircraft"]["statusName"] in ("闲置", "执行中", "充换电", "维保中", "故障离线")
    assert body["aircraft"]["merchantName"]
    assert body["maintenance"][0]["typeName"] == "故障维修"
    assert body["maintenance"][0]["statusName"] == "待处理"

    fl = next(x for x in body["flights"] if x["id"] == plan.id)
    assert fl["violations"] == 1
    assert fl["statusName"] == "已完成"
    assert fl["distanceKm"] >= 0
    assert body["stats"]["openViolations"] == 1
    assert body["stats"]["maintenanceOpen"] == 1
    # SUM() 在 MySQL 里是 DECIMAL，忘了转 int 出参就成了字符串 "4"
    assert all(isinstance(v, int) for v in body["stats"].values()), body["stats"]


def test_detail_marks_planned_duration_as_not_actual(api, ac_env, make_flight):
    """未落地的计划只能给申报时长；前端靠 actual 决定要不要标"（申报）"。"""
    aid = ac_env
    _, plan = make_flight("approved")
    plan.aircraft_id = aid
    plan.actual_takeoff_at = None
    plan.actual_landing_at = None
    plan.est_duration_sec = 300
    db.session.commit()

    body = api.as_("admin").get(f"/api/resource/aircraft/{aid}").get_json()["data"]
    row = next(x for x in body["flights"] if x["id"] == plan.id)
    assert row["actual"] is False and row["durationMin"] == 5.0
    assert row["takeoffAt"] is None or row["landingAt"] is None
    assert body["flying"] is None                 # 没起飞就不该出现在"在飞任务"里


def test_detail_reports_the_in_flying_plan(api, ac_env, make_flight):
    aid = ac_env
    _, plan = make_flight("flying")
    plan.aircraft_id = aid
    db.session.commit()
    body = api.as_("admin").get(f"/api/resource/aircraft/{aid}").get_json()["data"]
    assert body["flying"]["planNo"] == plan.plan_no
    assert body["flying"]["lastReportAt"]


def test_detail_404_for_unknown_aircraft(api):
    assert api.as_("admin").get("/api/resource/aircraft/999999").get_json()["code"] == 40400


def test_detail_respects_merchant_scope(api, ac_env, fresh_user, second_merchant):
    """别家商家不能借 detail 看别人的机（列表已收窄，详情页漏了就是后门）。"""
    aid = ac_env
    other = fresh_user("t_ac_other_m", role_code="merchant_maintainer", merchant_id=second_merchant)
    r = api.as_id(other.id).get(f"/api/resource/aircraft/{aid}").get_json()
    assert r["code"] == 40300 and "本商家" in r["message"]
    # 同商家的运维走同一个权限点必须放行，否则就是误伤
    same = fresh_user("t_ac_same_m", role_code="merchant_maintainer", merchant_id=1)
    assert api.as_id(same.id).get(f"/api/resource/aircraft/{aid}").get_json()["code"] == 0


def test_detail_gate_matches_the_list_gate(api, ac_env, fresh_user):
    """详情页的放行集合必须和台账列表一致：能看见机号却打不开详情，等于把入口做成死链。

    pilot / air_traffic 没有 resource:aircraft，但分别拿着 monitor:realtime，
    所以是放行的一侧；财务与客户两个都不沾，必须 40300。
    """
    aid = ac_env
    assert api.as_("admin").get(f"/api/resource/aircraft/{aid}").get_json()["code"] == 0
    assert api.as_("pilot").get(f"/api/resource/aircraft/{aid}").get_json()["code"] == 0
    assert api.as_("customer").get(f"/api/resource/aircraft/{aid}").get_json()["code"] == 40300
    fin = fresh_user("t_ac_finance", role_code="finance")
    assert api.as_id(fin.id).get(f"/api/resource/aircraft/{aid}").get_json()["code"] == 40300


def test_detail_requires_login(anon):
    assert anon.get("/api/resource/aircraft/1").status_code == 401


# ---------------------------------------------------------------- 维保↔状态

def test_repair_grounds_aircraft_but_inspection_does_not(api, ac_env):
    """三种类型里只有 1/2 锁机：巡检是停场也能顺带做的检查，锁机等于逼运维多点一次完结。"""
    aid = ac_env
    assert _status(aid) == 1
    mid = api.as_("admin").json("post", "/api/aux/maintenance", json={
        "aircraftId": aid, "recordType": 3, "faultType": "桨叶目视"})
    assert mid["code"] == 0
    assert _status(aid) == 1, "巡检登记后不该把飞行器锁出派单池"

    api.as_("admin").post("/api/aux/maintenance", json={
        "aircraftId": aid, "recordType": 2, "faultType": "电机异响"})
    assert _status(aid) == 4


def test_repair_does_not_override_executing_status(api, ac_env):
    """已经在飞的机不能因为登记了一条维保就被改成"维保中"，状态机口径优先。"""
    aid = ac_env
    db.session.execute(text("UPDATE aircraft SET status=2 WHERE id=:a"), {"a": aid})
    db.session.commit()
    api.as_("admin").post("/api/aux/maintenance", json={
        "aircraftId": aid, "recordType": 2, "faultType": "电机异响"})
    assert _status(aid) == 2


def test_aircraft_stays_grounded_until_last_open_record(api, ac_env):
    """两条报修只完结一条，机器必须还在维保中——否则等于绕过接地把没修完的机派出去。"""
    aid = ac_env
    api.as_("admin").post("/api/aux/maintenance", json={
        "aircraftId": aid, "recordType": 2, "faultType": "电机异响"})
    api.as_("admin").post("/api/aux/maintenance", json={
        "aircraftId": aid, "recordType": 1, "faultType": "定检"})
    assert _status(aid) == 4

    rows = db.session.execute(text(
        "SELECT id FROM maintenance_record WHERE aircraft_id=:a AND status=1 ORDER BY id"
        ), {"a": aid}).all()
    api.as_("admin").put(f"/api/aux/maintenance/{rows[0][0]}", json={"status": 3})
    assert _status(aid) == 4, "还有一条在办维保，不能放回闲置"

    api.as_("admin").put(f"/api/aux/maintenance/{rows[1][0]}", json={"status": 3})
    assert _status(aid) == 1, "最后一条完结后必须自动恢复可派"
    db.session.expire_all()
    nxt = db.session.execute(text(
        "SELECT next_maintain_at FROM aircraft WHERE id=:a"), {"a": aid}).scalar()
    assert nxt is not None and nxt.year >= 2026      # 完结顺带把下次维保时间推后


def test_maintenance_write_is_scoped_to_own_merchant(api, ac_env, fresh_user, second_merchant):
    aid = ac_env
    other = fresh_user("t_mt_other_m", role_code="merchant_maintainer", merchant_id=second_merchant)
    r = api.as_id(other.id).post("/api/aux/maintenance", json={
        "aircraftId": aid, "recordType": 2, "faultType": "电机异响"}).get_json()
    assert r["code"] == 40300
    assert _status(aid) == 1                          # 被拒就不能顺手改台账
