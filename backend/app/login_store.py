import json
from typing import Any

from redis.asyncio import Redis

from app.security import decrypt_text, encrypt_text, get_fernet


def _login_key(login_id: str) -> str:
    return f"tgchecker:login:{login_id}"


async def store_login(redis: Redis, login_id: str, payload: dict[str, Any]) -> None:
    token = encrypt_text(json.dumps(payload), get_fernet())
    await redis.set(_login_key(login_id), token, ex=600)


async def load_login(redis: Redis, login_id: str) -> dict[str, Any] | None:
    token = await redis.get(_login_key(login_id))
    if not token:
        return None
    try:
        data = json.loads(decrypt_text(token, get_fernet()))
    except Exception:
        await redis.delete(_login_key(login_id))
        return None
    if not isinstance(data, dict):
        await redis.delete(_login_key(login_id))
        return None
    return data


async def delete_login(redis: Redis, login_id: str) -> None:
    await redis.delete(_login_key(login_id))
