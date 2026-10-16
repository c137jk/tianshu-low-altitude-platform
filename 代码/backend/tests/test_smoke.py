"""冒烟：测试库确实是被隔离重建的，登录/续期链路可用。"""
from flask_jwt_extended import decode_token
from sqlalchemy import text


def decode_claims(token: str) -> dict:
    return decode_token(token)


def test_running_on_test_database(app):
    assert f"/{__import__('conftest').TEST_DB}" in app.config["SQLALCHEMY_DATABASE_URI"]


def test_seed_data_present(session):
    assert session.execute(text("SELECT COUNT(*) FROM role")).scalar() >= 9
    assert session.execute(text("SELECT COUNT(*) FROM permission")).scalar() >= 40
    assert session.execute(text("SELECT COUNT(*) FROM users WHERE username='admin'")).scalar() == 1
    # 演示库里的业务数据不应该被带进来
    assert session.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 0


def test_login_returns_token_and_menus(login):
    r = login("admin")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["code"] == 0
    assert body["data"]["token"]
    assert any(m["permCode"] == "system" for m in body["data"]["menus"])


def test_login_rejects_wrong_password(login):
    r = login("admin", "wrong-password")
    assert r.status_code == 401
    assert r.get_json()["message"] == "账号或密码错误"


# ------------------------------------------------------------------
# D1 契约里的 POST /auth/refresh（设计03 列了、代码早期没实现，本次补齐）
# ------------------------------------------------------------------
def test_login_issues_access_and_refresh_pair(client):
    """有效期口径：PC 8h / 移动 2h / 刷新凭证 7 天，两个凭证必须能区分开。"""
    pc = client.post("/api/auth/login",
                     json={"username": "admin", "password": "Admin@123", "deviceType": "PC"}
                     ).get_json()["data"]
    mobile = client.post("/api/auth/login",
                         json={"username": "admin", "password": "Admin@123",
                               "deviceType": "Mobile"}).get_json()["data"]
    assert pc["expireSeconds"] == 8 * 3600, pc["expireSeconds"]
    assert mobile["expireSeconds"] == 2 * 3600, mobile["expireSeconds"]
    for d in (pc, mobile):
        assert d["refreshToken"] and d["refreshExpireSeconds"] == 7 * 86400
        assert decode_token(d["token"])["type"] == "access"
        assert decode_token(d["refreshToken"])["type"] == "refresh"
        # 续期时靠 device claim 还原有效期，所以两个凭证都要带上它
        assert decode_token(d["refreshToken"])["device"] in ("PC", "MOBILE")


def test_refresh_returns_working_access_token(client):
    login = client.post("/api/auth/login", json={"username": "pilot", "password": "Admin@123",
                                                 "deviceType": "PC"}).get_json()["data"]
    body = client.post("/api/auth/refresh",
                       headers={"Authorization": f"Bearer {login['refreshToken']}"}).get_json()
    assert body["code"] == 0, body
    new_token = body["data"]["token"]
    assert new_token and new_token != login["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}).get_json()
    assert me["code"] == 0 and me["data"]["userInfo"]["username"] == "pilot", me
    # 新访问凭证必须带着原来的角色/用户类型，否则前端守卫会当场掉菜单、后端门禁会误判身份
    claims = decode_claims(new_token)
    assert claims["roles"] == ["pilot"], claims
    assert claims["user_type"] == 3 and claims["device"] == "PC", claims


def test_refresh_token_can_not_be_used_as_access_token(client):
    """刷新凭证不能直接拿去调业务接口（jwt_required() 只认 access）。"""
    login = client.post("/api/auth/login", json={"username": "pilot", "password": "Admin@123"}
                        ).get_json()["data"]
    resp = client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {login['refreshToken']}"})
    assert resp.status_code >= 400, resp.get_json()
    body = resp.get_json() or {}
    assert body.get("code") not in (0, None), body


def test_refresh_needs_credential_and_rejects_frozen_account(client, fresh_user):
    """续期是"重新发牌"，账号状态必须现查：冻结中的账号不能再拿到新凭证。

    这里刻意不套 `with app.app_context()`：测试里 autouse 的 app_ctx 已经铺好，
    而 Flask 3 的 RequestContext.push() 见着同一个 app 的上下文就直接复用（见 flask/ctx.py），
    所以 test_client 发出的请求和用例本身共用一条 session、共用一个 g。
    再套一层就会多出一条 session：那边提交完，这边身份表里的对象还是旧的 status=1，
    请求读到的是脏快照，用例会以"该拦的没拦住"这种假通过形式骗人。
    """
    from app.extensions import db
    from app.models import User

    assert client.post("/api/auth/refresh").status_code == 401     # 匿名

    u = fresh_user("t_refresh_ok", "dispatcher")
    login = client.post("/api/auth/login",
                        json={"username": "t_refresh_ok", "password": "Admin@123"}
                        ).get_json()["data"]
    db.session.get(User, u.id).status = 2      # 等价于管理员在 M-81 点了"停用"
    db.session.commit()
    resp = client.post("/api/auth/refresh",
                       headers={"Authorization": f"Bearer {login['refreshToken']}"}).get_json()
    assert resp["code"] == 40300 and "无法续期" in resp["message"], resp
    # 冻结前签发的访问凭证也不能再干活（perm_required 里有同一道状态闸）
    me = client.get("/api/dispatch/resources",
                    headers={"Authorization": f"Bearer {login['token']}"}).get_json()
    assert me["code"] == 40300, me
