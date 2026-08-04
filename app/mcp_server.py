"""MCP server(streamable HTTP):用户在 Claude/ChatGPT 里加一个 URL 就能用。

每个工具返回的 dict 里带 `instruction` 字段——那是给 AI 看的转述指导,
保证不同 AI 的语气/流程一致。
"""
from mcp.server.fastmcp import FastMCP

from . import greetings, matching, models, notify, payments, photos, questionnaire
from .config import get_settings
from .db import SessionLocal
from .security import hash_visit_code, new_visit_code

def build_mcp() -> FastMCP:
    """工厂:每个 app 实例一个 FastMCP(session manager 不可复用)。"""
    mcp = FastMCP(
        "hongniang",
        instructions=(
            "这是一个 AI 红娘服务。你(AI)代表用户与本服务交互:帮用户注册答题、"
            "查看每日推荐、发起/回应打招呼。规则:1) 用户的回访码是唯一凭证,注册后"
            "务必提醒用户保存;2) 逐题引导,自然对话,不要一次抛多题;3) 涉及付款时,"
            "把金额、地址、'金额必须一分不差'讲清楚;4) 转述拒绝消息时语气委婉。"),
    )
    _register_tools(mcp)
    return mcp


def _db():
    return SessionLocal()


def _auth(db, visit_code: str) -> models.User:
    u = db.query(models.User).filter_by(
        visit_code_hash=hash_visit_code(visit_code)).first()
    if not u:
        raise ValueError("回访码不对,请让用户检查;丢失可通过注册时的通知渠道找回")
    u.last_seen_at = models.utcnow()
    db.commit()
    return u


