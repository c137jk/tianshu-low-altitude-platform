"""D8 统计报表蓝图：三类口径报表 + 自定义报表 + CSV 导出（设计03 D8 / 设计06 M-12）。

门禁口径：
  · 读接口统一 report:view（设计06 里 M-12 的可见角色是管理员/财务，
    再加空管/运维/商家管理员各自需要的口径）；
  · 导出单独 report:export——把"看一眼"和"把整份经营数据带走"分开授权：
    导出是全量落盘的动作，权限被改窄时只关导出这一扇门，不影响看数；
  · 数据范围不在这里判，由 stats_service 内部走 scope.merchant_filter，
    商家员工拿到的每个数字都只含本商家。

/stats/* 同时服务 M-11 综合驾驶舱：驾驶舱的趋势线以前是前端写死的假数据，
现在和报表中心读同一个 overview，两处不可能再各说一套。
"""
from flask import Blueprint, request

from app.common.decorators import current_user, perm_required
from app.common.export import csv_response
from app.common.response import success
from app.services import stats_service

bp = Blueprint("stats", __name__, url_prefix="/api")

# service 只认这几个参数；其余查询串一律不透传，免得把未知键当维度名递进白名单校验
QUERY_ARGS = ("start", "end", "grain", "report", "dataset", "dims", "metrics")


def _args() -> dict:
    return {k: request.args.get(k) for k in QUERY_ARGS}


@bp.get("/stats/overview")
@perm_required("report:view")
def stats_overview():
    """综合运营口径：KPI + 趋势 + 状态/品类/违规分布。"""
    return success(stats_service.overview(current_user(), _args()))


@bp.get("/stats/merchant")
@perm_required("report:view")
def stats_merchant():
    """商家经营口径：平台出全量对比，商家员工出本商家一行。"""
    return success(stats_service.merchant_report(current_user(), _args()))


@bp.get("/stats/airspace")
@perm_required("report:view")
def stats_airspace():
    """空管监管口径：申报/通过率/审批时长/冲突率 + 空域穿行统计。"""
    return success(stats_service.airspace_report(current_user(), _args()))


@bp.get("/reports/fields")
@perm_required("report:view")
def report_fields():
    """字段池：数据集与它们能提供的维度、指标，页面右侧列表由这里渲染。"""
    return success(stats_service.field_pool())


@bp.get("/reports/custom")
@perm_required("report:view")
def report_custom():
    """自定义报表：数据集 + 维度 + 指标 → 一张交叉表。"""
    return success(stats_service.custom_report(current_user(), _args()))


@bp.get("/reports/export")
@perm_required("report:export")
def report_export():
    """CSV 导出：与页面同一个 service 入口，导出数字必然等于屏幕数字。"""
    text, title = stats_service.report_csv(current_user(), _args())
    meta = {"start": request.args.get("start"), "end": request.args.get("end")}
    suffix = f"{meta['start']}_{meta['end']}" if all(meta.values()) else ""
    return csv_response(text, f"{title}{('-' + suffix) if suffix else ''}.csv")
