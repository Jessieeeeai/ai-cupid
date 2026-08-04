"""双向打招呼状态机(方案文档第 6 节)。

awaiting_payment → pending → matched / rejected / expired
被拒/超时:1U 退回 A 的内部余额。
"""
import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, notify
from .config import get_settings
from .security import decrypt


class GreetingError(Exception):
    pass


def _sent_today(db: Session, user_id: int) -> int:
    start = models.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(models.Greeting.id)).filter(
        models.Greeting.from_user_id == user_id,
        models.Greeting.created_at >= start,
        models.Greeting.status != models.GreetingStatus.awaiting_payment).scalar() or 0


def create(db: Session, from_user: models.User, to_user_id: int,
           message: str) -> models.Greeting:
    s = get_settings()
    to_user = db.get(models.User, to_user_id)
    if not to_user or to_user.status != models.UserStatus.active:
        raise GreetingError("对方不存在或暂不可用")
    if to_user.id == from_user.id:
        raise GreetingError("不能给自己打招呼")
    if _sent_today(db, from_user.id) >= s.daily_greeting_cap:
        raise GreetingError(f"每天最多发起 {s.daily_greeting_cap} 次打招呼,明天再来")
    # 拒绝冷却:30天内被同一人拒绝过不能再发
    cooldown = models.utcnow() - dt.timedelta(days=s.reject_cooldown_days)
    recent_reject = db.query(models.Greeting).filter(
        models.Greeting.from_user_id == from_user.id,
        models.Greeting.to_user_id == to_user_id,
        models.Greeting.status == models.GreetingStatus.rejected,
        models.Greeting.resolved_at >= cooldown).first()
    if recent_reject:
        raise GreetingError("对方最近拒绝过你的打招呼,先看看别人吧")
    # 已有进行中的不重复发
    open_g = db.query(models.Greeting).filter(
        models.Greeting.from_user_id == from_user.id,
        models.Greeting.to_user_id == to_user_id,
        models.Greeting.status.in_([models.GreetingStatus.awaiting_payment,
                                    models.GreetingStatus.pending,
                                    models.GreetingStatus.matched])).first()
    if open_g:
        if open_g.status == models.GreetingStatus.matched:
            raise GreetingError("你们已经匹配成功了!")
        if open_g.status == models.GreetingStatus.pending:
            raise GreetingError("你已经给TA打过招呼了,等TA回应")
        open_g.message = message  # 未付款的更新留言即可
        db.commit()
        return open_g
    g = models.Greeting(from_user_id=from_user.id, to_user_id=to_user_id,
                        message=message.strip()[:500])
    db.add(g)
    db.commit()
    return g


def activate(db: Session, greeting_id: int) -> None:
    """付款确认后激活:进入 pending 并通知对方。"""
    g = db.get(models.Greeting, greeting_id)
    if g is None or g.status != models.GreetingStatus.awaiting_payment:
        return
    g.status = models.GreetingStatus.pending
    g.paid_at = models.utcnow()
    from_user = db.get(models.User, g.from_user_id)
    to_user = db.get(models.User, g.to_user_id)
    db.add(models.Event(user_id=to_user.id, kind="greeting_received",
                        payload={"greeting_id": g.id,
                                 "from_nickname": from_user.nickname}))
    db.commit()
    notify.push(db, to_user, "greeting_received",
                f"有人付费想认识你!{from_user.nickname} 给你留了言,"
                f"看看TA的资料再决定要不要认识(查看免费)。")


def respond(db: Session, to_user: models.User, greeting_id: int,
            accept: bool) -> dict:
    g = db.get(models.Greeting, greeting_id)
    if g is None or g.to_user_id != to_user.id:
        raise GreetingError("找不到这条打招呼")
    if g.status != models.GreetingStatus.pending:
        raise GreetingError("这条打招呼已经处理过或已过期")
    from_user = db.get(models.User, g.from_user_id)
    g.resolved_at = models.utcnow()
    if accept:
        g.status = models.GreetingStatus.matched
        _bump_reputation(to_user, +0.05)
        db.add(models.Event(user_id=from_user.id, kind="greeting_matched",
                            payload={"greeting_id": g.id,
                                     "nickname": to_user.nickname}))
        db.commit()
        notify.push(db, from_user, "greeting_matched",
                    f"好消息!{to_user.nickname} 同意认识你,快去打个招呼吧。")
        return {"status": "matched",
                "contact_exchange": contact_card(db, g, viewer=to_user)}
    else:
        g.status = models.GreetingStatus.rejected
        from . import payments
        payments.ledger_add(db, from_user, get_settings().price_greeting,
                            "refund", f"greeting:{g.id}")
        db.add(models.Event(user_id=from_user.id, kind="greeting_rejected",
                            payload={"greeting_id": g.id}))
        db.commit()
        notify.push(db, from_user, "greeting_rejected",
                    "这次没有缘分,1U 已退回你的余额,下次打招呼直接可用。别灰心,今天的推荐里也许就有对的人。")
        return {"status": "rejected"}


def expire_stale(db: Session) -> int:
    """定时任务:72h 未响应 → expired,退款,对方口碑降一点。"""
    deadline = models.utcnow() - dt.timedelta(hours=get_settings().greeting_expire_hours)
    stale = db.query(models.Greeting).filter(
        models.Greeting.status == models.GreetingStatus.pending,
        models.Greeting.paid_at < deadline).all()
    from . import payments
    for g in stale:
        g.status = models.GreetingStatus.expired
        g.resolved_at = models.utcnow()
        from_user = db.get(models.User, g.from_user_id)
        to_user = db.get(models.User, g.to_user_id)
        payments.ledger_add(db, from_user, get_settings().price_greeting,
                            "refund", f"greeting:{g.id}")
        _bump_reputation(to_user, -0.05)
        db.add(models.Event(user_id=from_user.id, kind="greeting_expired",
                            payload={"greeting_id": g.id}))
    db.commit()
    return len(stale)


def contact_card(db: Session, g: models.Greeting, viewer: models.User) -> dict:
    """matched 后互看联系方式。"""
    if g.status != models.GreetingStatus.matched:
        raise GreetingError("还没匹配成功")
    if viewer.id not in (g.from_user_id, g.to_user_id):
        raise GreetingError("无权查看")
    other_id = g.to_user_id if viewer.id == g.from_user_id else g.from_user_id
    other = db.get(models.User, other_id)
    return {"nickname": other.nickname,
            "contact": decrypt(other.contact_encrypted) if other.contact_encrypted else "",
            "note": "联系方式仅你们两人可见,请友善相处。"}


def inbox(db: Session, user: models.User) -> list[dict]:
    """待处理的打招呼(给被打招呼一方看)。"""
    rows = db.query(models.Greeting).filter(
        models.Greeting.to_user_id == user.id,
        models.Greeting.status == models.GreetingStatus.pending).all()
    out = []
    for g in rows:
        from_user = db.get(models.User, g.from_user_id)
        from .photos import signed_photo_urls
        out.append({
            "greeting_id": g.id,
            "from": {"nickname": from_user.nickname, "age": from_user.age(),
                     "city": from_user.city, "goal": from_user.goal,
                     "intro": (from_user.profile.answers or {}).get("q9", "")
                     if from_user.profile else "",
                     "photos": signed_photo_urls(from_user)},
            "message": g.message,
            "hours_left": max(0, int(get_settings().greeting_expire_hours -
                              (models.utcnow() - g.paid_at).total_seconds() / 3600)),
        })
    return out


def _bump_reputation(user: models.User, delta: float) -> None:
    user.reputation = min(1.0, max(0.0, user.reputation + delta))
