"""应用工厂（D-02 模块化单体）。

create_app() 负责：加载配置 → 初始化扩展 → 注册蓝图 → 注册全局异常处理。
新增领域模块时，只动 blueprints 注册中心，不在这里堆业务代码。
"""
from flask import Flask

from .config import Config
from .extensions import db, jwt, cors, compress
from .common.errors import register_error_handlers
from .common.jwt_handlers import register_jwt_handlers
from .blueprints import register_blueprints


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # 1) 扩展
    db.init_app(app)
    jwt.init_app(app)
    compress.init_app(app)
    # 前后端分离：Vite 默认 5173 端口，开发期放开跨域
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

    # 2) 统一错误信封
    register_error_handlers(app)
    register_jwt_handlers(jwt)

    # 3) 领域蓝图（D1~D9 逐周追加）
    register_blueprints(app)

    # 4) W4 飞行仿真器：后台线程让"执行中"计划沿航线真实推进（无 WebSocket，前端 2s 轮询）
    from .services import flight_simulator, scheduler
    flight_simulator.start(app)

    # 5) B6 定时任务：预约提醒 / 资质到期 / 轨迹分区 / 券下架（与仿真器共用一个停用开关）
    scheduler.start(app)

    @app.get("/")
    def index():
        return {"code": 0, "message": "天枢后端已启动，接口前缀 /api", "data": None}

    # 6) 操作日志：变更类 API 调用全部落 operation_log（best effort，不影响主流程）
    import time as _time
    from flask import g, request
    from sqlalchemy import text as _text

    @app.before_request
    def _log_start():
        g._t0 = _time.time()

    @app.after_request
    def _write_log(resp):
        try:
            if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/"):
                uid = None; uname = None; ident = None
                try:
                    from flask_jwt_extended import get_jwt_identity
                    ident = get_jwt_identity()
                except Exception:
                    ident = None
                if ident:
                    uid = ident
                    row = db.session.execute(_text(
                        "SELECT username FROM users WHERE id=:i"), {"i": ident}).mappings().first()
                    uname = row["username"] if row else None
                module = request.path.strip("/").split("/")[1:3]
                db.session.execute(_text("""
                    INSERT INTO operation_log (user_id, username_snap, module, action, method,
                                              request_url, ip, status, cost_ms, created_at)
                    VALUES (:u,:n,:m,:a,:meth,:url,:ip,:s,:c,NOW())"""),
                    {"u": uid, "n": uname, "m": "/".join(module), "a": request.endpoint or "",
                     "meth": request.method, "url": request.path[:255],
                     "ip": request.remote_addr or "", "s": resp.status_code,
                     "c": int((_time.time() - getattr(g, "_t0", _time.time())) * 1000)})
                db.session.commit()
        except Exception as _e:
            app.logger.warning("operation_log write failed: %s", _e)
            db.session.rollback()
        return resp

    return app
