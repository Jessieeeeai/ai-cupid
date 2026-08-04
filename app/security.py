"""回访码 + 敏感字段加密。匿名优先:服务器只存回访码哈希与密文。"""
import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet

from .config import get_settings

_WORDS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 去掉易混淆字符


def new_visit_code() -> str:
    """生成回访码,如 LOVE-7F3K-9QMD。"""
    def block(n: int) -> str:
        return "".join(secrets.choice(_WORDS) for _ in range(n))
    return f"LOVE-{block(4)}-{block(4)}"


def hash_visit_code(code: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, code.strip().upper().encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    raw = hashlib.sha256(("enc:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode()).decode()


def new_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)
