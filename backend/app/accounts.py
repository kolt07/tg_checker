import hashlib
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountSession
from app.security import decrypt_text, encrypt_text, get_fernet


def session_fingerprint(string_session: str) -> str:
    return hashlib.sha256(string_session.encode("utf-8")).hexdigest()


def profile_from_me(me: object) -> dict[str, str | None]:
    phone = getattr(me, "phone", None)
    phone_text: str | None
    if phone:
        raw = str(phone)
        phone_text = raw if raw.startswith("+") else f"+{raw}"
    else:
        phone_text = None
    username = getattr(me, "username", None) or None
    first_name = getattr(me, "first_name", None) or None
    return {"phone": phone_text, "username": username, "first_name": first_name}


def apply_profile(account: AccountSession, me: object) -> None:
    profile = profile_from_me(me)
    if profile["phone"]:
        account.phone = profile["phone"]
    if profile["username"]:
        account.username = profile["username"]
    if profile["first_name"]:
        account.first_name = profile["first_name"]
    account.updated_at = datetime.now(timezone.utc)


def decrypt_account(account: AccountSession) -> tuple[int, str, str]:
    fernet = get_fernet()
    api_hash = decrypt_text(account.api_hash_enc, fernet)
    string_session = decrypt_text(account.string_session_enc, fernet)
    if not string_session:
        raise ValueError("empty session")
    return int(account.api_id), api_hash, string_session


async def upsert_account(
    db: AsyncSession,
    *,
    api_id: int,
    api_hash: str,
    string_session: str,
    label: str | None,
    phone: str | None,
    username: str | None,
    first_name: str | None,
) -> AccountSession:
    fingerprint = session_fingerprint(string_session)
    fernet = get_fernet()
    existing = await db.scalar(select(AccountSession).where(AccountSession.session_fingerprint == fingerprint))
    now = datetime.now(timezone.utc)
    encrypted_hash = encrypt_text(api_hash, fernet)
    encrypted_session = encrypt_text(string_session, fernet)
    if existing is None:
        existing = AccountSession(
            api_id=api_id,
            api_hash_enc=encrypted_hash,
            string_session_enc=encrypted_session,
            session_fingerprint=fingerprint,
            label=label,
            phone=phone,
            username=username,
            first_name=first_name,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
        return existing
    existing.api_id = api_id
    existing.api_hash_enc = encrypted_hash
    existing.string_session_enc = encrypted_session
    existing.updated_at = now
    if label:
        existing.label = label
    if phone:
        existing.phone = phone
    if username:
        existing.username = username
    if first_name:
        existing.first_name = first_name
    return existing
