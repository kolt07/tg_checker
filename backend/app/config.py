from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str
    session_encryption_key: str
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    default_phone_region: str = "UA"
    resolve_interval_seconds: float = 3.0
    import_chunk_size: int = 5
    import_pause_seconds: float = 5.0
    enrich_interval_seconds: float = 1.0
    max_import_retries: int = 2
    max_phones: int = 5000
    max_accounts: int = 8
    worker_concurrency: int = 4
    db_pool_size: int = 10
    db_max_overflow: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
