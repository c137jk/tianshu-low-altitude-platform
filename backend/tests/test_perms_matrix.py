"""P0-1 权限门禁回归：没有端点在裸奔，而且 403 恰好等于"缺权限点"。

差距分析的原始缺口是"93 个端点只有 12 个挂了权限点"。这里用四组互补断言把它钉死：

  1. 匿名扫描：url_map 里每个 /api/* 方法组合，不带 token 必须 401（业务码 40100），
     只有 5 个公开端点例外（登录 / 注册 / 健康检查 / 公告 / 大屏脱敏读）。
  2. 静态归类：每个端点必须落在"公开 / 登录自助 / 权限门禁"三类之一。
     以后有人新加一个只写 @jwt_required() 的接口、又没人记得补用例，这条会直接失败，
     不依赖 reviewer 肉眼扫 diff。归类依据是 decorators 里挂的 auth_kind / req_perms。
  3. 角色 × 门禁矩阵：缺权限点 ⇒ 必 403 且 message 带上权限点；有权限点 ⇒ 必不 403。
     写接口（POST/PUT/DELETE）在"有权限"这一侧不执行，免得把真实业务写进测试库。
  4. 门禁先于业务：越权请求 403 之后，库里/内存里不许留下任何痕迹。

跑法：TEST_DB_RESET=0 .venv/Scripts/python.exe -m pytest tests/test_perms_matrix.py -q
"""
import re

import pytest
from sqlalchemy import text

from app.extensions import db

# ------------------------------------------------------------------
# 端点归类（改这里等于改安全边界，请连理由一起写清楚）
# ------------------------------------------------------------------
# 公开：不需要登录。大屏是"可选登录"，匿名自动走脱敏分支（见 test_screen_masking.py）。
PUBLIC = {
    "auth.login",              # 账号密码登录
    "auth.register",           # 客户自助注册
    "health.health",           # 探活
    "aux.art_public",          # 对外公告/帮助
    "monitor.screen",          # 投屏大屏（匿名脱敏）
}

# 登录自助：只要求登录态，数据范围由本人 user_id 或 scope.merchant_scope 收窄。
SELF = {
    "auth.me", "auth.menus", "auth.logout", "auth.change_password", "auth.refresh",
    # 客户下单链路 + 运营接单/拒单（服务层里校验 user_type 与商家归属）
    "orders.estimate", "orders.create_order",
    "orders.pay_order", "orders.cancel_order", "orders.confirm_order",
    "orders.accept_order", "orders.reject_order",
    "orders.bill_payments", "orders.bill_settlements",
    # 订单三条读口：客户按登录态放行（customer 一个权限点都没有，挂装饰器会把门户整片打死），
    # 员工侧在视图里补 order:list，否则平台岗位/机长直接敲 GET /api/orders 就是全量客户台账。
    # 见 orders._reader 与 test_order_query.test_order_read_needs_order_list_for_staff。
    "orders.list_orders", "orders.order_detail", "orders.order_filter_options",
    # 计划/空域读：按身份与商家过滤
    "dispatch.list_plans", "dispatch.plan_detail", "airspace.list_airspaces",
    # 围栏图层：平台看全量，商家看本商家 + 全局围栏（视图内过滤，同 list_airspaces 口径）
    "airspace.list_geofences",
    # 违规通知书：视图内校验"本商家 或 被处罚机长本人"，机长没有运营权限也能读发给自己的通知书
    "aux.vio_notice",
    # 客户侧资料与自助
    "aux.addr_list", "aux.addr_save", "aux.addr_default", "aux.addr_del",
    "aux.fb_list", "aux.fb_create", "aux.wo_create",
    "aux.msg_list", "aux.msg_read", "aux.msg_read_all",
    "aux.realname_status", "aux.realname_submit", "aux.cp_list",
    # 券包：领券/查自己的券都按本人 user_id 收窄，领券限额与库存由 SQL 条件自增兜底
    "aux.cp_claim", "aux.cp_mine",
    # 商家入驻：申请人还没商家身份，只有"自己那一条"可读；审核端走 merchant:audit 权限门禁
    "system.merchant_apply", "system.merchant_mine",
    "system.role_options",
    # 我的资质与值班：视图里写死 subject_id/user_id = 本人，机长没有任何 resource:* 也要能查自己的证
    "resource.my_duty",
    # 本人自助上传资质：同样写死主体=本人，且新建一律落 0 待审核，不给任何台账权限
    "resource.upload_my_qualification",
    # 本人读自己那份原件：台账那道门认的是 qualification/system:user/schedule/aircraft，
    # 机长四条全无，装饰器会先于视图拦掉——所以自助读必须单独一条口。
    "resource.my_qualification_file",
}

