from datetime import datetime
from typing import Any, Literal

from app.security import as_utc

ROW_STATUSES = (
    "found",
    "not_found_or_private",
    "invalid_phone",
    "retry_later",
    "error",
)

ROW_UPDATE_FIELDS = {
    "status",
    "telegram_id",
    "username",
    "first_name",
    "last_name",
    "premium",
    "verified",
    "bot",
    "scam",
    "fake",
    "presence",
    "was_online",
    "bio",
    "error_detail",
    "enrich_pending",
}

FORBIDDEN_FIELDS = {
    "access_hash",
    "string_session",
    "api_hash",
    "api_hash_enc",
    "string_session_enc",
    "phone_code_hash",
    "auth_key",
}

PRESENCE_BY_TYPE = {
    "UserStatusOnline": "online",
    "UserStatusOffline": "offline",
    "UserStatusRecently": "recently",
    "UserStatusLastWeek": "last_week",
    "UserStatusLastMonth": "last_month",
    "UserStatusEmpty": "hidden",
}

NOT_FOUND_ERRORS = frozenset({"PhoneNotOccupiedError"})
INVALID_ERRORS = frozenset({"PhoneNumberInvalidError", "PhoneNumberBannedError"})
SESSION_FATAL_ERRORS = frozenset(
    {
        "AuthKeyUnregisteredError",
        "AuthKeyInvalidError",
        "AuthKeyPermEmptyError",
        "SessionRevokedError",
        "SessionExpiredError",
        "UserDeactivatedError",
        "UserDeactivatedBanError",
        "InputUserDeactivatedError",
        "AuthKeyDuplicatedError",
    }
)

DecisionKind = Literal["row", "flood", "fatal"]


class RowDecision:
    def __init__(
        self,
        kind: DecisionKind,
        status: str | None,
        seconds: int | None,
        error_name: str,
    ) -> None:
        self.kind = kind
        self.status = status
        self.seconds = seconds
        self.error_name = error_name


def scrub_row_fields(fields: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if key in FORBIDDEN_FIELDS:
            raise ValueError("forbidden field")
        if key not in ROW_UPDATE_FIELDS:
            raise ValueError("unknown field")
        cleaned[key] = value
    return cleaned


def map_presence(status: object | None) -> tuple[str, datetime | None]:
    if status is None:
        return "hidden", None
    presence = PRESENCE_BY_TYPE.get(type(status).__name__, "hidden")
    was_online = None
    if presence == "offline":
        raw = getattr(status, "was_online", None)
        was_online = as_utc(raw) if isinstance(raw, datetime) else None
    return presence, was_online


def _blank_user_fields() -> dict[str, Any]:
    return {
        "telegram_id": None,
        "username": None,
        "first_name": None,
        "last_name": None,
        "premium": False,
        "verified": False,
        "bot": False,
        "scam": False,
        "fake": False,
        "presence": None,
        "was_online": None,
        "bio": None,
        "error_detail": None,
        "enrich_pending": False,
    }


def public_user_fields(user: object) -> dict[str, Any]:
    presence, was_online = map_presence(getattr(user, "status", None))
    username = getattr(user, "username", None) or None
    return {
        "status": "found",
        "telegram_id": int(getattr(user, "id")),
        "username": username,
        "first_name": getattr(user, "first_name", None) or "",
        "last_name": getattr(user, "last_name", None) or "",
        "premium": bool(getattr(user, "premium", False)),
        "verified": bool(getattr(user, "verified", False)),
        "bot": bool(getattr(user, "bot", False)),
        "scam": bool(getattr(user, "scam", False)),
        "fake": bool(getattr(user, "fake", False)),
        "presence": presence,
        "was_online": was_online,
        "bio": None,
        "error_detail": None,
        "enrich_pending": False,
    }


def not_found_fields() -> dict[str, Any]:
    return {**_blank_user_fields(), "status": "not_found_or_private"}


def invalid_phone_fields() -> dict[str, Any]:
    return {
        **_blank_user_fields(),
        "status": "invalid_phone",
        "error_detail": "Некоректний номер",
    }


def retry_later_fields(detail: str = "retry_contacts") -> dict[str, Any]:
    return {**_blank_user_fields(), "status": "retry_later", "error_detail": detail}


def error_fields(error_name: str) -> dict[str, Any]:
    return {**_blank_user_fields(), "status": "error", "error_detail": error_name}


def classify_exception(exc: BaseException) -> RowDecision:
    name = type(exc).__name__
    message = getattr(exc, "message", "")
    message_text = message if isinstance(message, str) else ""
    if "FloodWait" in name and getattr(exc, "seconds", None) is not None:
        return RowDecision("flood", None, int(getattr(exc, "seconds")), name)
    if name in NOT_FOUND_ERRORS or "PHONE_NOT_OCCUPIED" in message_text:
        return RowDecision("row", "not_found_or_private", None, name)
    if name in INVALID_ERRORS or "PHONE_NUMBER_INVALID" in message_text:
        return RowDecision("row", "invalid_phone", None, name)
    if name in SESSION_FATAL_ERRORS:
        return RowDecision("fatal", None, None, name)
    return RowDecision("row", "error", None, name)


def fields_for_decision(decision: RowDecision) -> dict[str, Any]:
    if decision.status == "not_found_or_private":
        return not_found_fields()
    if decision.status == "invalid_phone":
        return invalid_phone_fields()
    if decision.status == "retry_later":
        return retry_later_fields(decision.error_name)
    return error_fields(decision.error_name)


def failure_message(exc: BaseException) -> str:
    name = type(exc).__name__
    decision = classify_exception(exc)
    wrapped = ""
    if name == "SessionFatal" and exc.args and isinstance(exc.args[0], str):
        wrapped = exc.args[0]
    if name == "AuthKeyDuplicatedError" or wrapped == "AuthKeyDuplicatedError" or decision.error_name == "AuthKeyDuplicatedError":
        return "Сесію було відкрито другим з’єднанням. Зачекайте і запустіть перевірку знову."
    if name == "SessionFatal" or decision.kind == "fatal":
        return "Сесія Telegram недійсна або акаунт вимкнено"
    return decision.error_name


def bio_from_full(full: object) -> str | None:
    full_user = getattr(full, "full_user", None)
    about = getattr(full_user, "about", None) if full_user is not None else getattr(full, "about", None)
    if not isinstance(about, str):
        return None
    text = about.strip()
    return text or None


def assign_import(
    client_ids_to_phone: dict[int, str],
    imported_pairs: list[tuple[int, int]],
    users_by_id: dict[int, object],
    retry_ids: set[int],
) -> dict[str, dict[str, Any] | None]:
    """Повернути поля рядка. None означає, що номер ще в retry_contacts."""
    imported_by_client = {client_id: user_id for client_id, user_id in imported_pairs}
    assigned: dict[str, dict[str, Any] | None] = {}
    for client_id, phone in client_ids_to_phone.items():
        if client_id in imported_by_client:
            user = users_by_id.get(imported_by_client[client_id])
            if user is None:
                assigned[phone] = not_found_fields()
            else:
                assigned[phone] = public_user_fields(user)
            continue
        if client_id in retry_ids:
            assigned[phone] = None
            continue
        assigned[phone] = not_found_fields()
    return assigned
