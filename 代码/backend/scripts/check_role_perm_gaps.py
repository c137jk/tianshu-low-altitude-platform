"""权限门禁一致性清扫：找出"某角色进得去页面、页面却要调它没权的接口"的那些 GET 端点。

起因：平台运维（ops_maintainer）只有 resource:schedule，人员管理页的菜单门因此放行它，
但页面 onMounted 无条件拉 GET /resource/qualifications（要 resource:qualification 或 system:user），
一点就是一句 403。这类"菜单门与接口门不是同一份名单"的错位不会自己冒出来，得扫。

用法（只读，不写任何业务数据）：
    FLASK_SIM_DISABLED=1 python scripts/check_role_perm_gaps.py ops_maintainer
带路径参数的端点这轮不猜 id，所以只报无参 GET；报出来的每一条都要自己判断
"它的消费方页面/页签是不是也按同一个权限显隐了"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa                                    # noqa: E402
from app import create_app                                  # noqa: E402
from app.extensions import db                              # noqa: E402


def main() -> int:
    role_code = sys.argv[1] if len(sys.argv) > 1 else "ops_maintainer"
    app = create_app()
    blocked = []
    with app.app_context():
        perms = {r[0] for r in db.session.execute(sa.text(
            "SELECT p.perm_code FROM role_permission rp JOIN permission p ON p.id=rp.permission_id "
            "JOIN role r ON r.id=rp.role_id WHERE r.role_code=:c"), {"c": role_code})}
        print(f"角色 {role_code} 有 {len(perms)} 个权限点")

        for rule in app.url_map.iter_rules():
            if "GET" not in rule.methods or rule.endpoint == "static" or rule.arguments:
                continue
            view = app.view_functions[rule.endpoint]
            need = set(getattr(view, "req_perms", ()) or ())
            if not need:
                continue
            mode = getattr(view, "perm_mode", "all")
            if not ((need & perms) if mode == "any" else (need <= perms)):
                blocked.append((str(rule.rule), sorted(need), mode))

        print(f"该角色会吃 403 的无参 GET 端点：{len(blocked)} 个\n")
        for url, need, mode in sorted(blocked):
            print(f"  {url:52} {'任一' if mode == 'any' else '全部'}: {', '.join(need)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
