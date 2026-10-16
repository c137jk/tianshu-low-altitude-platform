"""空域台账生命周期（M-41/42，需求 4.2.4-2/3）：绘制 → 审核 → 改边界 → 下架。

这一张台账是"合规判定的口径"本身，所以用例钉的是**状态之间的门**，不是接口能不能通：
  · 画完不等于生效。自绘空域落库 audit_status=0，既不进地图默认图层，也不进
    `dispatch_service.active_airspaces`——否则半截草稿会把在飞的机器判成闯区。
  · 改边界 = 改口径。几何/类型/管制时段任一变动就退回待审核（自绘来源），
    空管同步来源例外：那是属地下发的台账，平台只做起停与限高维护。
  · 驳回必须留原因，且驳回即失效。
  · 下架要 airspace:audit（和审核同级），空管同步来源不给删，
    范围内还有在飞任务也不给删——空中撤区等于把它的判定依据抽掉。
"""
from datetime import datetime, timedelta

from sqlalchemy import text

from app.extensions import db
from app.services import dispatch_service

# 瓯江口可飞示范区外侧的一块空地，画出来的多边形不会压到任何种子空域
DRAFT_BOX = [[121.20, 28.20], [121.24, 28.20], [121.24, 28.24], [121.20, 28.24], [121.20, 28.20]]


def _geo(ring=None):
    return {"type": "Polygon", "coordinates": [ring or DRAFT_BOX]}


def _create(api, **kw) -> int:
    body = {"airspaceName": kw.pop("airspaceName", "pytest 自绘管制区"),
            "airspaceType": kw.pop("airspaceType", 2),
            "altitudeMin": kw.pop("altitudeMin", 0),
            "altitudeMax": kw.pop("altitudeMax", 90),
            "regionName": kw.pop("regionName", "pytest 区"),
            "geometry": kw.pop("geometry", None) or _geo()}
    body.update(kw)   # 临时管制要传 controlStartAt / controlEndAt
    r = api.as_("admin").post("/api/airspaces", json=body).get_json()
    assert r["code"] == 0, r
    return int(r["data"]["id"])


def _row(aid) -> dict:
    db.session.expire_all()
    return db.session.execute(text(
        "SELECT airspace_no, airspace_name, source, status, audit_status, audited_by,"
        " audit_opinion, deleted, geometry_json, airspace_type, altitude_max"
        " FROM airspace WHERE id=:i"), {"i": aid}).mappings().first()


def _geo_of(api, aid) -> dict:
    """按接口出参取几何——这正是前端编辑表单原样回填、再次提交的那份数据。"""
    body = api.as_("admin").get("/api/airspaces?all=1").get_json()
    return next(x["geometry"] for x in body["data"]["list"] if x["id"] == aid)


def _in_default_list(api, aid) -> bool:
    """大屏/调度台用的那张列表（不带 all=1）——判定口径的真实投影。"""
    body = api.as_("admin").get("/api/airspaces").get_json()
    return any(x["id"] == aid for x in body["data"]["list"])


def _fly_route(plan, ring_points: list[tuple[float, float]]):
    """把一条真链路（make_flight 走状态机造出来的在飞计划）的航线挪进新空域里。

    不走 make_flight 直接 INSERT 的话，flight_plan.order_id 有外键，
    而且"空中有机器"这件事本来就是状态机给的，自己拼一行等于在测假现场。
    """
    plan.route_json = [{"lng": lng, "lat": lat} for lng, lat in ring_points]
    db.session.commit()
    return plan.plan_no


# ---------------------------------------------------------------- 绘制 → 待审核

def test_drawn_airspace_pends_audit_and_stays_out_of_judgement(api):
    aid = _create(api)
    r = _row(aid)
    assert r["audit_status"] == 0 and r["source"] == 2
    assert r["airspace_no"].startswith("AS-SYS-")
    # 台账全量里有（空管要能看见自己画的草稿），地图默认图层与合规判定里都没有
    body = api.as_("admin").get("/api/airspaces?all=1").get_json()
    assert any(x["id"] == aid for x in body["data"]["list"])
    assert not _in_default_list(api, aid)
    assert aid not in {a.id for a in dispatch_service.active_airspaces()}


