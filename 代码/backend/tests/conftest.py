"""pytest 公共夹具：整套测试跑在独立库 tianshu_test 上，绝不碰演示库。

为什么这样搭：
  - 结构 + 种子数据直接复用 sql/tianshu-ddl-v1.sql（它本身就是"先 DROP 再 CREATE"的幂等脚本），
    只把库名换成测试库。测试里另写一套建表语句迟早会和线上漂移，那就失去意义了。
  - 仿真器后台线程一律关闭（FLASK_SIM_DISABLED=1）：需要推进飞行就显式调
    flight_simulator.tick() / dispatch_service.finish_landing()，测试才是确定性的，
    也不会有个线程在背后往测试库里乱写。
  - 服务层状态机用例直接调 service；权限门禁用例走 HTTP（test_client），
    两类断言各管一段，出问题能一眼知道是业务规则坏了还是门禁坏了。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 必须在 import app 之前设置：flight_simulator.start() 会读这个开关
os.environ.setdefault("FLASK_SIM_DISABLED", "1")

from sqlalchemy import create_engine, text  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db as _db  # noqa: E402

db = _db  # 夹具里直接写 db.session，和服务层代码用同一个对象

TEST_DB = os.getenv("TEST_DB_NAME", "tianshu_test")
DDL_FILE = ROOT / "sql" / "tianshu-ddl-v1.sql"
SEED_PASSWORD = os.getenv("TEST_SEED_PASSWORD", "Admin@123")


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{Config.DB_USER}:{Config.DB_PASSWORD}@{Config.DB_HOST}:{Config.DB_PORT}"
        f"/{TEST_DB}?charset=utf8mb4"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300,
                                 "pool_size": 3, "max_overflow": 3}
    # 资质附件落盘的位置：整场测试用一个临时目录，跑完整套删掉（见 _upload_sandbox）。
    # 不指回 backend/instance/uploads —— 那会把测试产物堆进真实运行目录。
    UPLOAD_ROOT = tempfile.mkdtemp(prefix="tianshu_test_uploads_")


# ------------------------------------------------------------------
# 建库：MySQL 没有 create_if_exists 的 ORM 快捷方式，直连 server 执行
# ------------------------------------------------------------------
def _server_uri() -> str:
    return (f"mysql+pymysql://{Config.DB_USER}:{Config.DB_PASSWORD}@"
            f"{Config.DB_HOST}:{Config.DB_PORT}/?charset=utf8mb4")


def _split_statements(sql_text: str) -> list[str]:
    """按引号感知的扫描切语句。

    不能简单按行丢注释：DDL 里有 `(9,10),(9,11);    -- 客户` 这种"行尾注释"，
    丢整行会把语句结尾的分号一起吃掉，下一条 INSERT 就被并进来了。
    """
    out, buf, i, n = [], [], 0, len(sql_text)
    quote = ""
    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:          # 转义字符整体吃掉
                buf.append(sql_text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-":               # 行注释
            while i < n and sql_text[i] != "\n":
                i += 1
            continue
        if ch == "#":                              # MySQL 井号注释
            while i < n and sql_text[i] != "\n":
                i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _exec_script(conn, sql_text: str, tag: str) -> int:
    """在一个连接上顺序执行脚本里的每条语句（PREPARE/EXECUTE 是会话级的，不能换连接）。"""
    cur = conn.connection.cursor()
    count = 0
    for stmt in _split_statements(sql_text):
        try:
            cur.execute(stmt)
            count += 1
        except Exception as e:
            cur.close()
            raise RuntimeError(f"{tag} 第 {count + 1} 条语句失败：\n  {stmt[:160]}\n  -> {e}") from e
    cur.close()
    return count


def bootstrap_database(force: bool | None = None) -> None:
    """建库 + 灌 DDL（角色/权限/演示账号/场站/飞机/空域种子）+ 按文件名顺序跑迁移脚本。

    迁移必须一起跑：线上 permission 有 41 行，比 DDL 主文件多 8 个后补权限点
    （demo:run / system:article / report:view 等），漏掉的话权限用例就是假环境。
    """
    if force is None:
        force = os.getenv("TEST_DB_RESET", "1") == "1"
    srv = create_engine(_server_uri(), isolation_level="AUTOCOMMIT")
    with srv.connect() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{TEST_DB}` "
                             f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
    srv.dispose()

    eng = create_engine(TestConfig.SQLALCHEMY_DATABASE_URI, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        ready = 0
        try:
            ready = conn.exec_driver_sql("SELECT COUNT(*) FROM role").scalar() or 0
        except Exception:
            ready = 0
        if ready and not force:
            eng.dispose()
            return
        ddl = DDL_FILE.read_text(encoding="utf-8").replace("tianshu_utms", TEST_DB)
        _exec_script(conn, ddl, "DDL 主脚本")
        for mig in sorted((ROOT / "sql" / "migrations").glob("*.sql")):
            _exec_script(conn, mig.read_text(encoding="utf-8"), f"迁移 {mig.name}")
    eng.dispose()


@pytest.fixture(scope="session")
def app():
    bootstrap_database()
    application = create_app(TestConfig)
    yield application
    # 资质附件用例落的文件跟着这一场一起清掉：它们和 _truncate 清的表是同一类东西
    # （用例造的现场），留在临时目录里只会越堆越多。
    shutil.rmtree(TestConfig.UPLOAD_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def engine(app):
    with app.app_context():
        yield _db.engine


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def app_ctx(app):
    """整个用例共用一个 app_context。

    Flask-SQLAlchemy 的 session 作用域是 app_context，夹具各自再套一层
    就会出现"同一个用例里两条 session"：A 提交完 B 还停在旧快照上，
    而且清场时 remove() 只会清掉自己那层，留下别人持有的元数据锁。

    顺带记住一件事：Flask 3 的 RequestContext.push() 发现栈顶已经是同一个 app 的
    上下文就不再新开（flask/ctx.py），所以 test_client 发出的请求和用例本身
    共用同一条 session、同一个 g。两个直接影响：
      - 用例里改数据别再 `with app.app_context()`，直接在当前上下文改并 commit，
        否则请求侧的身份表里躺着旧对象，读到的还是改之前的值（假通过）。
      - 同一个用例里换身份发请求，g 不会自动重置；current_user() 的缓存键带上了
        JWT sub，就是为了让"上一个访问者的身份"不会漏给下一个。
    """
    ctx = app.app_context()
    ctx.push()
    yield ctx
    _db.session.remove()
    ctx.pop()


@pytest.fixture()
def session(app_ctx):
    """服务层用例直接用这条 session（和 make_flight 用的是同一个对象）。"""
    return _db.session


# ------------------------------------------------------------------
# 鉴权：token 直接签（等价于登录后的态），另外提供真实登录通道
# ------------------------------------------------------------------
@pytest.fixture(scope="session")
def user_ids(app):
    with app.app_context():
        rows = _db.session.execute(text("SELECT id, username FROM users")).all()
        _db.session.remove()      # 会话级夹具自己开的事务要收掉，否则后面 DDL 等锁
    return {r[1]: r[0] for r in rows}


@pytest.fixture()
def token_for(app, user_ids):
    from flask_jwt_extended import create_access_token

    def make(username: str) -> str:
        uid = user_ids[username]
        return create_access_token(identity=str(uid),
                                   additional_claims={"roles": [], "user_type": 0})
    return make


@pytest.fixture()
def token_for_id(app):
    """临时账号（fresh_user 现造的）不在 user_ids 快照里，直接按 id 签 token。"""
    from flask_jwt_extended import create_access_token

    def make(uid: int) -> str:
        return create_access_token(identity=str(uid),
                                   additional_claims={"roles": [], "user_type": 0})
    return make


@pytest.fixture()
def api(client, token_for, token_for_id):
    """带身份的请求助手：api.as_('pilot').get('/api/system/users')
                              api.as_id(u.id).post('/api/simulator/inject', json={})"""
    class Authed:
        def __init__(self, token: str):
            self.h = {"Authorization": f"Bearer {token}"}

        def req(self, method, url, **kw):
            headers = {**self.h, **(kw.pop("headers", {}) or {})}
            return getattr(client, method)(url, headers=headers, **kw)

        def get(self, url, **kw): return self.req("get", url, **kw)
        def post(self, url, **kw): return self.req("post", url, **kw)
        def put(self, url, **kw): return self.req("put", url, **kw)
        def delete(self, url, **kw): return self.req("delete", url, **kw)

        def json(self, method, url, **kw):
            return self.req(method, url, **kw).get_json()

    def as_(username: str) -> Authed:
        return Authed(token_for(username))

    def as_id(uid: int) -> Authed:
        return Authed(token_for_id(uid))

    from types import SimpleNamespace
    return SimpleNamespace(as_=as_, as_id=as_id)


@pytest.fixture()
def anon(client):
    """未登录：大屏/公开接口要能读，受保护接口必须 401。"""
    class Anon:
        def get(self, url, **kw): return client.get(url, **kw)
        def post(self, url, **kw): return client.post(url, **kw)
        def put(self, url, **kw): return client.put(url, **kw)
        def delete(self, url, **kw): return client.delete(url, **kw)
    return Anon()


@pytest.fixture()
def login(client):
    """真实走 /api/auth/login，用于验证登录态校验链路（冻结/注销账号在这层被拦）。"""
    def do(username: str, password: str = SEED_PASSWORD):
        r = client.post("/api/auth/login",
                        json={"username": username, "password": password, "deviceType": "PC"})
        return r
    return do


# ------------------------------------------------------------------
# 飞行链路工厂：造一条订单/计划，并推进到指定状态
# ------------------------------------------------------------------
STATES = ("draft", "paid", "accepted", "planned", "submitted",
          "approved", "assigned", "flying", "landed")


@pytest.fixture()
def make_flight(app, user_ids, app_ctx):
    """按 D1/D3 的真实服务函数造数据，不绕过状态机（绕过就等于在测自己的假实现）。

    用法：f = make_flight(); order, plan = f("flying")
    """
    import math
    from decimal import Decimal

    from app.models import Order, User, Vertiport
    from app.services import dispatch_service, geo, order_service, pricing_service

    DEMO_WEIGHT = Decimal("1.0")

    def _one(username: str) -> User:
        return db.session.get(User, user_ids[username])

    def _free_aircraft(exclude: set[int]):
        """挑一架"闲置 + 在线 + 停在某场站 + 载重够用"的飞机（同商家在飞的除外）。

        一个用例里可能要造两三条链路（例如"在途任务锁机"），
        所以必须能把上一轮已经占用的资源排除掉，而不是让 D3 的闸口报错当测试失败。
        """
        mid = pricing_service.get_demo_merchant().id
        for a in sorted(dispatch_service.idle_aircraft(mid), key=lambda x: x.id):
            if a.id in exclude or float(a.max_load_kg or 0) < float(DEMO_WEIGHT):
                continue
            return a
        raise AssertionError("测试现场没有空闲且载重足够的飞行器，检查上一条链路是否忘了降落")

    def _free_pilot(exclude: set[int]) -> User:
        mid = pricing_service.get_demo_merchant().id
        for p in sorted(dispatch_service._pilots(mid), key=lambda x: x.id):
            if p.id not in exclude:
                return p
        raise AssertionError("测试现场没有空闲机长，检查上一条链路是否忘了降落")

    def build(target: str = "flying", sender_offset=(60.0, 40.0),
              receiver_offset=(120.0, -80.0)) -> tuple[Order, object]:
        idx = STATES.index(target)
        customer, staff = _one("customer"), _one("merchant")
        used_ac = {p.aircraft_id for p in dispatch_service._occupied_plan_query().all()}
        used_pilots = dispatch_service._pilot_busy_ids()
        ac = _free_aircraft(used_ac)
        pilot = _free_pilot(used_pilots)
        vp1 = db.session.get(Vertiport, ac.current_vertiport_id)
        vp2 = (db.session.query(Vertiport)
               .filter(Vertiport.deleted == 0, Vertiport.status == 1,
                       Vertiport.id != vp1.id).first()) or vp1
        sender = geo.offset_meters(float(vp1.lng), float(vp1.lat), *sender_offset)
        receiver = geo.offset_meters(float(vp2.lng), float(vp2.lat), *receiver_offset)

        order = order_service.create_order(customer, {
            "senderName": "测试·寄件", "senderPhone": "13900000001",
            "senderAddress": f"{vp1.station_name}取货点",
            "senderLng": Decimal(str(round(sender[0], 6))),
            "senderLat": Decimal(str(round(sender[1], 6))),
            "receiverName": "测试·收件", "receiverPhone": "13900000002",
            "receiverAddress": f"{vp2.station_name}收货点",
            "receiverLng": Decimal(str(round(receiver[0], 6))),
            "receiverLat": Decimal(str(round(receiver[1], 6))),
            "goodsType": "文件", "goodsName": "测试件",
            "weightKg": DEMO_WEIGHT, "deliveryType": 1, "orderType": 1,
            "remark": "pytest 造数",
        })
        plan = None
        if idx >= 1:
            order_service.pay_order(order, customer, pay_method=1)
        if idx >= 2:
            order_service.staff_accept_order(order, staff)
        if idx >= 3:
            route = dispatch_service.build_route(order, vp1, vp2)
            need = math.ceil(route["durationSec"] / 60 * dispatch_service.RESERVE_RATIO
                             / max(ac.endurance_min or 1, 1) * 100)
            ac.battery = max(int(ac.battery or 0), need)
            db.session.commit()
            plan = dispatch_service.create_plan(staff, {
                "orderId": order.id, "aircraftId": ac.id, "pilotId": pilot.id,
                "takeoffVertiportId": vp1.id, "landingVertiportId": vp2.id,
                "altitudeM": 80, "cruiseSpeed": 15,
                "taskName": f"pytest-{vp1.station_name}→{vp2.station_name}",
                "purpose": "自动化测试",
            })
        if idx >= 4:
            dispatch_service.submit_plan(staff, plan)
        if idx >= 5:
            dispatch_service.approve_plan(_one("admin"), plan, "测试审批通过")
        if idx >= 6:
            dispatch_service.assign_plan(staff, plan)
        if idx >= 7:
            dispatch_service.takeoff(pilot, plan)
        if idx >= 8:
            dispatch_service.land(pilot, plan)
        return order, plan

    def ctx(target: str = "flying", **kw):
        return build(target, **kw)
    yield ctx


@pytest.fixture()
def cleanup(app_ctx):
    """显式清场（大多数用例由 autouse 的 _clean 兜住，需要中途清一次时用它）。"""
    def run():
        _truncate()
    return run


FLIGHT_TABLES = ("flight_track", "violation_record", "emergency_event", "approval_record",
                 "message_notification", "payment_transaction", "settlement",
                 "station_reservation", "weather_snapshot", "operation_log",
                 "device_lost_mark", "orders", "flight_plan",
                 # 券链路：user_coupon 是纯关系表必须清；coupon 在 DDL 里没有种子行，
                 # 用例自己建的测试券不带走就会出现在下一条用例的「领券中心」里。
                 "user_coupon", "coupon")

# 种子资源（DDL 7.7/7.8）：跑完一个用例后飞机可能停在"执行中/另一场站/掉电"，
# 不清回来下一个用例就选不到可调度飞机，测试会开始互相污染。
SEED_AIRCRAFT = (
    (1, 1, 100, 1, 0, 0),      # id, status, battery, current_vertiport_id, task_count, total_flight_min
    (2, 1, 96, 2, 0, 0),
)


def _truncate() -> None:
    """清业务流水 + 把飞行器/场站复位到种子状态；账号/角色/权限/空域属于种子，保持不动。

    两个坑都是现场踩出来的，改这里前请先读完：
      1. Flask-SQLAlchemy 的 session 作用域是 _app_ctx_id，每个 app_context 一个 session。
         夹具里"再套一层 app_context 去 remove()"清掉的只是自己那层，用例真正在用的那条
         连接还开着；MySQL 里开着事务读过的表会持有元数据锁，TRUNCATE 等排他 MDL 不是报错
         而是干等（默认能等一天）。所以必须先 close_all() 把注册表里所有 session 都收掉。
      2. 万一还有别的连接占着表，把 lock_wait_timeout 压到 10s，让问题以报错暴露出来。

    还有一条要记住：role / permission / role_permission / users **不在**清场名单里
    （它们是种子数据，每次重置等于重放一遍 DDL，慢，而且会把临时账号的外键扯进来）。
    所以凡是"整组替换角色授权"的用例，必须自己在夹具里快照 + 归还，
    参考 tests/test_system_users.py 的 clean_tmp_and_role_perms —— 没还的现场会一直
    留在 tianshu_test 里，下一次跑要么假通过，要么莫名 403。
    """
    _db.session.close_all()
    conn = _db.engine.connect()
    try:
        conn.exec_driver_sql("SET SESSION lock_wait_timeout=10")
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        for t in FLIGHT_TABLES:
            conn.exec_driver_sql(f"TRUNCATE TABLE `{t}`")
        for aid, status, battery, vp_id, task_count, minutes in SEED_AIRCRAFT:
            conn.exec_driver_sql(
                "UPDATE aircraft SET status=%s, battery=%s, current_vertiport_id=%s, "
                "task_count=%s, total_flight_min=%s, online=1, current_lng=NULL, current_lat=NULL "
                "WHERE id=%s", (status, battery, vp_id, task_count, minutes, aid))
        # 场站/飞行器回到种子状态：有用例会临时把降落场置为"关闭"验证闸口，
        # 也有用例（资源台账删除接口）会把它们置成 deleted=1 —— 都不许外溢到下一个用例。
        conn.exec_driver_sql("UPDATE vertiport SET status=1, occupied_count=0, deleted=0 WHERE id IN (1,2)")
        conn.exec_driver_sql("UPDATE aircraft SET deleted=0 WHERE id IN (1,2)")
        # 种子空域按编号认（DDL §7.8 的 AS-WZ-*），不按 id 认：§7.8 后来补了 AS-WZ-004 鹿城禁飞区，
        # 写死 id IN (1,2,3) 会把这条种子当"用例现造的"删掉——测试库里就没有这块区，
        # 而演示库有，两边合规判定从此不是一套地理。用 LEFT() 而不是 LIKE，是因为
        # exec_driver_sql 会走 pymysql 的 % 插值，语句里出现裸 % 直接 ValueError。
        conn.exec_driver_sql("UPDATE airspace SET status=1, deleted=0 WHERE LEFT(airspace_no,6)='AS-WZ-'")
        # 用例现造的空域（空域台账/审核用例走 HTTP 画的多边形）一律删掉：
        # 它们会一直留在 all=1 台账里，还会被 active_airspaces 捞去参与合规判定，
        # 让后面任何一条飞行链路用例莫名多出一个"闯区"。
        conn.exec_driver_sql("DELETE FROM airspace WHERE LEFT(airspace_no,6) <> 'AS-WZ-'")
        # 用例现造的飞行器/场站一律删掉：make_flight 会按 id 排序选"第一架空闲飞机"，
        # 多出一架 current_vertiport_id 为空的飞机就会让下一条链路建不起来。
        conn.exec_driver_sql("DELETE FROM aircraft WHERE id NOT IN (1,2)")
        conn.exec_driver_sql("DELETE FROM vertiport WHERE id NOT IN (1,2)")
        conn.commit()      # TRUNCATE 是 DDL 自带隐式提交，UPDATE 必须显式 commit
    finally:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")   # 别把开关带回连接池
        conn.close()


@pytest.fixture(autouse=True)
def _clean_between_cases(request, app_ctx):
    """每个用例前后各清一次业务表，用例之间互不依赖（标记 nocleanup 可跳过，
    用于"跑完链路再断言同一条链路"这种需要保留现场的场景）。"""
    if "nocleanup" not in request.keywords:
        _truncate()
    yield
    if "nocleanup" not in request.keywords:
        _truncate()


@pytest.fixture()
def second_merchant(app_ctx):
    """跨商家用例用的第二家运营公司（种子里只有 id=1，users 表有外键约束）。

    用 INSERT IGNORE 常驻测试库而不是每个用例建删：一是外键指向它的所有者不能被清掉，
    二是它属于"环境"而不是"数据"，_truncate 不会动 merchants 表。
    """
    db.session.execute(text(
        "INSERT IGNORE INTO merchants (id, merchant_no, merchant_name, unified_credit_code, "
        "legal_person, audit_status, status, expire_at) "
        "VALUES (2,'M2026090002','测试·隔壁市运营公司','91330300MA2TS0002X','李隔壁',2,1,'2027-12-31')"))
    db.session.commit()
    return 2


@pytest.fixture()
def fresh_user(app_ctx):
    """临时账号：用例结束物理删除，避免测试账号在 users 表里越堆越多。

    删账号前必须先把它挂在业务表上的外键一起删掉。DDL 里有 13 张表 REFERENCES users(id)
    （orders.customer_id / flight_plan.pilot_id / approval_record.approver_id …），
    临时账号只要真的参与过一次业务（例如顶空管账号批了个计划），
    `DELETE FROM users` 就会 1451 报错——而且是在拆卸阶段报，用例照样"通过"，
    账号却留在了库里，往后会串进"角色账号数"这类统计里。
    这些表都在 _truncate 的清理名单里（或本身就是流水），先删不破坏别的用例的现场。
    """
    from app.models import User
    from app.models.rbac import user_role      # 拆卸阶段也要用，必须在夹具作用域里
    created: list[int] = []

    USER_FKS = (("user_coupon", "user_id"), ("maintenance_record", "handler_id"),
                ("staff_schedule", "user_id"), ("station_reservation", "user_id"),
                ("orders", "customer_id"), ("flight_plan", "pilot_id"),
                ("approval_record", "approver_id"), ("violation_record", "pilot_id"),
                ("settlement", "customer_id"), ("payment_transaction", "user_id"),
                ("address_book", "user_id"), ("evaluation_complaint", "user_id"),
                ("customer_profile", "user_id"))

    def purge(uid: int) -> None:
        db.session.execute(user_role.delete().where(user_role.c.user_id == uid))
        for table, column in USER_FKS:
            db.session.execute(text(f"DELETE FROM {table} WHERE {column}=:i"), {"i": uid})
        db.session.execute(text("DELETE FROM users WHERE id=:i"), {"i": uid})
        db.session.commit()

    def make(username: str, role_code: str | None = None, **kw) -> User:
        import bcrypt
        # 上一次跑到一半崩掉会留下同名账号；uk_username 是硬约束，先清再建才幂等
        left = db.session.execute(
            text("SELECT id FROM users WHERE username=:n"), {"n": username}).scalar()
        if left:
            purge(left)
        u = User(username=username,
                 password_hash=bcrypt.hashpw(SEED_PASSWORD.encode(), bcrypt.gensalt(10)).decode(),
                 user_type=kw.pop("user_type", 3), real_name=kw.pop("real_name", username),
                 phone_cipher=kw.pop("phone_cipher", f"T:{username}"),
                 phone_digest=kw.pop("phone_digest", f"digest:{username}"),
                 merchant_id=kw.pop("merchant_id", 1),
                 status=kw.pop("status", 1),
                 realname_status=kw.pop("realname_status", 2), **kw)
        db.session.add(u)
        db.session.flush()
        if role_code:
            rid = db.session.execute(
                text("SELECT id FROM role WHERE role_code=:c"), {"c": role_code}).scalar()
            if rid:
                db.session.execute(user_role.insert().values(user_id=u.id, role_id=rid))
        db.session.commit()
        created.append(u.id)
        return u

    # app_ctx 已经把上下文铺好了，这里再套一层只会多出一条 session。
    # 用例结束时 app_ctx 还没弹出，直接读 created 就能把临时账号连角色关系一起删掉，
    # 不让测试账号在 users 里越堆越多（角色账号数 userCount 这类断言会被它们带偏）。
    yield make
    for uid in list(created):
        purge(uid)
