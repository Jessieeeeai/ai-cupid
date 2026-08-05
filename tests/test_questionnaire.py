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
    """圈内题(q15)可跳过。"""
    from tests.conftest import ANSWERS
    answers = list(ANSWERS)
    answers[12] = "跳过"  # 第13题:圈内题
    code = register_full(client, answers)
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert "q15" not in me["answers"]
    assert me["status"] == "active"


def test_email_notify_choice(client):
    code = register_full(client)  # 最后一题填了邮箱
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert me["status"] == "active"


def test_numeric_option_answer(client):
    """选择题回数字 = 选第N个选项(聊天场景的'按钮')。"""
    r = client.post("/api/register/start").json()
    code = r["visit_code"]
    client.post("/api/answer", json={"visit_code": code, "answer": "小数"})
    client.post("/api/answer", json={"visit_code": code, "answer": "1995-08-20"})
    res = client.post("/api/answer", json={"visit_code": code, "answer": "1"}).json()  # 性别→男
    assert res["error"] is None
    res = client.post("/api/answer", json={"visit_code": code, "answer": "2"}).json()  # 想找→女
    assert res["error"] is None
    client.post("/api/answer", json={"visit_code": code, "answer": "上海"})
    client.post("/api/answer", json={"visit_code": code, "answer": "180cm"})
    res = client.post("/api/answer", json={"visit_code": code, "answer": "1"}).json()  # 学历→本科
    assert res["error"] is None
    res = client.post("/api/answer", json={"visit_code": code, "answer": "1"}).json()  # 目标→认真长期
    assert res["error"] is None
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert me["reg_progress"].startswith("8/")
    assert me["answers"]["edu"] == "本科"


def test_update_answer(client):
    code = register_full(client)
    r = client.post("/api/update_answer",
                    json={"visit_code": code, "key": "q11", "answer": "滑雪、冲浪"})
    assert r.status_code == 200
    me = client.post("/api/me", json={"visit_code": code}).json()
    assert me["answers"]["q11"] == "滑雪、冲浪"
