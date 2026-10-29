from typing import Any

from telethon.tl.functions.contacts import ResolvePhoneRequest

from app.limits import LookupLimits
from app.lookup.base import FloodPause, LookupHooks, SessionFatal, SessionLockLost
from app.lookup.mapping import classify_exception, fields_for_decision, not_found_fields, public_user_fields
from app.lookup.pacer import Pacer


class ResolvePhoneLookup:
    """contacts.resolvePhone: один номер за виклик, без запису в контакти."""

    async def run(self, client: Any, hooks: LookupHooks, limits: LookupLimits) -> None:
        pacer = Pacer(limits.resolve_interval_seconds)
        while True:
            if hooks.lock_lost():
                raise SessionLockLost()
            phones = await hooks.pending_phones(1)
            if not phones:
                return
            phone = phones[0]
            await pacer.wait()
            if hooks.lock_lost():
                raise SessionLockLost()
            try:
                resolved = await client(ResolvePhoneRequest(phone=phone))
            except (FloodPause, SessionLockLost, SessionFatal):
                raise
            except Exception as exc:
                if hooks.lock_lost():
                    raise SessionLockLost() from exc
                decision = classify_exception(exc)
                if decision.kind == "flood":
                    raise FloodPause(decision.seconds or 0) from exc
                if decision.kind == "fatal":
                    raise SessionFatal(decision.error_name) from exc
                await hooks.update_phone(phone, fields_for_decision(decision))
                continue
            users = [user for user in getattr(resolved, "users", []) if getattr(user, "id", None) is not None]
            if not users:
                await hooks.update_phone(phone, not_found_fields())
                continue
            await hooks.update_phone(phone, public_user_fields(users[0]))
