import asyncio
import inspect
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.csv_export import HEADERS, check_to_json, rows_to_csv
from app.distribute import assign_lane_indexes
from app.limits import build_limits, clamp_resolve_interval, limits_for_timeout
from app.lookup.base import SessionFatal
from app.lookup.mapping import (
    assign_import,
    classify_exception,
    failure_message,
    fields_for_decision,
    map_presence,
    public_user_fields,
    scrub_row_fields,
)
from app.models import AccountSession, CheckLane, PhoneCheckRow
from app.phones import expand_phone_inputs, normalize_phone
from app.schemas import CheckAccount, CheckCreate, CheckRowPublic, SessionPublic, row_to_public
from app.security import (
    api_key_matches,
    build_fernet,
    decrypt_text,
    encrypt_text,
    redact,
    sanitize_validation_errors,
)


class PhoneNotOccupiedError(Exception):
    pass


class PhoneNumberInvalidError(Exception):
    pass


class FloodWaitError(Exception):
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds


class AuthKeyDuplicatedError(Exception):
    pass


class AuthKeyUnregisteredError(Exception):
    pass


class UserStatusOnline:
    pass


class UserStatusOffline:
    def __init__(self, was_online: datetime) -> None:
        self.was_online = was_online


class UserStatusRecently:
    pass


class UserStatusLastWeek:
    pass


class UserStatusLastMonth:
    pass


class UserStatusEmpty:
    pass


def test_normalize_example_numbers() -> None:
    import phonenumbers
    from phonenumbers import PhoneNumberFormat

    example = phonenumbers.example_number("UA")
    assert example is not None
    e164 = phonenumbers.format_number(example, PhoneNumberFormat.E164)
    international = phonenumbers.format_number(example, PhoneNumberFormat.INTERNATIONAL)
    national = phonenumbers.format_number(example, PhoneNumberFormat.NATIONAL)
    assert normalize_phone(e164, "UA") == e164
    assert normalize_phone(international, "UA") == e164
    assert normalize_phone(national, "UA") == e164
    assert normalize_phone("123", "UA") is None
    assert normalize_phone("", "UA") is None
    assert normalize_phone("не номер", "UA") is None
    ru = phonenumbers.example_number("RU")
    assert ru is not None
    ru_e164 = phonenumbers.format_number(ru, PhoneNumberFormat.E164)
    ru_digits = ru_e164.removeprefix("+")
    assert normalize_phone(ru_digits, "UA") == ru_e164
    assert normalize_phone("8" + ru_digits[1:], "UA") == ru_e164
    assert normalize_phone("00" + ru_digits, "UA") == ru_e164


def test_expand_keeps_spaces_and_skips_header() -> None:
    values = expand_phone_inputs(["+380 50 123 45 67\n0501112233", "phone", "номер"])
    assert values == ["+380 50 123 45 67", "0501112233"]


def test_resolve_interval_never_below_three_seconds() -> None:
    assert clamp_resolve_interval(0) == 3
    assert clamp_resolve_interval(1) == 3
    assert clamp_resolve_interval(3) == 3
    assert clamp_resolve_interval(9) == 9
    limits = build_limits(resolve_interval_seconds=1, import_chunk_size=100, import_pause_seconds=0)
    assert limits.resolve_interval_seconds == 3
    assert limits.import_chunk_size == 20
    assert limits.import_pause_seconds == 1
    settings = SimpleNamespace(
        resolve_interval_seconds=3,
        import_chunk_size=5,
        import_pause_seconds=5,
        enrich_interval_seconds=1,
        max_import_retries=2,
    )
    paced = limits_for_timeout(settings, 8)
    assert paced.resolve_interval_seconds == 8
    assert paced.import_pause_seconds == 8
    assert paced.enrich_interval_seconds == 8
    floor = limits_for_timeout(settings, 1)
    assert floor.resolve_interval_seconds == 3
    assert floor.import_pause_seconds == 1
    assert floor.enrich_interval_seconds == 1
    assert limits_for_timeout(settings, None).import_pause_seconds == 5


