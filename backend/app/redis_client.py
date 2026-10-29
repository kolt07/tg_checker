from redis.asyncio import Redis


def create_redis() -> Redis:
    from app.config import get_settings

    return Redis.from_url(get_settings().redis_url, decode_responses=True)
