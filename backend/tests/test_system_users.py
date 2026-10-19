"""P0-4 账号维护 + P0-9 角色权限维护：状态口径、越权保护、授权集合。

这一块是"系统中心"两个页面的服务端真相，全部走 HTTP（test_client），
因为要验的就是装饰器 + 视图 + 数据范围三层凑在一起之后的实际行为。

三条最容易回退的口径单独钉住：
  · users.status 只有 1 正常 / 2 冻结 / 3 注销（早期前端开关传 0/1 会静默变成"未知"）；
  · _assert_manageable 的四道保护：不能动自己、超级管理员不可动、
    平台侧账号只许管理员操作、商家只许动本商家；
  · 角色权限整组替换时不许自锁（内置 platform_admin 不可改、自己所属角色不可改），
    并且勾了子权限必须自动补上父菜单，否则动态菜单树会挂空。
"""
import itertools
import json

import pytest
from sqlalchemy import text

from app.common.crypto import encrypt_phone, phone_digest
from app.extensions import db
from app.models import User
from app.models.rbac import Role, user_role

TMP_PREFIX = "t_sys_"
_phone_seq = itertools.count(1)


def _phone() -> str:
    """每次调用给一个没被占用过的 11 位手机号（phone_digest 有唯一校验）。"""
    return f"1391{next(_phone_seq):07d}"[:11]


def _drop_tmp_rows() -> None:
    """清掉本模块现造的临时账号/临时角色，重跑不撞 uk_username、role_code 唯一索引。"""
    role_ids = [r[0] for r in db.session.execute(text(
        f"SELECT id FROM role WHERE role_code LIKE '{TMP_PREFIX}%'")).all()]
    for rid in role_ids:
        db.session.execute(text("DELETE FROM role_permission WHERE role_id=:r"), {"r": rid})
        db.session.execute(user_role.delete().where(user_role.c.role_id == rid))
    if role_ids:
        db.session.execute(text(f"DELETE FROM role WHERE role_code LIKE '{TMP_PREFIX}%'"))
    db.session.execute(text(
        "DELETE ur FROM user_role ur JOIN users u ON u.id = ur.user_id "
        f"WHERE u.username LIKE '{TMP_PREFIX}%'"))
    db.session.execute(text(
        "DELETE cp FROM customer_profile cp JOIN users u ON u.id = cp.user_id "
        f"WHERE u.username LIKE '{TMP_PREFIX}%'"))
    db.session.execute(text(f"DELETE FROM users WHERE username LIKE '{TMP_PREFIX}%'"))
    db.session.commit()


@pytest.fixture(autouse=True)
def clean_tmp_and_role_perms(app_ctx):
    """临时数据清场 + 角色授权集合原样归还。

    为什么授权集合要单独管：conftest 的 _truncate 只清业务流水表，role_permission
    是种子数据不在名单里。本模块有整组替换权限的用例，不还回去的话，跑在后面的
    权限矩阵（test_perms_matrix）测的就是被上一个用例改写过的授权，等于假通过。
    """
    _drop_tmp_rows()
    snapshot = db.session.execute(text(
        "SELECT role_id, permission_id FROM role_permission")).all()
    yield snapshot
    db.session.execute(text("DELETE FROM role_permission"))
    for role_id, permission_id in snapshot:
        db.session.execute(text("INSERT INTO role_permission (role_id, permission_id) "
                                "VALUES (:r, :p)"), {"r": role_id, "p": permission_id})
    db.session.commit()
    _drop_tmp_rows()


# ------------------------------------------------------------------
# A. 列表与数据范围
# ------------------------------------------------------------------
def test_merchant_sees_only_its_own_accounts(api, user_ids):
    body = api.as_("merchant").get("/api/system/users?pageSize=100").get_json()
    assert body["code"] == 0, body
    rows = body["data"]["list"]
    assert rows, "商家管理员（M-81）应该能看到本商家账号，列表为空说明授权没收口对"
    assert {r["merchantId"] for r in rows} == {1}, "商家侧看到了非本商家的账号"
    assert user_ids["admin"] not in {r["id"] for r in rows}
    assert user_ids["customer"] not in {r["id"] for r in rows}, "客户不属于商家员工"