def test_presence_mapping() -> None:
    moment = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)
    assert map_presence(None) == ("hidden", None)
    assert map_presence(UserStatusEmpty()) == ("hidden", None)
    assert map_presence(UserStatusOnline()) == ("online", None)
    assert map_presence(UserStatusRecently()) == ("recently", None)
    assert map_presence(UserStatusLastWeek()) == ("last_week", None)
    assert map_presence(UserStatusLastMonth()) == ("last_month", None)
    presence, was_online = map_presence(UserStatusOffline(moment))
    assert presence == "offline"
    assert was_online == moment


def test_public_user_fields_omit_access_hash() -> None:
    user = SimpleNamespace(
        id=42,
        access_hash=987654321,
        username="anna",
        first_name="Анна",
        last_name="К",
        premium=True,
        verified=True,
        bot=False,
        scam=False,
        fake=False,
        status=UserStatusOnline(),
    )
    fields = public_user_fields(user)
    assert fields["status"] == "found"
    assert fields["telegram_id"] == 42
    assert fields["username"] == "anna"
    assert fields["presence"] == "online"
    assert "access_hash" not in fields


def test_phone_not_occupied_matches_empty_import() -> None:
    decision = classify_exception(PhoneNotOccupiedError())
    assert decision.status == "not_found_or_private"
    occupied = fields_for_decision(decision)
    empty = assign_import({1: "+380501112233"}, [], {}, set())
    assert empty["+380501112233"] == occupied
    assert occupied["status"] == "not_found_or_private"


def test_import_maps_user_retry_and_missing() -> None:
    user = SimpleNamespace(
        id=7,
        access_hash=1,
        username=None,
        first_name="Іра",
        last_name="",
        premium=False,
        verified=False,
        bot=False,
        scam=False,
        fake=False,
        status=UserStatusRecently(),
    )
    assigned = assign_import(
        {1: "+100", 2: "+200", 3: "+300"},
        [(1, 7)],
        {7: user},
        {2},
    )
    assert assigned["+100"]["status"] == "found"
    assert assigned["+100"]["presence"] == "recently"
    assert "access_hash" not in assigned["+100"]
    assert assigned["+200"] is None
    assert assigned["+300"]["status"] == "not_found_or_private"


def test_invalid_phone_and_flood_classification() -> None:
    invalid = classify_exception(PhoneNumberInvalidError())
    assert invalid.status == "invalid_phone"
    flood = classify_exception(FloodWaitError(30))
    assert flood.kind == "flood"
    assert flood.seconds == 30
    fatal = classify_exception(AuthKeyUnregisteredError())
    assert fatal.kind == "fatal"
    assert failure_message(AuthKeyDuplicatedError()).startswith("Сесію було відкрито")
    assert failure_message(SessionFatal("AuthKeyUnregisteredError")).startswith("Сесія Telegram")
    assert failure_message(PhoneNotOccupiedError()) == "PhoneNotOccupiedError"


def test_scrub_rejects_secrets() -> None:
    with pytest.raises(ValueError):
        scrub_row_fields({"access_hash": 1})
    with pytest.raises(ValueError):
        scrub_row_fields({"string_session": "x"})
    assert scrub_row_fields({"status": "found", "bio": None})["status"] == "found"


def test_models_do_not_store_access_hash_or_plain_session() -> None:
    row_columns = set(PhoneCheckRow.__table__.columns.keys())
    session_columns = set(AccountSession.__table__.columns.keys())
    assert "access_hash" not in row_columns
    assert "string_session" not in row_columns
    assert "string_session" not in session_columns
    assert "string_session_enc" in session_columns
    assert "api_hash_enc" in session_columns
    lane_columns = set(CheckLane.__table__.columns.keys())
    assert "string_session" not in lane_columns
    assert "auth_key" not in lane_columns
    assert "session_id" in lane_columns


