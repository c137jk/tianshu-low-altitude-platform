"""资质台账「看得到」与「改得动」是两件事，本人的资质与值班得有一条自助口。

这一组用例来自一次真实反馈：运维登录后点「人员管理」，页面弹
`没有操作权限：resource:qualification 或 system:user`。根因不是 RBAC 配错，
而是三道门用了三份名单——菜单和路由按 qualification|schedule 放行，
页里的资质台账接口只认 qualification|system:user。
修的时候没有给运维补资质审核权限（那是写权限），而是把"读台账"和"改台账"拆开：

  · 读侧名单与人员管理页的准入名单逐字对齐，谁进得来这一页谁就读得到台账；
  · 复审动作仍然只认 resource:qualification，403 之后库里一行不许动；
  · 数据范围不变，非平台账号只看得到本商家的主体；
  · 机长/空管/财务这类没有任何 resource:* 的岗位，用 /my-duty 查自己那一份，
    预警站内信的发信口径是"主体=本人"，收信人必须有一个打得开的地方。
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import bindparam, text

from app.extensions import db

URL_QUAL = "/api/resource/qualifications"
URL_MYDUTY = "/api/resource/my-duty"
URL_AUDIT_TPL = "/api/resource/qualifications/{}/audit"


def _q(stmt, **p):
    return db.session.execute(text(stmt), p)


def _cfg(key: str, value: str):
    """改 sys_config 要连着清 10 秒 TTL 缓存，否则读到的还是旧值（用例假通过）。"""
    from app.common import scope
    _q("UPDATE sys_config SET config_value=:v WHERE config_key=:k", v=value, k=key)
    db.session.commit()
    scope.invalidate_cfg(key)


# ------------------------------------------------------------------
# 造数：qualification / staff_schedule 都不在 conftest 的清场名单里（它们是台账），
# 谁造的谁收回去，否则这些行会一直留在测试库里被后面的用例读到。
# ------------------------------------------------------------------
@pytest.fixture()
def ledger(app_ctx):
    made_quals: list[int] = []
    made_shifts: list[int] = []

    def qual(subject_type: int, subject_id: int, *, name: str, days: int = 30,
             status: int = 1) -> int:
        rid = int(_q(
            "INSERT INTO qualification (subject_type, subject_id, qual_type, qual_name,"
            " expire_at, status, created_at, updated_at, deleted)"
            " VALUES (:t, :sid, 'license', :n, :e, :st, NOW(), NOW(), 0)",
            t=subject_type, sid=subject_id, n=name,
            e=datetime.now() + timedelta(days=days), st=status).lastrowid)
        made_quals.append(rid)
        return rid

    def shift(user_id: int, *, merchant_id: int = 1, offset_days: int = 0,
              shift_type: int = 1, area: str = "瓯江口示范区", status: int = 1) -> int:
        """(人, 日期, 班次) 上有唯一键，而 p10 迁移会按 CURDATE() 铺未来 7 天班，
        所以撞车很常见：撞了就返回既有行、并且**不登记回收**，否则 teardown 会把
        种子班表删掉，后面所有读排班的用例都在一份残缺数据上跑。"""
        d = date.today() + timedelta(days=offset_days)
        hit = _q("SELECT id FROM staff_schedule WHERE user_id=:u AND shift_date=:d"
                 " AND shift_type=:s", u=user_id, d=d, s=shift_type).scalar()
        if hit:
            return int(hit)
        start = datetime.combine(d, datetime.min.time()).replace(hour=7)
        rid = int(_q(
            "INSERT INTO staff_schedule (user_id, merchant_id, shift_date, shift_type,"
            " start_time, end_time, responsible_area, status, created_at, updated_at, deleted)"
            " VALUES (:u, :m, :d, :st, :b, :e, :a, :s, NOW(), NOW(), 0)",
            u=user_id, m=merchant_id, d=d, st=shift_type, b=start,
            e=start + timedelta(hours=8), a=area, s=status).lastrowid)
        made_shifts.append(rid)
        return rid

    # SimpleNamespace 不是类实例属性，装进去的函数不会被绑成方法（用 type() 造对象就会多一个 self）
    from types import SimpleNamespace
    yield SimpleNamespace(qual=qual, shift=shift)
    for table, ids in (("qualification", made_quals), ("staff_schedule", made_shifts)):
        if ids:
            db.session.execute(text(f"DELETE FROM {table} WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)), {"ids": ids})
    db.session.commit()


def _ops(fresh_user):
    return fresh_user("u_qlops", "ops_maintainer", user_type=6, merchant_id=None)


def _maintainer(fresh_user):
    return fresh_user("uql_maint", "merchant_maintainer", user_type=3, merchant_id=1)


def _status_of(qid: int) -> int:
    db.session.expire_all()
    return int(_q("SELECT status FROM qualification WHERE id=:i", i=qid).scalar())


# ------------------------------------------------------------------
# 1. 读侧：只有排班权限的岗位也要读得到资质台账
# ------------------------------------------------------------------
def test_schedule_only_role_can_read_qual_ledger(api, ledger, user_ids, fresh_user):
    """运维/设备运维只有 resource:schedule，过去一进人员管理就吃 403。"""
    ops = _ops(fresh_user)
    maint = _maintainer(fresh_user)
    qid = ledger.qual(2, user_ids["pilot"], name="UQL 机长驾驶证", days=45)

    for who, label in ((api.as_id(ops.id), "平台运维"), (api.as_id(maint.id), "设备运维")):
        body = who.get(URL_QUAL).get_json()
        assert body.get("code") == 0, f"{label} 读资质台账失败：{body}"
        assert any(r["id"] == qid for r in body["data"]["list"]), \
            f"{label} 看不到机长的资质（他正要给这个人排班）"


def test_qual_ledger_scope_still_narrowed_for_merchant(api, ledger, fresh_user):
    """放宽的是"能不能读"，不是"能读谁的"：别家商家的主体一条都不许漏进来。"""
    maint = _maintainer(fresh_user)
    mine = ledger.qual(1, 1, name="UQL 本商家营业执照")           # 归属商家 = 1
    theirs = ledger.qual(1, 9001, name="UQL 别家商家营业执照")     # 归属商家 = 9001

    ids = {r["id"] for r in api.as_id(maint.id).get(URL_QUAL).get_json()["data"]["list"]}
    assert mine in ids, "本商家主体的资质被一起挡掉了"
    assert theirs not in ids, f"跨商家泄露：{theirs} 出现在了设备运维的台账里"
    assert theirs in {r["id"] for r in api.as_("admin").get(URL_QUAL).get_json()["data"]["list"]}, \
        "平台侧台账应当仍然是全量"


def test_audit_still_requires_qualification_perm(api, ledger, user_ids, fresh_user):
    """读台账不等于审台账：403 之后那条资质的状态必须还在原地。"""
    ops = _ops(fresh_user)
    qid = ledger.qual(2, user_ids["pilot"], name="UQL 待复审", days=-3, status=3)

    resp = api.as_id(ops.id).post(URL_AUDIT_TPL.format(qid), json={"result": 1})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["code"] == 40300 and "resource:qualification" in body["message"], body
    assert _status_of(qid) == 3, "越权的复审请求把资质改成有效了"

    ok = api.as_(  # 平台管理员持有 resource:qualification，同一份报文必须能过
        "admin").post(URL_AUDIT_TPL.format(qid), json={"result": 1}).get_json()
    assert ok.get("code") == 0, ok
    assert _status_of(qid) == 1


# ------------------------------------------------------------------
# 2. 自助口：我的资质与值班
# ------------------------------------------------------------------
# 排班造在 +20 天而不是今天：p10 迁移会按 CURDATE() 给花名册铺未来 7 天班，
# 落在种子区间里就会撞唯一键，撞了拿到的是种子行，标记区域根本写不进去。
_F = (date.today() + timedelta(days=20)).isoformat()
_T = (date.today() + timedelta(days=26)).isoformat()
NEXT_URL = f"{URL_MYDUTY}?from={_F}&to={_T}"


def test_my_duty_returns_only_my_rows(api, ledger, user_ids):
    """数据范围写死在 SQL 里：别人排了什么班、别人的证在不在期，都不在这份响应里。"""
    pilot, other = user_ids["pilot"], user_ids["merchant"]
    mine = ledger.qual(2, pilot, name="UQL 我的驾驶证", days=5)
    theirs = ledger.qual(2, other, name="UQL 别人的证", days=400)
    ledger.shift(pilot, offset_days=20, shift_type=1, area="UQL-我-早班")
    ledger.shift(other, offset_days=20, shift_type=1, area="UQL-别人-早班")

    d = api.as_("pilot").get(NEXT_URL).get_json()
    assert d.get("code") == 0, d
    data = d["data"]
    by_id = {q["id"]: q for q in data["qualifications"]}
    assert mine in by_id, "自己的资质没进来"
    assert theirs not in by_id, "读到了别人的资质"
    assert [s["area"] for s in data["shifts"]] == ["UQL-我-早班"], data["shifts"]
    assert data["range"] == {"from": _F, "to": _T}


def test_my_duty_flags_expiring_with_configured_days(api, ledger, user_ids):
    """daysLeft/warn 必须跟着 staff_qual_warn_days 走，和人员台账、定时任务同一口径。"""
    _cfg("staff_qual_warn_days", "7")
    try:
        far = ledger.qual(2, user_ids["pilot"], name="UQL 十天后的证", days=10)
        near = ledger.qual(2, user_ids["pilot"], name="UQL 三天的证", days=3)
        data = api.as_("pilot").get(URL_MYDUTY).get_json()["data"]
        assert data["qualWarnDays"] == 7, "阈值没读到系统参数"
        by_id = {q["id"]: q for q in data["qualifications"]}
        assert by_id[near]["daysLeft"] == 3 and by_id[near]["warn"] is True
        assert by_id[far]["daysLeft"] == 10 and by_id[far]["warn"] is False
    finally:
        _cfg("staff_qual_warn_days", "15")


def test_duty_board_gives_counts_not_names(api, ledger, user_ids):
    """在岗看板只出计数：机长要的是"今天几个人在值班"，不是全区人员的姓名表。"""
    ledger.shift(user_ids["merchant"], shift_type=3, area="UQL-同事的区域")
    data = api.as_("pilot").get(URL_MYDUTY).get_json()["data"]
    board = {b["shiftType"]: b["count"] for b in data["dutyBoard"]}
    assert set(board) == {1, 2, 3, 4}, "四种班次都得有格子，缺一种页面就少一块"
    assert board[3] >= 1 and data["todayOnDutyTotal"] >= 1, board
    assert sum(board.values()) >= data["todayOnDutyTotal"], "分班次计数与总人数对不上"
    assert {k for b in data["dutyBoard"] for k in b} == {"shiftType", "shiftName", "count"}, \
        f"在岗看板泄漏了人员字段：{data['dutyBoard'][0]}"
    assert "UQL-同事" not in str(data["dutyBoard"]), "把别人的责任区域带进来了"


def test_today_flag_ignores_the_query_range(api, ledger, user_ids):
    """查下个月的班表时，"我今天上不上班"也得照常回答——这一项不能跟着范围走。"""
    who = user_ids["merchant"]        # 花名册（pilot/运维岗）之外，种子班表里没有他
    assert api.as_id(who).get(NEXT_URL).get_json()["data"]["todayOnDuty"] is False
    ledger.shift(who, shift_type=2, area="UQL-今天")
    data = api.as_id(who).get(NEXT_URL).get_json()["data"]
    assert data["shifts"] == [] and data["todayOnDuty"] is True, \
        "范围外没班就顺手把今天也判成不上班了"


def test_my_duty_rejects_bad_range(api, ledger, user_ids):
    """日期参数在进 SQL 之前判掉：范围反了不能静默返回空表，那和"你没班"长得一模一样。"""
    bad = api.as_("pilot").get(f"{URL_MYDUTY}?from=2026-10-10&to=2026-10-01").get_json()
    assert bad["code"] == 40000, bad
    garbage = api.as_("pilot").get(f"{URL_MYDUTY}?from=10月1日").get_json()
    assert garbage["code"] == 40000, garbage


def test_my_duty_is_staff_only(api):
    """客户账号没有排班也没有人员资质，自助口对他关闭（403，不是返回空列表）。"""
    body = api.as_("customer").get(URL_MYDUTY).get_json()
    assert body["code"] == 40300 and "运营侧" in body["message"], body


def test_pilot_without_any_resource_perm_can_open_his_alert_source(api, user_ids):
    """机长的权限点里没有一条 resource:*：资质到期预警发给本人，本人必须查得到。"""
    perms = set(db.session.execute(text(
        "SELECT DISTINCT p.perm_code FROM permission p JOIN role_permission rp ON rp.permission_id=p.id"
        " JOIN user_role ur ON ur.role_id=rp.role_id WHERE ur.user_id=:u"),
        {"u": user_ids["pilot"]}).scalars().all())
    assert not [p for p in perms if p.startswith("resource:")], f"种子机长被加了资源权限：{perms}"
    assert api.as_("pilot").get(URL_MYDUTY).get_json()["code"] == 0
    # 但台账对他仍然关闭——自助口不是台账的后门
    assert api.as_("pilot").get(URL_QUAL).status_code == 403
