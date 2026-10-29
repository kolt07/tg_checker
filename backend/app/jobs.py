import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.tl.functions.contacts import DeleteByPhonesRequest

from app.accounts import apply_profile, decrypt_account
from app.callbacks import notify_callback
from app.config import get_settings
from app.counters import has_open_work, refresh_progress
from app.hooks import CheckHooks, enrich_key
from app.limits import limits_for_timeout
from app.locking import SessionLock, check_lock_key, cooldown_ttl, session_lock_key, set_cooldown
from app.lookup import DeleteDeferred, FloodPause, SessionFatal, SessionLockLost, get_strategy
from app.lookup.mapping import classify_exception, failure_message
from app.models import AccountSession, CheckLane, PhoneCheck, PhoneCheckRow
from app.queueing import enqueue
from app.telegram_client import open_client, user_authorized

logger = logging.getLogger("tgchecker.worker")

_TRANSIENT = {"ConnectionError", "OSError", "TimeoutError", "TimedOutError", "ConnectionErrorError"}


async def process_check(check_id: str, redis: Redis, owner: str) -> None:
    try:
        check_uuid = UUID(check_id)
    except ValueError:
        return
    from app.db import get_sessionmaker
    from app.redis_client import create_redis

    lock_redis = create_redis()
    try:
        async with get_sessionmaker()() as db:
            await _process(db, redis, lock_redis, check_uuid, owner)
    except Exception as exc:
        logger.info("check %s crashed %s", check_id, type(exc).__name__)
    finally:
        await lock_redis.aclose()


async def _process(
    db: AsyncSession,
    redis: Redis,
    lock_redis: Redis,
    check_id: UUID,
    owner: str,
) -> None:
    check = await db.get(PhoneCheck, check_id)
    if check is None or check.status in {"completed", "failed"}:
        return
    lanes = await _load_lanes(db, check.id)
    now = datetime.now(timezone.utc)
    if not lanes and check.status == "paused" and check.flood_wait_until and check.flood_wait_until > now:
        delay = (check.flood_wait_until - now).total_seconds()
        await enqueue(redis, str(check.id), delay=delay)
        return
    if lanes:
        try:
            await _process_parallel(db, redis, lock_redis, check.id, owner)
        except Exception as exc:
            logger.info("check %s parallel crashed %s", check_id, type(exc).__name__)
            await db.rollback()
            await _requeue(db, redis, check_id, delay=5, count_transient=True)
        return
    if not await has_open_work(db, check):
        await _mark_terminal(db, check.id, "completed", None)
        await _send_callback(db, check.id)
        await redis.delete(enrich_key(check.id))
        return
    if check.session_id is None:
        await _mark_terminal(db, check.id, "failed", "Сесію не знайдено")
        await _send_callback(db, check.id)
        return
    account = await db.get(AccountSession, check.session_id)
    if account is None:
        await _mark_terminal(db, check.id, "failed", "Сесію не знайдено")
        await _send_callback(db, check.id)
        return
    try:
        api_id, api_hash, string_session = decrypt_account(account)
    except Exception:
        await _mark_terminal(db, check.id, "failed", "Сесію не вдалося прочитати")
        await _send_callback(db, check.id)
        return

    fingerprint = account.session_fingerprint
    cooling = await cooldown_ttl(redis, fingerprint)
    if cooling > 0:
        await enqueue(redis, str(check.id), delay=cooling)
        return

    session_lock = SessionLock(lock_redis, session_lock_key(fingerprint), owner)
    job_lock = SessionLock(lock_redis, check_lock_key(check.id), owner)
    if not await job_lock.acquire():
        await enqueue(redis, str(check.id), delay=5)
        return
    if not await session_lock.acquire():
        await job_lock.release()
        await enqueue(redis, str(check.id), delay=5)
        return

    lost = asyncio.Event()
    outcome: str | None = None
    logger.info("check %s strategy %s", check.id, check.strategy)
    try:
        try:
            async with open_client(api_id, api_hash, string_session) as client:
                watch = asyncio.create_task(_refresh_locks([session_lock, job_lock], client, lost))
                try:
                    outcome = await _run_connected(db, redis, client, check, account, lost)
                finally:
                    watch.cancel()
                    with suppress(asyncio.CancelledError):
                        await watch
        except FloodPause as exc:
            await db.rollback()
            await _pause_for_flood(db, redis, check_id, fingerprint, exc.seconds)
            outcome = "paused"
        except DeleteDeferred:
            await db.rollback()
            outcome = await _retry_cleanup(db, redis, check_id)
        except (SessionLockLost, SessionFatal) as exc:
            await db.rollback()
            if isinstance(exc, SessionFatal):
                await _mark_terminal(db, check_id, "failed", failure_message(exc))
                outcome = "failed"
            else:
                outcome = await _requeue(db, redis, check_id, delay=5, count_transient=True)
        except Exception as exc:
            await db.rollback()
            outcome = await _handle_unexpected(db, redis, check_id, fingerprint, lost, exc)
    finally:
        await session_lock.release()
        await job_lock.release()

    if outcome in {"completed", "failed"}:
        await _send_callback(db, check_id)
        await redis.delete(enrich_key(check_id))