def test_draw_requires_name_and_valid_polygon(api):
    bad = api.as_("admin").post("/api/airspaces", json={
        "airspaceName": "", "airspaceType": 2, "geometry": _geo()}).get_json()
    assert bad["code"] == 40000 and "airspaceName" in bad["message"]

    two_pts = api.as_("admin").post("/api/airspaces", json={
        "airspaceName": "两点不算区", "airspaceType": 2,
        "geometry": {"type": "Polygon", "coordinates": [[[121.2, 28.2], [121.24, 28.2]]]}}).get_json()
    assert two_pts["code"] == 40000 and "3 个顶点" in two_pts["message"]

    not_poly = api.as_("admin").post("/api/airspaces", json={
        "airspaceName": "圆的？", "airspaceType": 2,
        "geometry": {"type": "Circle", "coordinates": []}}).get_json()
    assert not_poly["code"] == 40000 and "Polygon" in not_poly["message"]


def test_temporary_control_requires_time_window(api):
    body = {"airspaceName": "pytest 临时管制无时段", "airspaceType": 4, "geometry": _geo()}
    r = api.as_("admin").post("/api/airspaces", json=body).get_json()
    assert r["code"] == 40000 and "管制起止时间" in r["message"]

    body.update({"controlStartAt": (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
                 "controlEndAt": (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")})
    created = api.as_("admin").post("/api/airspaces", json=body).get_json()
    aid = int(created["data"]["id"])
    # 窗口内但还没审核通过，判定口径依然不含它
    assert aid not in {a.id for a in dispatch_service.active_airspaces()}
    db.session.execute(text("UPDATE airspace SET audit_status=1 WHERE id=:i"), {"i": aid})
    db.session.commit()
    assert aid in {a.id for a in dispatch_service.active_airspaces()}

    # 时段已过 → active_airspaces 要把它摘掉（临时管制只在窗口内算数）
    db.session.execute(text("UPDATE airspace SET control_end_at=:e WHERE id=:i"),
                       {"e": datetime.now() - timedelta(hours=1), "i": aid})
    db.session.commit()
    assert aid not in {a.id for a in dispatch_service.active_airspaces()}


def test_audit_pass_puts_it_on_the_map(api):
    aid = _create(api)
    r = api.as_("admin").post(f"/api/airspaces/{aid}/audit",
                              json={"result": 1, "opinion": "同意生效"}).get_json()
    assert r["code"] == 0
    row = _row(aid)
    assert (row["audit_status"], row["status"], row["audited_by"]) == (1, 1, 1)
    assert _in_default_list(api, aid)
    assert aid in {a.id for a in dispatch_service.active_airspaces()}


def test_reject_needs_a_reason_and_disables_the_zone(api):
    aid = _create(api)
    empty = api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 2}).get_json()
    assert empty["code"] == 40000 and "驳回必须写明原因" in empty["message"]

    ok = api.as_("admin").post(f"/api/airspaces/{aid}/audit",
                               json={"result": 2, "opinion": "压到了在用航线"}).get_json()
    assert ok["code"] == 0
    row = _row(aid)
    assert (row["audit_status"], row["status"], row["audit_opinion"]) == (2, 2, "压到了在用航线")
    assert not _in_default_list(api, aid)


def test_audit_result_only_accepts_1_or_2(api):
    aid = _create(api)
    r = api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 3}).get_json()
    assert r["code"] == 40000


def test_audit_needs_audit_perm_not_just_draw(api):
    """商家既没有 draw 也没有 audit，两层都要挡；这条专门钉"审核权 ≠ 绘制权"。"""
    aid = _create(api)
    r = api.as_("merchant").post(f"/api/airspaces/{aid}/audit", json={"result": 1}).get_json()
    assert r["code"] == 40300


# ---------------------------------------------------------------- 改边界就退审

def test_editing_geometry_of_self_drawn_airspace_requeues_it(api):
    aid = _create(api)
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    r = api.as_("admin").put(f"/api/airspaces/{aid}", json={
        "geometry": {"type": "Polygon",
                     "coordinates": [[[121.3, 28.3], [121.34, 28.3], [121.34, 28.34],
                                      [121.3, 28.34], [121.3, 28.3]]]}}).get_json()
    assert r["code"] == 0 and r["data"]["auditStatus"] == 0
    assert not _in_default_list(api, aid)


def test_editing_type_also_requeues(api):
    aid = _create(api)
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    r = api.as_("admin").put(f"/api/airspaces/{aid}", json={"airspaceType": 1}).get_json()
    assert r["data"]["auditStatus"] == 0


