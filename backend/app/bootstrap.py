import asyncio
import logging

from sqlalchemy import text

from app.config import Settings, get_settings
from app.db import Base, get_engine
from app.redis_client import create_redis
from app.security import build_fernet

logger = logging.getLogger("tgchecker.bootstrap")


def validate_settings(settings: Settings) -> None:
    if len(settings.api_key.strip()) < 8:
        raise RuntimeError("API_KEY має містити щонайменше 8 символів")
    try:
        build_fernet(settings.session_encryption_key)
    except ValueError as exc:
        raise RuntimeError("SESSION_ENCRYPTION_KEY має бути ключем Fernet") from exc


async def wait_for_db() -> None:
    engine = get_engine()
    last_error = "error"
    for _ in range(30):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_error = type(exc).__name__
            logger.info("database not ready %s", last_error)
            await asyncio.sleep(1)
    raise RuntimeError(f"База даних недоступна ({last_error})")


async def wait_for_redis() -> None:
    client = create_redis()
    last_error = "error"
    try:
        for _ in range(30):
            try:
                await client.ping()
                return
            except Exception as exc:
                last_error = type(exc).__name__
                logger.info("redis not ready %s", last_error)
                await asyncio.sleep(1)
        raise RuntimeError(f"Redis недоступний ({last_error})")
    finally:
        await client.aclose()


async def create_tables() -> None:
    from app import models  # noqa: F401

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_columns()


async def ensure_columns() -> None:
    statements = [
        """
        ALTER TABLE phone_checks
        ADD COLUMN IF NOT EXISTS account_count INTEGER NOT NULL DEFAULT 1
        """,
        """
        ALTER TABLE phone_checks
        ADD COLUMN IF NOT EXISTS timeout_seconds DOUBLE PRECISION
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'phone_check_rows'
                  AND column_name = 'lane_id'
            ) THEN
                ALTER TABLE phone_check_rows
                ADD COLUMN lane_id UUID REFERENCES check_lanes(id) ON DELETE SET NULL;
            END IF;
        END $$
        """,
        "CREATE INDEX IF NOT EXISTS ix_phone_check_rows_lane_id ON phone_check_rows (lane_id)",
        "CREATE INDEX IF NOT EXISTS ix_check_lanes_check_id ON check_lanes (check_id)",
        "CREATE INDEX IF NOT EXISTS ix_check_lanes_session_id ON check_lanes (session_id)",
    ]
    async with get_engine().begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))


async def prepare_runtime() -> Settings:
    settings = get_settings()
    validate_settings(settings)
    await wait_for_db()
    await wait_for_redis()
    await create_tables()
    return settings