def test_admin_sees_platform_and_all_merchants(api, user_ids):
    body = api.as_("admin").get("/api/system/users?pageSize=100").get_json()
    rows = body["data"]["list"]
    assert user_ids["admin"] in {r["id"] for r in rows}
    assert user_ids["pilot"] in {r["id"] for r in rows}
    assert any(r["merchantId"] is None for r in rows), "平台侧账号（merchant_id 为空）没出现"


def test_list_filters_and_pagination(api, fresh_user):
    fresh_user(f"{TMP_PREFIX}kw", "dispatcher", real_name="测试关键词")
    mids = [u.id for u in db.session.query(User).filter(User.username.like(f"{TMP_PREFIX}%")).all()]
    q = api.as_("admin").get("/api/system/users?keyword=测试关键词")
    assert q.get_json()["data"]["total"] == len(mids)
    by_status = api.as_("admin").get("/api/system/users?status=2").get_json()["data"]
    assert all(r["status"] == 2 for r in by_status["list"]), "status 过滤没生效"
    by_role = api.as_("admin").get("/api/system/users?roleCode=pilot").get_json()["data"]
    assert by_role["total"] >= 2 and all("pilot" in r["roles"] for r in by_role["list"])
    capped = api.as_("admin").get("/api/system/users?pageSize=9999").get_json()["data"]
    assert capped["pageSize"] == 100, "pageSize 未设上限，容易被拖库"


def test_phone_is_masked_in_list(api):
    body = api.as_("admin").get("/api/system/users?pageSize=100").get_json()
    dumped = json.dumps(body, ensure_ascii=False)
    for key in ("phone_cipher", "phone_digest", "password_hash", "SEED:"):
        assert key not in dumped, f"账号列表泄露敏感列：{key}"
    phones = [r["phone"] for r in body["data"]["list"] if r["phone"]]
    assert phones and all("*" in p for p in phones), f"手机号未脱敏：{phones[:3]}"


# ------------------------------------------------------------------
# B. 建号
# ------------------------------------------------------------------
def test_merchant_creates_dispatcher_and_can_login(api, login):
    name = f"{TMP_PREFIX}disp"
    resp = api.as_("merchant").post("/api/system/users", json={
        "username": name, "password": "Disp@1234", "roleCode": "dispatcher",
        "phone": _phone(), "realName": "新调度"})
    assert resp.get_json()["code"] == 0, resp.get_json()
    assert resp.get_json()["data"]["initialPassword"] == "Disp@1234"
    u = db.session.query(User).filter_by(username=name).first()
    assert (u.user_type, u.merchant_id, u.status) == (3, 1, 1), "建号口径：商家员工=本商家/user_type 3/正常"
    assert u.role_codes == ["dispatcher"]
    body = login(name, "Disp@1234").get_json()
    assert body["code"] == 0, body
    assert "dispatch:plan" in body["data"]["userInfo"]["perms"]


@pytest.mark.parametrize("payload,keyword", [
    ({"username": "", "roleCode": "dispatcher", "phone": "13900001111"}, "账号不能为空"),
    ({"username": "x", "roleCode": "", "phone": "13900001111"}, "请选择岗位角色"),
    ({"username": "x", "roleCode": "dispatcher", "password": "123", "phone": "13900001111"}, "至少 8 位"),
    ({"username": "x", "roleCode": "no_such_role", "phone": "13900001111"}, "未知角色"),
    ({"username": "x", "roleCode": "dispatcher", "phone": "139"}, "11 位手机号"),
    ({"username": "x", "roleCode": "dispatcher", "phone": "1390000abce"}, "11 位手机号"),
])
def test_create_user_param_validation(api, payload, keyword):
    body = api.as_("merchant").post("/api/system/users", json=payload).get_json()
    assert body["code"] == 40000, body
    assert keyword in body["message"], body


