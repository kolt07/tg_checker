from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.redis_client import create_redis
from app.security import api_key_matches


async def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if not api_key_matches(x_api_key or "", get_settings().api_key):
        raise HTTPException(status_code=401, detail="Невірний API-ключ")


async def db_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def redis_conn() -> AsyncIterator[Redis]:
    client = create_redis()
    try:
        yield client
    finally:
        await client.aclose()


Db = Annotated[AsyncSession, Depends(db_session)]
RedisClient = Annotated[Redis, Depends(redis_conn)]
