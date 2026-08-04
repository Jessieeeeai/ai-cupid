"""全局配置 — 一切品牌/密钥/链地址都从环境变量读,改名只改这里。"""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 品牌
    brand_name: str = "AI丘比特"
    base_url: str = "http://localhost:8000"  # 对外可访问的根地址,生成上传/回访链接用

    # 数据库:默认 SQLite(存 data/ 目录,方便挂持久卷);生产可换 postgresql+psycopg://...
    database_url: str = "sqlite:///./data/hongniang.db"

    # 安全
    secret_key: str = "dev-secret-change-me"  # 用于回访码哈希盐 & 派生加密密钥

    # 收款地址(你的地址)
    solana_address: str = ""
    base_address: str = ""
    # 扫链 webhook 校验密钥(Helius/Alchemy 后台配置同一值)
    chain_webhook_secret: str = ""

    # 定价(单位 U)
    price_greeting: float = 1.0
    price_extra_recos: float = 0.5

    # 通知
    telegram_bot_token: str = ""
    resend_api_key: str = ""
    email_from: str = "hello@example.com"

    # LLM / Embedding(不填则用离线降级实现,功能可跑但匹配质量低)
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # 业务参数
    daily_reco_count: int = 3
    daily_exposure_cap: int = 10      # 每人每天最多出现在多少人的推荐里
    daily_greeting_cap: int = 5       # 每人每天最多发几次打招呼
    greeting_expire_hours: int = 72
    rerecommend_cooldown_days: int = 30
    reject_cooldown_days: int = 30
    order_expire_minutes: int = 15
    min_pool_to_open: int = 0         # 推荐池门槛:0=不设限,注册完立刻可看推荐(已确认)
    photo_dir: str = "./data/photos"  # 本地存储;生产换 R2 时改 storage backend

    class Config:
        env_file = ".env"
        env_prefix = "HN_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
