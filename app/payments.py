"""支付:唯一尾数金额订单 + 扫链对账 + 内部余额 ledger。

规则(与方案文档 5.x 一致):
- 金额 = 基础价 + 0.000001 × 序号,同链同金额同时只有一个 pending 订单
- 15 分钟过期,尾数回收复用
- 多付差额记余额;余额不可提现
"""
import datetime as dt

from sqlalchemy.orm import Session

from . import models
from .config import get_settings

CHAINS = {"solana", "base"}
TAIL = 0.000001
AMOUNT_EPS = TAIL / 2


def _addr_for(chain: str) -> str:
    s = get_settings()
    return {"solana": s.solana_address, "base": s.base_address}.get(chain, "")


def ledger_add(db: Session, user: models.User, delta: float, reason: str,
               ref: str = "") -> None:
    user.balance = round(user.balance + delta, 6)
    db.add(models.LedgerEntry(user_id=user.id, delta=round(delta, 6), reason=reason,
                              ref=ref, balance_after=user.balance))
    db.add(user)


def expire_stale_orders(db: Session) -> int:
    now = models.utcnow()
    stale = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.pending,
        models.Order.expires_at < now).all()
    for o in stale:
        o.status = models.OrderStatus.expired
        db.add(o)
    db.commit()
    return len(stale)


def create_order(db: Session, user: models.User, order_type: models.OrderType,
                 chain: str, base_price: float, ref_id: int | None = None) -> models.Order:
    if chain not in CHAINS:
        raise ValueError(f"暂不支持的链:{chain}(可选 solana / base)")
    if not _addr_for(chain):
        raise ValueError(f"{chain} 收款地址未配置")
    expire_stale_orders(db)
    # 找一个未被 pending 占用的尾数
    used = {round(o.amount, 6) for o in db.query(models.Order).filter(
        models.Order.chain == chain,
        models.Order.status == models.OrderStatus.pending).all()}
    seq = 1
    while round(base_price + seq * TAIL, 6) in used:
        seq += 1
        if seq > 500_000:
            raise RuntimeError("尾数耗尽")
    amount = round(base_price + seq * TAIL, 6)
    order = models.Order(
        user_id=user.id, order_type=order_type, ref_id=ref_id, chain=chain,
        amount=amount,
        expires_at=models.utcnow() + dt.timedelta(minutes=get_settings().order_expire_minutes))
    db.add(order)
    db.commit()
    return order


def payment_instructions(order: models.Order) -> dict:
    return {
        "order_id": order.id,
        "chain": order.chain,
        "token": "USDT 或 USDC" if order.chain == "solana" else "USDC",
        "amount": f"{order.amount:.6f}",
        "address": _addr_for(order.chain),
        "expires_in_minutes": int((order.expires_at - models.utcnow()).total_seconds() // 60),
        "note": "金额必须精确到最后一位小数,这是系统认出这笔付款是你的唯一方式。",
    }


def match_incoming_payment(db: Session, chain: str, amount: float,
                           txhash: str) -> models.Order | None:
    """扫链 webhook 调这里:金额→订单。返回被确认的订单(或 None)。"""
    expire_stale_orders(db)
    # 防重放:同 txhash 只记一次
    if db.query(models.Order).filter(models.Order.txhash == txhash).first():
        return None
    order = db.query(models.Order).filter(
        models.Order.chain == chain,
        models.Order.status == models.OrderStatus.pending,
        models.Order.amount >= amount - AMOUNT_EPS,
        models.Order.amount <= amount + AMOUNT_EPS,
    ).first()
    if order is None:
        return None
    order.status = models.OrderStatus.paid
    order.txhash = txhash
    order.paid_at = models.utcnow()
    user = db.get(models.User, order.user_id)
    # 入账 + 立刻按订单类型消费,资金流都走 ledger,可审计
    ledger_add(db, user, order.amount, "deposit", f"order:{order.id}")
    _fulfill(db, order, user)
    db.commit()
    return order


def _fulfill(db: Session, order: models.Order, user: models.User) -> None:
    from . import greetings  # 延迟导入避免环
    if order.order_type == models.OrderType.greeting:
        ledger_add(db, user, -get_settings().price_greeting, "greeting",
                   f"greeting:{order.ref_id}")
        greetings.activate(db, order.ref_id)
    elif order.order_type == models.OrderType.extra_recos:
        ledger_add(db, user, -get_settings().price_extra_recos, "extra_recos",
                   f"order:{order.id}")
        # 实际加推在 API 层调 matching.get_daily_recommendations(extra=True)


def pay_with_balance(db: Session, user: models.User, order_type: models.OrderType,
                     price: float, ref_id: int | None = None) -> bool:
    """余额够就直接扣,不走链上。"""
    if user.balance + AMOUNT_EPS < price:
        return False
    from . import greetings
    ledger_add(db, user, -price, order_type.value,
               f"{order_type.value}:{ref_id or ''}")
    if order_type == models.OrderType.greeting and ref_id:
        greetings.activate(db, ref_id)
    db.commit()
    return True
