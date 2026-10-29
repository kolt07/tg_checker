from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SessionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None = None
    phone: str | None = None
    username: str | None = None
    first_name: str | None = None
    created_at: datetime


class SendCodeRequest(BaseModel):
    api_id: int = Field(ge=1)
    api_hash: str = Field(min_length=16, max_length=128)
    phone: str = Field(min_length=5, max_length=32)

    def __repr__(self) -> str:
        return "SendCodeRequest()"


class SendCodeResponse(BaseModel):
    login_id: UUID
    phone: str
    hint: str
    delivery: str
    can_resend: bool = False
    resend_in: int | None = None


class ResendCodeBody(BaseModel):
    login_id: UUID

    def __repr__(self) -> str:
        return "ResendCodeBody()"


class QrStartRequest(BaseModel):
    api_id: int = Field(ge=1)
    api_hash: str = Field(min_length=16, max_length=128)

    def __repr__(self) -> str:
        return "QrStartRequest()"


class QrStartResponse(BaseModel):
    login_id: UUID
    qr_svg: str
    hint: str


class QrStatusResponse(BaseModel):
    status: Literal["pending", "password", "ready", "error"]
    qr_svg: str | None = None
    hint: str | None = None
    session: SessionPublic | None = None


class QrPasswordBody(BaseModel):
    login_id: UUID
    password: str = Field(min_length=1, max_length=256)

    def __repr__(self) -> str:
        return "QrPasswordBody()"


class SignInRequest(BaseModel):
    login_id: UUID
    code: str = Field(default="", max_length=32)
    password: str | None = Field(default=None, max_length=256)

    def __repr__(self) -> str:
        return "SignInRequest()"


class SignInResponse(BaseModel):
    password_required: bool
    login_id: UUID | None = None
    session: SessionPublic | None = None


class SessionCreate(BaseModel):
    api_id: int = Field(ge=1)
    api_hash: str = Field(min_length=16, max_length=128)
    string_session: str = Field(min_length=20, max_length=10000)
    label: str | None = Field(default=None, max_length=120)

    def __repr__(self) -> str:
        return "SessionCreate()"


class CheckAccount(BaseModel):
    api_id: int = Field(ge=1)
    api_hash: str = Field(min_length=16, max_length=128)
    string_session: str | None = Field(default=None, min_length=20, max_length=10000)
    auth_key: str | None = Field(default=None, min_length=32, max_length=4000)
    dc_id: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def credential(self) -> "CheckAccount":
        if self.string_session is not None:
            self.string_session = self.string_session.strip() or None
        if self.auth_key is not None:
            self.auth_key = self.auth_key.strip() or None
        self.api_hash = self.api_hash.strip()
        has_session = bool(self.string_session)
        has_key = bool(self.auth_key)
        if has_session == has_key:
            raise ValueError("Потрібна string_session або MTProto auth_key")
        if has_key and self.dc_id is None:
            raise ValueError("Для MTProto ключа потрібен dc_id")
        return self

    def __repr__(self) -> str:
        return "CheckAccount()"


class CheckCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "phones": ["+380501112233", "+380671112233"],
                    "timeout": 4,
                    "accounts": [
                        {
                            "api_id": 123456,
                            "api_hash": "YOUR_API_HASH",
                            "string_session": "YOUR_STRING_SESSION",
                        },
                        {
                            "api_id": 123456,
                            "api_hash": "YOUR_API_HASH",
                            "auth_key": "MTPROTO_AUTH_KEY_HEX_OR_BASE64",
                            "dc_id": 2,
                        },
                    ],
                }
            ]
        }
    )

    phones: list[str] = Field(min_length=1)
    session_id: UUID | None = None
    session_ids: list[UUID] | None = Field(default=None, max_length=50)
    api_id: int | None = Field(default=None, ge=1)
    api_hash: str | None = Field(default=None, min_length=16, max_length=128)
    string_session: str | None = Field(default=None, min_length=20, max_length=10000)
    auth_key: str | None = Field(default=None, min_length=32, max_length=4000)
    dc_id: int | None = Field(default=None, ge=1, le=5)
    accounts: list[CheckAccount] | None = Field(default=None, max_length=50)
    timeout: float | None = Field(default=None, ge=0, le=3600)
    callback_url: str | None = None

    @field_validator("callback_url")
    @classmethod
    def check_callback(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        if len(value) > 2000:
            raise ValueError("callback_url задовгий")
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("callback_url має бути http або https")
        return value.strip()

    @model_validator(mode="after")
    def credentials(self) -> "CheckCreate":
        if self.accounts is not None and len(self.accounts) == 0:
            self.accounts = None
        if self.session_ids is not None and len(self.session_ids) == 0:
            self.session_ids = None
        if self.auth_key is not None:
            self.auth_key = self.auth_key.strip() or None
        if self.string_session is not None:
            self.string_session = self.string_session.strip() or None
        if self.session_id is not None:
            self.api_hash = None
            self.string_session = None
            self.auth_key = None
        has_saved = self.session_id is not None or bool(self.session_ids)
        has_accounts = bool(self.accounts)
        has_session = bool(self.string_session)
        has_key = bool(self.auth_key)
        if has_session and has_key:
            raise ValueError("Вкажіть або string_session, або auth_key")
        if has_key and self.dc_id is None:
            raise ValueError("Для MTProto ключа потрібен dc_id")
        has_inline = bool(self.api_id and self.api_hash and (has_session or has_key))
        if has_inline or has_saved or has_accounts:
            return self
        raise ValueError(
            "Потрібні session_id, session_ids, accounts або api_id, api_hash і string_session чи auth_key"
        )

    def __repr__(self) -> str:
        saved = len(self.session_ids or []) + (1 if self.session_id else 0)
        return (
            f"CheckCreate(phones={len(self.phones)}, "
            f"accounts={len(self.accounts or [])}, sessions={saved})"
        )


class CheckCreated(BaseModel):
    id: UUID
    status: str
    total: int
    processed: int
    account_count: int = 1
    timeout: float | None = None


class CheckRowPublic(BaseModel):
    input_phone: str
    phone: str | None = None
    status: str | None = None
    telegram_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    premium: bool = False
    verified: bool = False
    bot: bool = False
    scam: bool = False
    fake: bool = False
    presence: str | None = None
    was_online: datetime | None = None
    bio: str | None = None
    error_detail: str | None = None


class CheckSummary(BaseModel):
    id: UUID
    session_id: UUID | None
    status: str
    total: int
    processed: int
    found: int
    pause_reason: str | None = None
    flood_wait_until: datetime | None = None
    last_flood_wait_seconds: int | None = None
    flood_wait_count: int = 0
    error_message: str | None = None
    created_at: datetime
    account_count: int = 1
    timeout: float | None = None


class CheckDetail(CheckSummary):
    counts: dict[str, int]
    flood_wait_seconds: int | None = None
    callback_url: str | None = None
    callback_status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows: list[CheckRowPublic]


def session_to_public(row: Any) -> SessionPublic:
    return SessionPublic(
        id=row.id,
        label=row.label,
        phone=row.phone,
        username=row.username,
        first_name=row.first_name,
        created_at=row.created_at,
    )


def row_to_public(row: Any) -> CheckRowPublic:
    return CheckRowPublic(
        input_phone=row.input_phone,
        phone=row.phone_e164,
        status=row.status,
        telegram_id=row.telegram_id,
        username=row.username,
        first_name=row.first_name,
        last_name=row.last_name,
        premium=bool(row.premium),
        verified=bool(row.verified),
        bot=bool(row.bot),
        scam=bool(row.scam),
        fake=bool(row.fake),
        presence=row.presence,
        was_online=row.was_online,
        bio=row.bio,
        error_detail=row.error_detail,
    )


def summary_from_check(check: Any) -> CheckSummary:
    return CheckSummary(
        id=check.id,
        session_id=check.session_id,
        status=check.status,
        total=check.total,
        processed=check.processed,
        found=check.found_count,
        pause_reason=check.pause_reason,
        flood_wait_until=check.flood_wait_until,
        last_flood_wait_seconds=check.last_flood_wait_seconds,
        flood_wait_count=check.flood_wait_count or 0,
        error_message=check.error_message,
        created_at=check.created_at,
        account_count=check.account_count or 1,
        timeout=check.timeout_seconds,
    )