def test_public_schemas_hide_secrets() -> None:
    assert "string_session" not in SessionPublic.model_fields
    assert "api_hash" not in SessionPublic.model_fields
    assert "access_hash" not in CheckRowPublic.model_fields
    assert "strategy" not in CheckCreate.model_fields
    created = CheckCreate(
        phones=["+380501112233"],
        session_id="00000000-0000-0000-0000-000000000000",
        api_id=12345,
        api_hash="a" * 32,
        string_session="S" * 40,
    )
    text = repr(created)
    assert "string_session" not in text
    assert "S" * 40 not in text
    assert created.string_session is None
    with pytest.raises(ValueError):
        CheckCreate(phones=["+380501112233"], callback_url="javascript:alert(1)")
    key = "ab" * 256
    parallel = CheckCreate(
        phones=["+380501112233", "+380671112233"],
        timeout=4,
        accounts=[
            {"api_id": 1, "api_hash": "a" * 32, "string_session": "S" * 40},
            {"api_id": 2, "api_hash": "b" * 32, "auth_key": key, "dc_id": 2},
        ],
    )
    assert parallel.timeout == 4
    assert parallel.accounts is not None
    assert len(parallel.accounts) == 2
    hidden = repr(parallel)
    assert key not in hidden
    assert "S" * 40 not in hidden
    with pytest.raises(ValueError):
        CheckCreate(phones=["+380501112233"])
    with pytest.raises(ValueError):
        CheckAccount(
            api_id=1,
            api_hash="a" * 32,
            string_session="S" * 40,
            auth_key=key,
            dc_id=2,
        )


def test_row_public_ignores_access_hash() -> None:
    row = SimpleNamespace(
        input_phone="050",
        phone_e164="+380501112233",
        status="found",
        telegram_id=5,
        username=None,
        first_name="А",
        last_name=None,
        premium=False,
        verified=False,
        bot=False,
        scam=False,
        fake=False,
        presence="hidden",
        was_online=None,
        bio=None,
        error_detail=None,
        access_hash=123456,
    )
    dumped = row_to_public(row).model_dump()
    assert dumped["phone"] == "+380501112233"
    assert "access_hash" not in dumped


def test_csv_export_shape() -> None:
    body = rows_to_csv(
        [
            {
                "phone": "+380501112233",
                "status": "found",
                "telegram_id": 5,
                "username": "anna",
                "first_name": "Анна",
                "last_name": "",
                "presence": "recently",
                "was_online": None,
                "premium": True,
                "verified": False,
                "bot": False,
                "scam": False,
                "fake": False,
                "bio": "привіт",
                "error_detail": None,
                "access_hash": 999,
            }
        ]
    )
    assert body.startswith("\ufeff")
    assert "access_hash" not in body
    header = body.splitlines()[0].lstrip("\ufeff")
    assert header.split(",")[0] == HEADERS[0]
    assert "Анна" in body
    assert "привіт" in body


def test_access_log_filter_keeps_uvicorn_args() -> None:
    from app.logging_setup import SecretLogFilter

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1", "GET", "/api/v1/health", "1.1", 200),
        exc_info=None,
    )
    assert SecretLogFilter().filter(record) is True
    assert record.args == ("127.0.0.1", "GET", "/api/v1/health", "1.1", 200)


def test_redact_and_encryption() -> None:
    secret = "a" * 32
    session = "S" * 48
    auth_key = "cd" * 256
    text = redact(f"api_hash={secret} string_session={session} auth_key={auth_key} перевірка номерів")
    assert secret not in text
    assert session not in text
    assert auth_key not in text
    assert "перевірка номерів" in text
    key = Fernet.generate_key().decode()
    fernet = build_fernet(key)
    token = encrypt_text("session-secret", fernet)
    assert token != "session-secret"
    assert decrypt_text(token, fernet) == "session-secret"
    assert api_key_matches("right-key", "right-key")
    assert not api_key_matches("short", "right-key")
    cleaned = sanitize_validation_errors(
        [{"loc": ("body", "string_session"), "msg": "bad", "input": "SECRET", "ctx": {"x": 1}}]
    )
    assert cleaned == [{"loc": ["body", "string_session"], "msg": "bad"}]
    assert "SECRET" not in str(cleaned)


