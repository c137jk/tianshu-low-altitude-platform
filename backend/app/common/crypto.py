"""敏感字段加密 / 摘要（设计04 全局约定）。

- 手机号等 PII：AES-256-GCM 加密落库，密文前缀 "v1:"；
- phone_digest：HMAC-SHA256(密钥, 手机号)，用于唯一索引与查重，不可逆；
- phone_tail_digest：同一密钥对"后 4 位"取摘要，给密文列做尾号检索（不落明文）；
- DDL 种子里的历史占位密文格式为 "SEED:13800000001"，decrypt 兼容原样返回。

密钥从环境变量 TIANSHU_PHONE_KEY / PHONE_HMAC_KEY 读，缺省用演示种子 tianshu-dev-seed（仅开发）。
读环境变量这件事必须发生在 .env 加载之后，所以这里显式 import 一次 app.config
（config.py 模块体里就 load_dotenv），否则"先 import 本模块、后 load_dotenv"
会让密钥静默退化成种子值，换密钥的部署会在重启后出现摘要算不回去。
"""
import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Config   # noqa: F401  只要它的 load_dotenv 副作用

_PREFIX = "v1:"
_SEED_PREFIX = "SEED:"
_SECRET = (os.getenv("TIANSHU_PHONE_KEY") or Config.PHONE_HMAC_KEY or "tianshu-dev-seed").encode()
_aesgcm = AESGCM(hashlib.sha256(_SECRET).digest())
# HMAC 用原始密钥字节，**不是** sha256 派生后的那一把。
# 这个口径不是随手定的：DDL 7.5 的 users.phone_digest 全部按 HMAC(b"tianshu-dev-seed", 手机号)
# 生成，代码里曾经顺手复用了 AES 的派生密钥（sha256(secret)），于是运行时算出来的摘要
# 和库里的种子摘要永远是两套值——手机号查重（system.py）对种子账号全部失效，
# 用 13800000001 注册第二个账号也能通过唯一索引。改回来后由
# scripts/backfill_order_phone.py 统一校正存量行。
# 两把密钥分开也顺带满足"同一份密钥材料不复用于 AES 和 HMAC"。
_HMAC_KEY = _SECRET


def encrypt_phone(plain: str) -> str:
    """明文 → v1:base64(nonce(12)+密文+tag)。"""
    if plain is None:
        return plain
    plain = str(plain).strip()
    if plain.startswith(_PREFIX) or plain.startswith(_SEED_PREFIX):
        return plain  # 已加密/占位，不重复加密
    nonce = os.urandom(12)
    ct = _aesgcm.encrypt(nonce, plain.encode("utf-8"), None)
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_phone(cipher: str) -> str:
    """密文 → 明文；兼容 SEED: 占位明文。"""
    if not cipher:
        return ""
    if cipher.startswith(_SEED_PREFIX):
        return cipher[len(_SEED_PREFIX):]
    if not cipher.startswith(_PREFIX):
        return cipher  # 裸明文，直接返回
    raw = base64.urlsafe_b64decode(cipher[len(_PREFIX):].encode("ascii"))
    nonce, ct = raw[:12], raw[12:]
    return _aesgcm.decrypt(nonce, ct, None).decode("utf-8")


def phone_digest(plain: str) -> str:
    """HMAC-SHA256 十六进制摘要（密钥口径与 DDL 种子一致：原始 b"tianshu-dev-seed"）。"""
    return hmac.new(_HMAC_KEY, str(plain).strip().encode("utf-8"), hashlib.sha256).hexdigest()


def phone_tail_digest(plain: str) -> str:
    """手机号后 4 位摘要，给"尾号检索"用。

    为什么不多存一列明文后 4 位：orders 表本来就承诺 PII 密文落库（需求 10.4），
    存明文尾号等于开了个旁路；摘要能等值匹配就够了——客户记住的本来就是尾号。
    """
    digits = "".join(ch for ch in str(plain or "") if ch.isdigit())
    return phone_digest(digits[-4:]) if len(digits) >= 4 else ""


def mask_phone(plain: str) -> str:
    """138****0001 脱敏展示。"""
    plain = decrypt_phone(plain)
    if len(plain) >= 7:
        return plain[:3] + "****" + plain[-4:]
    return plain
