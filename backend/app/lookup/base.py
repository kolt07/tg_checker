from typing import Any, Protocol

from app.limits import LookupLimits


class FloodPause(Exception):
    def __init__(self, seconds: int) -> None:
        self.seconds = max(0, int(seconds))
        super().__init__("FLOOD_WAIT")


class DeleteDeferred(Exception):
    """Імпортовані номери ще треба зняти з адресної книги."""


class SessionLockLost(Exception):
    """Блокування сесії втрачено. З’єднання треба закрити."""


class SessionFatal(Exception):
    def __init__(self, error_name: str) -> None:
        self.error_name = error_name
        super().__init__(error_name)


class LookupHooks(Protocol):
    def lock_lost(self) -> bool: ...

    async def update_phone(self, phone: str, fields: dict[str, Any]) -> None: ...

    async def pending_phones(self, limit: int) -> list[str]: ...

    async def enrich_backlog(self) -> list[str]: ...

    async def save_enrich_secret(self, phone: str, user_id: int, access_hash: int) -> None: ...

    async def peek_enrich_secret(self, phone: str) -> tuple[int, int] | None: ...

    async def drop_enrich_secret(self, phone: str) -> None: ...

    async def defer_delete(self, phones: list[str]) -> None: ...

    async def clear_cleanup(self, phones: list[str]) -> None: ...


class PhoneLookup(Protocol):
    async def run(self, client: Any, hooks: LookupHooks, limits: LookupLimits) -> None: ...
