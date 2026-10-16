"""每个角色进每个页面时"首屏那一发请求"的回归：菜单进得去、口打不开 = 缺陷。

这一类的来历都是真的：运维账号点进人员管理吃 403（没有 resource:qualification 却持有
resource:aircraft）、空管/财务打不开排班页（只给了写权限没给读权限）、财务点结算页 404。
test_perms_matrix.py 证的是"没有端点在裸奔"（声明层），它看不出"页面按菜单权限放行、
首屏请求却被接口权限挡下"这一层错位——因为那一发请求本身是分类正确的。
所以这里按前端的入口表逐条打一次：
  · 该角色能打开这一页 → 这一页 onMounted 会打的口必须 code=0；
  · 不该看到的 → 必须 40300（不是 200 的空数组，也不是 500）。
用 200 + 业务错误码当"通过"是最坑的假绿，所以断言只认 envelope 的 code。
"""
import pytest

from app.extensions import db
from app.models import Vertiport
from tests.conftest import SEED_PASSWORD  # noqa: F401  （登录密码口径留档）

# (账号键, 角色码, user_type, 说明) —— 演示库里有这些账号，测试库里用 fresh_user 现造
ROLE_ACCOUNTS = (
    ("atc", "air_traffic", 5, "空管"),
    ("fin", "finance", 3, "财务"),
    ("ops", "ops_maintainer", 6, "平台运维"),
    ("mnt", "merchant_maintainer", 3, "设备运维"),
)