def test_renaming_only_does_not_requeue(api):
    """名称/区域/说明不影响判定范围，改这些不该把已生效的空域打回待审核。"""
    aid = _create(api)
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    r = api.as_("admin").put(f"/api/airspaces/{aid}",
                             json={"airspaceName": "pytest 改了名字", "regionName": "乐清"}).get_json()
    assert r["data"]["auditStatus"] == 1
    assert _row(aid)["airspace_name"] == "pytest 改了名字"
    assert _in_default_list(api, aid)


def test_resubmitting_the_same_geometry_does_not_requeue(api):
    """编辑表单每次都会把 geometry 原样带回去提交——值没变就不算"改了边界"。

    否则空管只是想补个说明，这块区就当场从合规判定里消失了（比误删还难查）。
    """
    aid = _create(api)
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    r = api.as_("admin").put(f"/api/airspaces/{aid}", json={
        "geometry": _geo_of(api, aid), "controlReason": "补一句说明"}).get_json()
    assert r["code"] == 0 and r["data"]["auditStatus"] == 1
    assert _in_default_list(api, aid)

    # 顶点动了一下就是一次改口径，必须重新审核
    moved = _geo_of(api, aid)
    moved["coordinates"][0][0][0] += 0.01
    r2 = api.as_("admin").put(f"/api/airspaces/{aid}", json={"geometry": moved}).get_json()
    assert r2["data"]["auditStatus"] == 0
    assert not _in_default_list(api, aid)


def test_synced_source_only_allows_altitude_and_toggle(api):
    """空管同步来源是属地下发的台账，平台只能"改启停与限高"：动边界一律挡回去，与同步来源不能删同级的闸门。

    种子空域 source 全是 2，这里临时翻成 1 再造这个前提。
    老行为只是"改了也不退回审核"，等于让有绘制权的人原地改写机场禁飞区、改完还继续生效，
    一条审核痕迹都不留。
    """
    aid = _create(api)
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    db.session.execute(text("UPDATE airspace SET source=1 WHERE id=:i"), {"i": aid})
    db.session.commit()

    same = _geo_of(api, aid)
    r = api.as_("admin").put(f"/api/airspaces/{aid}",
                             json={"geometry": same, "altitudeMax": 45}).get_json()
    assert r["code"] == 0, "几何原样回填 + 只改限高，是允许的维护动作"
    row = _row(aid)
    assert (row["altitude_max"], row["audit_status"]) == (45, 1)

    moved = _geo_of(api, aid)
    moved["coordinates"][0][0][0] += 0.01
    blocked = api.as_("admin").put(f"/api/airspaces/{aid}", json={"geometry": moved}).get_json()
    assert blocked["code"] == 40900 and "只能改启停与限高" in blocked["message"]
    assert _geo_of(api, aid) == same, "挡回去就必须一个顶点都没落库"
    assert api.as_("admin").put(f"/api/airspaces/{aid}",
                                json={"airspaceType": 1}).get_json()["code"] == 40900
    assert api.as_("admin").put(f"/api/airspaces/{aid}", json={"status": 2}).get_json()["code"] == 0
    assert _row(aid)["status"] == 2


def test_altitude_range_is_validated(api):
    aid = _create(api)
    r = api.as_("admin").put(f"/api/airspaces/{aid}", json={"altitudeMax": 5000}).get_json()
    assert r["code"] == 40000 and "3000" in r["message"]
    ok = api.as_("admin").put(f"/api/airspaces/{aid}", json={"altitudeMax": 0}).get_json()
    assert ok["code"] == 0


def test_update_unknown_airspace_is_404(api):
    r = api.as_("admin").put("/api/airspaces/999999", json={"airspaceName": "x"}).get_json()
    assert r["code"] == 40400


# ---------------------------------------------------------------- 下架

def test_delete_needs_audit_perm(api):
    aid = _create(api)
    r = api.as_("merchant").delete(f"/api/airspaces/{aid}").get_json()
    assert r["code"] == 40300
    assert _row(aid)["deleted"] == 0


def test_atc_synced_airspace_cannot_be_deleted(api):
    aid = _create(api)
    db.session.execute(text("UPDATE airspace SET source=1 WHERE id=:i"), {"i": aid})
    db.session.commit()
    r = api.as_("admin").delete(f"/api/airspaces/{aid}").get_json()
    assert r["code"] == 40900 and "停用" in r["message"]
    assert _row(aid)["deleted"] == 0