def test_create_user_conflicts(api, fresh_user):
    """重名/重手机号都要 409，且第二次请求不能因为手机号是假摘要就漏判。"""
    phone = _phone()
    fresh_user(f"{TMP_PREFIX}dup", "dispatcher",
               phone_cipher=encrypt_phone(phone), phone_digest=phone_digest(phone))
    dup = api.as_("merchant").post("/api/system/users", json={
        "username": f"{TMP_PREFIX}dup", "password": "Admin@123",
        "roleCode": "dispatcher", "phone": phone}).get_json()
    assert dup["code"] == 40900 and "账号已存在" in dup["message"], dup
    again = api.as_("merchant").post("/api/system/users", json={
        "username": f"{TMP_PREFIX}brand_new", "password": "Admin@123",
        "roleCode": "dispatcher", "phone": phone}).get_json()
    assert again["code"] == 40900 and "手机号已注册" in again["message"], again
    assert db.session.query(User).filter_by(username=f"{TMP_PREFIX}brand_new").first() is None


def test_merchant_cannot_create_platform_accounts(api):
    body = api.as_("merchant").post("/api/system/users", json={
        "username": f"{TMP_PREFIX}evil", "password": "Admin@123",
        "roleCode": "air_traffic", "phone": _phone()}).get_json()
    assert body["code"] == 40300 and "角色权限管理" in body["message"], body
    assert db.session.query(User).filter_by(username=f"{TMP_PREFIX}evil").first() is None


def test_admin_creates_platform_and_customer_accounts(api):
    admin = api.as_("admin")
    ops_name = f"{TMP_PREFIX}ops"
    assert admin.post("/api/system/users", json={
        "username": ops_name, "password": "Admin@123",
        "roleCode": "ops_maintainer", "phone": _phone()}).get_json()["code"] == 0
    ops = db.session.query(User).filter_by(username=ops_name).first()
    assert (ops.user_type, ops.merchant_id) == (6, None), "运维是平台侧岗位，不该挂商家"

    cust_name = f"{TMP_PREFIX}cust"
    assert admin.post("/api/system/users", json={
        "username": cust_name, "password": "Admin@123",
        "roleCode": "customer", "phone": _phone(), "merchantId": 1}).get_json()["code"] == 0
    cust = db.session.query(User).filter_by(username=cust_name).first()
    assert (cust.user_type, cust.merchant_id) == (1, None), "客户账号不允许挂到商家名下"


# ------------------------------------------------------------------
# C. 状态口径（P0-4 的核心）
# ------------------------------------------------------------------
@pytest.mark.parametrize("raw", [0, "0", False, None, "", 5, -1, "abc"])
def test_status_rejects_values_outside_1_2_3(api, fresh_user, raw):
    u = fresh_user(f"{TMP_PREFIX}st", "dispatcher")
    body = api.as_("admin").put(f"/api/system/users/{u.id}/status",
                               json={"status": raw}).get_json()
    assert body["code"] == 40000, f"状态值 {raw!r} 竟被接受：{body}"
    db.session.expire_all()
    assert db.session.get(User, u.id).status == 1, f"{raw!r} 不该改动库里状态"


def test_status_0_is_rejected_with_the_unified_wording(api, fresh_user):
    """0 是早期前端开关的值：必须明确告诉调用方"停用请传 2"，不能静默写 0。"""
    u = fresh_user(f"{TMP_PREFIX}zero", "dispatcher")
    body = api.as_("admin").put(f"/api/system/users/{u.id}/status", json={"status": 0}).get_json()
    assert body["code"] == 40000
    assert "1 正常 / 2 冻结 / 3 注销" in body["message"], body
    assert "停用请传 2" in body["message"], body


