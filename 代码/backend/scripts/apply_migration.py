"""执行 SQL 迁移脚本（幂等，逐语句提交）。

用法（backend 目录，激活虚拟环境）：
    python scripts/apply_migration.py sql/migrations/20260928_p0_perms.sql
    python scripts/apply_migration.py sql/migrations/            # 按文件名顺序全跑

拆分规则：忽略 -- 行注释，按语句结尾的分号切分；脚本内不含分号字面量。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 支持 python scripts/xxx.py 直跑

from app import create_app
from app.extensions import db


def split_statements(sql_text: str) -> list[str]:
    lines = [l for l in sql_text.splitlines() if not l.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", body) if s.strip()]


def run(path: Path) -> None:
    stmts = split_statements(path.read_text(encoding="utf-8"))
    app = create_app()
    with app.app_context(), db.engine.connect() as conn:
        for i, s in enumerate(stmts, 1):
            try:
                conn.exec_driver_sql(s)
                conn.commit()
                print(f"  [{i:02d}] OK   {s.splitlines()[0][:72]}")
            except Exception as e:  # 迁移失败必须显式暴露，不能吞
                conn.rollback()
                print(f"  [{i:02d}] FAIL {s.splitlines()[0][:72]}\n       -> {e}")
                raise SystemExit(1)
    print(f"迁移完成：{path.name}（{len(stmts)} 条语句）")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python scripts/apply_migration.py <sql文件|目录>")
    target = Path(sys.argv[1])
    if not target.is_absolute():
        target = Path(__file__).resolve().parent.parent / target
    if target.is_dir():
        for f in sorted(target.glob("*.sql")):
            print(f"\n== {f.name} ==")
            run(f)
    else:
        run(target)


if __name__ == "__main__":
    main()