# (账号, 页面, 首屏请求路径, 查询参数, 期望 code)
CASES = [
    # ---- 空管：空域台账 / 审批 / 监控 / 只读排班 ----
    ("admin", "空域台账", "/api/airspaces", {"all": "1"}, 0),
    ("atc", "空域台账", "/api/airspaces", {"all": "1"}, 0),
    ("atc", "电子围栏", "/api/geofences", {}, 0),
    ("atc", "计划审批", "/api/flight-plans", {"status": "1"}, 0),
    ("atc", "违规台账", "/api/aux/violations", {}, 0),
    ("atc", "实时告警", "/api/monitor/alerts", {}, 0),
    ("atc", "排班只读", "/api/aux/schedules", {}, 0),
    ("atc", "人员台账只读", "/api/resource/personnel", {}, 0),
    ("atc", "不该开结算页", "/api/settlement/bills", {}, 40300),
    ("atc", "不该开用户管理", "/api/system/users", {}, 40300),
    # ---- 财务：结算 / 报表 / 只读排班 ----
    ("fin", "账单", "/api/settlement/bills", {}, 0),
    ("fin", "支付流水", "/api/settlement/payments", {}, 0),
    ("fin", "报表中心", "/api/stats/overview", {}, 0),
    ("fin", "排班只读", "/api/aux/schedules", {}, 0),
    ("fin", "人员台账只读", "/api/resource/personnel", {}, 0),
    # 空域列表是 @jwt_required 的公开读（大屏/调度台/合规图层都要用，见 airspace.py 头部口径），
    # 财务 200 是设计，不是漏门；真正的写门在 airspace:draw / airspace:audit。
    ("fin", "空域图层（登录即可读）", "/api/airspaces", {}, 0),
    ("fin", "不该开资质台账", "/api/resource/qualifications", {}, 40300),
    ("fin", "不该开角色维护", "/api/system/roles", {}, 40300),
    # ---- 平台运维 / 设备运维 ----
    ("ops", "场站台账", "/api/resource/vertiports", {}, 0),
    ("ops", "飞行器台账", "/api/resource/aircraft", {}, 0),
    ("ops", "排班", "/api/aux/schedules", {}, 0),
    ("ops", "人员台账", "/api/resource/personnel", {}, 0),
    ("ops", "设备在线", "/api/monitor/devices", {}, 0),
    ("ops", "优惠券", "/api/aux/coupons", {}, 0),
    ("ops", "工单", "/api/aux/work-orders", {}, 0),
    ("ops", "应急", "/api/aux/emergencies", {}, 0),
    ("ops", "不该看违规台账", "/api/aux/violations", {}, 40300),
    ("mnt", "维保台账", "/api/aux/maintenance", {}, 0),
    ("mnt", "飞行器台账", "/api/resource/aircraft", {}, 0),
    ("mnt", "场站预约", "/api/resource/reservations", {}, 0),
    ("mnt", "不该开结算", "/api/settlement/bills", {}, 40300),
    # ---- 商家管理员（merchant_admin + dispatcher）----
    ("merchant", "订单列表", "/api/orders", {}, 0),
    ("merchant", "筛选元数据", "/api/orders/filter-options", {}, 0),
    ("merchant", "调度台资源", "/api/dispatch/resources", {}, 0),
    ("merchant", "我的券包", "/api/aux/coupons/mine", {}, 0),
    ("merchant", "账单", "/api/bills/payments", {}, 0),
    ("merchant", "入驻状态", "/api/system/merchants/mine", {}, 0),
    # 商家持有 resource:aircraft，正好落在资质台账的准入名单里（人员管理页进得来这一页），
    # 读到的行由 scope.merchant_scope 收窄成本商家主体 —— 这不是跨商家泄漏。
    ("merchant", "资质台账（本商家范围）", "/api/resource/qualifications", {}, 0),
    ("merchant", "不该开定时任务", "/api/aux/scheduler/jobs", {}, 40300),
    ("merchant", "不该开系统参数", "/api/aux/configs", {}, 40300),
    # ---- 机长：任务台 + 自助 ----
    ("pilot", "我的任务", "/api/flight-plans", {"mine": "true"}, 0),
    ("pilot", "空域图层", "/api/airspaces", {}, 0),
    ("pilot", "个人中心自助", "/api/resource/my-duty", {}, 0),
    ("pilot", "不该开资质台账", "/api/resource/qualifications", {}, 40300),
    ("pilot", "不该开调度台", "/api/dispatch/resources", {}, 40300),
    ("pilot", "不该开设备在线", "/api/monitor/devices", {}, 40300),
    # ---- 客户：门户四页 ----
    ("customer", "我的订单", "/api/orders", {}, 0),
    ("customer", "常用地址", "/api/aux/addresses", {}, 0),
    ("customer", "领券中心", "/api/aux/coupons", {"scope": "available"}, 0),
    ("customer", "评价投诉", "/api/aux/feedback", {}, 0),
    ("customer", "我的账单", "/api/bills/payments", {}, 0),
    ("customer", "站内信", "/api/aux/messages", {}, 0),
    ("customer", "不该看报表", "/api/stats/overview", {}, 40300),
    ("customer", "不该看用户管理", "/api/system/users", {}, 40300),
    ("customer", "不该看人员台账", "/api/resource/personnel", {}, 40300),
    # ---- 管理员：全模块首屏 ----
    ("admin", "统计报表", "/api/stats/overview", {}, 0),
    ("admin", "商家口径", "/api/stats/merchant", {}, 0),
    ("admin", "空管口径", "/api/stats/airspace", {}, 0),
    ("admin", "自定义报表字段", "/api/reports/fields", {}, 0),
    ("admin", "定时任务", "/api/aux/scheduler/jobs", {}, 0),
    ("admin", "系统日志", "/api/aux/logs", {}, 0),
    ("admin", "参数中心", "/api/aux/configs", {}, 0),
    ("admin", "角色权限", "/api/system/roles", {}, 0),
    ("admin", "仿真场景", "/api/simulator/scenarios", {}, 0),
    ("admin", "资源空域监管", "/api/aux/report", {}, 0),
    ("admin", "轨迹回放", "/api/monitor/tracks", {}, 0),
    ("admin", "场站列表", "/api/resource/vertiports", {}, 0),
    ("admin", "入驻审核", "/api/system/merchants", {}, 0),
]

IDS = {"admin", "merchant", "customer", "pilot"}


