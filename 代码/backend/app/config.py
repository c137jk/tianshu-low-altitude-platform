"""全局配置：全部从环境变量/.env 读取，代码里不写死密码和密钥。"""
import os
from dotenv import load_dotenv

# backend 根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    # ---- 数据库 ----
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "tianshu_utms")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}"
        f"/{DB_NAME}?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,   # 连接前探活，避免 MySQL 8 小时断连
        "pool_recycle": 3600,
        "pool_size": 10,
    }

    # ---- JWT（D-03：PC 8 小时 / 移动端 2 小时，在签发接口区分）----
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "tianshu-dev")
    JWT_ACCESS_TOKEN_EXPIRES_HOURS_PC = 8
    JWT_ACCESS_TOKEN_EXPIRES_HOURS_MOBILE = 2

    # ---- Redis ----
    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    # ---- 敏感字段 ----
    AES_SECRET_KEY = os.getenv("AES_SECRET_KEY", "tianshu-aes-16ch")
    PHONE_HMAC_KEY = os.getenv("PHONE_HMAC_KEY", "tianshu-dev-seed")

    # ---- 高德 ----
    AMAP_WEB_KEY = os.getenv("AMAP_WEB_KEY", "")

    # ---- 仿真器 ----
    SIM_DEFAULT_SPEED = int(os.getenv("SIM_DEFAULT_SPEED", "1"))

    # ---- 上传（资质证照原件）----
    # 落在 static 外面：这个目录里的文件名是服务端生成的 uuid，但库里一旦被写进
    # 一个"看起来像 HTML"的路径，挂在 static 下就等于把上传件变成同源可直读的资源。
    # 原件只从 GET /api/resource/qualifications/<id>/file 出去，那道门和台账读门同一份名单。
    UPLOAD_ROOT = os.getenv("UPLOAD_ROOT") or os.path.join(BASE_DIR, "instance", "uploads")
    # 全站请求体上限：证照附件够用（单张扫描件一般 3~8MB），
    # 批量下单的 CSV 上限 200 行、轨迹单批 2000 帧，折算下来都远在这之下。
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024

    # ---- 业务开关 ----
    JSON_AS_ASCII = False  # 中文直接返回，不转 unicode
