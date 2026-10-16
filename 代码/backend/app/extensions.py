"""Flask 扩展实例集中在这里创建，避免循环导入。

约定：扩展只在这里 new 一次，在 create_app() 里 init_app，业务代码直接 import 使用。
"""
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_compress import Compress

db = SQLAlchemy()
jwt = JWTManager()
cors = CORS()
compress = Compress()
# W4 再启用：socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
