"""16 题问卷:服务器驱动进度,任何入口(MCP/网页/TG)共用同一状态机。

每题定义 key / 文案 / 校验器。校验失败返回 error,不推进进度。
"""
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .security import encrypt, new_token

CHOICE_MAPS = {
    "gender": {"男": "male", "女": "female", "其他": "other"},
    "seeking": {"男": "male", "女": "female", "都可以": "any"},
    "distance": {"同城": "same_city", "同国": "same_country", "异地也行": "long_distance", "纯线上也行": "online_ok"},
    "goal": {"认真长期": "serious", "先聊聊看": "open_chat", "交朋友": "friends", "开放心态": "casual"},
    "notify": {"telegram": "telegram", "tg": "telegram", "邮箱": "email", "email": "email"},
}


@dataclass
class Question:
    key: str
    text: str
    hint: str = ""
    skippable: bool = False
    validate: Callable[[str], tuple[bool, str | None]] | None = None  # (ok, error)
    soft: bool = False  # 软性题存 profile.answers
    # 选择题选项:[{"label": 展示文案, "value": 点击后提交的答案; None=需要用户输入}]
    options: list | None = None


def _v_choice(mapname: str):
    def v(ans: str):
        m = CHOICE_MAPS[mapname]
        key = ans.strip().lower()
        for k, val in m.items():
            if key == k.lower() or key == val:
                return True, None
        return False, f"请从这些里选一个:{' / '.join(m.keys())}"
    return v


def _norm_choice(mapname: str, ans: str) -> str:
    m = CHOICE_MAPS[mapname]
    key = ans.strip().lower()
    for k, val in m.items():
        if key == k.lower() or key == val:
            return val
    raise ValueError(ans)


def _v_birthday(ans: str):
    try:
        d = _parse_date(ans)
    except ValueError:
        return False, "生日格式看不懂,请用 1995-08-20 这种写法"
    age = (dt.date.today() - d).days // 365
    if age < 18:
        return False, "本服务仅面向 18 岁以上用户,抱歉不能为你注册"
    if age > 99:
        return False, "这个生日看起来不太对,再检查一下?"
    return True, None


def _parse_date(ans: str) -> dt.date:
    ans = re.sub(r"[年月/.]", "-", ans.strip()).replace("日", "")
    return dt.date.fromisoformat(ans)


def _v_age_range(ans: str):
    m = re.findall(r"\d+", ans)
    if len(m) < 2:
        return False, "请给一个范围,比如 25-35"
    lo, hi = int(m[0]), int(m[1])
    if not (18 <= lo <= hi <= 99):
        return False, "范围要在 18-99 之间,且前小后大"
    return True, None


def _v_nonempty(ans: str):
    return (len(ans.strip()) > 0, None if ans.strip() else "这题不能跳过哦")


def _opts(*labels: str) -> list:
    return [{"label": l, "value": l} for l in labels]


QUESTIONS: list[Question] = [
    Question("nickname", "1/16 想让别人怎么称呼你?(昵称,不用真名)", validate=_v_nonempty),
    Question("birthday", "2/16 你的生日?(如 1995-08-20,对外只显示年龄)", validate=_v_birthday),
    Question("gender", "3/16 你的性别?", hint="男 / 女 / 其他", validate=_v_choice("gender"),
             options=_opts("男", "女", "其他")),
    Question("seeking", "4/16 你想找的性别?", hint="男 / 女 / 都可以", validate=_v_choice("seeking"),
             options=_opts("男", "女", "都可以")),
    Question("city", "5/16 你目前在哪个城市/国家?(写到城市即可)", validate=_v_nonempty),
    Question("body", "6/16 身高体重?(如 170cm/55kg,体重不想说就只写身高)",
             soft=True, validate=_v_nonempty),
    Question("edu", "7/16 你的学历?(想带上学校就一起写,如 '本科 浙大')",
             soft=True, validate=_v_nonempty,
             options=_opts("本科", "硕士及以上", "大专", "高中及以下") + [
                 {"label": "自己输入(可带学校)", "value": None}]),
    Question("goal", "8/16 你的感情目标?", hint="认真长期 / 先聊聊看 / 交朋友 / 开放心态",
             validate=_v_choice("goal"),
             options=_opts("认真长期", "先聊聊看", "交朋友", "开放心态")),
    Question("distance", "9/16 你能接受的关系距离?", hint="同城 / 同国 / 异地也行 / 纯线上也行",
             validate=_v_choice("distance"),
             options=_opts("同城", "同国", "异地也行", "纯线上也行")),
    Question("age_range", "10/16 期望对方的年龄范围?", hint="点一个或自己输入,如 25-35",
             validate=_v_age_range,
             options=_opts("20-30", "25-35", "30-45", "18-99 不限") + [
                 {"label": "自己输入", "value": None}]),
    Question("q9", "11/16 用一两句话介绍你自己(在做什么、是个什么样的人)",
             soft=True, validate=_v_nonempty),
    Question("q11", "12/16 平时最大的三个爱好?", soft=True, validate=_v_nonempty),
    Question("q15", "13/16 圈内题:你怎么进的 crypto?你信什么?(可跳过)", soft=True, skippable=True,
             options=[{"label": "跳过", "value": "跳过"}, {"label": "自己输入", "value": None}]),
    Question("q18", "14/16 想对未来对象说的一句话(会展示在你的资料卡上)",
             soft=True, validate=_v_nonempty),
    Question("contact", "15/16 匹配成功后,对方用什么联系你?(微信号/TG/邮箱,仅双方同意后互相可见)",
             validate=_v_nonempty),
    Question("notify", "16/16 系统怎么通知你有人对你心动?此信息永不展示给任何用户。",
             hint="选 TG(稍后给绑定链接)或直接输入一个邮箱地址", validate=None,
             options=[{"label": "绑定 Telegram", "value": "TG"},
                      {"label": "用邮箱接收(输入邮箱)", "value": None}]),
]

