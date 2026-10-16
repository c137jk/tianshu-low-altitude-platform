"""P0 权限门禁回归（只读为主，写操作要么走 403 分支要么自清理）。

用法（backend 目录）：
    FLASK_SIM_DISABLED=1 python scripts/check_p0_gates.py

期望值口径：
    200/403/401/409/200+code  —— 断言 HTTP 状态码；信封 code 单独打印便于定位。
脚本不改动业务数据：需要"已授权但会写库"的路径，用非法参数走 CONFLICT 分支验证。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402

PW = "Admin@123"
ROLES = {                      # 简称 -> 登录账号
    "admin": "admin",          # role1 平台管理员
    "atc": "kongguan",         # role2 空管
    "ops": "yunwei",           # role3 平台运维
    "merchant": "merchant",    # role4+5 商家管理员/调度员
    "finance": "caiwu",        # role6 财务
    "pilot": "pilot",          # role7 机长
    "maint": "weixiu",         # role8 设备运维
    "customer": "customer",    # role9 客户
}
# (method, path, json_body, {角色: 期望 HTTP})；未列出的角色期望同 "ALL"
CASES = [
    # 迁移 p5 之后 merchant_admin 拿到 system:user（M-81 账号维护页要能用），
    # 但数据范围仍然锁在本商家，见 main() 里的"数据范围"专项断言。
    ("GET", "/api/system/users", None, {"admin": 200, "merchant": 200,
                                        "ops": 403, "pilot": 403, "customer": 403, "ALL": 403}),
    ("POST", "/api/system/users", {"username": "hack_atc", "roleCode": "air_traffic"},
     {"customer": 403, "pilot": 403, "merchant": 403, "ALL": 403}),
    ("PUT", "/api/system/users/1/status", {"status": 2},
     {"admin": 403, "ALL": 403}),                      # 超管账号不可被停用
    ("PUT", "/api/system/users/3/status", {"status": 0},
     {"admin": 200, "ALL": 403}),                     # 老口径 0 必须报参数错（信封 code=40000）
    ("GET", "/api/system/roles", None, {"admin": 200, "merchant": 403, "ALL": 403}),
    ("GET", "/api/system/permissions", None, {"admin": 200, "ops": 403, "ALL": 403}),
    ("PUT", "/api/system/roles/1/permissions", {"permissionIds": [1]},
     {"admin": 403, "ALL": 403}),                     # 内置超管角色不可改

    ("GET", "/api/resource/vertiports", None, {"admin": 200, "merchant": 200,
                                               "pilot": 200, "customer": 403, "ALL": 200}),
    ("POST", "/api/resource/vertiports", {"stationName": "门禁测试场"},
     {"admin": 400, "merchant": 403, "maint": 403, "ALL": 403}),   # admin 缺经纬度→400，不写库
    ("PUT", "/api/resource/aircraft/1", {"status": 99},
     {"admin": 400, "maint": 400, "pilot": 403, "customer": 403, "ALL": 403}),

    ("GET", "/api/settlement/bills", None, {"admin": 200, "finance": 200, "merchant": 200,
                                            "pilot": 403, "customer": 403, "ALL": 403}),
    ("POST", "/api/settlement/bills/reconcile", {"ids": [99999999]},
     {"admin": 200, "finance": 200, "merchant": 403, "pilot": 403, "ALL": 403}),

    ("GET", "/api/monitor/tracks", None, {"admin": 200, "pilot": 200, "merchant": 200,
                                          "customer": 403, "ALL": 403}),
    ("GET", "/api/monitor/alerts", None, {"admin": 200, "atc": 200, "customer": 403, "ALL": 403}),

    ("GET", "/api/aux/work-orders", None, {"admin": 200, "merchant": 200, "ops": 200,
                                           "pilot": 403, "customer": 403, "ALL": 403}),
    ("GET", "/api/aux/configs", None, {"admin": 200, "ops": 200, "merchant": 403, "ALL": 403}),
    ("GET", "/api/aux/logs", None, {"admin": 200, "ops": 200, "merchant": 403, "ALL": 403}),
    ("GET", "/api/aux/violations", None, {"admin": 200, "atc": 200, "merchant": 200,
                                          "customer": 403, "ALL": 403}),
    ("POST", "/api/aux/violations/1/penalty", {"penaltyType": 1},
     {"admin": 200, "merchant": 403, "pilot": 403, "ALL": 403}),
    ("GET", "/api/aux/coupons", None, {"admin": 200, "ops": 200, "merchant": 403,
                                       "customer": 403, "ALL": 403}),
    ("GET", "/api/aux/coupons?scope=available", None,
     {"admin": 200, "customer": 200, "ALL": 200}),
    ("GET", "/api/aux/report", None, {"admin": 200, "atc": 200, "ops": 200,
                                      "merchant": 403, "customer": 403, "ALL": 403}),
    ("GET", "/api/aux/schedules", None, {"admin": 200, "maint": 200, "ops": 200,
                                         "customer": 403, "ALL": 403}),
    ("POST", "/api/aux/realname/31/audit", {"pass": True},
     {"admin": 200, "ops": 200, "merchant": 403, "customer": 403, "ALL": 403}),

    ("POST", "/api/demo/run", {}, {"merchant": 403, "customer": 403, "pilot": 403,
                                   "finance": 403, "maint": 403, "ALL": 403}),

    # 故意传非法 geometry：命中参数校验就抛错、绝不落库（曾用 altitudeMax=200 把种子里
    # 龙湾禁飞区的限高改成 200，属于污染演示数据，已还原）。admin/atc 期望 200+code=40000。
    ("PUT", "/api/airspaces/1", {"geometry": {"type": "Polygon", "coordinates": [[[120.6, 27.9]]]}},
     {"admin": 200, "atc": 200, "merchant": 403, "ALL": 403}),
]


def _counts(client=None, app=None) -> dict:
    """快照几张关键表的行数，用来证明"门禁回归不写库"。"""
    from app.extensions import db
    out = {}
    with app.app_context():
        for t in ("orders", "flight_plan", "violation_record", "emergency_event",
                  "flight_track", "message_notification"):
            out[t] = db.session.execute(db.text(f"SELECT COUNT(*) FROM {t}")).scalar()
        out["airspace1_altMax"] = db.session.execute(
            db.text("SELECT altitude_max FROM airspace WHERE id=1")).scalar()
    return out


def main() -> int:
    app = create_app()
    client = app.test_client()
    before = _counts(app=app)
    tokens = {}
    for key, username in ROLES.items():
        r = client.post("/api/auth/login", json={"username": username, "password": PW})
        body = r.get_json() or {}
        if body.get("code") != 0:
            print(f"[FATAL] login failed {username}: {body.get('message')}")
            return 2
        tokens[key] = body["data"]["token"]
    print("login ok:", " ".join(sorted(ROLES)))

    fails = []
    for method, path, body, expects in CASES:
        run_as = [k for k in ROLES if k in expects]
        targets = run_as or ["admin"]
        for key in targets:
            want = expects.get(key, expects.get("ALL"))
            if want is None:
                continue
            hdr = {"Authorization": f"Bearer {tokens[key]}"}
            resp = client.open(path, method=method, json=body, headers=hdr)
            got = resp.status_code
            code = (resp.get_json() or {}).get("code")
            ok = got == want
            mark = "ok " if ok else "FAIL"
            print(f"  {mark} {method:4} {path:38} {key:8} http={got} code={code} (want {want})")
            if not ok:
                fails.append((method, path, key, want, got, code))

    # 数据范围专项（P0-3）：授权给商家之后，"能看谁"必须还是收死的。
    # 只看列表不写库，所以放在门禁用例后面单独跑。
    for key, want_mids in (("merchant", {1}), ("admin", None)):
        resp = client.get("/api/system/users?pageSize=100",
                          headers={"Authorization": f"Bearer {tokens[key]}"})
        rows = ((resp.get_json() or {}).get("data") or {}).get("list") or []
        mids = {r.get("merchantId") for r in rows}
        if want_mids is None:
            ok = bool(rows) and None in mids          # 平台侧账号（不挂商家）必须在结果里
            desc = f"{len(rows)} 行，含平台侧账号={None in mids}"
        else:
            ok = bool(rows) and mids == want_mids     # 只许本商家员工，客户/平台账号都不能混进来
            desc = f"{len(rows)} 行，merchantId 集合={sorted(x for x in mids if x is not None)}"
        print(f"  {'ok ' if ok else 'FAIL'} 范围 GET /api/system/users          {key:8} {desc}")
        if not ok:
            fails.append(("SCOPE", "/api/system/users", key, want_mids, mids, None))

    # 未登录（匿名）基线
    for path in ["/api/system/users", "/api/monitor/tracks", "/api/aux/configs",
                 "/api/monitor/screen"]:
        resp = client.get(path)
        want = 200 if path.endswith("/screen") else 401
        mark = "ok " if resp.status_code == want else "FAIL"
        print(f"  {mark} GET  {path:38} {'anon':8} http={resp.status_code} (want {want})")
        if resp.status_code != want:
            fails.append(("GET", path, "anon", want, resp.status_code, None))

    # 大屏脱敏核对：匿名不得出现机长实名与完整收货地址
    anon = (client.get("/api/monitor/screen").get_json() or {}).get("data", {}) or {}
    flying = anon.get("flying") or []
    print(f"  -- screen(anon) flying={len(flying)} masked={[(f.get('pilot'), f.get('receiver')) for f in flying][:1]}")

    after = _counts(app=app)
    for k, v in before.items():
        if after[k] != v:
            fails.append(("COUNT", k, v, after[k], None, None))
            print(f"  FAIL 门禁回归期间数据发生变化 {k}: {v} -> {after[k]}")
    print(f"  -- 行数快照（应完全不变）: {after}")

    print(f"\nRESULT: {len(fails)} mismatch(es)")
    for f in fails:
        print("   ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