def test_freeze_login_and_unfreeze(api, fresh_user, login):
    u = fresh_user(f"{TMP_PREFIX}frz", "dispatcher")
    name = u.username
    assert login(name).get_json()["code"] == 0, "冻结前应能登录"

    body = api.as_("admin").put(f"/api/system/users/{u.id}/status",
                                json={"status": 2, "reason": "涉嫌违规调度"}).get_json()
    assert body["code"] == 0 and body["data"]["statusName"] == "冻结", body
    db.session.expire_all()
    assert db.session.get(User, u.id).frozen_reason == "涉嫌违规调度"
    frozen = login(name).get_json()
    assert frozen["code"] == 40300 and "账号已冻结" in frozen["message"], frozen
    assert "涉嫌违规调度" in frozen["message"], "冻结原因要能告诉用户，否则客服电话没法接"

    assert api.as_("admin").put(f"/api/system/users/{u.id}/status",
                                json={"status": 1}).get_json()["code"] == 0
    db.session.expire_all()
    assert db.session.get(User, u.id).frozen_reason is None, "解冻要清掉原因，否则下次冻结串数据"
    assert login(name).get_json()["code"] == 0


def test_cancel_soft_deletes_and_hides(api, fresh_user, login):
    u = fresh_user(f"{TMP_PREFIX}cxl", "dispatcher")
    body = api.as_("admin").put(f"/api/system/users/{u.id}/status", json={"status": 3}).get_json()
    assert body["code"] == 0 and body["data"]["statusName"] == "注销", body
    db.session.expire_all()
    target = db.session.get(User, u.id)
    assert (target.status, target.deleted) == (3, 1)
    ids = [r["id"] for r in api.as_("admin").get(
        "/api/system/users?pageSize=100").get_json()["data"]["list"]]
    assert u.id not in ids, "注销账号仍出现在列表里"
    assert login(u.username).get_json()["code"] == 40100, "注销账号不该还能登录"


def test_status_names_are_readback_of_code(api, fresh_user):
    u = fresh_user(f"{TMP_PREFIX}name", "dispatcher", status=2)
    row = next(r for r in api.as_("admin").get(
        "/api/system/users?pageSize=100").get_json()["data"]["list"] if r["id"] == u.id)
    assert row["status"] == 2 and row["statusName"] == "冻结", row


# ------------------------------------------------------------------
# D. 越权保护
# ------------------------------------------------------------------
def test_cannot_operate_on_yourself(api, user_ids):
    for who, uid in (("admin", user_ids["admin"]), ("merchant", user_ids["merchant"])):
        body = api.as_(who).put(f"/api/system/users/{uid}/status", json={"status": 2}).get_json()
        assert body["code"] == 40300 and "不能对自己执行" in body["message"], (who, body)
        body = api.as_(who).put(f"/api/system/users/{uid}/password",
                                json={"password": "Self@1234"}).get_json()
        assert "不能对自己执行" in body["message"], (who, body)


def test_super_admin_account_is_untouchable(api, user_ids):
    """连平台管理员自己都改不动 id=1：防止把唯一入口账号锁死。"""
    name = f"{TMP_PREFIX}admin2"
    created = api.as_("admin").post("/api/system/users", json={
        "username": name, "password": "Admin@123",
        "roleCode": "platform_admin", "phone": _phone()}).get_json()
    assert created["code"] == 0, created
    second = db.session.query(User).filter_by(username=name).first()
    assert (second.user_type, second.merchant_id) == (4, None)
    assert "platform_admin" in second.role_codes
    before_hash = db.session.get(User, user_ids["admin"]).password_hash
    for action, url, payload in (
            ("状态", f"/api/system/users/{user_ids['admin']}/status", {"status": 2}),
            ("密码", f"/api/system/users/{user_ids['admin']}/password", {"password": "Hack@1234"}),
            ("角色", f"/api/system/users/{user_ids['admin']}/roles", {"roleIds": [9]})):
        body = api.as_id(second.id).put(url, json=payload).get_json()
        assert body["code"] == 40300 and "超级管理员" in body["message"], (action, body)
    db.session.expire_all()
    admin = db.session.get(User, user_ids["admin"])
    assert (admin.status, admin.deleted) == (1, 0)
    assert admin.password_hash == before_hash, "越权请求改动到了超级管理员的密码哈希"


