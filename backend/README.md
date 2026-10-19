# 天枢后端（Flask 模块化单体）

对应设计03 的 D1~D9 领域划分、设计08 的 W1 任务。

## 目录结构

```
backend/
├─ run.py                # 启动入口
├─ requirements.txt      # 依赖
├─ .env.example          # 环境变量模板（复制为 .env 填真实值）
├─ sql/                  # 建库脚本（tianshu-ddl-v1.sql 单一真源）+ migrations/ 幂等增量
└─ app/
   ├─ __init__.py        # 应用工厂 create_app
   ├─ config.py          # 配置（全部读环境变量）
   ├─ extensions.py      # db/jwt/cors 等扩展单例
   ├─ common/            # 统一响应信封、全局异常
   ├─ blueprints/        # D1~D9 领域蓝图（W1 只有 health）
   └─ models/            # SQLAlchemy 模型（随后补）
```

## 首次运行（Windows PowerShell）

```powershell
# 1. 创建虚拟环境（只需一次）
python -m venv .venv
# 2. 激活
.\.venv\Scripts\Activate.ps1
# 3. 装依赖
python -m pip install -r requirements.txt
# 4. 配置环境变量
copy .env.example .env   # 然后编辑 .env 填 MySQL 密码
# 5. 启动
python run.py
```

浏览器打开 http://127.0.0.1:5000/api/health ，看到 `"status":"UP"` 即骨架成功。

## W2 客户闭环（已完成）

- 新增目录：`app/services/`（geo 地理计算、pricing_service 定价、order_service 订单）、
  `app/adapters/`（payment.py 为 Mock 支付适配器，不真实扣款；weather/utm/gcs 后续批次补）、
  `seed/generate_mock_data.py`（L2 造数）。
- 订单接口（前缀 /api）：`POST /orders/estimate` 试算、`POST /orders` 下单、`GET /orders`、
  `GET /orders/{id}`、`POST /orders/{id}/pay|cancel|confirm`、`GET /bills/payments|settlements`、
  `POST /auth/change-password`。
- 定价口径（pricing_service.py 注释为权威）：基础 = km×3.5 + kg×1.2；穿限飞区 10 元/个；
  禁飞/临时管制拒单（40900）；加急 = 基础×50%；平台抽佣 10%（结算演示）；巡航 12 m/s。
- 客户手机号 AES-256-GCM 加密落库（密钥环境变量 TIANSHU_PHONE_KEY），按关系解密/脱敏。

造数（幂等，可重复执行）：

```powershell
.\.venv\Scripts\python.exe seed\generate_mock_data.py --orders 30 --customers 5 --force
```

自测：`.\.venv\Scripts\python.exe scripts\check_d2.py`（24 项断言）。
