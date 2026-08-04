from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_settings

settings = get_settings()

# SQLite:确保数据目录存在(Railway/Docker 挂载卷场景)
if settings.database_url.startswith("sqlite:///"):
    import os
    _p = settings.database_url.replace("sqlite:///", "", 1)
    _d = os.path.dirname(os.path.abspath(_p))
    os.makedirs(_d, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    from . import models  # noqa: F401  确保模型已注册
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