def test_merchant_cannot_touch_platform_accounts(api, user_ids, fresh_user):
    """平台侧账号（user_type 4/5/6）对商家管理员一律不可操作，商家只在自己范围内停号。"""
    traffic = fresh_user(f"{TMP_PREFIX}at", "air_traffic", user_type=5, merchant_id=None)
    for uid, label in ((user_ids["admin"], "平台管理员"), (traffic.id, "空管")):
        for url, payload in ((f"/api/system/users/{uid}/status", {"status": 3}),
                             (f"/api/system/users/{uid}/password", {"password": "Hack@1234"})):
            body = api.as_("merchant").put(url, json=payload).get_json()
            assert body["code"] == 40300 and "只有平台管理员" in body["message"], (label, url, body)
    db.session.expire_all()
    assert (db.session.get(User, traffic.id).status,
            db.session.get(User, user_ids["admin"]).status) == (1, 1)


def test_merchant_cannot_touch_other_merchants(api, fresh_user, second_merchant):
    rival = fresh_user(f"{TMP_PREFIX}rival", "dispatcher", merchant_id=second_merchant)
    for url, payload in ((f"/api/system/users/{rival.id}/status", {"status": 2}),
                         (f"/api/system/users/{rival.id}/password", {"password": "Hack@1234"})):
        body = api.as_("merchant").put(url, json=payload).get_json()
        assert body["code"] == 40300 and "只能操作本商家" in body["message"], body
    db.session.expire_all()
    assert db.session.get(User, rival.id).status == 1


def test_merchant_cannot_cancel_a_platform_admin(api, fresh_user):
    """商家拿 system:user 也只能在本商家范围内停号。"""
    admin_like = fresh_user(f"{TMP_PREFIX}plat", "air_traffic", user_type=5, merchant_id=None)
    body = api.as_("merchant").put(f"/api/system/users/{admin_like.id}/status",
                                  json={"status": 3}).get_json()
    assert body["code"] == 40300 and "只有平台管理员" in body["message"], body
    body = api.as_("admin").put(f"/api/system/users/{admin_like.id}/status",
                               json={"status": 3}).get_json()
    assert body["code"] == 0, body


def test_admin_can_operate_merchant_staff(api, fresh_user, login):
    u = fresh_user(f"{TMP_PREFIX}ok", "pilot")
    assert api.as_("admin").put(f"/api/system/users/{u.id}/status",
                               json={"status": 2}).get_json()["code"] == 0
    assert login(u.username).get_json()["code"] == 40300
    assert api.as_("admin").put(f"/api/system/users/{u.id}/password",
                              json={"password": "Reset@123"}).get_json()["code"] == 0
    assert api.as_("admin").put(f"/api/system/users/{u.id}/status",
                              json={"status": 1}).get_json()["code"] == 0
    assert login(u.username, "Reset@123").get_json()["code"] == 0


def test_reset_password_length_and_target(api, fresh_user, login):
    u = fresh_user(f"{TMP_PREFIX}pw", "dispatcher")
    short = api.as_("admin").put(f"/api/system/users/{u.id}/password",
                                json={"password": "short"}).get_json()
    assert short["code"] == 40000 and "至少 8 位" in short["message"], short
    assert api.as_("merchant").put(f"/api/system/users/{u.id}/password",
                                 json={"password": "New@12345"}).get_json()["code"] == 0
    assert login(u.username, "New@12345").get_json()["code"] == 0
    assert login(u.username).get_json()["code"] == 40100, "旧密码必须立刻失效"


def test_unknown_user_id_returns_not_found(api):
    body = api.as_("admin").put("/api/system/users/999999/status", json={"status": 2}).get_json()
    assert body["code"] == 40400, body


# ------------------------------------------------------------------
# E. 角色分配（system:role）
# ------------------------------------------------------------------
def test_roles_endpoint_requires_system_role(api, fresh_user):
    u = fresh_user(f"{TMP_PREFIX}role", "dispatcher")
    role_id = db.session.query(Role).filter_by(role_code="pilot").first().id
    body = api.as_("merchant").put(f"/api/system/users/{u.id}/roles",
                                 json={"roleIds": [role_id]}).get_json()
    assert body["code"] == 40300 and "system:role" in body["message"], body
    db.session.expire_all()
    assert db.session.get(User, u.id).role_codes == ["dispatcher"]