def _purge(username: str) -> None:
    from app.models.rbac import user_role
    uid = db.session.execute(
        db.text("SELECT id FROM users WHERE username=:n"), {"n": username}).scalar()
    if not uid:
        return
    db.session.execute(user_role.delete().where(user_role.c.user_id == uid))
    db.session.execute(db.text("DELETE FROM users WHERE id=:i"), {"i": uid})
    db.session.commit()


@pytest.fixture(scope="module")
def role_users(app):
    """这一组角色账号整个模块共用：它们是"环境"，不是某个用例的现场。

    测试库的种子账号只有 5 个（admin/merchant/customer/pilot/pilot02），演示库里的
    空管/财务/运维/设备运维是造数脚本现造的 —— 用例不能依赖造数脚本，所以在这里按
    角色码把账号补出来，跑完统一删掉。

    建号与删号各用一个 app_context，中间不挂着 session：模块级夹具要是把上下文横跨
    yield 开着，这几个角色/账号表上的元数据锁会一直攥到最后一个用例结束。
    """
    from app.models import User
    from app.models.rbac import user_role

    def ensure() -> dict[str, int]:
        import bcrypt
        created: dict[str, int] = {}
        with app.app_context():
            for key, role_code, user_type, _label in ROLE_ACCOUNTS:
                username = f"pg_{key}"
                uid = db.session.execute(
                    db.text("SELECT id FROM users WHERE username=:n"), {"n": username}).scalar()
                if uid is not None:
                    # 上一次跑到一半崩在这里会留下同名账号：先删再建，夹具才是幂等的
                    _purge(username)
                u = User(username=username,                         password_hash=bcrypt.hashpw(SEED_PASSWORD.encode(),
                                                     bcrypt.gensalt(10)).decode(),
                         user_type=user_type, real_name=f"首屏点验·{role_code}",
                         phone_cipher=f"T:{username}", phone_digest=f"digest:{username}",
                         merchant_id=1, status=1, realname_status=2)
                db.session.add(u)
                db.session.flush()
                rid = db.session.execute(
                    db.text("SELECT id FROM role WHERE role_code=:c"), {"c": role_code}).scalar()
                assert rid, f"角色码 {role_code} 不在种子里"
                db.session.execute(user_role.insert().values(user_id=u.id, role_id=rid))
                db.session.commit()
                created[key] = u.id
                OWNED.append(u.id)
        return created

    OWNED: list[int] = []
    ids = ensure()
    yield ids
    with app.app_context():
        for uid in OWNED:
            db.session.execute(user_role.delete().where(user_role.c.user_id == uid))
            db.session.execute(db.text("DELETE FROM users WHERE id=:i"), {"i": uid})
        db.session.commit()


@pytest.fixture()
def who(api, role_users, user_ids):
    def as_(key: str):
        return api.as_(key) if key in IDS else api.as_id(role_users[key])
    return as_


@pytest.mark.parametrize("account,page,path,params,expect", CASES,
                         ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_page_first_screen_call(who, account, page, path, params, expect):
    body = who(account).get(path, query_string=params).get_json()
    assert body is not None, f"{account} 进「{page}」：{path} 没返回 JSON"
    assert body["code"] == expect, (
        f"{account} 进「{page}」打 {path} 期望 code={expect}，"
        f"实得 {body['code']}：{body['message']}")


def test_no_page_in_the_matrix_is_left_unchecked(api, user_ids):
    """这张入口表至少要有 40 条、覆盖到全部 9 类角色，否则它自己就是个假绿。

    用例数会随页面增减，但"每个角色都真的打过"这条底线不能塌。
    """
    assert len(CASES) >= 40
    assert {c[0] for c in CASES} == IDS | {k for k, *_ in ROLE_ACCOUNTS}


def test_seed_stations_are_usable(app):
    """点验依赖两个开放场站（首屏的空域/场站图层都读它们）——少一个这条文件就该红。"""
    with app.app_context():
        n = (db.session.query(Vertiport)
             .filter(Vertiport.deleted == 0, Vertiport.status == 1).count())
    assert n >= 2, f"开放场站只剩 {n} 个，首屏点验的前提没了"