async def _process_parallel(
    db: AsyncSession,
    redis: Redis,
    lock_redis: Redis,
    check_id: UUID,
    owner: str,
) -> None:
    job_lock = SessionLock(lock_redis, check_lock_key(check_id), owner)
    if not await job_lock.acquire():
        await enqueue(redis, str(check_id), delay=5)
        return
    delay: float | None = None
    try:
        delay = await _run_parallel_locked(db, redis, lock_redis, check_id, owner, job_lock)
    finally:
        await job_lock.release()
    if delay is not None:
        await enqueue(redis, str(check_id), delay=delay)


async def _run_parallel_locked(
    db: AsyncSession,
    redis: Redis,
    lock_redis: Redis,
    check_id: UUID,
    owner: str,
    job_lock: SessionLock,
) -> float | None:
    check = await db.get(PhoneCheck, check_id)
    if check is None or check.status in {"completed", "failed"}:
        return None
    if not await has_open_work(db, check):
        if await _mark_terminal(db, check.id, "completed", None):
            await _send_callback(db, check.id)
            await redis.delete(enrich_key(check.id))
        return None

    now = datetime.now(timezone.utc)
    lanes = await _load_lanes(db, check.id)
    runnable: list[UUID] = []
    waiting: list[float] = []
    for lane in lanes:
        if lane.status in {"completed", "failed"}:
            continue
        if lane.status == "running":
            lane.status = "queued"
        if not await _lane_open(db, lane):
            lane.status = "completed"
            lane.pause_reason = None
            lane.flood_wait_until = None
            continue
        if lane.flood_wait_until and lane.flood_wait_until > now:
            waiting.append((lane.flood_wait_until - now).total_seconds())
            lane.status = "paused"
            lane.pause_reason = "FLOOD_WAIT"
            continue
        account = await db.get(AccountSession, lane.session_id) if lane.session_id else None
        if account is None:
            await _fail_lane(db, lane, "Сесію не знайдено")
            continue
        cooling = await cooldown_ttl(redis, account.session_fingerprint)
        if cooling > 0:
            lane.status = "paused"
            lane.pause_reason = "FLOOD_WAIT"
            lane.flood_wait_until = now + timedelta(seconds=cooling)
            waiting.append(float(cooling))
            continue
        runnable.append(lane.id)
    await db.commit()

    if not runnable:
        return await _aggregate(db, redis, check_id, waiting)

    check = await db.get(PhoneCheck, check_id)
    if check is None:
        return None
    check.status = "running"
    check.pause_reason = None
    check.flood_wait_until = None
    if check.started_at is None:
        check.started_at = now
    check.heartbeat_at = now
    await db.commit()

    clients: list[object] = []
    lost_events: list[asyncio.Event] = []
    stop_watch = asyncio.Event()
    watch = asyncio.create_task(_watch_check_lock(job_lock, lost_events, clients, stop_watch))
    logger.info("check %s lanes %s", check_id, len(runnable))
    try:
        await asyncio.gather(
            *[
                _lane_task(check_id, lane_id, redis, lock_redis, owner, clients, lost_events)
                for lane_id in runnable
            ]
        )
    finally:
        stop_watch.set()
        watch.cancel()
        with suppress(asyncio.CancelledError):
            await watch
    return await _aggregate(db, redis, check_id, [])


async def _lane_task(
    check_id: UUID,
    lane_id: UUID,
    redis: Redis,
    lock_redis: Redis,
    owner: str,
    clients: list[object],
    lost_events: list[asyncio.Event],
) -> None:
    from app.db import get_sessionmaker

    try:
        async with get_sessionmaker()() as db:
            await _execute_lane(db, redis, lock_redis, check_id, lane_id, owner, clients, lost_events)
    except Exception as exc:
        logger.info("lane %s crashed %s", lane_id, type(exc).__name__)
        try:
            async with get_sessionmaker()() as db:
                await _requeue(db, redis, check_id, delay=5, count_transient=True, lane_id=lane_id)
        except Exception as inner:
            logger.info("lane %s cleanup failed %s", lane_id, type(inner).__name__)


