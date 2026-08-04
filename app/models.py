import datetime as dt
import enum

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Enum, Float,
                        ForeignKey, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class UserStatus(str, enum.Enum):
    registering = "registering"   # 问卷未完成
    active = "active"
    paused = "paused"             # 用户暂停曝光
    banned = "banned"


class NotifyChannel(str, enum.Enum):
    telegram = "telegram"
    email = "email"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    visit_code_hash = Column(String(64), unique=True, index=True, nullable=False)  # 回访码只存哈希
    nickname = Column(String(50))
    birthday = Column(Date)
    gender = Column(String(10))            # male / female / other
    seeking = Column(String(10))           # 想找的性别: male / female / any
    city = Column(String(80))
    timezone = Column(String(40))
    distance_pref = Column(String(20))     # same_city / same_country / long_distance / online_ok
    goal = Column(String(20))              # serious / open_chat / friends / casual
    age_min = Column(Integer)
    age_max = Column(Integer)

    contact_encrypted = Column(Text)       # 第19题:交友联系方式(加密),仅matched透出
    notify_channel = Column(Enum(NotifyChannel))
    notify_addr_encrypted = Column(Text)   # TG chat_id 或邮箱(加密),永不透出
    tg_bind_token = Column(String(32), index=True)  # 绑定TG用的一次性口令

    status = Column(Enum(UserStatus), default=UserStatus.registering, nullable=False)
    reg_step = Column(Integer, default=0, nullable=False)  # 问卷进行到第几题
    reputation = Column(Float, default=0.5, nullable=False)  # 0~1 口碑分
    balance = Column(Float, default=0.0, nullable=False)     # 内部余额(U),不可提现
    created_at = Column(DateTime, default=utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=utcnow, nullable=False)

    profile = relationship("Profile", uselist=False, back_populates="user")
    photos = relationship("Photo", back_populates="user", order_by="Photo.position")

    def age(self, today: dt.date | None = None) -> int | None:
        if not self.birthday:
            return None
        today = today or dt.date.today()
        return today.year - self.birthday.year - (
            (today.month, today.day) < (self.birthday.month, self.birthday.day))


class Profile(Base):
    __tablename__ = "profiles"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    answers = Column(JSON, default=dict)        # {"q9": "...", ...} 软性题答案
    hobby_tags = Column(JSON, default=list)     # 归一化爱好标签
    embedding = Column(JSON)                    # 画像向量(list[float]);生产可换pgvector
    user = relationship("User", back_populates="profile")


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    storage_key = Column(String(200), nullable=False)
    position = Column(Integer, default=0)
    authenticity = Column(String(20), default="unchecked")  # unchecked/ok/suspect
    created_at = Column(DateTime, default=utcnow)
    user = relationship("User", back_populates="photos")


class UploadToken(Base):
    __tablename__ = "upload_tokens"

    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


class Recommendation(Base):
    """推荐记录:既是'今日推荐'展示,也是防重推/曝光上限的依据。"""
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("user_id", "target_id", "date", name="uq_reco"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    target_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    date = Column(Date, index=True, nullable=False)
    reason = Column(Text)
    score = Column(Float)
    created_at = Column(DateTime, default=utcnow)


class GreetingStatus(str, enum.Enum):
    awaiting_payment = "awaiting_payment"
    pending = "pending"      # 已付款,等对方回应
    matched = "matched"
    rejected = "rejected"
    expired = "expired"


class Greeting(Base):
    __tablename__ = "greetings"

    id = Column(Integer, primary_key=True)
    from_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    message = Column(Text)
    status = Column(Enum(GreetingStatus), default=GreetingStatus.awaiting_payment, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    paid_at = Column(DateTime)
    resolved_at = Column(DateTime)


class OrderType(str, enum.Enum):
    greeting = "greeting"
    extra_recos = "extra_recos"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    expired = "expired"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("chain", "amount", "status", name="uq_open_amount"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    order_type = Column(Enum(OrderType), nullable=False)
    ref_id = Column(Integer)              # greeting.id 等关联对象
    chain = Column(String(20), nullable=False)   # solana / base
    amount = Column(Float, nullable=False)       # 唯一尾数金额
    status = Column(Enum(OrderStatus), default=OrderStatus.pending, nullable=False)
    txhash = Column(String(120))
    created_at = Column(DateTime, default=utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    paid_at = Column(DateTime)


class LedgerEntry(Base):
    __tablename__ = "ledger"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    delta = Column(Float, nullable=False)        # 正=入账,负=消费
    reason = Column(String(40), nullable=False)  # deposit/greeting/refund/extra_recos/overpay
    ref = Column(String(60))
    balance_after = Column(Float, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Event(Base):
    """未读事件收件箱:任何入口上线时都会带出,兜底通知。"""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    kind = Column(String(30), nullable=False)  # greeting_received/greeting_matched/greeting_rejected/...
    payload = Column(JSON, default=dict)
    read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
