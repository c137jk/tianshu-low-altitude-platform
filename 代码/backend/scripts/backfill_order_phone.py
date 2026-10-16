"""手机号摘要列回填 / 校正（迁移 20260929_p12 的数据部分）。

做两件事：
  1) orders 的 4 个新摘要列（sender/receiver × 整号/尾号）——SQL 里算不出 HMAC，
     密文又是 AES-256-GCM 随机 nonce，只能解密→重算→回写；
  2) users.phone_digest 的密钥漂移校正——代码曾经把 HMAC 密钥写成 sha256(secret)，
     而 DDL 种子按原始 secret 生成，导致运行时摘要与种子摘要永远是两套值
     （手机号查重对种子账号全失效）。详见 app/common/crypto.py 的注释。

默认只处理摘要列为空/不一致的行，可重复执行；--all 强制整表重算（换密钥后用）。

用法（backend 目录）：
    FLASK_SIM_DISABLED=1 python scripts/backfill_order_phone.py
    FLASK_SIM_DISABLED=1 python scripts/backfill_order_phone.py --all
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 支持 python scripts/xxx.py 直跑

from sqlalchemy import text  # noqa: E402

from app import create_app  # noqa: E402
from app.common.crypto import decrypt_phone, phone_digest, phone_tail_digest  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402

_COLS = ("sender_phone_digest", "sender_phone_tail",
         "receiver_phone_digest", "receiver_phone_tail")

_WHERE_PENDING = ' OR '.join(f'{c} IS NULL' for c in _COLS)

_SELECT = text(f"""
    SELECT id, sender_phone, receiver_phone
      FROM orders
     WHERE {_WHERE_PENDING}
     ORDER BY id
""")

_SELECT_ALL = text("SELECT id, sender_phone, receiver_phone FROM orders ORDER BY id")

_UPDATE = text("""
    UPDATE orders
       SET sender_phone_digest = :sd, sender_phone_tail = :st,
           receiver_phone_digest = :rd, receiver_phone_tail = :rt
     WHERE id = :id
""")


def _key_selfcheck() -> None:
    """跑之前先确认"当前进程的 HMAC 密钥 == DDL 种子摘要用的密钥"。

    起因：配置里躺着 PHONE_HMAC_KEY，代码读的是 TIANSHU_PHONE_KEY，两个名字不一样，
    谁哪天只在其中一个上配了非默认值，摘要就会整体对不上（搜索恒 0 结果，还查不出为什么）。
    这里用种子账号的已知手机号反查一次摘要，不一致就大声提醒。
    """
    row = db.session.execute(text(
        "SELECT phone_cipher, phone_digest FROM users WHERE username='admin'")).first()
    if not row:
        return
    plain = decrypt_phone(row[0])
    if plain and phone_digest(plain) != row[1]:
        print("!! 警告：HMAC 密钥与库中已有摘要不是同一把（users.admin 的 phone_digest 对不上）。")
        print("!!      orders 的摘要仍然能自洽检索，但 users 侧的唯一索引/查重会失效，先把密钥统一。")


def _fix_users() -> int:
    """校正 users.phone_digest：HMAC 密钥口径修好之后，存量行里"运行时写入"的那批是旧值。

    种子账号（DDL 7.5）本来就是按原始密钥算的，改完代码正好对上，不会被本函数改动；
    会被改写的是注册接口/后台建号写进去的摘要——不改的话手机号查重仍然漏。
    """
    fixed = 0
    rows = db.session.execute(text("SELECT id, username, phone_cipher, phone_digest FROM users")).all()
    for uid, username, cipher, stored in rows:
        plain = decrypt_phone(cipher)
        want = phone_digest(plain) if plain else stored
        if not want or want == stored:
            continue
        try:
            db.session.execute(text("UPDATE users SET phone_digest=:d WHERE id=:i"), {"d": want, "i": uid})
            fixed += 1
        except Exception as e:                      # uk_phone_digest 撞上说明库里有两个同号账号
            db.session.rollback()
            print(f"  !! users.{username}(id={uid}) 摘要改写失败：{e}")
    db.session.commit()
    return fixed


def run(all_rows: bool = False) -> int:
    _key_selfcheck()
    print(f"users.phone_digest 校正 {_fix_users()} 行")
    rows = db.session.execute(_SELECT_ALL if all_rows else _SELECT).all()
    done = skipped = 0
    for r in rows:
        try:
            s, rec = decrypt_phone(r[1]), decrypt_phone(r[2])
        except Exception as e:                       # 密文坏掉不能中断整批回填
            skipped += 1
            print(f"  跳过 id={r[0]}：解密失败 {e}")
            continue
        db.session.execute(_UPDATE, {
            "id": r[0],
            "sd": phone_digest(s) if s else None, "st": phone_tail_digest(s) if s else None,
            "rd": phone_digest(rec) if rec else None, "rt": phone_tail_digest(rec) if rec else None,
        })
        done += 1
        if done % 200 == 0:
            db.session.commit()
            print(f"  已回填 {done} 行…")
    db.session.commit()
    print(f"回填完成：{done} 行；跳过 {skipped} 行；待处理 {len(rows)} 行")
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="覆盖库名（默认用 .env 的 DB_NAME）")
    ap.add_argument("--all", action="store_true", help="整表重算（换过密钥之后用一次）")
    args = ap.parse_args()

    cfg = Config
    if args.db:
        class _Cfg(Config):
            SQLALCHEMY_DATABASE_URI = Config.SQLALCHEMY_DATABASE_URI.rsplit("/", 1)[0] + \
                f"/{args.db}?charset=utf8mb4"
        cfg = _Cfg

    app = create_app(cfg)
    print(f"目标库：{app.config['SQLALCHEMY_DATABASE_URI'].split('@')[-1]}")
    with app.app_context():
        run(all_rows=args.all)


if __name__ == "__main__":
    main()
