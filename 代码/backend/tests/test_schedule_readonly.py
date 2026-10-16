"""排班：看得懂班表 ≠ 改得动班表。

用户口径："空管、财务要看别人的排班，但是只能看看，没有修改之类的权限。"
`resource:schedule` 这一份 code 同时管着读（GET /aux/schedules）和写（POST / DELETE），
所以他们要么整份进来——顺带拿到排班、删班的写权，要么根本进不来。这里把"看"单独拆成
`resource:schedule:view`（迁移 p19，授空管 2 / 财务 6）：读门认它，写门一寸不动。

这组用例钉三件事，少一件都会退化成上次那种"三层门对不上"：
  · 只读口**不放大数据范围**：商家侧身份（财务种子账号就是 user_type 3 / merchant 1）
    只看得到本商家的班，跨商家一行都不许漏；
  · 排班页 `onMounted` 必拉花名册来标归属，所以 `/resource/personnel` 的读门必须是
    排班页准入名单的超集，否则只读岗一进页面就吃 403；
  · 403 的写请求必须在库里留不下痕迹（不是"报错了但也写进去了"）。
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import bindparam, text

from app.extensions import db

URL_ROSTER = "/api/aux/schedules"
URL_PERSONNEL = "/api/resource/personnel"
OTHER_MERCHANT = 9102          # 造出来专供"跨商家"这一条，用例结束删干净


def _q(stmt, **p):
    return db.session.execute(text(stmt), p)


def _scalar(stmt, **p):
    return _q(stmt, **p).scalar()


# ------------------------------------------------------------------
# 造数：staff_schedule 与 users 都不在 conftest 的清场名单里，谁造谁收。
# 回收顺序必须是 班表 → 账号 → 商家：前两张表都用外键指向 merchants。
# ------------------------------------------------------------------
@pytest.fixture()
def roster(app_ctx):
    made_shifts: list[int] = []
    made_users: list[int] = []

    _q("INSERT INTO merchants (id, merchant_no, merchant_name, audit_status, status)"
       " VALUES (:i, 'SR-TEST-9102', 'UQLR 别家商家', 2, 1)", i=OTHER_MERCHANT)
    db.session.commit()

    def user(username: str, merchant_id: int, real_name: str) -> int:
        """不登录的纯subject：password_hash 给个占位值就行，用例只读它的排班。"""
        uid = int(_q(
            "INSERT INTO users (username, password_hash, user_type, real_name,"
            " phone_cipher, phone_digest, merchant_id, status, deleted)"
            " VALUES (:n, 'no-login', 3, :r, :c, :d, :m, 1, 0)",
            n=username, r=real_name, c=f"T:{username}",
            d=("ab" + f"{username}").ljust(64, "0")[:64], m=merchant_id).lastrowid)
        made_users.append(uid)
        return uid

    def shift(user_id: int, merchant_id: int, *, area: str,
              offset_days: int = 21, shift_type: int = 1) -> int:
        """日期默认放到 +21 天：p10 迁移按 CURDATE() 铺了未来 7 天的班，
        撞 uk_user_date_shift 的话返回既有行且不登记回收（那张班不是本次造的）。"""
        d = date.today() + timedelta(days=offset_days)
        hit = _scalar("SELECT id FROM staff_schedule WHERE user_id=:u AND shift_date=:d"
                      " AND shift_type=:s", u=user_id, d=d, s=shift_type)
        if hit:
            return int(hit)
        start = datetime.combine(d, datetime.min.time()).replace(hour=7)
        rid = int(_q(
            "INSERT INTO staff_schedule (user_id, merchant_id, shift_date, shift_type,"
            " start_time, end_time, responsible_area, status, created_at, updated_at, deleted)"
            " VALUES (:u, :m, :d, :s, :b, :e, :a, 1, NOW(), NOW(), 0)",
            u=user_id, m=merchant_id, d=d, s=shift_type, b=start,
            e=start + timedelta(hours=8), a=area).lastrowid)
        made_shifts.append(rid)
        return rid

    from types import SimpleNamespace
    # made_shifts 直接挂出去：走接口排出来的班（POST /aux/schedules）也要有人回收，
    # 而它的 id 只有用例自己知道。
    yield SimpleNamespace(user=user, shift=shift, made_shifts=made_shifts)
    for sql, ids in (("DELETE FROM staff_schedule WHERE id IN :ids", made_shifts),
                     ("DELETE FROM users WHERE id IN :ids", made_users)):
        if ids:
            db.session.execute(text(sql).bindparams(
                bindparam("ids", expanding=True)), {"ids": ids})
    _q("DELETE FROM merchants WHERE id=:i", i=OTHER_MERCHANT)
    db.session.commit()


@pytest.fixture()
def viewers(api, fresh_user):
    """只读岗：空管是平台侧（user_type 5，全区可见），财务是商家侧员工（user_type 3 / 商家 1）。

    现造的账号不在 `user_ids` 那份种子名单里，只能按 id 取凭证（`api.as_` 查的是种子用户名）。
    """
    traffic = fresh_user("u_sr_traffic", "air_traffic", user_type=5, merchant_id=None)
    finance = fresh_user("u_sr_finance", "finance", user_type=3, merchant_id=1)
    return {"traffic": api.as_id(traffic.id), "finance": api.as_id(finance.id)}


def _rows(who) -> list[dict]:
    body = who.get(URL_ROSTER).get_json()
    assert body.get("code") == 0, f"只读岗进排班页就吃 403：{body}"
    return body["data"]


# ------------------------------------------------------------------
# 1. 读：两个只读岗都进得来，且页面上依赖的花名册也读得到
# ------------------------------------------------------------------
def test_view_only_roles_can_read_roster(viewers, roster, user_ids):
    mine = roster.shift(user_ids["pilot"], 1, area="SR-本商家-班")

    for label, who in viewers.items():
        ids = {r["id"] for r in _rows(who)}
        assert mine in ids, f"{label} 看不到班表里的这一条"
        got = next(r for r in _rows(who) if r["id"] == mine)
        assert got["user_name"], f"{label} 的班表里没有人名，等于看不出排了谁"


def test_schedule_page_personnel_dependency_is_readable(viewers):
    """排班页 onMounted 无条件拉 /resource/personnel（给班表标归属），
    这一条挂了就是"运维点人员管理吃 403"的翻版，所以单独钉住。"""
    for label, who in viewers.items():
        assert who.get(URL_PERSONNEL).get_json().get("code") == 0, f"{label} 读花名册失败"


# ------------------------------------------------------------------
# 2. 范围：只读 ≠ 全平台，商家侧身份仍然只看得到本商家
# ------------------------------------------------------------------
def test_view_perm_does_not_widen_merchant_scope(viewers, roster):
    outsider = roster.user("u_sr_outsider", OTHER_MERCHANT, "SR 别家机长")
    theirs = roster.shift(outsider, OTHER_MERCHANT, area="SR-别家-班")
    mine = roster.shift(roster.user("u_sr_local", 1, "SR 本家机长"), 1, area="SR-本家-班")

    # 空管是平台身份（user_type 5）→ 两边都看得到，这才是"要看别人的排班"
    traffic_ids = {r["id"] for r in _rows(viewers["traffic"])}
    assert {theirs, mine} <= traffic_ids, "平台侧只读岗应该看全区班表"

    finance_ids = {r["id"] for r in _rows(viewers["finance"])}
    assert mine in finance_ids, "本商家的班不该被挡"
    assert theirs not in finance_ids, f"给了只读点就跨商家泄露：{theirs} 出现在财务的班表里"
    assert {r["merchant_id"] for r in _rows(viewers["finance"])} == {1}, \
        "财务这一份里混进了别的商家的行"


# ------------------------------------------------------------------
# 3. 写：只读岗的新增与删除都必须 403，而且库里留不下痕迹
# ------------------------------------------------------------------
def test_view_only_roles_cannot_create_shift(viewers, roster, user_ids):
    d = (date.today() + timedelta(days=30)).isoformat()
    for label, who in viewers.items():
        resp = who.post(URL_ROSTER, json={
            "userId": user_ids["pilot"], "date": d, "shiftType": 2,
            "start": f"{d} 15:00:00", "end": f"{d} 23:00:00", "area": f"SR-{label}-想排班"})
        body = resp.get_json()
        assert body.get("code") == 40300 and "resource:schedule" in body.get("message", ""), \
            f"{label} 的写请求没被权限门禁拦住：{body}"
    # 不用 LIKE 匹配区域名：这条 SQL 没带绑定参数，但 % 在 pyformat 驱动里是地雷（见 conftest 对迁移脚本的同款提醒）
    assert _scalar("SELECT COUNT(*) FROM staff_schedule WHERE user_id=:u"
                   " AND shift_date=:d AND shift_type=2",
                   u=user_ids["pilot"], d=d) == 0, "403 了但班还是排进去了"


def test_view_only_roles_cannot_delete_shift(viewers, roster, user_ids):
    sid = roster.shift(user_ids["pilot"], 1, area="SR-不许删", offset_days=22, shift_type=3)
    for label, who in viewers.items():
        body = who.delete(f"/api/aux/schedules/{sid}").get_json()
        assert body.get("code") == 40300, f"{label} 删班没被拦：{body}"
    db.session.expire_all()
    assert _scalar("SELECT deleted FROM staff_schedule WHERE id=:i", i=sid) == 0, \
        "只读岗把班删掉了"


def test_manage_role_can_still_write(api, roster, user_ids):
    """别把该排班的人一起挡在门外：resource:schedule 的写链路必须照常通。

    start/end 是 DATETIME 列，前端由 `shiftRange()` 拼成 "YYYY-MM-DD HH:MM:00" 再发，
    这里给同一份形态——只发 "00:00" 会被 MySQL 判 Incorrect datetime value。
    """
    d = (date.today() + timedelta(days=31)).isoformat()
    body = api.as_("admin").post(URL_ROSTER, json={
        "userId": user_ids["pilot"], "date": d, "shiftType": 4,
        "start": f"{d} 00:00:00", "end": f"{d} 23:59:00", "area": "SR-管理员排班"}).get_json()
    assert body.get("code") == 0, body
    sid = int(_scalar("SELECT id FROM staff_schedule WHERE responsible_area=:a",
                      a="SR-管理员排班"))
    roster.made_shifts.append(sid)          # 交还给 fixture 回收
    assert api.as_("admin").delete(f"/api/aux/schedules/{sid}").get_json().get("code") == 0