SKIP_WORDS = {"跳过", "跳", "skip", "pass"}


def current_question(user: models.User) -> Question | None:
    if user.reg_step >= len(QUESTIONS):
        return None
    return QUESTIONS[user.reg_step]


def _apply_answer(db: Session, user: models.User, q: Question, ans: str) -> None:
    ans = ans.strip()
    if q.soft:
        prof = user.profile or models.Profile(user_id=user.id, answers={})
        answers = dict(prof.answers or {})
        answers[q.key] = ans
        prof.answers = answers
        db.add(prof)
        return
    if q.key == "nickname":
        user.nickname = ans[:50]
    elif q.key == "birthday":
        user.birthday = _parse_date(ans)
    elif q.key == "gender":
        user.gender = _norm_choice("gender", ans)
    elif q.key == "seeking":
        user.seeking = _norm_choice("seeking", ans)
    elif q.key == "city":
        user.city = ans[:80]
    elif q.key == "distance":
        user.distance_pref = _norm_choice("distance", ans)
    elif q.key == "goal":
        user.goal = _norm_choice("goal", ans)
    elif q.key == "age_range":
        nums = re.findall(r"\d+", ans)
        user.age_min, user.age_max = int(nums[0]), int(nums[1])

    elif q.key == "contact":
        user.contact_encrypted = encrypt(ans)
    elif q.key == "notify":
        low = ans.lower()
        if "@" in ans:
            user.notify_channel = models.NotifyChannel.email
            user.notify_addr_encrypted = encrypt(ans)
        elif "tg" in low or "telegram" in low:
            user.notify_channel = models.NotifyChannel.telegram
            user.tg_bind_token = new_token(8)
        else:
            raise ValueError("notify")


def answer(db: Session, user: models.User, ans: str) -> dict:
    """提交当前题答案。返回 {done, error, next_question, extra}。

    选择题支持直接回数字:回 "2" = 选第2个选项(聊天场景里的"按钮")。
    """
    q = current_question(user)
    if q is None:
        return {"done": True, "error": None, "next_question": None, "extra": "问卷已完成"}

    # 数字快捷选择
    stripped = ans.strip().rstrip(".。、)")
    if q.options and stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(q.options) and q.options[idx].get("value"):
            ans = q.options[idx]["value"]

    if ans.strip() in SKIP_WORDS:
        if not q.skippable:
            return {"done": False, "error": "这一题不能跳过", "next_question": _fmt(q), "extra": None}
    else:
        if q.validate:
            ok, err = q.validate(ans)
            if not ok:
                return {"done": False, "error": err, "next_question": _fmt(q), "extra": None}
        try:
            _apply_answer(db, user, q, ans)
        except ValueError:
            return {"done": False, "error": "没看懂,再试一次?回复 'TG' 或一个邮箱地址",
                    "next_question": _fmt(q), "extra": None}

    user.reg_step += 1
    nxt = current_question(user)
    extra = None
    if nxt is None:
        user.status = models.UserStatus.active
        extra = _finish_extra(user)
    db.add(user)
    db.commit()
    return {"done": nxt is None, "error": None,
            "next_question": _fmt(nxt) if nxt else None, "extra": extra}


def _fmt(q: Question) -> dict:
    return {"key": q.key, "text": q.text, "hint": q.hint,
            "skippable": q.skippable, "options": q.options}


def _finish_extra(user: models.User) -> str:
    s = get_settings()
    parts = ["问卷完成!接下来请上传 3 张照片(至少 1 张露脸),调用 get_upload_link 获取上传链接。"]
    if user.notify_channel == models.NotifyChannel.telegram:
        parts.append(
            f"你选择了 TG 通知:请打开 Telegram 给机器人发送 /start {user.tg_bind_token} 完成绑定"
            f"(bot 链接见 {s.base_url}/tg)。")
    return " ".join(parts)