def test_admin_assigns_role_and_user_type_follows(api, fresh_user):
    u = fresh_user(f"{TMP_PREFIX}switch", "dispatcher", user_type=3)
    pilot_id = db.session.query(Role).filter_by(role_code="pilot").first().id
    body = api.as_("admin").put(f"/api/system/users/{u.id}/roles",
                              json={"roleIds": [pilot_id]}).get_json()
    assert body["code"] == 0 and body["data"]["roles"] == ["pilot"], body
    db.session.expire_all()
    target = db.session.get(User, u.id)
    assert target.user_type == 3 and target.role_codes == ["pilot"]
    # 换成平台岗位时身份要跟着走，避免出现"空管却是商家身份"
    at_id = db.session.query(Role).filter_by(role_code="air_traffic").first().id
    assert api.as_("admin").put(f"/api/system/users/{u.id}/roles",
                              json={"roleIds": [at_id]}).get_json()["code"] == 0
    db.session.expire_all()
    assert db.session.get(User, u.id).user_type == 5


@pytest.mark.parametrize("role_ids,keyword", [
    ([], "至少选择一个角色"),
    ([999999], "无效角色"),
])
def test_assign_role_validation(api, fresh_user, role_ids, keyword):
    u = fresh_user(f"{TMP_PREFIX}badrole", "dispatcher")
    body = api.as_("admin").put(f"/api/system/users/{u.id}/roles",
                              json={"roleIds": role_ids}).get_json()
    assert body["code"] == 40000 and keyword in body["message"], body


def test_role_options_scope(api):
    merchant = api.as_("merchant").get("/api/system/role-options").get_json()["data"]["list"]
    assert merchant, "登录页角色下拉不能为空"
    assert {m["roleCode"] for m in merchant} <= {
        "merchant_admin", "dispatcher", "pilot", "finance", "merchant_maintainer", "customer"}
    admin = api.as_("admin").get("/api/system/role-options").get_json()["data"]["list"]
    assert "platform_admin" in {a["roleCode"] for a in admin}
    assert len(admin) > len(merchant)


# ------------------------------------------------------------------
# F. 角色与权限维护（M-83）
# ------------------------------------------------------------------
def test_roles_list_counts_match_database(api):
    body = api.as_("admin").get("/api/system/roles").get_json()["data"]
    dispatcher = next(r for r in body["list"] if r["roleCode"] == "dispatcher")
    real_perms = db.session.execute(text(
        "SELECT COUNT(*) FROM role_permission rp JOIN role r ON r.id=rp.role_id "
        "WHERE r.role_code='dispatcher'")).scalar()
    real_users = db.session.execute(text(
        "SELECT COUNT(*) FROM user_role ur JOIN role r ON r.id=ur.role_id "
        "WHERE r.role_code='dispatcher'")).scalar()
    assert dispatcher["permCount"] == real_perms, dispatcher
    assert dispatcher["userCount"] == real_users, dispatcher
    assert dispatcher["builtIn"] == 1 and dispatcher["roleScope"] == 2


def test_permission_tree_is_complete(api):
    tree = api.as_("admin").get("/api/system/permissions").get_json()["data"]["tree"]
    flat = db.session.execute(text(
        "SELECT id, parent_id, perm_type FROM permission WHERE deleted=0")).mappings().all()
    roots = [n for n in tree if n["parentId"] == 0]
    assert roots and all(n["permType"] == 1 for n in roots), "一级菜单必须是 menu 类型"
    children = [c for n in roots for c in n["children"]]
    assert len(roots) + len(children) == len(flat), "权限树有挂空或重复挂载的节点"
    ids = {n["id"] for n in roots} | {c["id"] for c in children}
    assert ids == {f["id"] for f in flat}


def test_platform_admin_role_cannot_be_edited(api):
    rid = db.session.query(Role).filter_by(role_code="platform_admin").first().id
    body = api.as_("admin").put(f"/api/system/roles/{rid}/permissions",
                              json={"permissionIds": [1, 2]}).get_json()
    assert body["code"] == 40300 and "内置平台管理员" in body["message"], body
    kept = db.session.execute(text(
        "SELECT COUNT(*) FROM role_permission WHERE role_id=:r"), {"r": rid}).scalar()
    assert kept >= 40, f"权限被越权请求改动：{kept}"