# 身份清单：种子账号直接复用，平台岗位用 fresh_user 现造
# （种子里只有 admin/merchant/customer/pilot/pilot02，空管、财务、运维都得现场建）。
FRESH_IDENTITIES = (
    # (username, role_code, user_type, merchant_id)
    ("u_traffic", "air_traffic", 5, None),
    ("u_finance", "finance", 3, 1),
    ("u_ops", "ops_maintainer", 6, None),
    ("u_maintainer", "merchant_maintainer", 3, 1),
    ("u_permless", None, 3, 1),        # 无角色：任何权限门禁都必须拦住
)
SEED_IDENTITIES = ("admin", "merchant", "pilot", "customer")

GATE_KEYWORD = "没有操作权限"


def _api_rules(app):
    """[(method, rule_path, endpoint)] —— 排除 Flask 自动附加的 OPTIONS/HEAD。"""
    out = []
    for rule in app.url_map.iter_rules():
        path = str(rule)
        if not path.startswith("/api/"):
            continue
        for method in sorted(rule.methods - {"OPTIONS", "HEAD"}):
            out.append((method, path, rule.endpoint))
    return out


def _concrete(path: str) -> str:
    """<int:uid> / <name> 一律填 1：门禁挡在视图之前，路径参数存不存在不影响判定。"""
    return re.sub(r"<(?:[^:]+:)?[^>]+>", "1", path)


def _send(client_or_wrapper, method: str, url: str, **kw):
    fn = getattr(client_or_wrapper, method.lower(), None)
    if fn is None:                                        # 例如 PATCH：走 open() 兜底
        return client_or_wrapper.open(url, method=method, **kw)
    return fn(url, **kw)


def _is_gate_denied(resp, *perm_codes) -> bool:
    """只认"权限门禁 403"，别和业务层 403（跨商家、不能操作自己等）混为一谈。"""
    if resp.status_code != 403:
        return False
    body = resp.get_json() or {}
    message = body.get("message") or ""
    if body.get("code") != 40300 or GATE_KEYWORD not in message:
        return False
    if not perm_codes:
        return True
    return any(c in message for c in perm_codes)


def _perms_of(uid: int) -> set[str]:
    """直接从库里查角色授权，不复用 User.has_perm —— 那等于拿被测代码验证被测代码。"""
    return set(db.session.execute(text(
        "SELECT DISTINCT p.perm_code FROM permission p "
        "JOIN role_permission rp ON rp.permission_id = p.id "
        "JOIN user_role ur ON ur.role_id = rp.role_id WHERE ur.user_id = :u"),
        {"u": uid}).scalars().all())


def _role_codes_of(username: str) -> set[str]:
    return set(db.session.execute(text(
        "SELECT r.role_code FROM role r JOIN user_role ur ON ur.role_id = r.id "
        "JOIN users u ON u.id = ur.user_id WHERE u.username = :n"),
        {"n": username}).scalars().all())


@pytest.fixture()
def identities(app, api, fresh_user, user_ids):
    """{username: (请求助手, 权限点集合, 是否 platform_admin)}。"""
    out = {}
    for name in SEED_IDENTITIES:
        out[name] = (api.as_(name), _perms_of(user_ids[name]),
                     "platform_admin" in _role_codes_of(name))
    for name, role_code, user_type, mid in FRESH_IDENTITIES:
        u = fresh_user(name, role_code, user_type=user_type, merchant_id=mid)
        out[name] = (api.as_id(u.id), _perms_of(u.id), role_code == "platform_admin")
    return out


@pytest.fixture()
def gated(app):
    """[(method, url, endpoint, 需要的权限点)] —— 挂了权限点的服务端门禁端点。"""
    rows = []
    for method, path, ep in _api_rules(app):
        view = app.view_functions[ep]
        if getattr(view, "auth_kind", None) == "perm":
            rows.append((method, _concrete(path), ep, tuple(getattr(view, "req_perms", ()))))
    assert rows, "一个权限门禁端点都没扫到，decorators 的元信息标记掉了"
    return rows