def test_delete_blocked_while_a_flying_plan_is_inside(api, make_flight):
    _, plan = make_flight("flying")
    aid = _create(api)
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    plan_no = _fly_route(plan, [(121.21, 28.21), (121.22, 28.22), (121.23, 28.23)])

    blocked = api.as_("admin").delete(f"/api/airspaces/{aid}").get_json()
    assert blocked["code"] == 40900
    assert "在飞任务" in blocked["message"] and plan_no in blocked["message"]
    assert _row(aid)["deleted"] == 0

    # 航线飞出场外（或同一架次落地）就可以下架——闸门看的是"范围内还有没有在飞的"
    _fly_route(plan, [(120.01, 27.01), (120.02, 27.02)])
    assert api.as_("admin").delete(f"/api/airspaces/{aid}").get_json()["code"] == 0


def test_delete_removes_it_from_every_view(api):
    aid = _create(api)
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    assert api.as_("admin").delete(f"/api/airspaces/{aid}").get_json()["code"] == 0

    row = _row(aid)
    assert (row["deleted"], row["status"]) == (1, 2)
    assert aid not in {a.id for a in dispatch_service.active_airspaces()}
    for url in ("/api/airspaces", "/api/airspaces?all=1"):
        body = api.as_("admin").get(url).get_json()
        assert not any(x["id"] == aid for x in body["data"]["list"]), url
    # 下架是个"改口径"动作，要回送给有绘制权的人
    assert db.session.execute(text(
        "SELECT COUNT(*) FROM message_notification WHERE biz_type='airspace_change' AND biz_id=:i"),
        {"i": aid}).scalar() >= 1


def test_double_delete_is_404(api):
    aid = _create(api)
    assert api.as_("admin").delete(f"/api/airspaces/{aid}").get_json()["code"] == 0
    assert api.as_("admin").delete(f"/api/airspaces/{aid}").get_json()["code"] == 40400


# ---------------------------------------------------------------- 同步与查询

def test_sync_expires_finished_temporary_control(api):
    now = datetime.now()
    aid = _create(api, airspaceType=4, airspaceName="pytest 过期临时管制",
                  controlStartAt=(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
                  controlEndAt=(now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"))
    api.as_("admin").post(f"/api/airspaces/{aid}/audit", json={"result": 1})
    ended = now - timedelta(minutes=5)
    db.session.execute(text("UPDATE airspace SET control_start_at=:s, control_end_at=:e WHERE id=:i"),
                       {"s": ended - timedelta(hours=2), "e": ended, "i": aid})
    db.session.commit()

    r = api.as_("admin").post("/api/airspaces/sync").get_json()
    assert r["code"] == 0
    assert "pytest 过期临时管制" in r["data"]["expired"]
    assert _row(aid)["status"] == 2
    assert aid not in {a.id for a in dispatch_service.active_airspaces()}
    assert any(s["source"] == "平台自绘" for s in r["data"]["stat"])


def test_query_filters_on_the_ledger(api):
    aid = _create(api, airspaceName="pytest 瓯江北岸限飞", airspaceType=2)
    hits = api.as_("admin").get("/api/airspaces?all=1&keyword=瓯江北岸限飞&type=2").get_json()
    assert any(x["id"] == aid for x in hits["data"]["list"])
    miss = api.as_("admin").get("/api/airspaces?all=1&keyword=瓯江北岸限飞&type=1").get_json()
    assert not any(x["id"] == aid for x in miss["data"]["list"])
    # 审核态在前端是表格筛选项，后端把它带进每一条，别靠前端自己猜
    assert next(x for x in hits["data"]["list"] if x["id"] == aid)["auditName"] == "待审核"


def test_ledger_view_all_needs_draw_or_audit(api):
    """`all=1` 是台账视角（未过审空域的几何 + 审核意见都在里面），服务端必须跟着收窄。

    前端「一张图 / 台账与审核」两张页的路由守卫要求 airspace:draw 或 airspace:audit，
    但守卫只是菜单条件——参数手拼一下就绕过去了。没有这两个点的人（客户、机长）
    拿到的必须与不带 all 的公开列表一字不差。
    """
    aid = _create(api, airspaceName="pytest 未过审空域", airspaceType=2)
    assert aid in {x["id"] for x in api.as_("admin").get(
        "/api/airspaces?all=1").get_json()["data"]["list"]}, "有审核权的人就该看到全量"

    for acct in ("customer", "pilot", "merchant"):
        got = api.as_(acct).get("/api/airspaces?all=1").get_json()["data"]["list"]
        assert aid not in {x["id"] for x in got}, f"{acct} 不该看到未过审空域"
        assert got == api.as_(acct).get("/api/airspaces").get_json()["data"]["list"], \
            f"{acct} 的 all=1 必须与公开列表等价"