def test_application_modules_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/tg")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    import app.jobs
    import app.main
    import app.worker
    from app.lookup import get_strategy

    assert app.main.app.title
    assert get_strategy("resolve_phone").__class__.__name__ == "ResolvePhoneLookup"
    assert get_strategy("import_contacts").enrich is False
    assert get_strategy("import_and_enrich").enrich is True
    assert app.jobs.process_check
    assert app.worker.main


def test_open_client_connects_without_interactive_start(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeClient:
        def is_connected(self) -> bool:
            return True

        async def connect(self) -> None:
            calls.append("connect")

        async def disconnect(self) -> None:
            calls.append("disconnect")

        async def start(self) -> None:
            calls.append("start")

    monkeypatch.setattr("app.telegram_client.make_client", lambda *_args, **_kwargs: FakeClient())

    async def run() -> None:
        from app.telegram_client import open_client

        async with open_client(1, "h" * 32) as client:
            assert client.is_connected()

    asyncio.run(run())
    assert calls == ["connect", "disconnect"]


def test_telethon_context_manager_calls_interactive_start() -> None:
    from telethon import TelegramClient

    source = inspect.getsource(TelegramClient.__aenter__)
    assert "start" in source


def test_qr_svg_is_markup() -> None:
    from urllib.parse import unquote

    from app.api.auth import qr_svg

    uri = qr_svg("tg://login?token=abc")
    assert uri.startswith("data:image/svg+xml")
    markup = unquote(uri)
    assert "xmlns='http://www.w3.org/2000/svg'" in markup or 'xmlns="http://www.w3.org/2000/svg"' in markup
    assert "<script" not in markup.lower()


def test_sent_code_hint_names_the_channel() -> None:
    from fastapi import HTTPException

    from app.api.auth import describe_sent_code, hint_for_delivery

    app_type = type("SentCodeTypeApp", (), {})()
    sms_type = type("SentCodeTypeSms", (), {})()
    email_type = type("SentCodeTypeEmailCode", (), {"email_pattern": "a***@mail.com"})()
    setup_type = type("SentCodeTypeSetUpEmailRequired", (), {})()
    assert "застосунку Telegram" in describe_sent_code(SimpleNamespace(type=app_type))
    app_with_sms = SimpleNamespace(type=app_type, next_type=type("CodeTypeSms", (), {})(), timeout=45)
    hint = hint_for_delivery(app_with_sms, 45)
    assert "SMS" in hint
    assert "45" in hint
    no_sms = hint_for_delivery(SimpleNamespace(type=app_type, next_type=None, timeout=None), None)
    assert "не надсилає SMS" in no_sms
    assert "SMS" in describe_sent_code(SimpleNamespace(type=sms_type))
    assert "a***@mail.com" in describe_sent_code(SimpleNamespace(type=email_type))
    with pytest.raises(HTTPException) as caught:
        describe_sent_code(SimpleNamespace(type=setup_type))
    assert caught.value.status_code == 400


def test_phones_are_split_across_accounts() -> None:
    assert assign_lane_indexes(["+1", "+2", "+3", "+4"], 2) == [0, 1, 0, 1]
    assert assign_lane_indexes(["+1", None, "+2", "+1", "+3"], 2) == [0, None, 1, 0, 0]


def test_auth_key_becomes_string_session() -> None:
    import base64

    from telethon.sessions import StringSession

    from app.mtproto import PRODUCTION_DC_IPV4, auth_key_to_string_session

    raw = bytes(range(256))
    saved = auth_key_to_string_session(raw.hex(), 2)
    session = StringSession(saved)
    assert session.dc_id == 2
    assert bytes(session.auth_key.key) == raw
    assert session.server_address == PRODUCTION_DC_IPV4[2]
    restored = StringSession(auth_key_to_string_session(base64.urlsafe_b64encode(raw).decode(), 4))
    assert restored.dc_id == 4
    assert bytes(restored.auth_key.key) == raw
    with pytest.raises(ValueError):
        auth_key_to_string_session("abcd", 2)
    with pytest.raises(ValueError):
        auth_key_to_string_session(raw.hex(), 9)


def test_json_export_keeps_ukrainian_text() -> None:
    body = check_to_json({"status": "completed", "rows": [{"bio": "привіт"}]})
    assert "привіт" in body
    assert "\\u" not in body
