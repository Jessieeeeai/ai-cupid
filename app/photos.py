"""照片:一次性上传链接 + 本地存储(生产换 R2 只改这层)。"""
import datetime as dt
import os

from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .security import new_token

MAX_PHOTOS = 3
ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_SIZE = 8 * 1024 * 1024  # 8MB


def create_upload_token(db: Session, user: models.User) -> str:
    tok = new_token(24)
    db.add(models.UploadToken(
        token=tok, user_id=user.id,
        expires_at=models.utcnow() + dt.timedelta(minutes=15)))
    db.commit()
    return tok


def upload_url(token: str) -> str:
    return f"{get_settings().base_url}/upload/{token}"


def consume_token(db: Session, token: str) -> models.User | None:
    t = db.get(models.UploadToken, token)
    if not t or t.used or t.expires_at < models.utcnow():
        return None
    return db.get(models.User, t.user_id)


def save_photo(db: Session, user: models.User, content: bytes, content_type: str,
               position: int) -> models.Photo:
    if content_type not in ALLOWED:
        raise ValueError("仅支持 JPG/PNG/WebP")
    if len(content) > MAX_SIZE:
        raise ValueError("单张不超过 8MB")
    s = get_settings()
    os.makedirs(s.photo_dir, exist_ok=True)
    key = f"u{user.id}_{new_token(8)}{ALLOWED[content_type]}"
    with open(os.path.join(s.photo_dir, key), "wb") as f:
        f.write(content)
    # 超过3张:替换同位置旧图
    old = [p for p in user.photos if p.position == position]
    for p in old:
        db.delete(p)
    photo = models.Photo(user_id=user.id, storage_key=key, position=position,
                         authenticity="unchecked")  # TODO: 接AI真实性检测
    db.add(photo)
    db.commit()
    return photo


def signed_photo_urls(user: models.User) -> list[str]:
    """展示用临时URL。本地模式直接静态路径;R2 模式换成签名URL。"""
    s = get_settings()
    return [f"{s.base_url}/photos/{p.storage_key}" for p in user.photos[:MAX_PHOTOS]]
