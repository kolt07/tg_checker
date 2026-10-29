import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AccountSession(Base):
    __tablename__ = "account_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_id: Mapped[int] = mapped_column(Integer)
    api_hash_enc: Mapped[str] = mapped_column(Text)
    string_session_enc: Mapped[str] = mapped_column(Text)
    session_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PhoneCheck(Base):
    __tablename__ = "phone_checks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("account_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    strategy: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    callback_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    callback_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    callback_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    pause_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flood_wait_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flood_wait_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_flood_wait_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flood_wait_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cleanup_phones: Mapped[list] = mapped_column(JSON, default=list)
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    transient_attempts: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    timeout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


class CheckLane(Base):
    """Частка перевірки, яку веде один акаунт."""

    __tablename__ = "check_lanes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("phone_checks.id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("account_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    pause_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flood_wait_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flood_wait_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_flood_wait_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flood_wait_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cleanup_phones: Mapped[list] = mapped_column(JSON, default=list)
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    transient_attempts: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PhoneCheckRow(Base):
    __tablename__ = "phone_check_rows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("phone_checks.id", ondelete="CASCADE"),
        index=True,
    )
    lane_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("check_lanes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)
    input_phone: Mapped[str] = mapped_column(String(80))
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    premium: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    bot: Mapped[bool] = mapped_column(Boolean, default=False)
    scam: Mapped[bool] = mapped_column(Boolean, default=False)
    fake: Mapped[bool] = mapped_column(Boolean, default=False)
    presence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    was_online: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    enrich_pending: Mapped[bool] = mapped_column(Boolean, default=False)
