from tests.conftest import ANSWERS, register_full

FEMALE = ["小红", "1997-03-15", "女", "男", "上海",
          "165cm/50kg", "本科",
          "认真长期", "同城", "25-38",
          "UI设计师,猫奴", "撸猫、爬山、摄影", "跳过",
          "一起去看世界吧", "wechat: xiaohong", "xiaohong@example.com"]

FEMALE_FAR = ["阿花", "1996-05-05", "女", "男", "新加坡",
              "168cm", "硕士及以上",
              "先聊聊看", "异地也行", "25-40",
              "web3产品经理", "健身、看书", "跳过",
              "hi", "tg: @ahua", "ahua@example.com"]


def test_recommendation_flow(client):
    code_m = register_full(client, ANSWERS)
    register_full(client, FEMALE)
    register_full(client, FEMALE_FAR)
    r = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    assert r["pool_open"] is True
    nicks = [x["nickname"] for x in r["recommendations"]]
    assert "小红" in nicks       # 同城互配
    assert "阿花" in nicks       # 她接受异地
    for x in r["recommendations"]:
        assert x["reason"]       # 每个推荐都有理由


def test_gender_filter(client):
    code_m = register_full(client, ANSWERS)
    male2 = list(ANSWERS)
    male2[0] = "小刚"
    register_full(client, male2)  # 另一个找女生的男生
    r = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    nicks = [x["nickname"] for x in r["recommendations"]]
    assert "小刚" not in nicks


def test_age_filter(client):
    code_m = register_full(client, ANSWERS)  # 期望25-35
    too_young = list(FEMALE)
    too_young[0] = "小小红"
    too_young[1] = "2005-01-01"   # 21岁,不在25-35内
    register_full(client, too_young)
    register_full(client, FEMALE)
    r = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    nicks = [x["nickname"] for x in r["recommendations"]]
    assert "小小红" not in nicks and "小红" in nicks


def test_idempotent_daily(client):
    code_m = register_full(client, ANSWERS)
    register_full(client, FEMALE)
    r1 = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    r2 = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    assert [x["target_id"] for x in r1["recommendations"]] == \
           [x["target_id"] for x in r2["recommendations"]]


def test_pool_gate(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "min_pool_to_open", 100)
    code_m = register_full(client, ANSWERS)
    r = client.post("/api/recommendations", json={"visit_code": code_m}).json()
    assert r["pool_open"] is False
