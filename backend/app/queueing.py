import time

from redis.asyncio import Redis

READY_KEY = "tgchecker:ready"
DELAYED_KEY = "tgchecker:delayed"

_ENQUEUE = """
redis.call('LREM', KEYS[1], 0, ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
local delay = tonumber(ARGV[2])
if delay > 0 then
  redis.call('ZADD', KEYS[2], tonumber(ARGV[3]), ARGV[1])
else
  redis.call('LPUSH', KEYS[1], ARGV[1])
end
return 1
"""

_PROMOTE = """
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, id in ipairs(ids) do
  redis.call('ZREM', KEYS[1], id)
  redis.call('LPUSH', KEYS[2], id)
end
return #ids
"""


async def enqueue(redis: Redis, check_id: str, delay: float = 0) -> None:
    delay_seconds = max(0.0, float(delay))
    score = time.time() + delay_seconds
    await redis.eval(_ENQUEUE, 2, READY_KEY, DELAYED_KEY, check_id, str(delay_seconds), str(score))


async def promote_due(redis: Redis) -> None:
    await redis.eval(_PROMOTE, 2, DELAYED_KEY, READY_KEY, str(time.time()))
