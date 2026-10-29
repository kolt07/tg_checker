from redis.asyncio import Redis

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

_REFRESH = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def session_lock_key(fingerprint: str) -> str:
    return f"tgchecker:lock:session:{fingerprint}"


def check_lock_key(check_id: object) -> str:
    return f"tgchecker:lock:check:{check_id}"


def cooldown_key(fingerprint: str) -> str:
    return f"tgchecker:cooldown:{fingerprint}"


class SessionLock:
    def __init__(self, redis: Redis, key: str, owner: str) -> None:
        self.redis = redis
        self.key = key
        self.owner = owner

    async def acquire(self, ttl: int = 120) -> bool:
        return bool(await self.redis.set(self.key, self.owner, nx=True, ex=ttl))

    async def refresh(self, ttl: int = 120) -> bool:
        result = await self.redis.eval(_REFRESH, 1, self.key, self.owner, str(ttl))
        return bool(result)

    async def release(self) -> None:
        await self.redis.eval(_RELEASE, 1, self.key, self.owner)


async def set_cooldown(redis: Redis, fingerprint: str, seconds: int) -> None:
    await redis.set(cooldown_key(fingerprint), "1", ex=max(int(seconds), 1))


async def cooldown_ttl(redis: Redis, fingerprint: str) -> int:
    ttl = await redis.ttl(cooldown_key(fingerprint))
    if ttl is None or int(ttl) < 0:
        return 0
    return int(ttl)
