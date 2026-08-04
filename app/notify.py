"""通知分发:TG 或邮箱(二选一)。发送失败静默降级为站内 Event(已在业务层记录)。"""
import httpx

from . import models
from .config import get_settings
from .security import decrypt


def push(db, user: models.User, kind: str, text: str) -> bool:
    """尽力而为地推一条通知;返回是否推出去了。站内 Event 由调用方负责。"""
    s = get_settings()
    try:
        if user.notify_channel == models.NotifyChannel.telegram and user.notify_addr_encrypted:
            if not s.telegram_bot_token:
                return False
            chat_id = decrypt(user.notify_addr_encrypted)
            r = httpx.post(
                f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": f"[{s.brand_name}] {text}"},
                timeout=10)
            return r.status_code == 200
        if user.notify_channel == models.NotifyChannel.email and user.notify_addr_encrypted:
            if not s.resend_api_key:
                return False
            email = decrypt(user.notify_addr_encrypted)
            r = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {s.resend_api_key}"},
                json={"from": s.email_from, "to": [email],
                      "subject": f"[{s.brand_name}] 你有新消息",
                      "text": text}, timeout=10)
            return r.status_code in (200, 201)
    except Exception:
        return False
    return False


def unread_events(db, user: models.User, mark_read: bool = True) -> list[dict]:
    rows = db.query(models.Event).filter_by(user_id=user.id, read=False)\
        .order_by(models.Event.created_at).all()
    out = [{"kind": e.kind, "payload": e.payload,
            "at": e.created_at.isoformat()} for e in rows]
    if mark_read:
        for e in rows:
            e.read = True
        db.commit()
    return out
