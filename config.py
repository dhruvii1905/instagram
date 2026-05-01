from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Instagram Graph API
    instagram_access_token: str = ""
    instagram_business_account_id: str = ""
    instagram_api_version: str = "v21.0"

    # PostgreSQL
    database_url: str = "postgresql://iguser:igpass@localhost:5432/igdb"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # API rate limiting (requests per hour)
    instagram_api_rate_limit: int = 200

    # Job intervals (seconds)
    sync_posts_interval: int = 300        # every 5 min
    sync_engagement_interval: int = 120   # every 2 min
    score_leads_interval: int = 60        # every 1 min
    hashtag_analysis_interval: int = 300

    # Lead scoring weights
    score_comment: int = 5
    score_like: int = 2
    score_repeat: int = 10

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
