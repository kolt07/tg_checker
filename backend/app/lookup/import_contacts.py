import asyncio
from typing import Any

from telethon.tl.functions.contacts import DeleteByPhonesRequest, ImportContactsRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPhoneContact, InputUser

from app.limits import LookupLimits
from app.lookup.base import DeleteDeferred, FloodPause, LookupHooks, SessionFatal, SessionLockLost
from app.lookup.mapping import (
    assign_import,
    bio_from_full,
    classify_exception,
    fields_for_decision,
    retry_later_fields,
)
from app.lookup.pacer import Pacer


class ImportContactsLookup:
    """contacts.importContacts невеликими пакетами і одразу contacts.deleteByPhones.

    enrich=True після імпорту викликає users.getFullUser.
    """

    def __init__(self, enrich: bool) -> None:
        self.enrich = enrich

    async def run(self, client: Any, hooks: LookupHooks, limits: LookupLimits) -> None:
        if self.enrich:
            backlog = await hooks.enrich_backlog()
            if backlog:
                await self._finish_enrich(client, hooks, limits, backlog)
        first = True
        while True:
            if hooks.lock_lost():
                raise SessionLockLost()
            phones = await hooks.pending_phones(limits.import_chunk_size)
            if not phones:
                return
            if not first:
                await self._pause(limits.import_pause_seconds, hooks)
            first = False
            await self._import_chunk(client, hooks, limits, phones)

    async def _import_chunk(
        self,
        client: Any,
        hooks: LookupHooks,
        limits: LookupLimits,
        phones: list[str],
    ) -> None:
        contacts: list[InputPhoneContact] = []
        mapping: dict[int, str] = {}
        for index, phone in enumerate(phones, start=1):
            mapping[index] = phone
            contacts.append(
                InputPhoneContact(client_id=index, phone=phone, first_name=".", last_name="")
            )
        await hooks.defer_delete(phones)
        try:
            try:
                result = await client(ImportContactsRequest(contacts=contacts))
            except Exception as exc:
                self._raise_control(hooks, exc)
                decision = classify_exception(exc)
                for phone in phones:
                    await hooks.update_phone(phone, fields_for_decision(decision))
                return
            await self._apply_results(client, hooks, limits, contacts, mapping, result)
        finally:
            await self._delete_phones(client, hooks, phones)

    async def _apply_results(
        self,
        client: Any,
        hooks: LookupHooks,
        limits: LookupLimits,
        contacts: list[InputPhoneContact],
        mapping: dict[int, str],
        result: Any,
    ) -> None:
        remaining = list(contacts)
        current = result
        final: dict[str, dict[str, Any]] = {}
        users_by_phone: dict[str, Any] = {}
        round_index = 0
        while remaining:
            if hooks.lock_lost():
                raise SessionLockLost()
            assignment, users = self._read_result(remaining, mapping, current)
            next_remaining: list[InputPhoneContact] = []
            for contact in remaining:
                phone = mapping[contact.client_id]
                fields = assignment.get(phone)
                if fields is None:
                    next_remaining.append(contact)
                    continue
                final[phone] = fields
                if fields.get("status") == "found" and phone in users:
                    users_by_phone[phone] = users[phone]
            remaining = next_remaining
            if not remaining:
                break
            if round_index >= limits.max_import_retries:
                for contact in remaining:
                    final[mapping[contact.client_id]] = retry_later_fields()
                break
            round_index += 1
            await self._pause(limits.import_pause_seconds, hooks)
            try:
                current = await client(ImportContactsRequest(contacts=remaining))
            except Exception as exc:
                self._raise_control(hooks, exc)
                decision = classify_exception(exc)
                for contact in remaining:
                    final[mapping[contact.client_id]] = fields_for_decision(decision)
                break

        if not self.enrich:
            for phone, fields in final.items():
                await hooks.update_phone(phone, fields)
            return

        to_enrich: dict[str, Any] = {}
        for phone, fields in final.items():
            if fields.get("status") == "found" and phone in users_by_phone:
                pending = dict(fields)
                pending["enrich_pending"] = True
                await hooks.update_phone(phone, pending)
                to_enrich[phone] = users_by_phone[phone]
                continue
            await hooks.update_phone(phone, fields)
        if to_enrich:
            await self._enrich_entities(client, hooks, limits, to_enrich)

    def _read_result(
        self,
        remaining: list[InputPhoneContact],
        mapping: dict[int, str],
        result: Any,
    ) -> tuple[dict[str, dict[str, Any] | None], dict[str, Any]]:
        subset = {contact.client_id: mapping[contact.client_id] for contact in remaining}
        users_by_id = {
            int(user.id): user
            for user in getattr(result, "users", [])
            if getattr(user, "id", None) is not None
        }
        imported_pairs = [
            (int(item.client_id), int(item.user_id)) for item in getattr(result, "imported", [])
        ]
        retry_ids = {int(item) for item in (getattr(result, "retry_contacts", None) or [])}
        assignment = assign_import(subset, imported_pairs, users_by_id, retry_ids)
        imported_by_client = {client_id: user_id for client_id, user_id in imported_pairs}
        users_for_phone: dict[str, Any] = {}
        for contact in remaining:
            user_id = imported_by_client.get(contact.client_id)
            if user_id is None:
                continue
            user = users_by_id.get(user_id)
            if user is not None:
                users_for_phone[mapping[contact.client_id]] = user
        return assignment, users_for_phone

    async def _enrich_entities(
        self,
        client: Any,
        hooks: LookupHooks,
        limits: LookupLimits,
        users_by_phone: dict[str, Any],
    ) -> None:
        phones = list(users_by_phone)
        pacer = Pacer(limits.enrich_interval_seconds)
        for index, phone in enumerate(phones):
            if hooks.lock_lost():
                raise SessionLockLost()
            await pacer.wait()
            user = users_by_phone[phone]
            user_id = getattr(user, "id", None)
            access_hash = getattr(user, "access_hash", None)
            if user_id is None or access_hash is None:
                await hooks.update_phone(phone, {"enrich_pending": False})
                continue
            try:
                full = await client(GetFullUserRequest(InputUser(int(user_id), int(access_hash))))
            except Exception as exc:
                if hooks.lock_lost():
                    raise SessionLockLost() from exc
                decision = classify_exception(exc)
                if decision.kind == "flood":
                    await self._stash_users(hooks, phones[index:], users_by_phone)
                    raise FloodPause(decision.seconds or 0) from exc
                if decision.kind == "fatal":
                    raise SessionFatal(decision.error_name) from exc
                await hooks.update_phone(phone, {"enrich_pending": False})
                continue
            await hooks.update_phone(phone, {"bio": bio_from_full(full), "enrich_pending": False})

    async def _stash_users(
        self,
        hooks: LookupHooks,
        phones: list[str],
        users_by_phone: dict[str, Any],
    ) -> None:
        for phone in phones:
            user = users_by_phone[phone]
            user_id = getattr(user, "id", None)
            access_hash = getattr(user, "access_hash", None)
            if user_id is None or access_hash is None:
                await hooks.update_phone(phone, {"enrich_pending": False})
                continue
            await hooks.save_enrich_secret(phone, int(user_id), int(access_hash))

    async def _finish_enrich(
        self,
        client: Any,
        hooks: LookupHooks,
        limits: LookupLimits,
        phones: list[str],
    ) -> None:
        pacer = Pacer(limits.enrich_interval_seconds)
        for phone in phones:
            if hooks.lock_lost():
                raise SessionLockLost()
            secret = await hooks.peek_enrich_secret(phone)
            if secret is None:
                await hooks.update_phone(phone, {"enrich_pending": False})
                continue
            user_id, access_hash = secret
            await pacer.wait()
            try:
                full = await client(GetFullUserRequest(InputUser(user_id, access_hash)))
            except Exception as exc:
                if hooks.lock_lost():
                    raise SessionLockLost() from exc
                decision = classify_exception(exc)
                if decision.kind == "flood":
                    raise FloodPause(decision.seconds or 0) from exc
                if decision.kind == "fatal":
                    raise SessionFatal(decision.error_name) from exc
                await hooks.update_phone(phone, {"enrich_pending": False})
                await hooks.drop_enrich_secret(phone)
                continue
            await hooks.update_phone(phone, {"bio": bio_from_full(full), "enrich_pending": False})
            await hooks.drop_enrich_secret(phone)

    async def _delete_phones(self, client: Any, hooks: LookupHooks, phones: list[str]) -> None:
        try:
            await client(DeleteByPhonesRequest(phones=list(phones)))
        except Exception as exc:
            await hooks.defer_delete(phones)
            if hooks.lock_lost():
                raise SessionLockLost() from exc
            decision = classify_exception(exc)
            if decision.kind == "flood":
                raise FloodPause(decision.seconds or 0) from exc
            if decision.kind == "fatal":
                raise SessionFatal(decision.error_name) from exc
            raise DeleteDeferred() from exc
        await hooks.clear_cleanup(phones)

    async def _pause(self, seconds: float, hooks: LookupHooks) -> None:
        remaining = seconds
        while remaining > 0:
            if hooks.lock_lost():
                raise SessionLockLost()
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step

    def _raise_control(self, hooks: LookupHooks, exc: Exception) -> None:
        if hooks.lock_lost():
            raise SessionLockLost() from exc
        decision = classify_exception(exc)
        if decision.kind == "flood":
            raise FloodPause(decision.seconds or 0) from exc
        if decision.kind == "fatal":
            raise SessionFatal(decision.error_name) from exc
