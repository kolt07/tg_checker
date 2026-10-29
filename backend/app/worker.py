import asyncio
import logging
import signal
import sys
from contextlib import suppress
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.bootstrap import prepare_runtime
from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.locking import set_cooldown
from app.logging_setup import setup_logging
from app.models import AccountSession, CheckLane, PhoneCheck
from app.queueing import READY_KEY, enqueue, promote_due
from app.redis_client import create_redis

logger = logging.getLogger("tgchecker.worker")


async def recover_jobs(redis: object) -> None:
    now = datetime.now(timezone.utc)
    plans: list[tuple[str, float, str | None]] = []
    async with get_sessionmaker()() as db:
        result = await db.execute(
            select(PhoneCheck).where(PhoneCheck.status.in_(("queued", "running", "paused")))
        )
        checks = list(result.scalars().all())
        for check in checks:
            fingerprint = None
            if check.session_id is not None:
                account = await db.get(AccountSession, check.session_id)
                if account is not None:
                    fingerprint = account.session_fingerprint
            delay = 0.0
            if check.flood_wait_until and check.flood_wait_until > now:
                delay = (check.flood_wait_until - now).total_seconds()
                check.status = "paused"
            elif check.status == "running":
                check.status = "queued"
            plans.append((str(check.id), delay, fingerprint))
        running_lanes = await db.execute(select(CheckLane).where(CheckLane.status == "running"))
        for lane in running_lanes.scalars().all():
            lane.status = "queued"
        await db.commit()
    for check_id, delay, fingerprint in plans:
        if fingerprint and delay > 0:
            await set_cooldown(redis, fingerprint, int(delay) or 1)
        await enqueue(redis, check_id, delay=delay)
    logger.info("recovered %s checks", len(plans))


async def run_worker() -> None:
    setup_logging()
    await prepare_runtime()
    redis = create_redis()
    owner = str(uuid4())
    stop = asyncio.Event()
    _install_signals(stop)
    inflight: set[asyncio.Task[None]] = set()
    concurrency = max(1, int(get_settings().worker_concurrency))
    try:
        await recover_jobs(redis)
        while not stop.is_set():
            finished = {task for task in inflight if task.done()}
            for task in finished:
                with suppress(Exception):
                    task.result()
            inflight -= finished
            await promote_due(redis)
            if len(inflight) >= concurrency:
                await asyncio.wait(inflight, timeout=1, return_when=asyncio.FIRST_COMPLETED)
                continue
            item = await redis.blpop([READY_KEY], timeout=2)
            if not item:
                continue
            check_id = item[1]
            if isinstance(check_id, bytes):
                check_id = check_id.decode("utf-8")
            from app.jobs import process_check

            inflight.add(asyncio.create_task(process_check(str(check_id), redis, owner)))
    finally:
        if inflight:
            await asyncio.wait(inflight)
        await redis.aclose()
        await get_engine().dispose()


def _install_signals(stop: asyncio.Event) -> None:
    if sys.platform == "win32":
        return
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            return


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
