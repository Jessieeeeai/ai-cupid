"""REST API:三入口(MCP/网页/TG bot)共用的业务接口 + 扫链/TG webhook + 静态页。"""
import os

from fastapi import (APIRouter, Depends, File, Form, Header, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import (chains, greetings, matching, models, notify, payments, photos,
               questionnaire)
from .config import get_settings
from .db import get_db
from .security import encrypt, hash_visit_code, new_visit_code

router = APIRouter()
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


# ---------- 鉴权 ----------

def auth(db: Session, visit_code: str) -> models.User:
    u = db.query(models.User).filter_by(
        visit_code_hash=hash_visit_code(visit_code)).first()
    if not u:
        raise HTTPException(401, "回访码不对。丢了的话用注册时留的通知渠道找回。")
    if u.status == models.UserStatus.banned:
        raise HTTPException(403, "账号不可用")
    u.last_seen_at = models.utcnow()
    db.commit()
    return u


class CodeBody(BaseModel):
    visit_code: str


# ---------- 注册与问卷 ----------

@router.post("/api/register/start")
def register_start(db: Session = Depends(get_db)):
    code = new_visit_code()
    u = models.User(visit_code_hash=hash_visit_code(code))
    db.add(u)
    db.commit()
    q = questionnaire.current_question(u)
    return {"visit_code": code,
            "important": "请让用户立刻保存好回访码,这是唯一凭证!",
            "next_question": questionnaire._fmt(q)}


class AnswerBody(CodeBody):
    answer: str


@router.post("/api/answer")
def answer(body: AnswerBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    res = questionnaire.answer(db, u, body.answer)
    if res["done"] and not res["error"]:
        matching.refresh_profile_derivatives(db, u)
    return res


class UpdateBody(CodeBody):
    key: str
    answer: str


@router.post("/api/update_answer")
def update_answer(body: UpdateBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    qmap = {q.key: q for q in questionnaire.QUESTIONS}
    q = qmap.get(body.key)
    if not q:
        raise HTTPException(400, f"没有这个题目:{body.key}")
    if q.validate:
        ok, err = q.validate(body.answer)
        if not ok:
            raise HTTPException(400, err)
    questionnaire._apply_answer(db, u, q, body.answer)
    db.commit()
    matching.refresh_profile_derivatives(db, u)
    return {"ok": True, "updated": body.key}


@router.post("/api/me")
def me(body: CodeBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    q = questionnaire.current_question(u)
    return {
        "nickname": u.nickname, "status": u.status.value,
        "reg_progress": f"{u.reg_step}/{len(questionnaire.QUESTIONS)}",
        "next_question": questionnaire._fmt(q) if q else None,
        "photos": photos.signed_photo_urls(u),
        "answers": (u.profile.answers if u.profile else {}),
        "balance": u.balance,
        "unread_events": notify.unread_events(db, u),
    }


# ---------- 照片 ----------

@router.post("/api/upload_link")
def upload_link(body: CodeBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    tok = photos.create_upload_token(db, u)
    return {"upload_url": photos.upload_url(tok), "expires_minutes": 15,
            "note": "请让用户点开链接,从相册选最多3张照片(至少1张露脸)。"}


@router.get("/upload/{token}", response_class=HTMLResponse)
def upload_page(token: str):
    with open(os.path.join(WEB_DIR, "upload.html"), encoding="utf-8") as f:
        return f.read().replace("{{TOKEN}}", token)


@router.post("/api/upload/{token}")
async def do_upload(token: str, files: list[UploadFile] = File(...),
                    db: Session = Depends(get_db)):
    u = photos.consume_token(db, token)
    if not u:
        raise HTTPException(400, "上传链接已过期,请回到对话里重新获取")
    if len(files) > photos.MAX_PHOTOS:
        raise HTTPException(400, "最多 3 张")
    saved = []
    for i, f in enumerate(files):
        content = await f.read()
        try:
            p = photos.save_photo(db, u, content, f.content_type or "", i)
        except ValueError as e:
            raise HTTPException(400, str(e))
        saved.append(p.storage_key)
    return {"ok": True, "saved": len(saved)}


@router.get("/photos/{key}")
def get_photo(key: str):
    path = os.path.join(get_settings().photo_dir, os.path.basename(key))
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path)


# ---------- 推荐 ----------

@router.post("/api/recommendations")
def recommendations(body: CodeBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    if u.status != models.UserStatus.active:
        raise HTTPException(400, "请先完成问卷")
    if not matching.pool_open(db):
        return {"pool_open": False, "recommendations": [],
                "message": "红娘还在认识大家,匹配池攒够人就开放推荐,会第一时间通知你!",
                "unread_events": notify.unread_events(db, u)}
    recos = matching.get_daily_recommendations(db, u)
    return {"pool_open": True, "recommendations": recos,
            "unread_events": notify.unread_events(db, u)}


class ExtraBody(CodeBody):
    chain: str = "solana"


@router.post("/api/recommendations/extra")
def extra_recommendations(body: ExtraBody, db: Session = Depends(get_db)):
    """当天加看3个:余额够直接扣,否则下单。"""
    u = auth(db, body.visit_code)
    s = get_settings()
    if payments.pay_with_balance(db, u, models.OrderType.extra_recos, s.price_extra_recos):
        return {"paid": "balance",
                "recommendations": matching.get_daily_recommendations(db, u, extra=True)}
    order = payments.create_order(db, u, models.OrderType.extra_recos,
                                  body.chain, s.price_extra_recos)
    return {"paid": "pending", "payment": payments.payment_instructions(order)}


# ---------- 打招呼(双向解锁) ----------

class GreetBody(CodeBody):
    target_id: int
    message: str
    chain: str = "solana"


@router.post("/api/greeting")
def send_greeting(body: GreetBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    s = get_settings()
    try:
        g = greetings.create(db, u, body.target_id, body.message)
    except greetings.GreetingError as e:
        raise HTTPException(400, str(e))
    if g.status != models.GreetingStatus.awaiting_payment:
        return {"greeting_id": g.id, "status": g.status.value}
    if payments.pay_with_balance(db, u, models.OrderType.greeting,
                                 s.price_greeting, ref_id=g.id):
        return {"greeting_id": g.id, "status": "pending", "paid": "balance",
                "note": "已用余额支付,对方已收到你的打招呼。"}
    order = payments.create_order(db, u, models.OrderType.greeting,
                                  body.chain, s.price_greeting, ref_id=g.id)
    return {"greeting_id": g.id, "status": "awaiting_payment",
            "payment": payments.payment_instructions(order)}


class OrderBody(CodeBody):
    order_id: int


@router.post("/api/order/status")
def order_status(body: OrderBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    payments.expire_stale_orders(db)
    o = db.get(models.Order, body.order_id)
    if not o or o.user_id != u.id:
        raise HTTPException(404, "订单不存在")
    return {"order_id": o.id, "status": o.status.value, "txhash": o.txhash}


class RespondBody(CodeBody):
    greeting_id: int
    accept: bool


@router.post("/api/greeting/respond")
def respond_greeting(body: RespondBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    try:
        return greetings.respond(db, u, body.greeting_id, body.accept)
    except greetings.GreetingError as e:
        raise HTTPException(400, str(e))


@router.post("/api/inbox")
def inbox(body: CodeBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    return {"pending_greetings": greetings.inbox(db, u)}


class ContactBody(CodeBody):
    greeting_id: int


@router.post("/api/contact")
def contact(body: ContactBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    g = db.get(models.Greeting, body.greeting_id)
    if not g:
        raise HTTPException(404)
    try:
        return greetings.contact_card(db, g, viewer=u)
    except greetings.GreetingError as e:
        raise HTTPException(400, str(e))


@router.post("/api/balance")
def balance(body: CodeBody, db: Session = Depends(get_db)):
    u = auth(db, body.visit_code)
    rows = db.query(models.LedgerEntry).filter_by(user_id=u.id)\
        .order_by(models.LedgerEntry.id.desc()).limit(20).all()
    return {"balance": u.balance,
            "note": "余额来自退款/多付差额,可用于打招呼和加看推荐,不可提现。",
            "history": [{"delta": r.delta, "reason": r.reason,
                         "at": r.created_at.isoformat()} for r in rows]}


# ---------- Webhooks ----------

@router.post("/webhooks/chain/{chain}")
async def chain_webhook(chain: str, request: Request, key: str = "",
                        x_webhook_secret: str = Header(default=""),
                        db: Session = Depends(get_db)):
    """密钥可放 URL 查询参数 ?key=...(Helius/Alchemy 都不支持自定义header,URL最通用)"""
    s = get_settings()
    if s.chain_webhook_secret and key != s.chain_webhook_secret \
            and x_webhook_secret != s.chain_webhook_secret:
        raise HTTPException(403)
    payload = await request.json()
    confirmed = []
    for amount, txhash in chains.parse_events(chain, payload):
        o = payments.match_incoming_payment(db, chain, amount, txhash)
        if o:
            confirmed.append(o.id)
    return {"confirmed_orders": confirmed}


@router.post("/webhooks/telegram")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    """处理 /start <bind_token>:把 chat_id 绑到用户。"""
    upd = await request.json()
    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if text.startswith("/start") and chat_id:
        parts = text.split()
        if len(parts) > 1:
            u = db.query(models.User).filter_by(tg_bind_token=parts[1]).first()
            if u:
                u.notify_addr_encrypted = encrypt(chat_id)
                u.tg_bind_token = None
                db.commit()
                notify.push(db, u, "bound", "绑定成功!有人对你心动时我会第一时间告诉你。")
    return {"ok": True}


# ---------- 维护任务(cron 每小时打一次;也可用系统 crontab)----------

@router.post("/internal/tick")
def tick(x_webhook_secret: str = Header(default=""), db: Session = Depends(get_db)):
    s = get_settings()
    if s.chain_webhook_secret and x_webhook_secret != s.chain_webhook_secret:
        raise HTTPException(403)
    return {"expired_orders": payments.expire_stale_orders(db),
            "expired_greetings": greetings.expire_stale(db)}


# ---------- 网页入口 ----------

@router.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        return f.read().replace("{{BRAND}}", get_settings().brand_name)
