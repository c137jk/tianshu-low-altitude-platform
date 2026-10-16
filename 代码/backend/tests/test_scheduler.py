"""B6-2 定时任务：五个 job 函数直接调，调度层只验开关与状态口径。

这里钉住的是"没人调用也会自己跑"的那部分行为，所以用例按最容易写歪的方向来：
  · **幂等**——每个任务连跑两轮，第二轮必须 0 条新消息、0 行状态变化。
    调度线程会一遍遍重跑，人去补跑一次也算；不幂等的提醒就是轰炸。
  · **收件人范围**——超时单只催本商家的接单岗（跨商家是信息泄露），
    资质过期才追加平台资质审核岗（快到期是本人的事）。
  · **写库方向**——资质状态只往前走，审核驳回（4）永不因为"时间到了"被改；
    券下架只动母表 status，user_coupon 一份不碰（过期由读侧现判）。
  · **分区 DROP 默认关**——用一张同构探针表验证：关着时只报"该删哪些"、数据一行不少，
    打开开关才真的删整月，pmax 永远不在删除列表里。
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.common import scope
from app.common.errors import ApiError
from app.extensions import db
from app.services import coupon_service, scheduler

RES_SEQ = iter(range(1, 999))


def _sql(stmt, **p):
    return db.session.execute(text(stmt), p)


def _msgs(biz_type: str, biz_id: int) -> list:
    """这一事件的通知行（user_id, title）。用 .all() 不用 .scalars()：
    两列结果集上 scalars() 只回第一列，后面按 title 断言就成了 'int' object is not subscriptable。"""
    db.session.expire_all()
    return _sql("SELECT user_id, title FROM message_notification"
                " WHERE biz_type=:b AND biz_id=:i", b=biz_type, i=biz_id).all()


def _cfg(key: str, value: str):
    """改 sys_config 必须连着清 TTL 缓存，否则任务读到的还是十秒内的旧值（用例假通过）。"""
    _sql("UPDATE sys_config SET config_value=:v WHERE config_key=:k", v=value, k=key)
    db.session.commit()
    scope.invalidate_cfg(key)


# ------------------------------------------------------------------
# 造数
# ------------------------------------------------------------------
def _mk_reservation(user_id: int, minutes_ahead: int, status: int = 2,
                    station_id: int = 1, merchant_id: int = 1) -> int:
    start = datetime.now() + timedelta(minutes=minutes_ahead)
    return int(_sql(
        "INSERT INTO station_reservation (reservation_no, station_id, merchant_id, user_id,"
        " purpose, start_at, end_at, status, created_at, updated_at, deleted)"
        " VALUES (:no, :s, :m, :u, 1, :st, :en, :stat, NOW(), NOW(), 0)",
        no=f"SCH-{next(RES_SEQ):04d}-{user_id}", s=station_id, m=merchant_id, u=user_id,
        st=start, en=start + timedelta(minutes=60), stat=status).lastrowid)


def _mk_qual(subject_type: int, subject_id: int, days: int, status: int = 1) -> int:
    """days 为负表示已过期（qualification 不在 conftest 清场名单里，靠 _clean_quals 收）。"""
    return int(_sql(
        "INSERT INTO qualification (subject_type, subject_id, qual_type, qual_name, expire_at,"
        " status, created_at, updated_at, deleted)"
        " VALUES (:t, :sid, 'license', :n, :e, :st, NOW(), NOW(), 0)",
        t=subject_type, sid=subject_id, n=f"pytest资质·{subject_type}/{subject_id}/{days}d",
        e=datetime.now() + timedelta(days=days), st=status).lastrowid)


def _mk_coupon(name: str, end_at: datetime, status: int = 1, total: int = 50) -> int:
    return int(_sql(
        "INSERT INTO coupon (coupon_name, coupon_type, threshold, amount, total_count,"
        " got_count, start_at, end_at, status, created_at, updated_at, deleted)"
        " VALUES (:n, 1, 20, 10, :tc, 0, :s, :e, :st, NOW(), NOW(), 0)",
        n=name, tc=total, s=end_at - timedelta(days=60), e=end_at, st=status).lastrowid)


@pytest.fixture()
def clean_quals(app_ctx):
    """qualification 不在 conftest 清场名单里（它是业务台账，各用例造各的），
    本文件造的资质行必须自己带走——留在库里会出现在后面所有资质台账用例的列表里。

    只按本文件的命名前缀删，不整表清空：DDL 与迁移都没有资质种子，
    但别人造的行不归我们收拾。
    """
    yield
    _sql("DELETE FROM qualification WHERE qual_name LIKE :p", p="pytest资质·%")
    db.session.commit()


def _month(n: int) -> date:
    """相对本月第 n 个月的月初（0=本月，1=下月，-8=八个月前）。

    分区名与边界一律从它算：写死 p202609 这类日历，每月 1 号"本月"也会变成缺失项，
    用例就会按月红一次，而红的原因和产品没关系。
    """
    return scheduler._add_months(scheduler._first_of_month(date.today()), n)


def _pname(n: int) -> str:
    return scheduler._part_name(_month(n))


def _pbound(n: int) -> str:
    """分区上界（不含），格式 YYYY-MM-DD——MySQL 要的是字符串常量，不传绑定参数。"""
    return f"{_month(n):%Y-%m-%d}"


@pytest.fixture()
def probe_table(app_ctx):
    """与 flight_track 同构（按月 RANGE + pmax 兜底）的探针表：本月已建、下月缺、外加一个出保的老月。

    分区边界必须递增，所以"往回补一个已经过去的月"在真表上做不到——想验 DROP 只能另起一张。
    """
    _sql("DROP TABLE IF EXISTS flight_track_probe")      # 上一条用例跑到一半崩了也会留残表
    _sql("CREATE TABLE flight_track_probe ("
         " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
         " track_time DATETIME NOT NULL,"
         " PRIMARY KEY (id, track_time)) ENGINE=InnoDB"
         " PARTITION BY RANGE (TO_DAYS(track_time)) ("
         f" PARTITION {_pname(-8)} VALUES LESS THAN (TO_DAYS('{_pbound(-7)}')),"
         f" PARTITION {_pname(0)} VALUES LESS THAN (TO_DAYS('{_pbound(1)}')),"
         " PARTITION pmax VALUES LESS THAN MAXVALUE)")
    _sql(f"INSERT INTO flight_track_probe (track_time) VALUES ('{_pbound(-8)} 10:00:00'),"
         f" ('{_pbound(0)} 10:00:00')")
    db.session.commit()
    yield "flight_track_probe"
    _sql("DROP TABLE IF EXISTS flight_track_probe")
    db.session.commit()


def _partitions(table: str) -> set[str]:
    return set(_sql("SELECT PARTITION_NAME FROM information_schema.PARTITIONS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=:t"
                    " AND PARTITION_NAME IS NOT NULL", t=table).scalars().all())


# ------------------------------------------------------------------
# 调度层：测试进程里绝不该有后台线程
# ------------------------------------------------------------------
def test_start_is_noop_when_disabled(app):
    """conftest 设了 FLASK_SIM_DISABLED → start() 直接返回，不建线程也不建调度器。"""
    assert scheduler._scheduler is None, "测试进程里调度器被起来了，后台会往测试库乱写"
    scheduler.start(app)
    scheduler.start(app)                       # 连调两次也不能起第二份
    assert scheduler._scheduler is None


def test_snapshot_lists_jobs_even_when_stopped(app):
    snap = scheduler.snapshot()
    assert snap["running"] is False and snap["disabled"] is True
    ids = [j["id"] for j in snap["jobs"]]
    assert ids == ["order_take_timeout", "reserve_remind", "qual_scan",
                   "track_partition", "coupon_offline"], "任务表变了要同步接口文档与前端说明"
    assert all(j["nextRunAt"] is None for j in snap["jobs"]), "没起来就没有下次执行时间"
    assert all(j["name"] and j["trigger"] for j in snap["jobs"])


def test_scheduler_jobs_endpoint_requires_config_perm(api, anon, app):
    """新端点靠 @perm_required 自动进权限矩阵；这里只确认门禁方向没写反。"""
    assert anon.get("/api/aux/scheduler/jobs").status_code == 401
    assert api.as_("pilot").get("/api/aux/scheduler/jobs").status_code == 403
    body = api.as_("admin").get("/api/aux/scheduler/jobs").get_json()
    assert body["code"] == 0 and body["data"]["running"] is False
    assert len(body["data"]["jobs"]) == 5


# ------------------------------------------------------------------
# 任务 1：超时未接单提醒
# ------------------------------------------------------------------
def test_order_timeout_reminds_only_merchant_staff_once(make_flight, user_ids):
    order, _ = make_flight("paid")
    fresh, _ = make_flight("paid")
    _sql("UPDATE orders SET created_at = NOW() - INTERVAL 40 MINUTE WHERE id=:i", i=order.id)
    db.session.commit()

    assert scheduler.remind_order_take_timeout() >= 1
    got = _msgs("order_take_timeout", order.id)
    assert [g[0] for g in got] == [user_ids["merchant"]],     \
        "只催本商家有 order:list 的人：机长/客户/平台都不该收到"
    assert "超时未接单" in got[0][1]

    assert scheduler.remind_order_take_timeout() == 0, "第二轮重复提醒就是轰炸"
    assert len(_msgs("order_take_timeout", order.id)) == len(got)
    assert _msgs("order_take_timeout", fresh.id) == [], "没到 15 分钟阈值的单不该被催"


def test_order_timeout_retries_until_someone_can_be_notified(
        make_flight, second_merchant, fresh_user):
    """这一家没人能接单时不能"提醒过了"就当没事：事件不该被空收件人吃掉。

    催办是按"此刻有 order:list 的人"发的，商家后面补一个调度员，下一轮就该把这单催到他头上。
    """
    order, _ = make_flight("paid")
    _sql("UPDATE orders SET created_at = NOW() - INTERVAL 40 MINUTE, merchant_id = :m"
         " WHERE id=:i", i=order.id, m=second_merchant)
    db.session.commit()

    assert scheduler.remind_order_take_timeout() == 0
    assert _msgs("order_take_timeout", order.id) == []

    staffer = fresh_user("sch_dispatcher", "dispatcher", merchant_id=second_merchant)
    assert scheduler.remind_order_take_timeout() == 1
    assert [m[0] for m in _msgs("order_take_timeout", order.id)] == [staffer.id]
    assert scheduler.remind_order_take_timeout() == 0


# ------------------------------------------------------------------
# 任务 2：场站预约开始提醒
# ------------------------------------------------------------------
def test_reserve_remind_only_approved_and_upcoming(user_ids):
    soon = _mk_reservation(user_ids["merchant"], minutes_ahead=10)
    late = _mk_reservation(user_ids["merchant"], minutes_ahead=240)
    pending = _mk_reservation(user_ids["merchant"], minutes_ahead=10, status=1)
    started = _mk_reservation(user_ids["merchant"], minutes_ahead=-5)

    assert scheduler.remind_station_reservations() == 1
    assert [m[0] for m in _msgs("reserve_remind", soon)] == [user_ids["merchant"]]
    assert "场站预约即将开始" in _msgs("reserve_remind", soon)[0][1]
    for rid in (late, pending, started):
        assert _msgs("reserve_remind", rid) == [], f"预约 {rid} 不该被提醒"
    assert scheduler.remind_station_reservations() == 0


# ------------------------------------------------------------------
# 任务 3：资质到期扫描
# ------------------------------------------------------------------
def _status(qid: int) -> int:
    db.session.expire_all()
    return int(_sql("SELECT status FROM qualification WHERE id=:i", i=qid).scalar())


def test_qualification_scan_advances_status_and_notifies(clean_quals, user_ids, api):
    pilot = user_ids["pilot"]
    expiring = _mk_qual(2, pilot, days=5)
    expired = _mk_qual(2, pilot, days=-1)
    safe = _mk_qual(2, pilot, days=200)
    rejected = _mk_qual(2, pilot, days=-1, status=4)

    out = scheduler.scan_qualifications()
    assert (_status(expiring), _status(expired), _status(safe), _status(rejected)) \
        == (2, 3, 1, 4), "驳回是审核结论，不许被时间推进覆盖；200 天外的还是有效"
    assert out["expiring"] == 1 and out["expired"] == 1

    assert [m[0] for m in _msgs("qual_expiring", expiring)] == [pilot]
    expiring_users = {m[0] for m in _msgs("qual_expired", expired)}
    assert expiring_users == {pilot, user_ids["admin"]}, \
        "已过期要同时让平台和本人知道——资质过期的人还能被排进飞行计划，那是道该关上的闸门"

    again = scheduler.scan_qualifications()
    assert again == {"expiring": 0, "expired": 0, "notified": 0}, "第二轮什么都不该发生"

    rows = api.as_("admin").get("/api/resource/qualifications").get_json()["data"]["list"]
    label = {r["id"]: r["statusName"] for r in rows}
    assert label[expired] == "已过期" and label[expiring] == "即将到期", \
        "台账页读的就是这个 status，扫描器是它唯一的写入方"


def test_qualification_warn_window_is_per_subject(clean_quals, user_ids):
    """三档预警窗口各归各的：商家 7 天、人员 15 天。第 10 天的商家证照不该被标成即将到期。"""
    far_for_merchant = _mk_qual(1, 1, days=10)      # merchant_qual_warn_days = 7
    near_for_staff = _mk_qual(2, user_ids["pilot"], days=10)   # staff_qual_warn_days = 15

    scheduler.scan_qualifications()
    assert _status(far_for_merchant) == 1, "超出本主体那一档窗口就还是有效，不能借别人的窗口判定"
    assert _status(near_for_staff) == 2
    assert [m[0] for m in _msgs("qual_expiring", near_for_staff)] == [user_ids["pilot"]]


def test_qualification_aircraft_notifies_merchant_qual_staff(clean_quals, user_ids):
    """飞行器没有"本人"：预警发给归属商家里管资质的人（这里是商家管理员）。"""
    qid = _mk_qual(3, 1, days=3)
    scheduler.scan_qualifications()
    assert _status(qid) == 2
    assert [m[0] for m in _msgs("qual_expiring", qid)] == [user_ids["merchant"]]


# ------------------------------------------------------------------
# 任务 4：轨迹月分区
# ------------------------------------------------------------------
def test_track_partition_idempotent_on_real_table():
    """种子 DDL 已把分区建到 2027-02，本月/下月都在 → 本任务对真表就该是空操作。"""
    have_before = _partitions(scheduler.TRACK_TABLE)
    assert {_pname(0), _pname(1)} <= have_before, (
        f"种子 DDL 没预建到 {_pname(0)}/{_pname(1)}：把 sql 里的分区往前铺，"
        "别让调度器在生产库上做 DDL")
    out = scheduler.maintain_track_partitions()
    assert out["created"] == [] and out["dropped"] == []
    assert out["dropEnabled"] is False, "DROP 开关默认必须是关的"
    assert _partitions(scheduler.TRACK_TABLE) == have_before


def test_track_partition_prebuild_then_drop(probe_table):
    cfg_key = "track_partition_drop_enabled"
    nxt, stale = _pname(1), _pname(-8)
    try:
        out = scheduler.maintain_track_partitions(table=probe_table)
        assert out["created"] == [nxt], "下个月分区缺就得补上，否则下月的帧全落 pmax"
        assert out["dueDrop"] == [stale] and out["dropped"] == []
        assert nxt in _partitions(probe_table)
        assert "pmax" not in out["dueDrop"], "兜底分区永远不在删除列表里"
        assert int(_sql(f"SELECT COUNT(*) FROM {probe_table}").scalar()) == 2, "开关关着，一行不许少"

        _cfg(cfg_key, "1")
        again = scheduler.maintain_track_partitions(table=probe_table)
        assert again["created"] == [] and again["dropped"] == [stale]
        parts = _partitions(probe_table)
        assert stale not in parts and "pmax" in parts and nxt in parts
        rest = [r[0] for r in _sql(f"SELECT track_time FROM {probe_table}").all()]
        assert len(rest) == 1 and f"{rest[0]:%Y-%m}" == f"{_month(0):%Y-%m}", "只该掉出保那个月的整分区"
    finally:
        _cfg(cfg_key, "0")


def test_track_partition_rejects_foreign_table():
    with pytest.raises(ValueError):
        scheduler.maintain_track_partitions(table="flight_track; DROP TABLE users")


# ------------------------------------------------------------------
# 任务 5：到期优惠券下架
# ------------------------------------------------------------------
def test_coupon_offline_only_after_grace_and_never_touches_user_coupons(user_ids):
    customer = user_ids["customer"]
    gone = _mk_coupon("pytest·过期 40 天", datetime.now() - timedelta(days=40))
    grace = _mk_coupon("pytest·过期 10 天", datetime.now() - timedelta(days=10))
    held = _mk_coupon("pytest·被人领着过期", datetime.now() + timedelta(days=1))
    coupon_service.claim(customer, held)
    _sql("UPDATE coupon SET end_at = NOW() - INTERVAL 40 DAY WHERE id=:i", i=held)
    db.session.commit()

    out = scheduler.archive_expired_coupons()
    assert out["offlined"] == 2, "过宽限期的两张都下架，还在 30 天窗内的 grace 那张不动"
    db.session.expire_all()
    assert _sql("SELECT status FROM coupon WHERE id=:i", i=grace).scalar() == 1
    assert {m[0] for m in _msgs("coupon_offline", gone)} == {user_ids["admin"]}

    # 已领的那张：母表下架了，user_coupon 一份都不动；"已过期"是读侧按 end_at 现判出来的
    assert int(_sql("SELECT status FROM user_coupon WHERE coupon_id=:i", i=held).scalar()) == 1
    packed = {c["couponId"]: c for c in coupon_service.mine(customer)}
    assert packed[held]["expired"] is True and packed[held]["status"] == 4

    with pytest.raises(ApiError) as exc:
        coupon_service.claimable(user_ids["merchant"], gone)
    assert "已停用" in exc.value.message

    assert scheduler.archive_expired_coupons()["offlined"] == 0
    assert len(_msgs("coupon_offline", gone)) == 1