def _seed_resources_intact():
    """门禁扫描必须是"纯拒绝"：跑完之后种子场站/飞行器还得在原位。

    这条断言是踩过坑加的 —— 一旦某个写接口在"有权限"这一侧被执行，
    它会真的改掉现场（例如删掉 VP1），后续所有建链路的用例都会以
    "起降场站必须为开放状态"的形式失败，很难归因。
    """
    alive = db.session.execute(text(
        "SELECT COUNT(*) FROM vertiport WHERE id IN (1,2) AND deleted = 0 AND status = 1")).scalar()
    planes = db.session.execute(text(
        "SELECT COUNT(*) FROM aircraft WHERE id IN (1,2) AND deleted = 0")).scalar()
    assert alive == 2 and planes == 2, f"扫描用例改动了种子资源：场站 {alive}/2、飞行器 {planes}/2"


# ------------------------------------------------------------------
# 1. 匿名扫描
# ------------------------------------------------------------------
def test_identity_does_not_leak_across_requests(api, user_ids):
    """连续切身份时，current_user() 必须跟着 token 走。

    flask.g 挂在 app context 而不是 request 上：只要外层已经 push 过上下文
    （测试夹具、CLI 里发内部请求、中间件包住多个请求），"g 里有值就直接用"
    会把第一个访问者（admin）的身份借给后面每一个请求。这是最恶劣的一种越权，
    因为它只会让权限用例"全绿"，所以单独钉一条断言。
    """
    for name in ("admin", "customer", "pilot", "merchant", "admin", "customer"):
        body = api.as_(name).get("/api/auth/me").get_json()
        info = body.get("data", {}).get("userInfo", {})
        assert info.get("id") == user_ids[name], f"{name} 的请求被解析成了 {info}"
        assert info.get("username") == name, f"{name} 的身份串了：{info.get('username')}"


def test_anonymous_requests_are_rejected_everywhere(app, client):
    """除公开白名单外，/api/* 一律 401 + 业务码 40100（统一信封，前端拦截器好处理）。"""
    leaks, bad_envelope, public_broken = [], [], []
    checked = 0
    for method, path, endpoint in _api_rules(app):
        url = _concrete(path)
        body = {} if method in ("POST", "PUT", "PATCH") else None
        resp = _send(client, method, url, json=body) if body is not None else _send(client, method, url)
        checked += 1
        if endpoint in PUBLIC:
            if resp.status_code == 401:
                public_broken.append(f"{method} {url} 是公开端点却要求登录")
            continue
        if resp.status_code != 401:
            leaks.append(f"{method} {url} -> {resp.status_code} "
                         f"{(resp.get_json() or {}).get('message')}")
        elif (resp.get_json() or {}).get("code") != 40100:
            bad_envelope.append(f"{method} {url} -> code={(resp.get_json() or {}).get('code')}")

    assert checked >= 100, f"只扫到 {checked} 个端点方法组合，先确认蓝图是否全部注册"
    assert not public_broken, f"公开端点被误加鉴权：{public_broken}"
    assert not leaks, f"存在未登录即可访问的端点（P0-1 回退）：{leaks}"
    assert not bad_envelope, f"401 信封不统一：{bad_envelope}"


def test_public_surface_is_exactly_five(app, client):
    """公开面要能一口说出有几个：多一个就是新的数据泄露口，少一个说明功能被打挂。"""
    found = {ep for _m, _p, ep in _api_rules(app) if ep in PUBLIC}
    assert found == PUBLIC, f"公开端点与白名单不一致，缺：{sorted(PUBLIC - found)}"
    extra = []
    for method, path, ep in _api_rules(app):
        if ep in PUBLIC:
            continue
        body = {} if method in ("POST", "PUT", "PATCH") else None
        resp = _send(client, method, _concrete(path), json=body) if body else _send(client, method, _concrete(path))
        if resp.status_code != 401:
            extra.append(f"{method} {path}")
    assert not extra, f"这些端点匿名可读，请归类进 PUBLIC 或补鉴权：{extra}"


