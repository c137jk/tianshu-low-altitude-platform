"""仿真控制器（设计07 的「演示可控性」）：状态 / 启停 / 倍速 / 5 类场景注入。

权限：demo:run（平台管理员、平台运维）。注入会让真实在飞计划偏航、失联、电量骤降，
     商家、机长、客户都不能碰，否则等于把飞行安全开关交给了租户。
"""
from flask import Blueprint, request

from app.common.decorators import current_user, perm_required
from app.common.errors import ApiError
from app.common.response import BizCode, success
from app.common.scope import invalidate_cfg, sys_cfg
from app.extensions import db
from app.services import flight_simulator

bp = Blueprint("simulator", __name__, url_prefix="/api/simulator")


@bp.get("/status")
@perm_required("demo:run")
def status():
    return success(flight_simulator.status())


@bp.get("/scenarios")
@perm_required("demo:run")
def scenarios():
    return success([{"key": k, "name": v} for k, v in flight_simulator.SCENARIO_NAMES.items()])


@bp.post("/inject")
@perm_required("demo:run")
def inject():
    """注入场景：{"scenario": "no_fly|link_loss|low_battery|bad_weather|deviate|normal",
                 "planId": 可选，不传就是对所有在飞计划生效}"""
    body = request.get_json(silent=True) or {}
    scenario = (body.get("scenario") or "").strip()
    if not scenario:
        raise ApiError(BizCode.PARAM_ERROR,
                       "scenario 必填，可选：" + "、".join(flight_simulator.SCENARIO_NAMES))
    plan_id = body.get("planId") or body.get("plan_id")
    if plan_id is not None and str(plan_id).isdigit():
        plan_id = int(plan_id)
    result = flight_simulator.inject(None if scenario == "normal" else scenario, plan_id)
    if result.get("error"):
        raise ApiError(BizCode.PARAM_ERROR,
                       f"{result['error']}：{scenario}，可选："
                       + "、".join(flight_simulator.SCENARIO_NAMES))
    if not flight_simulator.status()["flying"]:
        result["warning"] = "当前没有在飞计划，场景将在下一个起飞的计划上生效"
    return success(result, "场景已注入" if scenario != "normal" else "场景已恢复为正常巡航")


@bp.post("/pause")
@perm_required("demo:run")
def pause():
    body = request.get_json(silent=True) or {}
    paused = bool(body.get("paused", True))
    flight_simulator.set_paused(paused)
    return success({"paused": paused}, "仿真已暂停" if paused else "仿真已继续")


@bp.put("/speed")
@perm_required("demo:run")
def speed():
    """改仿真倍速（写系统参数，立即生效，无需重启）。"""
    body = request.get_json(silent=True) or {}
    raw = body.get("speedFactor", body.get("value"))
    try:
        factor = int(float(raw))
    except (TypeError, ValueError):
        raise ApiError(BizCode.PARAM_ERROR, "speedFactor 需为整数（1=实时，60=一分钟演完）")
    if not 1 <= factor <= 3600:
        raise ApiError(BizCode.PARAM_ERROR, "倍速需在 1~3600 之间")
    if sys_cfg("sim_speed_factor") is None:
        db.session.execute(db.text(
            "INSERT INTO sys_config (config_group, config_key, config_value, value_type, remark)"
            " VALUES ('sim','sim_speed_factor',:v,'number','仿真倍速')"), {"v": str(factor)})
    else:
        db.session.execute(db.text(
            "UPDATE sys_config SET config_value=:v WHERE config_key='sim_speed_factor'"),
            {"v": str(factor)})
    db.session.commit()
    invalidate_cfg("sim_speed_factor")
    return success({"speedFactor": flight_simulator.speed_factor()}, f"倍速已设为 {factor}×")
