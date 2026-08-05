"""匹配引擎:硬过滤 + 软性打分。池子小(<1万)时全量算,足够快。"""
import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import llm, models
from .config import get_settings

GOAL_COMPAT = {  # 感情目标兼容矩阵
    "serious": {"serious", "open_chat"},
    "open_chat": {"serious", "open_chat", "friends", "casual"},
    "friends": {"open_chat", "friends"},
    "casual": {"open_chat", "casual"},
}


def _gender_ok(a: models.User, b: models.User) -> bool:
    def want(u: models.User, other: models.User) -> bool:
        return u.seeking == "any" or u.seeking == other.gender
    return want(a, b) and want(b, a)


def _age_ok(a: models.User, b: models.User) -> bool:
    ta, tb = a.age(), b.age()
    if ta is None or tb is None:
        return False
    return (a.age_min <= tb <= a.age_max) and (b.age_min <= ta <= b.age_max)


def _distance_ok(a: models.User, b: models.User) -> bool:
    # 宽松版:任一方接受异地/线上即可;都要求同城则比对城市
    loose = {"long_distance", "online_ok"}
    if a.distance_pref in loose or b.distance_pref in loose:
        return True
    same_city = (a.city or "").strip() == (b.city or "").strip()
    if a.distance_pref == "same_city" or b.distance_pref == "same_city":
        return same_city
    return True  # same_country 级别:v1 无国家字段,城市不同也放行


def _goal_ok(a: models.User, b: models.User) -> bool:
    return b.goal in GOAL_COMPAT.get(a.goal, set()) and a.goal in GOAL_COMPAT.get(b.goal, set())


def hard_filter(db: Session, user: models.User) -> list[models.User]:
    candidates = db.query(models.User).filter(
        models.User.id != user.id,
        models.User.status == models.UserStatus.active,
    ).all()
    return [c for c in candidates
            if _gender_ok(user, c) and _age_ok(user, c)
            and _distance_ok(user, c) and _goal_ok(user, c)]


def _recent_target_ids(db: Session, user_id: int, days: int) -> set[int]:
    since = dt.date.today() - dt.timedelta(days=days)
    rows = db.query(models.Recommendation.target_id).filter(
        models.Recommendation.user_id == user_id,
        models.Recommendation.date >= since).all()
    return {r[0] for r in rows}


def _exposure_today(db: Session, target_id: int) -> int:
    return db.query(func.count(models.Recommendation.id)).filter(
        models.Recommendation.target_id == target_id,
        models.Recommendation.date == dt.date.today()).scalar() or 0


def score(user: models.User, cand: models.User, seen_before: bool) -> float:
    up, cp = user.profile, cand.profile
    emb = llm.cosine(up.embedding or [], cp.embedding or []) if up and cp else 0.0
    emb = (emb + 1) / 2  # 归一到 0~1
    tags_u = set((up.hobby_tags or [])) if up else set()
    tags_c = set((cp.hobby_tags or [])) if cp else set()
    overlap = len(tags_u & tags_c) / max(len(tags_u | tags_c), 1)
    fresh = 0.0 if seen_before else 1.0
    return 0.5 * emb + 0.2 * overlap + 0.2 * cand.reputation + 0.1 * fresh


def pool_open(db: Session) -> bool:
    n = db.query(func.count(models.User.id)).filter(
        models.User.status == models.UserStatus.active).scalar() or 0
    return n >= get_settings().min_pool_to_open


def get_daily_recommendations(db: Session, user: models.User,
                              extra: bool = False) -> list[dict]:
    """返回今日推荐(幂等:当天已生成则直接返回;extra=True 强制加一批)。"""
    s = get_settings()
    today = dt.date.today()
    existing = db.query(models.Recommendation).filter_by(
        user_id=user.id, date=today).order_by(models.Recommendation.id).all()
    if existing and not extra:
        return [_present(db, r) for r in existing]

    cooldown_ids = _recent_target_ids(db, user.id, s.rerecommend_cooldown_days)
    already_today = {r.target_id for r in existing}
    cands = [c for c in hard_filter(db, user)
             if c.id not in cooldown_ids and c.id not in already_today
             and _exposure_today(db, c.id) < s.daily_exposure_cap]

    ranked = sorted(cands, key=lambda c: score(user, c, False), reverse=True)
    picked = ranked[:s.daily_reco_count]

    new_rows = []
    for c in picked:
        sc = score(user, c, False)
        reason = _make_reason(user, c)
        row = models.Recommendation(user_id=user.id, target_id=c.id, date=today,
                                    reason=reason, score=sc)
        db.add(row)
        new_rows.append(row)
    db.commit()
    return [_present(db, r) for r in existing + new_rows]


def _make_reason(user: models.User, cand: models.User) -> str:
    up = user.profile.answers if user.profile else {}
    cp = cand.profile.answers if cand.profile else {}
    shared = list(set(llm.extract_hobby_tags(up.get("q11", ""))) &
                  set(llm.extract_hobby_tags(cp.get("q11", ""))))
    return llm.recommend_reason(
        llm.profile_text(up, user.city, user.goal),
        llm.profile_text(cp, cand.city, cand.goal),
        cand.nickname or "TA", shared)


def _present(db: Session, r: models.Recommendation) -> dict:
    from .photos import signed_photo_urls
    t = db.get(models.User, r.target_id)
    a = (t.profile.answers or {}) if t.profile else {}
    return {
        "target_id": t.id,
        "nickname": t.nickname,
        "age": t.age(),
        "city": t.city,
        "goal": t.goal,
        "body": a.get("body", ""),
        "edu": a.get("edu", ""),
        "intro": a.get("q9", ""),
        "message_to_future": a.get("q18", ""),
        "photos": signed_photo_urls(t),
        "reason": r.reason,
    }


def refresh_profile_derivatives(db: Session, user: models.User) -> None:
    """问卷完成/修改后调用:重算 embedding 与爱好标签。"""
    if not user.profile:
        return
    answers = user.profile.answers or {}
    user.profile.embedding = llm.embed_text(
        llm.profile_text(answers, user.city, user.goal))
    user.profile.hobby_tags = llm.extract_hobby_tags(answers.get("q11", ""))
    db.add(user.profile)
    db.commit()
