"""P0 飞行链路回归：一键演示 → 仿真推进 → 违规自动产生 → 应急/告警 → 落地走 D3。

用法（backend 目录）：
    FLASK_SIM_DISABLED=1 .venv/Scripts/python.exe scripts/check_p0_flight.py

会写库（用户已授权），但只写自己新建的演示订单/计划，不改已有演示数据；
脚本结尾把写进去的东西列清楚，方便对着 SQL 复核或清理。
仿真线程被 FLASK_SIM_DISABLED 关掉，所有推进都由脚本手动调 tick()，进度可控、可复现。
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                  # noqa: E402
from app.common.scope import invalidate_cfg                  # noqa: E402
from app.extensions import db                               # noqa: E402
from app.models import (Aircraft, FlightPlan, FlightTrack, Order, User,  # noqa: E402
                        ViolationRecord, EmergencyEvent)
from app.services import flight_simulator                    # noqa: E402

PW = "Admin@123"
report: dict = {"steps": [], "writes": [], "fails": []}


def check(name: str, ok: bool, detail=None):
    report["steps"].append({"case": name, "ok": bool(ok), "detail": detail})
    print(("  ok   " if ok else "  FAIL ") + name + (f" -> {detail}" if detail is not None else ""))
    if not ok:
        report["fails"].append(name)


def main() -> int:
    app = create_app()
    client = app.test_client()
    tok = (client.post("/api/auth/login", json={"username": "admin", "password": PW})
           .get_json() or {}).get("data", {}).get("token")
    if not tok:
        print("[FATAL] admin login failed")
        return 2
    H = {"Authorization": f"Bearer {tok}"}
    invalidate_cfg()                                        # 让新加的 sys_config 键立刻可见

    # ---------------------------------------------------------------- 1 立即送达
    r = client.post("/api/demo/run", json={"finish": True}, headers=H)
    body = r.get_json() or {}
    check("demo/run finish=true http200", r.status_code == 200, r.status_code)
    check("demo/run code=0", body.get("code") == 0, body.get("message"))
    data = body.get("data") or {}
    plan_no_a = data.get("planNo")
    order_no_a = data.get("orderNo")
    if order_no_a:
        report["writes"].append(f"order {order_no_a} / plan {plan_no_a}（已完成）")
    with app.app_context():
        plan_a = db.session.query(FlightPlan).filter_by(plan_no=plan_no_a).first() if plan_no_a else None
        check("计划已完成(status=4)", plan_a is not None and plan_a.status == 4,
              plan_a.status if plan_a else None)
        if plan_a:
            order_a = db.session.get(Order, plan_a.order_id)
            check("订单已送达(status=6)且回填 delivered_at",
                  order_a.status == 6 and order_a.delivered_at is not None,
                  f"{order_a.status}/{order_a.delivered_at}")
            ac = db.session.get(Aircraft, plan_a.aircraft_id)
            check("飞机归位闲置(status=1)且电量写回", ac.status == 1 and ac.battery is not None,
                  f"{ac.aircraft_no} status={ac.status} battery={ac.battery}")
            check("降落场=计划降落场（飞机位置回场站）",
                  ac.current_vertiport_id == plan_a.landing_vertiport_id, ac.current_vertiport_id)

    # ---------------------------------------------------------------- 2 留在空中
    r = client.post("/api/demo/run", json={}, headers=H)
    body = r.get_json() or {}
    data = body.get("data") or {}
    plan_id = data.get("planId")
    check("demo/run 不降落 http200 code0", r.status_code == 200 and body.get("code") == 0,
          body.get("message"))
    if not plan_id:
        print(json.dumps(report, ensure_ascii=False, indent=2)[:2000])
        return 1
    report["writes"].append(f"plan {plan_id} / order {data.get('orderNo')}（在飞→由脚本推进）")

    st = client.get("/api/simulator/status", headers=H).get_json()["data"]
    check("simulator/status 倍速=60", st["speedFactor"] == 60, st["speedFactor"])
    check("simulator/status 帧间隔=1s", abs(st["tickSec"] - 1.0) < 1e-6, st["tickSec"])
    check("在飞计划已被仿真器接管", any(f["planId"] == plan_id for f in st["flying"]),
          [f["planId"] for f in st["flying"]])

    # ---------------------------------------------------------------- 2b 大屏鉴权脱敏
    anon = (client.get("/api/monitor/screen").get_json() or {}).get("data") or {}
    auth = (client.get("/api/monitor/screen", headers=H).get_json() or {}).get("data") or {}
    a0 = (anon.get("flying") or [{}])[0]
    u0 = (auth.get("flying") or [{}])[0]
    check("匿名大屏仍有态势数据", bool(a0.get("lng")) and bool(a0.get("route")), a0.get("planNo"))
    check("匿名大屏机长姓名已脱敏", a0.get("pilot") and "*" in str(a0["pilot"]) and a0["masked"] is True,
          a0.get("pilot"))
    check("匿名大屏收货地址已脱敏", a0.get("receiver") and "*" in str(a0["receiver"]), a0.get("receiver"))
    check("登录运营看得到全量（姓名不脱敏）",
          u0.get("pilot") and "*" not in str(u0["pilot"]) and u0.get("masked") is False,
          u0.get("pilot"))
    check("匿名拿不到完整手机号/收件人电话",
          "1390000" not in json.dumps(anon, ensure_ascii=False), None)
    report["writes"].append("monitor/screen 匿名 vs 登录对比（只读）")

    # ---------------------------------------------------------------- 3 偏航 → 自动违规
    client.post("/api/simulator/inject", json={"scenario": "deviate", "planId": plan_id}, headers=H)
    with app.app_context():
        # 注入的横移每帧约 31m，偏航阈值 100m —— 5 帧内必然越界（不是靠巧合）
        for _ in range(6):
            flight_simulator.tick()
        frames = (db.session.query(FlightTrack).filter_by(plan_id=plan_id)
                  .order_by(FlightTrack.track_time.asc()).all())
        check("每 tick 落一帧（≥6 帧）", len(frames) >= 6, len(frames))
        check("轨迹帧回填 direction/battery",
              all(f.direction is not None and f.battery is not None for f in frames),
              [(float(f.lng), f.direction) for f in frames[:2]])
        vio = (db.session.query(ViolationRecord).filter_by(plan_id=plan_id, violation_type=2)
               .first())
        check("偏航自动生成违规台账(type=2)", vio is not None,
              vio.description if vio else None)
        tagged = [f for f in frames if f.is_violation]
        check("违规帧已打标 violation_type",
              bool(tagged) and tagged[0].violation_type == "route_deviation",
              [f.violation_type for f in tagged][:2])
        dup_before = db.session.query(ViolationRecord).filter_by(plan_id=plan_id).count()
        for _ in range(3):
            flight_simulator.tick()
        dup_after = db.session.query(ViolationRecord).filter_by(plan_id=plan_id).count()
        check("同类型违规不重复入账（去重）", dup_before == dup_after, f"{dup_before} -> {dup_after}")
        frames_after = (db.session.query(FlightTrack).filter_by(plan_id=plan_id).count())
        check("去重期间轨迹仍逐帧打标（回放看得出整段偏离）",
              frames_after > len(frames), f"{len(frames)} -> {frames_after}")

    # ---------------------------------------------------------------- 4 闯禁飞 → 应急事件
    with app.app_context():
        client.post("/api/simulator/inject", json={"scenario": "no_fly", "planId": plan_id}, headers=H)
        flight_simulator.tick()
        vio1 = (db.session.query(ViolationRecord).filter_by(plan_id=plan_id, violation_type=1)
                .first())
        check("闯禁飞自动生成违规台账(type=1)", vio1 is not None,
              vio1.description if vio1 else None)
        emg = (db.session.query(EmergencyEvent).filter_by(plan_id=plan_id, event_type=2)
               .first())
        check("闯禁飞同时产生应急事件(type=2)", emg is not None,
              f"{emg.event_no} level={emg.level} status={emg.status}" if emg else None)
        n = db.session.execute(db.text(
            "SELECT COUNT(*) c FROM message_notification WHERE biz_type IN ('violation','emergency')"
        )).scalar()
        check("站内信已写入（violation/emergency）", (n or 0) >= 1, n)

    # ---------------------------------------------------------------- 5 告警读真实数据
    alerts = (client.get("/api/monitor/alerts", headers=H).get_json() or {}).get("data") or []
    kinds = [a.get("type") or a.get("title") for a in (alerts if isinstance(alerts, list) else alerts.get("list", []))]
    check("/api/monitor/alerts 含违规/应急告警", any("禁飞" in str(k) or "违规" in str(k) for k in kinds),
          kinds[:6])
    check("告警里不再有硬编码演示文案",
          not any("演示环境" in str(k) for k in kinds), kinds[:3])

    # ---------------------------------------------------------------- 6 到点自动降落走 D3
    with app.app_context():
        client.post("/api/simulator/inject", json={"scenario": "normal", "planId": plan_id}, headers=H)
        plan = db.session.get(FlightPlan, plan_id)
        ac_id = plan.aircraft_id
        # 把起飞时间往前挪，模拟"已经飞完"，让本 tick 直接触到终点
        plan.actual_takeoff_at = datetime.now() - timedelta(
            seconds=int((plan.est_duration_sec or 120) * 2))
        db.session.commit()
        flight_simulator.tick()
        plan = db.session.get(FlightPlan, plan_id)
        check("仿真到点自动完成计划(status=4)", plan.status == 4, plan.status)
        order = db.session.get(Order, plan.order_id)
        check("订单自动转已送达(status=6)", order.status == 6, order.status)
        ac = db.session.get(Aircraft, ac_id)
        check("飞机自动归位(status=1)", ac.status == 1, ac.status)
        open_emg = (db.session.query(EmergencyEvent)
                    .filter(EmergencyEvent.plan_id == plan_id,
                            EmergencyEvent.status.in_((1, 2))).count())
        check("落地后应急事件自动解除", open_emg == 0, open_emg)

    # ---------------------------------------------------------------- 7 越权：机长不能注入
    pilot_tok = (client.post("/api/auth/login", json={"username": "pilot", "password": PW})
                 .get_json() or {}).get("data", {}).get("token")
    r = client.post("/api/simulator/inject", json={"scenario": "link_loss"},
                    headers={"Authorization": f"Bearer {pilot_tok}"})
    check("机长不能注入场景(403)", r.status_code == 403, r.status_code)
    r = client.get("/api/simulator/status", headers={})
    check("匿名不能看仿真状态(401)", r.status_code == 401, r.status_code)

    out = Path(tempfile.gettempdir()) / "p0_flight_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRESULT: {len(report['fails'])} failure(s); report -> {out}")
    return 1 if report["fails"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