# ------------------------------------------------------------------
# 2. 静态归类：不留裸端点
# ------------------------------------------------------------------
def test_every_endpoint_is_classified(app):
    """每个 /api/* 端点要么公开、要么"登录自助"、要么挂权限点，三类之外不许存在。"""
    seen, unclassified = set(), []
    for _method, _path, ep in _api_rules(app):
        seen.add(ep)
        if ep in PUBLIC or ep in SELF:
            continue
        if getattr(app.view_functions[ep], "auth_kind", None) == "perm":
            continue
        unclassified.append(f"{ep} ({_path})")
    assert not unclassified, (
        f"这些端点只有登录态校验，既不在自助白名单也没挂权限点：{sorted(unclassified)}")
    stale = (PUBLIC | SELF) - seen
    assert not stale, f"白名单里存在已下线的端点，改名或删除接口后请同步：{sorted(stale)}"


def test_guards_are_not_misconfigured(app, gated):
    """挂了的权限点必须是库里真实存在的 permission 行，否则永远 403（自锁）。"""
    known = set(db.session.execute(
        text("SELECT perm_code FROM permission WHERE deleted = 0")).scalars().all())
    assert len(known) >= 40, f"权限点行数异常：{len(known)}，检查迁移脚本是否跑全"
    unknown = sorted({c for _m, _u, _e, req in gated for c in req} - known)
    assert not unknown, f"门禁引用了不存在的权限点：{unknown}"


# ------------------------------------------------------------------
# 3. 角色 × 门禁矩阵
# ------------------------------------------------------------------
def test_matrix_403_matches_role_permissions(client, identities, gated):
    """缺权限点 ⇒ 必 403 且提示里带权限点名；有权限点 ⇒ 必不被门禁拦。

    写接口在"有权限"这一侧直接跳过（真实执行会往测试库写业务数据），
    它们的正向链路由 test_state_machine.py 覆盖，越权不落库由第 4 组断言覆盖。
    """
    bad = []
    for username, (caller, perms, is_admin) in sorted(identities.items()):
        for method, url, _ep, req in gated:
            allowed = is_admin or bool(set(req) & perms)
            if method != "GET" and allowed:
                continue
            resp = caller.req(method.lower(), url,
                              json={} if method != "GET" else None)
            denied = _is_gate_denied(resp, *req)
            if allowed and denied:
                bad.append(f"[{username}] 持有 {req} 却被门禁拦：{method} {url} -> "
                           f"{(resp.get_json() or {}).get('message')}")
            if not allowed and not denied:
                bad.append(f"[{username}] 无 {req} 却没被拦：{method} {url} -> "
                           f"{resp.status_code} {(resp.get_json() or {}).get('message')}")
    assert not bad, "权限矩阵与角色授权不一致：\n  " + "\n  ".join(bad[:25])
    _seed_resources_intact()


def test_matrix_covers_every_permission_point(identities, gated):
    """矩阵要有代表性：每个权限点都至少被一个身份拒绝、被一个身份放行过。"""
    all_perms = {c for _m, _u, _e, req in gated for c in req}
    denied_seen, allowed_seen = set(), set()
    for _name, (_caller, perms, is_admin) in identities.items():
        for _m, _u, _e, req in gated:
            for c in req:
                if is_admin or c in perms:
                    allowed_seen.add(c)
                else:
                    denied_seen.add(c)
    assert all_perms, "没扫到任何权限点"
    assert denied_seen == all_perms, f"这些权限点从没被拒绝过，用例没跑到：{sorted(all_perms - denied_seen)}"
    assert allowed_seen == all_perms, f"这些权限点没有任何身份持有：{sorted(all_perms - allowed_seen)}"
    assert len(all_perms) >= 20, f"门禁权限点只有 {len(all_perms)} 个，收口疑似回退"


def test_permless_account_blocked_on_every_gated_endpoint(identities, gated):
    """无角色账号（新建/被清空权限）在每一个门禁端点前都必须是 403。"""
    caller, perms, is_admin = identities["u_permless"]
    assert not perms and not is_admin, "u_permless 不该有任何权限，夹具配错了"
    for method, url, _ep, req in gated:
        resp = caller.req(method.lower(), url, json={} if method != "GET" else None)
        assert _is_gate_denied(resp, *req), \
            f"无权限账号可访问 {method} {url}：{resp.status_code} {resp.get_json()}"