def _register_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def register_start() -> dict:
        """开始注册:创建新用户,返回回访码和第一道问卷题。"""
        db = _db()
        try:
            code = new_visit_code()
            u = models.User(visit_code_hash=hash_visit_code(code))
            db.add(u)
            db.commit()
            q = questionnaire.current_question(u)
            return {"visit_code": code, "first_question": q.text, "hint": q.hint,
                    "instruction": ("先把回访码给用户并强调保存好(这是唯一凭证),"
                                    "然后开始逐题提问。")}
        finally:
            db.close()


    @mcp.tool()
    def answer_question(visit_code: str, answer: str) -> dict:
        """提交当前问卷题的答案,返回下一题(或完成提示)。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            res = questionnaire.answer(db, u, answer)
            if res["done"] and not res["error"]:
                matching.refresh_profile_derivatives(db, u)
            res["instruction"] = ("有error就温和地让用户重答;完成后按extra的说明引导"
                                  "上传照片/绑定通知。")
            return res
        finally:
            db.close()


    @mcp.tool()
    def get_upload_link(visit_code: str) -> dict:
        """获取一次性照片上传链接(15分钟有效,最多3张)。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            tok = photos.create_upload_token(db, u)
            return {"upload_url": photos.upload_url(tok),
                    "instruction": "把链接给用户,让TA点开从相册选照片(至少1张露脸)。"}
        finally:
            db.close()


    @mcp.tool()
    def get_recommendations(visit_code: str) -> dict:
        """今日推荐(最多3个,含照片链接+推荐理由),并带出未读事件。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            if u.status != models.UserStatus.active:
                q = questionnaire.current_question(u)
                return {"error": "问卷未完成", "next_question": q.text if q else None}
            events = notify.unread_events(db, u)
            if not matching.pool_open(db):
                return {"pool_open": False, "unread_events": events,
                        "instruction": "告诉用户红娘还在攒匹配池,开放后会通知。有未读事件先转述。"}
            recos = matching.get_daily_recommendations(db, u)
            return {"pool_open": True, "recommendations": recos, "unread_events": events,
                    "instruction": ("逐个展示:昵称/年龄/城市/照片链接/推荐理由。"
                                    "告诉用户想认识谁就说,1U 可以打招呼。"
                                    "未读事件(有人打招呼/被同意等)优先转述。")}
        finally:
            db.close()


    @mcp.tool()
    def send_greeting(visit_code: str, target_id: int, message: str,
                      chain: str = "solana") -> dict:
        """向某个推荐对象付费打招呼。余额够直接扣;否则返回付款指引(唯一尾数金额)。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            s = get_settings()
            try:
                g = greetings.create(db, u, target_id, message)
            except greetings.GreetingError as e:
                return {"error": str(e)}
            if g.status != models.GreetingStatus.awaiting_payment:
                return {"greeting_id": g.id, "status": g.status.value}
            if payments.pay_with_balance(db, u, models.OrderType.greeting,
                                         s.price_greeting, ref_id=g.id):
                return {"greeting_id": g.id, "status": "pending", "paid": "balance",
                        "instruction": "告诉用户已用余额支付,对方已收到打招呼,等回应即可。"}
            order = payments.create_order(db, u, models.OrderType.greeting,
                                          chain, s.price_greeting, ref_id=g.id)
            return {"greeting_id": g.id, "status": "awaiting_payment",
                    "payment": payments.payment_instructions(order),
                    "instruction": ("把链/代币/地址/金额展示给用户,强调金额必须精确到"
                                    "最后一位小数。付完可用 check_payment 查状态。")}
        finally:
            db.close()


    @mcp.tool()
    def check_payment(visit_code: str, order_id: int) -> dict:
        """查询订单到账状态。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            payments.expire_stale_orders(db)
            o = db.get(models.Order, order_id)
            if not o or o.user_id != u.id:
                return {"error": "订单不存在"}
            return {"order_id": o.id, "status": o.status.value, "txhash": o.txhash}
        finally:
            db.close()


    @mcp.tool()
    def get_inbox(visit_code: str) -> dict:
        """查看待处理的打招呼(别人付费想认识用户,查看免费)。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            return {"pending_greetings": greetings.inbox(db, u),
                    "instruction": ("展示每条打招呼:对方资料+留言+剩余时限。"
                                    "提醒用户:查看免费,同意后双方互换联系方式。")}
        finally:
            db.close()


    @mcp.tool()
    def respond_greeting(visit_code: str, greeting_id: int, accept: bool) -> dict:
        """同意或拒绝一条打招呼。同意=互换联系方式;拒绝=对方1U退回其余额。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            try:
                res = greetings.respond(db, u, greeting_id, accept)
            except greetings.GreetingError as e:
                return {"error": str(e)}
            res["instruction"] = ("matched时把contact_exchange里的联系方式给用户,"
                                  "并说明对方也同时看到了用户的联系方式。")
            return res
        finally:
            db.close()


    @mcp.tool()
    def get_contact(visit_code: str, greeting_id: int) -> dict:
        """匹配成功后(再次)查看对方联系方式。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            g = db.get(models.Greeting, greeting_id)
            if not g:
                return {"error": "找不到这条记录"}
            try:
                return greetings.contact_card(db, g, viewer=u)
            except greetings.GreetingError as e:
                return {"error": str(e)}
        finally:
            db.close()


    @mcp.tool()
    def get_my_profile(visit_code: str) -> dict:
        """查看自己的资料、问卷进度、照片和未读事件。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            q = questionnaire.current_question(u)
            return {"nickname": u.nickname, "status": u.status.value,
                    "reg_progress": f"{u.reg_step}/{len(questionnaire.QUESTIONS)}",
                    "next_question": q.text if q else None,
                    "answers": (u.profile.answers if u.profile else {}),
                    "photos": photos.signed_photo_urls(u),
                    "balance": u.balance,
                    "unread_events": notify.unread_events(db, u)}
        finally:
            db.close()


    @mcp.tool()
    def update_answer(visit_code: str, question_key: str, new_answer: str) -> dict:
        """修改某道问卷题的答案(如 q11 爱好、contact 联系方式)。用户说'重填问卷/改答案'时用。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            qmap = {q.key: q for q in questionnaire.QUESTIONS}
            q = qmap.get(question_key)
            if not q:
                return {"error": f"没有这个题目key。可用:{', '.join(qmap)}"}
            if q.validate:
                ok, err = q.validate(new_answer)
                if not ok:
                    return {"error": err}
            questionnaire._apply_answer(db, u, q, new_answer)
            db.commit()
            matching.refresh_profile_derivatives(db, u)
            return {"ok": True, "updated": question_key}
        finally:
            db.close()


    @mcp.tool()
    def get_balance(visit_code: str) -> dict:
        """查余额与最近流水(余额不可提现,可用于打招呼/加看推荐)。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            rows = db.query(models.LedgerEntry).filter_by(user_id=u.id)\
                .order_by(models.LedgerEntry.id.desc()).limit(20).all()
            return {"balance": u.balance,
                    "history": [{"delta": r.delta, "reason": r.reason,
                                 "at": r.created_at.isoformat()} for r in rows]}
        finally:
            db.close()


    @mcp.tool()
    def get_extra_recommendations(visit_code: str, chain: str = "solana") -> dict:
        """当天加看3个推荐(0.5U)。余额够直接扣,否则返回付款指引。"""
        db = _db()
        try:
            u = _auth(db, visit_code)
            s = get_settings()
            if payments.pay_with_balance(db, u, models.OrderType.extra_recos,
                                         s.price_extra_recos):
                return {"paid": "balance",
                        "recommendations": matching.get_daily_recommendations(
                            db, u, extra=True)}
            order = payments.create_order(db, u, models.OrderType.extra_recos,
                                          chain, s.price_extra_recos)
            return {"paid": "pending",
                    "payment": payments.payment_instructions(order),
                    "instruction": "付款确认后再调 get_recommendations 就能看到新一批。"}
        finally:
            db.close()