def test_cannot_edit_the_role_you_hold(api, fresh_user):
    """防自锁：管理员把自己所在角色的权限改掉，就再也没人能改回来了。"""
    rid = db.session.query(Role).filter_by(role_code="dispatcher").first().id
    created = api.as_("admin").post("/api/system/roles", json={
        "roleCode": f"{TMP_PREFIX}holder_role", "roleName": "临时·自锁测试",
        "roleScope": 1, "permissionIds": _sys_role_ids() + [1]}).get_json()
    assert created["code"] == 0, created
    new_role = db.session.get(Role, created["data"]["id"])
    holder = fresh_user(f"{TMP_PREFIX}holder", new_role.role_code,
                        user_type=4, merchant_id=None)

    body = api.as_id(holder.id).put(f"/api/system/roles/{new_role.id}/permissions",
                                   json={"permissionIds": [1]}).get_json()
    assert body["code"] == 40300 and "不能修改自己所属角色" in body["message"], body
    kept = db.session.execute(text(
        "SELECT COUNT(*) FROM role_permission WHERE role_id=:r"), {"r": new_role.id}).scalar()
    assert kept == len(_sys_role_ids()) + 1, f"自锁保护失效，角色权限已被改写：{kept}"
    # 换个人的角色就允许（这条同时验证保护没有扩得过大）
    assert api.as_id(holder.id).put(f"/api/system/roles/{rid}/permissions",
                                   json={"permissionIds": [1, 3, 13]}).get_json()["code"] == 0


def _sys_role_ids() -> list[int]:
    return [int(r[0]) for r in db.session.execute(text(
        "SELECT id FROM permission WHERE perm_code IN ('system','system:role')")).all()]


def test_update_role_permissions_adds_parent_menus(api):
    rid = db.session.query(Role).filter_by(role_code="finance").first().id
    child = db.session.execute(text(
        "SELECT id FROM permission WHERE perm_code='settlement:reconcile'")).scalar()
    parent = db.session.execute(text(
        "SELECT parent_id FROM permission WHERE id=:i"), {"i": child}).scalar()
    body = api.as_("admin").put(f"/api/system/roles/{rid}/permissions",
                              json={"permissionIds": [child]}).get_json()
    assert body["code"] == 0 and body["data"]["permCount"] == 2, body
    ids = set(db.session.execute(text(
        "SELECT permission_id FROM role_permission WHERE role_id=:r"), {"r": rid}).scalars().all())
    assert ids == {child, parent}, f"勾子权限必须自动补父菜单，否则动态菜单树挂空：{ids}"
    # 读回来也要一致
    read_back = api.as_("admin").get(f"/api/system/roles/{rid}/permissions").get_json()["data"]
    assert set(read_back["permissionIds"]) == ids


@pytest.mark.parametrize("payload,keyword", [
    ({"permissionIds": []}, "不能为空"),
    ({"permissionIds": [999999]}, "无效权限点"),
])
def test_update_role_permissions_validation(api, payload, keyword):
    rid = db.session.query(Role).filter_by(role_code="finance").first().id
    body = api.as_("admin").put(f"/api/system/roles/{rid}/permissions",
                              json=payload).get_json()
    assert body["code"] == 40000 and keyword in body["message"], body


