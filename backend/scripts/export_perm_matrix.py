"""接口 × 权限映射表：从代码和库里的真实授权反向生成，不手抄。

为什么要有这个脚本：系统设计报告里欠一张"93 端点 × 权限点"的映射表（差距分析 P3），
手写一次就漂一次。这里的输入全部是运行时真相 —— url_map、装饰器元数据、
role/permission 三张表 —— 所以只要有人改了门禁却没改代码，表就跟着变，不会出现
"文档说拦了、代码其实没拦"。

用法（backend 目录）：
    FLASK_SIM_DISABLED=1 .venv/Scripts/python.exe scripts/export_perm_matrix.py
    FLASK_SIM_DISABLED=1 .venv/Scripts/python.exe scripts/export_perm_matrix.py docs/x.md

归类口径（公开白名单 / 登录自助白名单）故意从 tests/test_perms_matrix.py 里 import：
那份名单是被测试钉住的安全边界，脚本只是把它渲染成表格；
在脚本里另抄一份就等于允许两边各说各话。

只读：不写任何业务数据，只查 url_map 与 RBAC 三张表。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from tests.test_perms_matrix import PUBLIC, SELF, _api_rules  # noqa: E402

# 蓝图 -> 设计03 的领域编号（一个蓝图跨域时写主域，备注里点出例外）
DOMAINS = {
    "auth": "D1 认证与账号",
    "system": "D9 系统配置（账号/角色/参数/日志）",
    "orders": "D2 订单",
    "dispatch": "D3 调度与飞行任务",
    "resource": "D4 低空资源",
    "airspace": "D5 空域合规",
    "monitor": "D6 实时监控",
    "simulator": "D6 飞行仿真",
    "demo": "D6 演示链路",
    "settlement": "D7 结算与财务",
    "aux": "D7 应急客服 + D8 统计报表",
    "health": "运维探活（非业务域）",
}


def _holders(perms: tuple[str, ...], mode: str, role_perms: dict[str, set[str]]) -> str:
    """哪些角色持有该门禁：all=每个权限点都要有，any=命中其一即可。

    platform_admin 特判：`User.has_perm` 对它是通配 True（代码里写死的兜底入口），
    所以库里没有对应的 role_permission 行也算持有 —— 表里如果按库判，会把"只有超管能用"
    的接口显示成"没人能用"，反而误导。
    """
    if not perms:
        return "-"
    hits = []
    for role_code, owned in role_perms.items():
        if role_code == "platform_admin":
            hits.append(role_code)
            continue
        ok = (set(perms) <= owned) if mode == "all" else bool(set(perms) & owned)
        if ok:
            hits.append(role_code)
    return "、".join(hits) or "（无：需先授权）"


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "接口权限映射表.md"
    app = create_app()

    with app.app_context():
        roles = db.session.execute(db.text(
            "SELECT role_code, role_name FROM role WHERE deleted=0 ORDER BY sort, id")).mappings().all()
        grant = db.session.execute(db.text(
            "SELECT r.role_code, p.perm_code FROM role_permission rp "
            "JOIN role r ON r.id=rp.role_id JOIN permission p ON p.id=rp.permission_id "
            "WHERE r.deleted=0 AND p.deleted=0")).all()
        perm_total = db.session.execute(
            db.text("SELECT COUNT(*) FROM permission WHERE deleted=0")).scalar()
        perm_rows = db.session.execute(db.text(
            "SELECT p.id, p.perm_code, p.perm_name, p.perm_type, pp.perm_code AS parent_code "
            "FROM permission p LEFT JOIN permission pp ON pp.id = p.parent_id "
            "WHERE p.deleted=0 ORDER BY p.id")).mappings().all()

    role_perms: dict[str, set[str]] = {r["role_code"]: set() for r in roles}
    for role_code, perm_code in grant:
        role_perms.setdefault(role_code, set()).add(perm_code)

    rows = []
    for method, path, endpoint in _api_rules(app):
        view = app.view_functions[endpoint]
        kind = getattr(view, "auth_kind", None)
        if kind == "perm":
            mode = getattr(view, "perm_mode", "all")
            gate = f"权限点（{'任一' if mode == 'any' else '全部'}）"
            perms = tuple(getattr(view, "req_perms", ()))
        elif kind == "staff":
            gate, perms, mode = "登录 + 运营身份(user_type 3~6)", (), "staff"
        elif endpoint in PUBLIC:
            gate, perms, mode = "公开（无 token）", (), "public"
        elif endpoint in SELF:
            gate, perms, mode = "登录自助（按本人/本商家收窄）", (), "self"
        else:
            gate, perms, mode = "!! 未归类", (), "unknown"
        rows.append({"bp": endpoint.split(".")[0], "method": method, "path": path,
                    "endpoint": endpoint, "gate": gate, "perms": perms, "mode": mode})

    unknown = [r for r in rows if r["mode"] == "unknown"]
    counts = {}
    for r in rows:
        counts[r["mode"]] = counts.get(r["mode"], 0) + 1

    lines = ["# 接口 × 权限映射表（由代码反向生成）", "",
             f"> 生成脚本：`scripts/export_perm_matrix.py`｜数据源：url_map + 装饰器元数据 + RBAC 三张表",
             f"> 端点 **{len(rows)}** 个｜权限点 **{perm_total}** 个｜角色 **{len(roles)}** 个｜"
             f"分类：权限门禁 {counts.get('all', 0) + counts.get('any', 0)}"
             f"（全需 {counts.get('all', 0)} / 任一 {counts.get('any', 0)}）"
             f" · 运营身份 {counts.get('staff', 0)} · 登录自助 {counts.get('self', 0)}"
             f" · 公开 {counts.get('public', 0)} · 未归类 {len(unknown)}",
             "",
             "读法：**「公开」是白名单**，其余全部要求 JWT；"
             "「登录自助」的数据范围由 `scope.merchant_scope` / 本人 user_id 收窄；"
             "「权限点」列出的编码必须出现在 `permission` 表里，否则 pytest 门禁用例会失败。",
             "",
             "## 权限点清单",
             "",
             "| id | 权限点 | 名称 | 类型 | 父菜单 | 持有角色 |",
             "|---|---|---|---|---|---|"]
    type_name = {1: "菜单", 2: "按钮", 3: "接口"}
    for p in perm_rows:
        lines.append(f"| {p['id']} | `{p['perm_code']}` | {p['perm_name']} | "
                     f"{type_name.get(p['perm_type'], p['perm_type'])} | "
                     f"`{p['parent_code'] or '-'}` | "
                     f"{_holders((p['perm_code'],), 'all', role_perms)} |")
    lines += ["", "> `platform_admin` 走 `User.has_perm` 通配，持有全部权限点，因此下表的角色列里"
              "它总是出现；内置超管角色的授权集合在 M-83 页面上禁止编辑（防自锁）。", ""]
    for bp_name in sorted({r["bp"] for r in rows}):
        group = [r for r in rows if r["bp"] == bp_name]
        lines += [f"## `{bp_name}` 蓝图 —— {DOMAINS.get(bp_name, bp_name)}", "",
                  "| 方法 | 路径 | 门禁 | 权限点 | 持有角色 |",
                  "|---|---|---|---|---|"]
        for r in sorted(group, key=lambda x: (x["path"], x["method"])):
            lines.append(f"| {r['method']} | `{r['path']}` | {r['gate']} | "
                         f"{'、'.join(f'`{p}`' for p in r['perms']) or '-'} | "
                         f"{_holders(r['perms'], r['mode'], role_perms)} |")
        lines.append("")

    if unknown:
        lines += ["## ⚠ 未归类端点（必须处理：补门禁或补白名单）", ""]
        lines += [f"- {r['method']} `{r['path']}` -> `{r['endpoint']}`" for r in unknown]
        lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"端点 {len(rows)} 个，分类 {counts}，未归类 {len(unknown)}")
    print(f"权限点 {perm_total} 个，角色 {len(roles)} 个")
    print(f"-> {out}")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
