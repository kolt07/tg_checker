import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.counters import refresh_progress
from app.lookup.mapping import scrub_row_fields
from app.models import CheckLane, PhoneCheck, PhoneCheckRow
from app.security import decrypt_text, encrypt_text


def enrich_key(check_id: object) -> str:
    return f"tgchecker:enrich:{check_id}"


class CheckHooks:
    def __init__(
        self,
        db: AsyncSession,
        redis: Redis,
        check: PhoneCheck,
        lost: asyncio.Event,
        lane: CheckLane | None = None,
    ) -> None:
        self.db = db
        self.redis = redis
        self.check = check
        self.lane = lane
        self._lost = lost

    def lock_lost(self) -> bool:
        return self._lost.is_set()

    async def update_phone(self, phone: str, fields: dict[str, Any]) -> None:
        cleaned = scrub_row_fields(fields)
        stmt = update(PhoneCheckRow).where(
            PhoneCheckRow.check_id == self.check.id,
            PhoneCheckRow.phone_e164 == phone,
        )
        if self.lane is not None:
            stmt = stmt.where(PhoneCheckRow.lane_id == self.lane.id)
        await self.db.execute(stmt.values(**cleaned))
        await refresh_progress(self.db, self.check)
        self.check.heartbeat_at = datetime.now(timezone.utc)
        await self.db.commit()

    def _scoped(self, stmt: Any) -> Any:
        if self.lane is not None:
            return stmt.where(PhoneCheckRow.lane_id == self.lane.id)
        return stmt

    async def pending_phones(self, limit: int) -> list[str]:
        stmt = self._scoped(
            select(PhoneCheckRow.phone_e164).where(
                PhoneCheckRow.check_id == self.check.id,
                PhoneCheckRow.status.is_(None),
                PhoneCheckRow.phone_e164.is_not(None),
            )
        )
        result = await self.db.execute(
            stmt.group_by(PhoneCheckRow.phone_e164).order_by(func.min(PhoneCheckRow.position)).limit(limit)
        )
        return [phone for (phone,) in result.all() if phone]

    async def enrich_backlog(self) -> list[str]:
        stmt = self._scoped(
            select(PhoneCheckRow.phone_e164).where(
                PhoneCheckRow.check_id == self.check.id,
                PhoneCheckRow.enrich_pending.is_(True),
                PhoneCheckRow.phone_e164.is_not(None),
            )
        )
        result = await self.db.execute(
            stmt.group_by(PhoneCheckRow.phone_e164).order_by(func.min(PhoneCheckRow.position))
        )
        return [phone for (phone,) in result.all() if phone]

    async def save_enrich_secret(self, phone: str, user_id: int, access_hash: int) -> None:
        token = encrypt_text(
            json.dumps({"user_id": int(user_id), "access_hash": int(access_hash)}),
            _fernet(),
        )
        key = enrich_key(self.check.id)
        await self.redis.hset(key, phone, token)
        await self.redis.expire(key, 6 * 3600)

    async def peek_enrich_secret(self, phone: str) -> tuple[int, int] | None:
        key = enrich_key(self.check.id)
        token = await self.redis.hget(key, phone)
        if not token:
            return None
        try:
            data = json.loads(decrypt_text(token, _fernet()))
            return int(data["user_id"]), int(data["access_hash"])
        except Exception:
            await self.redis.hdel(key, phone)
            return None

    async def drop_enrich_secret(self, phone: str) -> None:
        await self.redis.hdel(enrich_key(self.check.id), phone)

    def _cleanup_target(self) -> PhoneCheck | CheckLane:
        return self.lane if self.lane is not None else self.check

    async def defer_delete(self, phones: list[str]) -> None:
        target = self._cleanup_target()
        current = list(target.cleanup_phones or [])
        for phone in phones:
            if phone not in current:
                current.append(phone)
        target.cleanup_phones = current
        await self.db.commit()

    async def clear_cleanup(self, phones: list[str]) -> None:
        target = self._cleanup_target()
        remove = set(phones)
        target.cleanup_phones = [item for item in (target.cleanup_phones or []) if item not in remove]
        target.cleanup_attempts = 0
        await self.db.commit()


def _fernet() -> Any:
    from app.security import get_fernet

    return get_fernet()