async def _execute_lane(
    db: AsyncSession,
    redis: Redis,
    lock_redis: Redis,
    check_id: UUID,
    lane_id: UUID,
    owner: str,
    clients: list[object],
    lost_events: list[asyncio.Event],
) -> None:
    check = await db.get(PhoneCheck, check_id)
    lane = await db.get(CheckLane, lane_id)
    if check is None or lane is None or check.status in {"completed", "failed"}:
        return
    if lane.status in {"completed", "failed"}:
        return
    account = await db.get(AccountSession, lane.session_id) if lane.session_id else None
    if account is None:
        await _fail_lane(db, lane, "Сесію не знайдено")
        return
    try:
        api_id, api_hash, string_session = decrypt_account(account)
    except Exception:
        await _fail_lane(db, lane, "Сесію не вдалося прочитати")
        return

    fingerprint = account.session_fingerprint
    lost = asyncio.Event()
    lost_events.append(lost)
    session_lock = SessionLock(lock_redis, session_lock_key(fingerprint), f"{owner}:{lane_id}")
    if not await session_lock.acquire():
        lane.status = "queued"
        lane.heartbeat_at = datetime.now(timezone.utc)
        await db.commit()
        return
    try:
        try:
            async with open_client(api_id, api_hash, string_session) as client:
                clients.append(client)
                watch = asyncio.create_task(_refresh_locks([session_lock], client, lost))
                try:
                    await _run_connected(db, redis, client, check, account, lost, lane)
                finally:
                    watch.cancel()
                    with suppress(asyncio.CancelledError):
                        await watch
                    if client in clients:
                        clients.remove(client)
        except FloodPause as exc:
            await db.rollback()
            await _pause_for_flood(db, redis, check_id, fingerprint, exc.seconds, lane_id)
        except DeleteDeferred:
            await db.rollback()
            await _retry_cleanup(db, redis, check_id, lane_id)
        except (SessionLockLost, SessionFatal) as exc:
            await db.rollback()
            if isinstance(exc, SessionFatal):
                await _fail_lane_by_id(db, lane_id, failure_message(exc))
            else:
                await _requeue(db, redis, check_id, delay=5, count_transient=True, lane_id=lane_id)
        except Exception as exc:
            await db.rollback()
            await _handle_unexpected(db, redis, check_id, fingerprint, lost, exc, lane_id)
    finally:
        await session_lock.release()


async def _aggregate(
    db: AsyncSession,
    redis: Redis,
    check_id: UUID,
    extra_waits: list[float],
) -> float | None:
    db.expire_all()
    check = await db.get(PhoneCheck, check_id)
    if check is None or check.status in {"completed", "failed"}:
        return None
    lanes = await _load_lanes(db, check.id)
    now = datetime.now(timezone.utc)
    open_lanes: list[CheckLane] = []
    waits = [wait for wait in extra_waits if wait > 0]
    for lane in lanes:
        if lane.status in {"completed", "failed"}:
            continue
        if not await _lane_open(db, lane):
            lane.status = "completed"
            lane.pause_reason = None
            lane.flood_wait_until = None
            continue
        open_lanes.append(lane)
        if lane.flood_wait_until and lane.flood_wait_until > now:
            waits.append((lane.flood_wait_until - now).total_seconds())
    recorded = [lane.last_flood_wait_seconds for lane in lanes if lane.last_flood_wait_seconds]
    if recorded:
        check.last_flood_wait_seconds = max(recorded)
    check.flood_wait_count = sum(lane.flood_wait_count or 0 for lane in lanes)
    await refresh_progress(db, check)
    if not open_lanes and not await has_open_work(db, check):
        failed = [lane for lane in lanes if lane.status == "failed"]
        if lanes and len(failed) == len(lanes):
            message = next((lane.error_message for lane in failed if lane.error_message), "Усі акаунти завершились помилкою")
            changed = await _mark_terminal(db, check.id, "failed", message)
        else:
            changed = await _mark_terminal(db, check.id, "completed", None)
        if changed:
            await _send_callback(db, check.id)
            await redis.delete(enrich_key(check.id))
        return None
    check.heartbeat_at = now
    if open_lanes and all(_lane_is_waiting(lane, now) for lane in open_lanes):
        delay = max(min(waits) if waits else 5, 1)
        check.status = "paused"
        check.pause_reason = "FLOOD_WAIT"
        check.flood_wait_seconds = int(delay)
        check.flood_wait_until = now + timedelta(seconds=delay)
        await db.commit()
        return delay
    check.status = "queued"
    check.pause_reason = None
    check.flood_wait_until = None
    await db.commit()
    return 1


