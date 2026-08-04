from tests.conftest import ANSWERS, register_full


def test_full_registration(client):
    code = register_full(client)
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert me["status"] == "active"
    assert me["nickname"] == "小明"
    assert me["answers"]["q11"] == "爬山、撸猫、看电影"


def test_bad_visit_code(client):
    r = client.post("/api/me", json={"visit_code": "LOVE-XXXX-YYYY"})
    assert r.status_code == 401


def test_underage_rejected(client):
    r = client.post("/api/register/start").json()
    code = r["visit_code"]
    client.post("/api/answer", json={"visit_code": code, "answer": "小小"})
    res = client.post("/api/answer", json={"visit_code": code, "answer": "2015-01-01"}).json()
    assert res["error"] and "18" in res["error"]


def test_cannot_skip_required(client):
    r = client.post("/api/register/start").json()
    code = r["visit_code"]
    res = client.post("/api/answer", json={"visit_code": code, "answer": "跳过"}).json()
    assert res["error"]


def test_skippable_question(client):
    """q14 雷点可跳过。"""
    code = register_full(client)
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert "q14" not in me["answers"]


def test_email_notify_choice(client):
    code = register_full(client)  # 最后一题填了邮箱
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert me["status"] == "active"


def test_update_answer(client):
    code = register_full(client)
    r = client.post("/api/update_answer",
                    json={"visit_code": code, "key": "q11", "answer": "滑雪、冲浪"})
    assert r.status_code == 200
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert me["answers"]["q11"] == "滑雪、冲浪"
