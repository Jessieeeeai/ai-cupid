"""LLM/Embedding 适配层。
无 API key 时用离线降级实现(确定性哈希词袋向量 + 模板推荐理由),
整个系统不依赖外部服务也能跑通;配了 key 自动升级。
"""
import hashlib
import math
import re

import httpx

from .config import get_settings

DIM = 256


# ---------- Embedding ----------

def embed_text(text: str) -> list[float]:
    s = get_settings()
    if s.openai_api_key:
        try:
            return _openai_embed(text)
        except Exception:
            pass  # 降级
    return _hash_embed(text)


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    words = re.findall(r"[\w一-鿿]+", text.lower())
    # 中文按字切,英文按词
    tokens: list[str] = []
    for w in words:
        if re.match(r"[一-鿿]", w):
            tokens.extend(list(w))
            tokens.extend(w[i:i + 2] for i in range(len(w) - 1))  # bigram
        else:
            tokens.append(w)
    for t in tokens:
        h = int(hashlib.md5(t.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0 if (h >> 8) % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _openai_embed(text: str) -> list[float]:
    s = get_settings()
    r = httpx.post("https://api.openai.com/v1/embeddings",
                   headers={"Authorization": f"Bearer {s.openai_api_key}"},
                   json={"model": "text-embedding-3-small", "input": text[:6000]},
                   timeout=20)
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


# ---------- 爱好标签归一化 ----------

def extract_hobby_tags(hobby_answer: str) -> list[str]:
    parts = re.split(r"[,,、;;/\s和与及]+", hobby_answer.strip())
    return [p.lower() for p in parts if p.strip()][:10]


# ---------- 推荐理由 ----------

REASON_PROMPT = """你是一个真诚的红娘。根据两个人的资料,写2-3句推荐理由,说明为什么他们可能合适。
要求:必须引用双方答案里的具体细节,禁止空话套话,语气自然温暖,不要用"你们都"开头。
A(收到推荐的人)的资料:{a}
B(被推荐的人,昵称 {b_nick})的资料:{b}
直接输出推荐理由,不要任何前缀。"""


def recommend_reason(a_profile: str, b_profile: str, b_nick: str,
                     shared_tags: list[str]) -> str:
    s = get_settings()
    if s.anthropic_api_key:
        try:
            return _anthropic_reason(a_profile, b_profile, b_nick)
        except Exception:
            pass
    # 离线模板降级
    if shared_tags:
        return (f"你们在「{'、'.join(shared_tags[:3])}」上有共同兴趣,"
                f"{b_nick} 的自我介绍和你的感情目标也比较接近,值得认识一下。")
    return f"{b_nick} 的整体画像和你的期待重合度较高,资料细节里能看到不少共鸣点。"


def _anthropic_reason(a_profile: str, b_profile: str, b_nick: str) -> str:
    s = get_settings()
    r = httpx.post("https://api.anthropic.com/v1/messages",
                   headers={"x-api-key": s.anthropic_api_key,
                            "anthropic-version": "2023-06-01"},
                   json={"model": "claude-haiku-4-5", "max_tokens": 300,
                         "messages": [{"role": "user", "content": REASON_PROMPT.format(
                             a=a_profile[:2000], b=b_profile[:2000], b_nick=b_nick)}]},
                   timeout=30)
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def profile_text(answers: dict, city: str | None, goal: str | None) -> str:
    labels = {"body": "身高体重", "edu": "学历", "q9": "自我介绍",
              "q11": "爱好", "q15": "圈内", "q18": "想说的话"}
    parts = [f"城市:{city}", f"目标:{goal}"]
    parts += [f"{labels.get(k, k)}:{answers.get(k, '')}"
              for k in labels if answers.get(k)]
    return "\n".join(parts)