def _lane_is_waiting(lane: CheckLane, now: datetime) -> bool:
    if lane.flood_wait_until and lane.flood_wait_until > now:
        return True
    return lane.status == "paused" and lane.flood_wait_until is None


async def _run_connected(
    db: AsyncSession,
    redis: Redis,
    client: object,
    check: PhoneCheck,
    account: AccountSession,
    lost: asyncio.Event,
    lane: CheckLane | None = None,
) -> str:
    if lost.is_set():
        raise SessionLockLost()
    if not await user_authorized(client):
        if lane is None:
            await _mark_terminal(db, check.id, "failed", "Сесія не авторизована")
        else:
            await _fail_lane(db, lane, "Сесія не авторизована")
        return "failed"
    me = await client.get_me()
    apply_profile(account, me)
    now = datetime.now(timezone.utc)
    scope = lane if lane is not None else check
    scope.status = "running"
    scope.pause_reason = None
    if check.started_at is None:
        check.started_at = now
    scope.heartbeat_at = now
    check.heartbeat_at = now
    await db.commit()

    if scope.cleanup_phones:
        try:
            await client(DeleteByPhonesRequest(phones=list(scope.cleanup_phones)))
        except Exception as exc:
            if lost.is_set():
                raise SessionLockLost() from exc
            decision = classify_exception(exc)
            if decision.kind == "flood":
                raise FloodPause(decision.seconds or 0) from exc
            if decision.kind == "fatal":
                raise SessionFatal(decision.error_name) from exc
            raise DeleteDeferred() from exc
        scope.cleanup_phones = []
        scope.cleanup_attempts = 0
        await db.commit()

    before = check.processed
    before_open = await _open_count(db, lane) if lane is not None else 0
    hooks = CheckHooks(db, redis, check, lost, lane)
    strategy = get_strategy(check.strategy)
    await strategy.run(client, hooks, limits_for_timeout(get_settings(), check.timeout_seconds))
    if lost.is_set():
        raise SessionLockLost()
    if lane is not None:
        await db.refresh(lane)
    await db.refresh(check)
    open_work = await _lane_open(db, lane) if lane is not None else await has_open_work(db, check)
    if open_work:
        scope = lane if lane is not None else check
        cleanup = list(scope.cleanup_phones or [])
        if lane is None:
            stalled = check.processed == before and not cleanup
        else:
            stalled = await _open_count(db, lane) >= before_open and not cleanup
        if stalled:
            scope.transient_attempts = (scope.transient_attempts or 0) + 1
            if scope.transient_attempts >= 5:
                if lane is None:
                    await _mark_terminal(db, check.id, "failed", "Перевірка не просувається")
                else:
                    await _fail_lane(db, lane, "Перевірка не просувається")
                return "failed"
            delay = 10
        else:
            scope.transient_attempts = 0
            delay = 1
        scope.status = "queued"
        scope.heartbeat_at = datetime.now(timezone.utc)
        check.heartbeat_at = scope.heartbeat_at
        await db.commit()
        if lane is None:
            await enqueue(redis, str(check.id), delay=delay)
        return "requeued"
    if lane is None:
        await _mark_terminal(db, check.id, "completed", None)
        return "completed"
    lane.status = "completed"
    lane.pause_reason = None
    lane.flood_wait_until = None
    lane.error_message = None
    lane.heartbeat_at = datetime.now(timezone.utc)
    await db.commit()
    return "completed"


async def _handle_unexpected(
    db: AsyncSession,
    redis: Redis,
    check_id: UUID,
    fingerprint: str,
    lost: asyncio.Event,
    exc: Exception,
    lane_id: UUID | None = None,
) -> str:
    logger.info("check %s error %s", check_id, type(exc).__name__)
    if lost.is_set():
        return await _requeue(db, redis, check_id, delay=5, count_transient=True, lane_id=lane_id)
    decision = classify_exception(exc)
    if decision.kind == "flood":
        await _pause_for_flood(db, redis, check_id, fingerprint, decision.seconds or 0, lane_id)
        return "paused"
    if decision.kind == "fatal":
        message = failure_message(exc)
        if lane_id is None:
            await _mark_terminal(db, check_id, "failed", message)
        else:
            await _fail_lane_by_id(db, lane_id, message)
        return "failed"
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)) or type(exc).__name__ in _TRANSIENT:
        return await _requeue(db, redis, check_id, delay=5, count_transient=True, lane_id=lane_id)
    message = failure_message(exc)
    if lane_id is None:
        await _mark_terminal(db, check_id, "failed", message)
    else:
        await _fail_lane_by_id(db, lane_id, message)
    return "failed"


