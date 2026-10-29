from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.lookup.mapping import ROW_STATUSES
from app.models import CheckLane, PhoneCheck, PhoneCheckRow


async def status_counts(db: AsyncSession, check_id: object) -> dict[str, int]:
    result = await db.execute(
        select(PhoneCheckRow.status, func.count())
        .where(PhoneCheckRow.check_id == check_id)
        .group_by(PhoneCheckRow.status)
    )
    counts = {name: 0 for name in (*ROW_STATUSES, "pending")}
    for status, count in result.all():
        key = "pending" if status is None else str(status)
        if key not in counts:
            counts["error"] += int(count)
        else:
            counts[key] += int(count)
    return counts


async def refresh_progress(db: AsyncSession, check: PhoneCheck) -> dict[str, int]:
    await db.execute(select(PhoneCheck).where(PhoneCheck.id == check.id).with_for_update())
    counts = await status_counts(db, check.id)
    check.processed = sum(counts[name] for name in ROW_STATUSES)
    check.found_count = counts["found"]
    return counts


async def has_open_work(db: AsyncSession, check: PhoneCheck) -> bool:
    if check.cleanup_phones:
        return True
    lane_cleanup = await db.execute(
        select(CheckLane.cleanup_phones).where(
            CheckLane.check_id == check.id,
            CheckLane.status.notin_(("completed", "failed")),
        )
    )
    for (phones,) in lane_cleanup.all():
        if phones:
            return True
    pending = await db.scalar(
        select(func.count())
        .select_from(PhoneCheckRow)
        .where(PhoneCheckRow.check_id == check.id, PhoneCheckRow.status.is_(None))
    )
    if pending:
        return True
    enrich = await db.scalar(
        select(func.count())
        .select_from(PhoneCheckRow)
        .where(PhoneCheckRow.check_id == check.id, PhoneCheckRow.enrich_pending.is_(True))
    )
    return bool(enrich)
