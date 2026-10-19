"""D3 调度与飞行任务蓝图：推荐/方案/申报/审批/下发/起降。

状态机动作全部落在 dispatch_service，蓝图只做参数搬运与权限门禁。
权限点：dispatch:plan 方案 · dispatch:submit 申报 · approval:handle 审批 ·
        dispatch:assign 指派 · dispatch:execute 机长起降
"""
from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.common.decorators import current_user, perm_required
from app.common.errors import ApiError
from app.common.response import success, BizCode
from app.services import dispatch_service

bp = Blueprint("dispatch", __name__, url_prefix="/api")


def _paging() -> tuple[int, int]:
    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("pageSize", 10)), 1), 100)
    return page, page_size


@bp.get("/dispatch/resources")
@perm_required("dispatch:plan")
def resources():
    return success(dispatch_service.list_resources(current_user()))


@bp.post("/dispatch/recommend")
@perm_required("dispatch:plan")
def recommend():
    body = request.get_json(silent=True) or {}
    if not body.get("orderId"):
        raise ApiError(BizCode.PARAM_ERROR, "缺少 orderId")
    data = dispatch_service.recommend(current_user(), int(body["orderId"]))
    return success(data, "推荐完成")


@bp.post("/dispatch/recalc")
@perm_required("dispatch:plan")
def recalc():
    """调度台切换起降场后，重算航程/航时/航线点（不保存方案）。"""
    body = request.get_json(silent=True) or {}
    data = dispatch_service.recalc_route(current_user(), body)
    return success(data, "已重算")


@bp.post("/flight-plans")
@perm_required("dispatch:plan")
def create_plan():
    body = request.get_json(silent=True) or {}
    plan = dispatch_service.create_plan(current_user(), body)
    return success(dispatch_service.to_plan_dict(plan, with_approvals=True), "方案已保存（待申报）")


@bp.get("/flight-plans")
@jwt_required()
def list_plans():
    page, page_size = _paging()
    data = dispatch_service.list_plans(
        current_user(), page=page, page_size=page_size,
        status=request.args.get("status"), keyword=request.args.get("keyword"),
        mine=request.args.get("mine") == "true")
    return success(data)


@bp.get("/flight-plans/<int:plan_id>")
@jwt_required()
def plan_detail(plan_id: int):
    plan = dispatch_service._get_plan_for_user(plan_id, current_user())
    return success(dispatch_service.to_plan_dict(plan, with_approvals=True))


@bp.post("/flight-plans/<int:plan_id>/submit")
@perm_required("dispatch:submit")
def submit_plan(plan_id: int):
    user = current_user()
    plan = dispatch_service._get_plan_for_user(plan_id, user)
    resp = dispatch_service.submit_plan(user, plan)
    return success(resp, "已提交空管申报（Mock 签收）")


@bp.post("/flight-plans/batch-approve")
@perm_required("approval:handle")
def batch_approve():
    body = request.get_json(silent=True) or {}
    data = dispatch_service.batch_approve(current_user(), body.get("ids") or [],
                                          body.get("opinion"))
    return success(data, f"已批量通过 {data['count']} 条计划")


@bp.post("/flight-plans/<int:plan_id>/approve")
@perm_required("approval:handle")
def approve_plan(plan_id: int):
    body = request.get_json(silent=True) or {}
    user = current_user()
    plan = dispatch_service._get_plan_for_user(plan_id, user)
    dispatch_service.approve_plan(user, plan, body.get("opinion"))
    return success(None, "审批通过")


@bp.post("/flight-plans/<int:plan_id>/reject")
@perm_required("approval:handle")
def reject_plan(plan_id: int):
    body = request.get_json(silent=True) or {}
    user = current_user()
    plan = dispatch_service._get_plan_for_user(plan_id, user)
    dispatch_service.reject_plan(user, plan, body.get("reason"))
    return success(None, "已驳回")


@bp.post("/flight-plans/<int:plan_id>/assign")
@perm_required("dispatch:assign")
def assign_plan(plan_id: int):
    user = current_user()
    plan = dispatch_service._get_plan_for_user(plan_id, user)
    resp = dispatch_service.assign_plan(user, plan)
    return success(resp, "任务已下发机长（地面站 Mock ACK）")


@bp.post("/flight-plans/<int:plan_id>/takeoff")
@perm_required("dispatch:execute")
def takeoff(plan_id: int):
    user = current_user()
    plan = dispatch_service._get_plan_for_user(plan_id, user)
    resp = dispatch_service.takeoff(user, plan)
    return success(resp, "已确认起飞")


@bp.post("/flight-plans/<int:plan_id>/land")
@perm_required("dispatch:execute")
def land(plan_id: int):
    user = current_user()
    plan = dispatch_service._get_plan_for_user(plan_id, user)
    resp = dispatch_service.land(user, plan)
    return success(resp, "已确认降落送达")


@bp.post("/flight-plans/<int:plan_id>/cancel")
@perm_required("dispatch:plan")
def cancel_plan(plan_id: int):
    body = request.get_json(silent=True) or {}
    user = current_user()
    plan = dispatch_service._get_plan_for_user(plan_id, user)
    dispatch_service.cancel_plan(user, plan, body.get("reason"))
    return success(None, "计划已取消")
