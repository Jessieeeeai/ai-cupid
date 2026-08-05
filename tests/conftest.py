import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HN_DATABASE_URL"] = "sqlite:///./test_hongniang.db"
os.environ["HN_SOLANA_ADDRESS"] = "So1TestAddr111111111111111111111111"
os.environ["HN_BASE_ADDRESS"] = "0xTestBaseAddr"
# 门槛默认0(不设限);test_pool_gate 单独用 monkeypatch 测试门槛机制
os.environ["HN_PHOTO_DIR"] = "./test_photos"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine, SessionLocal  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    from main import create_app
    from app.db import init_db
    init_db()
    with TestClient(create_app()) as c:
        yield c


ANSWERS = [
    "小明", "1995-08-20", "男", "女", "上海",           # 1-5 基本信息
    "178cm/70kg", "本科 复旦",                          # 6-7 身高体重/学历
    "认真长期", "同城", "25-35",                         # 8-10 目标/距离/年龄
    "web3 程序员一枚,爱笑爱折腾",                        # 11 自我介绍
    "爬山、撸猫、看电影",                                # 12 爱好
    "2021年牛市进的圈,BTC本位",                          # 13 圈内(可跳)
    "希望我们都能做真实的自己",                           # 14 想说的话
    "tg: @xiaoming", "xiaoming@example.com",            # 15-16 联系/通知
]


def register_full(client, answers=None):
    """走完整个注册,返回回访码。"""
    r = client.post("/api/register/start").json()
    code = r["visit_code"]
    for a in (answers or ANSWERS):
        res = client.post("/api/answer", json={"visit_code": code, "answer": a}).json()
        assert res.get("error") is None, f"答'{a}'出错: {res}"
    return code
