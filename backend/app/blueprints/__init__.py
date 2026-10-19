"""蓝图注册中心。

按设计03 的 D1~D9 逐周挂进来：
    health → D1 auth → D2 orders → D3 dispatch → D5 airspace → D6 monitor ...
"""
from flask import Flask

from .health import bp as health_bp
from .auth import bp as auth_bp
from .orders import bp as order_bp
from .dispatch import bp as dispatch_bp
from .airspace import bp as airspace_bp
from .monitor import bp as monitor_bp
from .resource import bp as resource_bp
from .settlement import bp as settlement_bp
from .system import bp as system_bp
from .demo import bp as demo_bp
from .simulator import bp as simulator_bp
from .auxiliary import bp as aux_bp
from .stats import bp as stats_bp

# 领域蓝图按交付顺序追加
ALL_BLUEPRINTS = [health_bp, auth_bp, order_bp, dispatch_bp, airspace_bp, monitor_bp,
                  resource_bp, settlement_bp, system_bp, demo_bp, simulator_bp, aux_bp,
                  stats_bp]


def register_blueprints(app: Flask):
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