def test_each_module_has_a_gate(gated):
    """八大业务域都得有门禁端点，防止某个蓝图整块退回"登录即可"。"""
    prefixes = {"system.", "monitor.", "resource.", "settlement.", "dispatch.",
                "airspace.", "aux.", "simulator.", "demo."}
    have = {ep.split(".", 1)[0] + "." for _m, _u, ep, _r in gated}
    missing = sorted(prefixes - have)
    assert not missing, f"这些蓝图没有任何权限门禁：{missing}"


# ------------------------------------------------------------------
# 4. 门禁先于业务：越权请求不许留下痕迹
# ------------------------------------------------------------------
def test_denied_write_does_not_touch_database(identities):
    """机长拿完全合法的报文去建账号：必须 403，而且 users 表一行都不许多。

    这条证明 403 不是"跑完业务再补个错误码"，装饰器确实挡在视图之前。
    """
    from app.models import User

    before = db.session.query(User).count()
    caller, _perms, _admin = identities["pilot"]
    resp = caller.post("/api/system/users", json={
        "username": "pilot_made_one", "password": "Admin@123",
        "roleCode": "dispatcher", "phone": "13900007777", "realName": "机长建的人"})
    assert _is_gate_denied(resp, "system:user"), resp.get_json()
    db.session.expire_all()
    assert db.session.query(User).count() == before, "越权请求把数据写进库了"
    assert db.session.query(User).filter_by(username="pilot_made_one").first() is None


def test_denied_simulator_injection_is_not_applied(identities, make_flight):
    """商家/机长不能碰仿真开关：403 之后在飞计划的场景标记必须还是 normal。

    设计07 把场景注入限定 demo:run（平台运维/管理员），因为注入会改变真实在飞态势。
    """
    from app.services import flight_simulator

    _order, plan = make_flight("flying")
    assert flight_simulator.scenario_for(plan.id) == "normal"
    for who in ("merchant", "pilot", "customer"):
        caller, _p, _a = identities[who]
        resp = caller.post("/api/simulator/inject", json={"scenario": "no_fly"})
        assert _is_gate_denied(resp, "demo:run"), (who, resp.status_code, resp.get_json())
    assert flight_simulator.scenario_for(plan.id) == "normal", "场景被越权请求改掉了"


def test_denied_role_perm_update_keeps_role_intact(identities):
    """调度员不能改角色权限；403 之后角色的权限集合不许变化（防"先写后报错"）。"""
    dispatcher = db.session.execute(text(
        "SELECT r.id FROM role r WHERE r.role_code='dispatcher'")).scalar()
    role_perms_sql = ("SELECT p.perm_code FROM permission p "
                      "JOIN role_permission rp ON rp.permission_id = p.id "
                      "WHERE rp.role_id = :r")
    before = set(db.session.execute(text(role_perms_sql), {"r": dispatcher}).scalars().all())
    caller, perms, _admin = identities["merchant"]      # 商家管理员没有 system:role
    assert "system:role" not in perms
    resp = caller.put(f"/api/system/roles/{dispatcher}/permissions",
                      json={"permissionIds": [1, 2, 3, 27, 28]})
    assert _is_gate_denied(resp, "system:role"), resp.get_json()
    after = set(db.session.execute(text(
        "SELECT p.perm_code FROM permission p JOIN role_permission rp ON rp.permission_id=p.id "
        "WHERE rp.role_id=:r"), {"r": dispatcher}).scalars().all())
    assert after == before, "越权请求改掉了角色授权"


def test_customer_cannot_reach_platform_reads(identities):
    """客户身份：平台侧读接口一律 403，自己的自助读接口不受影响。"""
    caller, _p, _a = identities["customer"]
    for url in ("/api/system/users", "/api/system/roles", "/api/monitor/alerts",
                "/api/monitor/tracks", "/api/aux/logs", "/api/aux/articles",
                "/api/aux/report", "/api/settlement/bills", "/api/simulator/status",
                "/api/dispatch/resources", "/api/resource/aircraft",
                "/api/aux/violations", "/api/aux/work-orders"):
        resp = caller.get(url)
        assert _is_gate_denied(resp), f"客户可越权读取 {url}：{resp.status_code} {resp.get_json()}"
    assert caller.get("/api/orders").get_json()["code"] == 0, "客户自己的订单列表被误伤"
    assert caller.get("/api/auth/me").get_json()["code"] == 0, "自助接口被误伤"


