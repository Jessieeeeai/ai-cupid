import datetime as dt

from app import models
from app.db import SessionLocal
from tests.conftest import ANSWERS, register_full
from tests.test_matching import FEMALE


def _setup_pair(client):
    code_m = register_full(client, ANSWERS)
    code_f = register_full(client, FEMALE)
    r = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    target = next(x for x in r["recommendations"] if x["nickname"] == "小红")
    return code_m, code_f, target["target_id"]


def _pay(client, payment):
    """模拟扫链 webhook 到账。"""
    return client.post(f"/webhooks/chain/{payment['chain']}",
                       json={"amount": float(payment["amount"]),
                             "txhash": "tx_" + payment["amount"]}).json()


def test_greeting_payment_and_match(client):
    code_m, code_f, target_id = _setup_pair(client)
    g = client.post("/api/greeting", json={
        "visit_code": code_m, "target_id": target_id,
        "message": "你好,一起爬山吗", "chain": "solana"}).json()
    assert g["status"] == "awaiting_payment"
    p = g["payment"]
    assert p["amount"].startswith("1.000")
    assert p["address"]

    hook = _pay(client, p)
    assert hook["confirmed_orders"]

    # 对方信箱里能看到
    inbox = client.post("/api/inbox", json={"visit_code": code_f}).json()
    assert len(inbox["pending_greetings"]) == 1
    gid = inbox["pending_greetings"][0]["greeting_id"]
    assert inbox["pending_greetings"][0]["message"] == "你好,一起爬山吗"

    # 同意 → 互换联系方式
    res = client.post("/api/greeting/respond",
                      json={"visit_code": code_f, "greeting_id": gid,
                            "accept": True}).json()
    assert res["status"] == "matched"
    assert "xiaoming" in res["contact_exchange"]["contact"]

    # 发起方也能查到对方联系方式
    c = client.post("/api/contact",
                    json={"visit_code": code_m, "greeting_id": gid}).json()
    assert "xiaohong" in c["contact"]


def test_reject_refunds_to_balance(client):
    code_m, code_f, target_id = _setup_pair(client)
    g = client.post("/api/greeting", json={
        "visit_code": code_m, "target_id": target_id,
        "message": "hi", "chain": "base"}).json()
    _pay(client, g["payment"])
    inbox = client.post("/api/inbox", json={"visit_code": code_f}).json()
    gid = inbox["pending_greetings"][0]["greeting_id"]
    res = client.post("/api/greeting/respond",
                      json={"visit_code": code_f, "greeting_id": gid,
                            "accept": False}).json()
    assert res["status"] == "rejected"
    bal = client.post("/api/balance", json={"visit_code": code_m}).json()
    assert abs(bal["balance"] - 1.0) < 1e-6          # 1U 回余额
    reasons = [h["reason"] for h in bal["history"]]
    assert "refund" in reasons

    # 被拒后30天冷却:再次打招呼被拦
    g2 = client.post("/api/greeting", json={
        "visit_code": code_m, "target_id": target_id, "message": "再试试"})
    assert g2.status_code == 400


def test_balance_pays_next_greeting(client):
    """退款余额可直接支付下一次打招呼,不用再转账。"""
    code_m, code_f, target_id = _setup_pair(client)
    g = client.post("/api/greeting", json={
        "visit_code": code_m, "target_id": target_id, "message": "hi"}).json()
    _pay(client, g["payment"])
    inbox = client.post("/api/inbox", json={"visit_code": code_f}).json()
    gid = inbox["pending_greetings"][0]["greeting_id"]
    client.post("/api/greeting/respond",
                json={"visit_code": code_f, "greeting_id": gid, "accept": False})
    # 注册第三人,用余额打招呼
    third = list(FEMALE)
    third[0] = "小美"
    third[4] = "上海"
    register_full(client, third)
    db = SessionLocal()
    xiaomei_id = db.query(models.User).filter_by(nickname="小美").first().id
    db.close()
    g2 = client.post("/api/greeting", json={
        "visit_code": code_m, "target_id": xiaomei_id,
        "message": "你好呀"}).json()
    assert g2["paid"] == "balance"
    bal = client.post("/api/balance", json={"visit_code": code_m}).json()
    # 余额只剩下唯一尾数的"零头"(0.000001),说明 1U 已被正确消费
    assert abs(bal["balance"]) < 0.001


def test_unique_amounts(client):
    code_m, _, target_id = _setup_pair(client)
    amounts = set()
    db = SessionLocal()
    users = []
    from tests.conftest import register_full as reg
    # 同链同时开3个订单(3个不同用户各发1个),金额必须互不相同
    for i in range(3):
        ans = list(ANSWERS)
        ans[0] = f"路人{i}"
        code = reg(client, ans)
        g = client.post("/api/greeting", json={
            "visit_code": code, "target_id": target_id,
            "message": "hi", "chain": "solana"}).json()
        amounts.add(g["payment"]["amount"])
    assert len(amounts) == 3
    db.close()


def test_replay_txhash_ignored(client):
    code_m, code_f, target_id = _setup_pair(client)
    g = client.post("/api/greeting", json={
        "visit_code": code_m, "target_id": target_id, "message": "hi"}).json()
    p = g["payment"]
    _pay(client, p)
    hook2 = _pay(client, p)  # 同 txhash 重放
    assert hook2["confirmed_orders"] == []


def test_greeting_expiry_refund(client):
    code_m, code_f, target_id = _setup_pair(client)
    g = client.post("/api/greeting", json={
        "visit_code": code_m, "target_id": target_id, "message": "hi"}).json()
    _pay(client, g["payment"])
    # 手动把 paid_at 拨回 73 小时前
    db = SessionLocal()
    gr = db.query(models.Greeting).first()
    gr.paid_at = models.utcnow() - dt.timedelta(hours=73)
    db.commit()
    db.close()
    r = client.post("/internal/tick").json()
    assert r["expired_greetings"] == 1
    bal = client.post("/api/balance", json={"visit_code": code_m}).json()
    assert abs(bal["balance"] - 1.0) < 1e-6


def test_daily_greeting_cap(client):
    code_m, code_f, target_id = _setup_pair(client)
    # 注册6个女生,尝试发6次(上限5)
    ids = [target_id]
    for i in range(5):
        ans = list(FEMALE)
        ans[0] = f"姑娘{i}"
        code = register_full(client, ans)
        r = client.post("/api/recommendations", json={"visit_code": code}).json()
    r = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    ok = 0
    fail = 0
    # 需要至少6个可打招呼对象;逐个发,余额直付以简化
    db = SessionLocal()
    u = db.query(models.User).filter_by(nickname="小明").first()
    from app import payments as pay
    pay.ledger_add(db, u, 10.0, "deposit", "test")
    db.commit()
    all_f = db.query(models.User).filter(models.User.gender == "female").all()
    db.close()
    for f in all_f[:6]:
        resp = client.post("/api/greeting", json={
            "visit_code": code_m, "target_id": f.id, "message": "hi"})
        if resp.status_code == 200:
            ok += 1
        else:
            fail += 1
    assert ok == 5 and fail >= 1