def test_create_and_delete_custom_role(api):
    created = api.as_("admin").post("/api/system/roles", json={
        "roleCode": f"{TMP_PREFIX}auditor", "roleName": "临时·审计员", "roleScope": 1,
        "remark": "pytest", "permissionIds": [1, 8, 27, 30]}).get_json()
    assert created["code"] == 0, created
    rid = created["data"]["id"]
    role = db.session.get(Role, rid)
    assert (role.role_code, role.built_in, role.role_scope) == (f"{TMP_PREFIX}auditor", 0, 1)
    granted = set(db.session.execute(text(
        "SELECT permission_id FROM role_permission WHERE role_id=:r"), {"r": rid}).scalars().all())
    assert granted == {1, 8, 27, 30}
    dup = api.as_("admin").post("/api/system/roles", json={
        "roleCode": f"{TMP_PREFIX}auditor", "roleName": "重复"}).get_json()
    assert dup["code"] == 40900 and "已存在" in dup["message"], dup

    deleted = api.as_("admin").delete(f"/api/system/roles/{rid}").get_json()
    assert deleted["code"] == 0, deleted
    db.session.expire_all()
    assert db.session.get(Role, rid).deleted == 1
    assert db.session.execute(text(
        "SELECT COUNT(*) FROM role_permission WHERE role_id=:r"), {"r": rid}).scalar() == 0


def test_builtin_roles_cannot_be_deleted(api):
    """内置角色一律保护：pilot/finance 下面挂着种子账号，删掉等于把 RBAC 掏空。"""
    for code in ("pilot", "finance", "dispatcher"):
        rid = db.session.query(Role).filter_by(role_code=code).first().id
        body = api.as_("admin").delete(f"/api/system/roles/{rid}").get_json()
        assert body["code"] == 40300 and "内置角色" in body["message"], (code, body)
        db.session.expire_all()
        assert db.session.get(Role, rid).deleted == 0


def test_role_in_use_cannot_be_deleted(api, fresh_user):
    """非内置但还挂着账号：先解绑再删，否则账号会掉进"无角色"黑洞。"""
    created = api.as_("admin").post("/api/system/roles", json={
        "roleCode": f"{TMP_PREFIX}bound_role", "roleName": "临时·待删除",
        "roleScope": 2, "permissionIds": _sys_role_ids()}).get_json()
    assert created["code"] == 0, created
    rid = created["data"]["id"]
    u = fresh_user(f"{TMP_PREFIX}bound", "dispatcher")
    db.session.execute(user_role.insert().values(user_id=u.id, role_id=rid))
    db.session.commit()

    body = api.as_("admin").delete(f"/api/system/roles/{rid}").get_json()
    assert body["code"] == 40900 and "请先解绑" in body["message"], body
    db.session.expire_all()
    assert db.session.get(Role, rid).deleted == 0
    # 解绑之后就能删干净（授权集合一起清掉，本模块夹具会归还种子授权）
    db.session.execute(user_role.delete().where(user_role.c.role_id == rid))
    db.session.commit()
    assert api.as_("admin").delete(f"/api/system/roles/{rid}").get_json()["code"] == 0
    db.session.expire_all()
    assert db.session.get(Role, rid).deleted == 1


def test_missing_role_404(api):
    assert api.as_("admin").get("/api/system/roles/999999/permissions").get_json()["code"] == 40400
    assert api.as_("admin").put("/api/system/roles/999999/permissions",
                              json={"permissionIds": [1]}).get_json()["code"] == 40400
    assert api.as_("admin").delete("/api/system/roles/999999").get_json()["code"] == 40400


# ------------------------------------------------------------------
# G. 审计副作用：P6 修完之后 operation_log 才真的写进去
# ------------------------------------------------------------------
def test_account_operations_are_logged(api, fresh_user):
    u = fresh_user(f"{TMP_PREFIX}log", "dispatcher")
    before = db.session.execute(text("SELECT COUNT(*) FROM operation_log")).scalar()
    assert api.as_("admin").put(f"/api/system/users/{u.id}/status",
                              json={"status": 2}).get_json()["code"] == 0
    rows = db.session.execute(text(
        "SELECT method, request_url, status, username_snap FROM operation_log "
        "ORDER BY id DESC LIMIT 5")).mappings().all()
    assert len(rows) > before, f"操作日志没落库（P6 回归）：{rows}"
    hit = [r for r in rows if r["request_url"] == f"/api/system/users/{u.id}/status"]
    assert hit, rows
    assert hit[0]["status"] == 200, "HTTP 状态码写不进 operation_log.status（TINYINT 时代会 1264 溢出）"
    assert hit[0]["method"] == "PUT" and hit[0]["username_snap"] == "admin"