async def _mark_terminal(db: AsyncSession, check_id: UUID, status: str, message: str | None) -> bool:
    check = await db.get(PhoneCheck, check_id)
    if check is None or check.status in {"completed", "failed"}:
        return False
    now = datetime.now(timezone.utc)
    check.status = status
    check.error_message = message[:300] if message else None
    check.finished_at = now
    check.heartbeat_at = now
    if status == "completed":
        check.pause_reason = None
        check.flood_wait_until = None
    await _close_lanes(db, check_id, status)
    await refresh_progress(db, check)
    await db.commit()
    return True


async def _close_lanes(db: AsyncSession, check_id: UUID, status: str) -> None:
    lane_status = "completed" if status == "completed" else "failed"
    await db.execute(
        update(CheckLane)
        .where(
            CheckLane.check_id == check_id,
            CheckLane.status.notin_(("completed", "failed")),
        )
        .values(status=lane_status, heartbeat_at=datetime.now(timezone.utc))
    )


async def _send_callback(db: AsyncSession, check_id: UUID) -> None:
    check = await db.get(PhoneCheck, check_id)
    if check is None or not check.callback_url:
        return
    from app.counters import status_counts

    counts = await status_counts(db, check.id)
    await notify_callback(check, counts)
    await db.commit()


async def _pause_for_flood(
    db: AsyncSession,
    redis: Redis,
    check_id: UUID,
    fingerprint: str,
    seconds: int,
    lane_id: UUID | None = None,
) -> None:
    check = await db.get(PhoneCheck, check_id)
    lane = await db.get(CheckLane, lane_id) if lane_id is not None else None
    if check is None:
        return
    target = lane if lane is not None else check
    if target is None:
        return
    if lane is None and check.status in {"completed", "failed"}:
        return
    if lane is not None and lane.status in {"completed", "failed"}:
        return
    recorded = max(int(seconds), 0)
    delay = max(recorded, 1)
    now = datetime.now(timezone.utc)
    target.status = "paused"
    target.pause_reason = "FLOOD_WAIT"
    target.flood_wait_seconds = recorded
    target.last_flood_wait_seconds = recorded
    target.flood_wait_count = (target.flood_wait_count or 0) + 1
    target.flood_wait_until = now + timedelta(seconds=delay)
    target.heartbeat_at = now
    check.heartbeat_at = now
    await db.commit()
    await set_cooldown(redis, fingerprint, delay)
    key = enrich_key(check_id)
    if await redis.exists(key):
        await redis.expire(key, max(6 * 3600, delay + 3600))
    if lane_id is None:
        await enqueue(redis, str(check_id), delay=delay)
    logger.info("check %s FLOOD_WAIT %s", check_id, recorded)


async def _retry_cleanup(
    db: AsyncSession,
    redis: Redis,
    check_id: UUID,
    lane_id: UUID | None = None,
) -> str:
    if lane_id is not None:
        lane = await db.get(CheckLane, lane_id)
        if lane is None or lane.status in {"completed", "failed"}:
            return "failed"
        lane.cleanup_attempts = (lane.cleanup_attempts or 0) + 1
        if lane.cleanup_attempts >= 5:
            await _fail_lane(db, lane, "Не вдалося прибрати тимчасові контакти з адресної книги")
            return "failed"
        lane.status = "queued"
        lane.heartbeat_at = datetime.now(timezone.utc)
        await db.commit()
        return "requeued"
    check = await db.get(PhoneCheck, check_id)
    if check is None or check.status in {"completed", "failed"}:
        return "failed"
    check.cleanup_attempts = (check.cleanup_attempts or 0) + 1
    if check.cleanup_attempts >= 5:
        await _mark_terminal(db, check_id, "failed", "Не вдалося прибрати тимчасові контакти з адресної книги")
        return "failed"
    check.status = "queued"
    check.heartbeat_at = datetime.now(timezone.utc)
    await db.commit()
    await enqueue(redis, str(check_id), delay=10)
    return "requeued"


