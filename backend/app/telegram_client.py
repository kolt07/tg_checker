import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telethon import TelegramClient
from telethon.sessions import StringSession


def make_client(
    api_id: int,
    api_hash: str,
    string_session: str | None = None,
    *,
    receive_updates: bool = False,
) -> TelegramClient:
    session = StringSession(string_session) if string_session else StringSession()
    return TelegramClient(
        session,
        api_id,
        api_hash,
        receive_updates=receive_updates,
        flood_sleep_threshold=0,
        timeout=30,
        connection_retries=2,
        request_retries=1,
    )


@asynccontextmanager
async def open_client(
    api_id: int,
    api_hash: str,
    string_session: str | None = None,
) -> AsyncIterator[TelegramClient]:
    """Підключення без client.start(): контекстний менеджер Telethon питає телефон з stdin."""
    client = make_client(api_id, api_hash, string_session)
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


async def user_authorized(client: TelegramClient) -> bool:
    result = client.is_user_authorized()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)