def test_frozen_account_is_locked_out(api, fresh_user):
    """冻结账号（status=2）：token 仍然有效，也要被 perm_required 的状态校验挡下。"""
    u = fresh_user("u_frozen", "dispatcher", user_type=3, merchant_id=1, status=2)
    resp = api.as_id(u.id).get("/api/dispatch/resources")
    assert resp.status_code == 403
    assert "账号状态异常" in resp.get_json()["message"]


def test_deleted_account_token_is_unauthorized(api, fresh_user):
    """注销（status=3 + deleted=1）后 current_user() 查不到人 ⇒ 401，不能是 200。"""
    u = fresh_user("u_gone", "dispatcher", user_type=3, merchant_id=1,
                   status=3, deleted=1)
    resp = api.as_id(u.id).get("/api/dispatch/resources")
    assert resp.status_code == 401, f"注销账号仍可用：{resp.status_code} {resp.get_json()}"


@pytest.mark.parametrize("username,urls", [
    ("admin", ["/api/system/users", "/api/system/roles", "/api/aux/logs",
               "/api/aux/configs", "/api/aux/report", "/api/simulator/status",
               "/api/settlement/bills", "/api/monitor/alerts", "/api/monitor/tracks",
               "/api/resource/aircraft", "/api/aux/violations", "/api/aux/schedules",
               # 拆模块后低空资源侧的新读口
               "/api/resource/personnel", "/api/resource/qualifications?subjectType=2",
               "/api/resource/merchants", "/api/aux/maintenance"]),
    ("merchant", ["/api/dispatch/resources", "/api/system/users", "/api/aux/maintenance",
                  "/api/aux/schedules", "/api/aux/work-orders", "/api/settlement/bills",
                  "/api/resource/personnel", "/api/resource/qualifications"]),
    ("pilot", ["/api/monitor/alerts",
               # monitor:track 有，但回放按行裁（本商家 / 本人执飞）⇒ 行级口径在
               # test_monitor_replay.test_track_replay_is_row_scoped 里测，这个用例只管门
               "/api/monitor/tracks",
               # 机长没有任何 resource:*，但资质到期预警发给他本人 ⇒ 自助口必须通
               "/api/resource/my-duty"]),
    ("u_finance", ["/api/settlement/bills", "/api/resource/my-duty",
                   # resource:schedule:view（只读排班岗）：看得到班表与归属，改不动
                   "/api/aux/schedules", "/api/resource/personnel"]),
    ("u_traffic", ["/api/aux/violations", "/api/aux/report", "/api/monitor/alerts",
                   # 需求 4.2.4 空域合规：空管有一张图/台账/围栏，但没有资源台账
                   "/api/airspaces?all=1", "/api/geofences", "/api/geofences/owners",
                   "/api/resource/my-duty",
                   # 同上，且空管是平台身份 ⇒ 全区班表（数据范围由 scope 收，不由这个用例收）
                   "/api/aux/schedules", "/api/resource/personnel"]),
    ("u_ops", ["/api/aux/logs", "/api/aux/configs", "/api/simulator/status",
               "/api/resource/aircraft", "/api/aux/schedules", "/api/resource/merchants",
               "/api/resource/personnel",
               # 只有 resource:schedule 也读得到资质台账（读 ≠ 复审）
               "/api/resource/qualifications", "/api/resource/my-duty"]),
    ("u_maintainer", ["/api/aux/maintenance", "/api/resource/aircraft",
                      "/api/aux/schedules", "/api/resource/personnel",
                      "/api/resource/qualifications"]),
])
def test_permitted_reads_actually_work(app, identities, username, urls):
    """放行侧不能只看"没 403"：真正有权限的人必须拿到 code=0 的数据，
    否则门禁补对了、接口却挂了也是白搭（admin 用种子身份，其余含 fresh 账号）。"""
    if username not in identities:
        pytest.skip("该身份由其它用例创建")
    caller, _perms, _admin = identities[username]
    for url in urls:
        body = caller.get(url).get_json()
        assert body.get("code") == 0, f"{username} 读 {url} 失败：{body}"