async def _requeue(
    db: AsyncSession,
    redis: Redis,
    check_id: UUID,
    *,
    delay: float,
    count_transient: bool,
    lane_id: UUID | None = None,
) -> str:
    if lane_id is not None:
        lane = await db.get(CheckLane, lane_id)
        if lane is None or lane.status in {"completed", "failed"}:
            return "failed"
        if count_transient:
            lane.transient_attempts = (lane.transient_attempts or 0) + 1
            if lane.transient_attempts >= 5:
                await _fail_lane(db, lane, "З’єднання з Telegram перервалось")
                return "failed"
        lane.status = "queued"
        lane.heartbeat_at = datetime.now(timezone.utc)
        await db.commit()
        return "requeued"
    check = await db.get(PhoneCheck, check_id)
    if check is None or check.status in {"completed", "failed"}:
        return "failed"
    if count_transient:
        check.transient_attempts = (check.transient_attempts or 0) + 1
        if check.transient_attempts >= 5:
            await _mark_terminal(db, check_id, "failed", "З’єднання з Telegram перервалось")
            return "failed"
    check.status = "queued"
    check.heartbeat_at = datetime.now(timezone.utc)
    await db.commit()
    await enqueue(redis, str(check_id), delay=delay)
    return "requeued"


async def _fail_lane(db: AsyncSession, lane: CheckLane, message: str | None) -> None:
    if lane.status in {"completed", "failed"}:
        return
    now = datetime.now(timezone.utc)
    text = (message or "Помилка")[:300]
    lane.status = "failed"
    lane.error_message = text
    lane.cleanup_phones = []
    lane.heartbeat_at = now
    await db.execute(
        update(PhoneCheckRow)
        .where(PhoneCheckRow.lane_id == lane.id, PhoneCheckRow.status.is_(None))
        .values(status="error", error_detail=text, enrich_pending=False)
    )
    await db.execute(
        update(PhoneCheckRow)
        .where(PhoneCheckRow.lane_id == lane.id, PhoneCheckRow.enrich_pending.is_(True))
        .values(enrich_pending=False)
    )
    check = await db.get(PhoneCheck, lane.check_id)
    if check is not None:
        check.heartbeat_at = now
        await refresh_progress(db, check)
    await db.commit()


async def _fail_lane_by_id(db: AsyncSession, lane_id: UUID, message: str | None) -> None:
    lane = await db.get(CheckLane, lane_id)
    if lane is not None:
        await _fail_lane(db, lane, message)


async def _load_lanes(db: AsyncSession, check_id: UUID) -> list[CheckLane]:
    result = await db.execute(
        select(CheckLane).where(CheckLane.check_id == check_id).order_by(CheckLane.position, CheckLane.id)
    )
    return list(result.scalars().all())


async def _lane_open(db: AsyncSession, lane: CheckLane) -> bool:
    if lane.cleanup_phones:
        return True
    return await _open_count(db, lane) > 0


async def _open_count(db: AsyncSession, lane: CheckLane) -> int:
    pending = await db.scalar(
        select(func.count())
        .select_from(PhoneCheckRow)
        .where(PhoneCheckRow.lane_id == lane.id, PhoneCheckRow.status.is_(None))
    )
    enrich = await db.scalar(
        select(func.count())
        .select_from(PhoneCheckRow)
        .where(PhoneCheckRow.lane_id == lane.id, PhoneCheckRow.enrich_pending.is_(True))
    )
    return int(pending or 0) + int(enrich or 0)


async def _refresh_locks(locks: list[SessionLock], client: object, lost: asyncio.Event) -> None:
    try:
        while True:
            await asyncio.sleep(30)
            for lock in locks:
                if not await lock.refresh(120):
                    lost.set()
                    await _disconnect_client(client)
                    return
    except asyncio.CancelledError:
        return
    except Exception:
        lost.set()


async def _watch_check_lock(
    lock: SessionLock,
    lost_events: list[asyncio.Event],
    clients: list[object],
    stop: asyncio.Event,
) -> None:
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
                return
            except TimeoutError:
                pass
            if await lock.refresh(120):
                continue
            for event in list(lost_events):
                event.set()
            for client in list(clients):
                await _disconnect_client(client)
            return
    except asyncio.CancelledError:
        return
    except Exception:
        for event in list(lost_events):
            event.set()


async def _disconnect_client(client: object) -> None:
    disconnect = getattr(client, "disconnect", None)
    if disconnect is None:
        return
    try:
        result = disconnect()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        return
