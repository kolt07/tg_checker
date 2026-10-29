import hmac
import re
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

_KEY_VALUE = re.compile(
    r"(?i)(string_session|api_hash|phone_code_hash|access_hash|auth_key)(\s*[:=]\s*)(\"[^\"]*\"|\S+)"
)
_LONG_HEX = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_=-]{40,}\b")


def get_fernet() -> Fernet:
    from app.config import get_settings

    return build_fernet(get_settings().session_encryption_key)


def build_fernet(key: str) -> Fernet:
    try:
        return Fernet(key.strip().encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ValueError("SESSION_ENCRYPTION_KEY має бути ключем Fernet") from exc


def encrypt_text(value: str, fernet: Fernet) -> str:
    return fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(token: str, fernet: Fernet) -> str:
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Не вдалося розшифрувати сесію") from exc


def api_key_matches(provided: str, expected: str) -> bool:
    left = provided.encode("utf-8")
    right = expected.encode("utf-8")
    if not left or not right or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def redact(text: str) -> str:
    cleaned = _KEY_VALUE.sub(r"\1\2[redacted]", text)
    cleaned = _LONG_HEX.sub("[redacted]", cleaned)
    cleaned = _LONG_TOKEN.sub("[redacted]", cleaned)
    return cleaned


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def sanitize_validation_errors(errors: list[dict]) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    for error in errors:
        loc = error.get("loc", ())
        public.append(
            {
                "loc": [str(part) for part in loc],
                "msg": str(error.get("msg", "")),
            }
        )
    return public
