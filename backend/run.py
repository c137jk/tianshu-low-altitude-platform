"""天枢后端启动入口。

用法（在 backend 目录下）：
    .venv\\Scripts\\python run.py
开发期默认 http://127.0.0.1:5000 ，健康检查 GET /api/health
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    # W1 先用 Werkzeug 自带服务器；W4 接 SocketIO 后改为 socketio.run
    app.run(host="127.0.0.1", port=5000, debug=True)
