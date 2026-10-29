from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import upsert_account
from app.api.deps import Db, RedisClient
from app.config import get_settings
from app.csv_export import check_to_json, rows_to_csv
from app.counters import status_counts
from app.distribute import assign_lane_indexes
from app.models import AccountSession, CheckLane, PhoneCheck, PhoneCheckRow
from app.mtproto import auth_key_to_string_session
from app.phones import expand_phone_inputs, normalize_phone
from app.queueing import enqueue
from app.schemas import (
    CheckAccount,
    CheckCreate,
    CheckCreated,
    CheckDetail,
    CheckSummary,
    row_to_public,
    summary_from_check,
)

router = APIRouter(tags=["checks"])


def _parse_id(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc


async def _load_check(db: Db, check_id: str) -> PhoneCheck:
    check = await db.get(PhoneCheck, _parse_id(check_id, "Перевірку не знайдено"))
    if check is None:
        raise HTTPException(status_code=404, detail="Перевірку не знайдено")
    return check


def _remember_account(accounts: list[AccountSession], seen: set[UUID], account: AccountSession) -> None:
    if account.id in seen:
        raise HTTPException(
            status_code=422,
            detail="Одна сесія вказана кілька разів. Паралельно працюють лише різні акаунти.",
        )
    seen.add(account.id)
    accounts.append(account)


async def _saved_accounts(db: AsyncSession, session_ids: list[UUID]) -> list[AccountSession]:
    accounts: list[AccountSession] = []
    seen: set[UUID] = set()
    for session_id in session_ids:
        if session_id in seen:
            raise HTTPException(
                status_code=422,
                detail="Одна сесія вказана кілька разів. Паралельно працюють лише різні акаунти.",
            )
        seen.add(session_id)
        account = await db.get(AccountSession, session_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Сесію не знайдено")
        accounts.append(account)
    return accounts


async def _account_from_credentials(db: AsyncSession, item: CheckAccount) -> AccountSession:
    if item.string_session:
        string_session = item.string_session
    else:
        try:
            string_session = auth_key_to_string_session(item.auth_key or "", item.dc_id or 0)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    account = await upsert_account(
        db,
        api_id=item.api_id,
        api_hash=item.api_hash,
        string_session=string_session,
        label=None,
        phone=None,
        username=None,
        first_name=None,
    )
    await db.flush()
    return account


async def _accounts_for_check(db: AsyncSession, payload: CheckCreate) -> list[AccountSession]:
    raw_ids: list[UUID] = []
    if payload.session_ids:
        raw_ids.extend(payload.session_ids)
    if payload.session_id is not None and payload.session_id not in raw_ids:
        raw_ids.append(payload.session_id)
    accounts = await _saved_accounts(db, raw_ids)
    seen = {account.id for account in accounts}
    inline = list(payload.accounts or [])
    if payload.api_id and payload.api_hash and (payload.string_session or payload.auth_key):
        inline.append(
            CheckAccount(
                api_id=payload.api_id,
                api_hash=payload.api_hash,
                string_session=payload.string_session,
                auth_key=payload.auth_key,
                dc_id=payload.dc_id,
            )
        )
    for item in inline:
        _remember_account(accounts, seen, await _account_from_credentials(db, item))
    if not accounts:
        raise HTTPException(
            status_code=422,
            detail="Потрібні session_id, session_ids, accounts або api_id, api_hash і string_session чи auth_key",
        )
    return accounts


@router.post("/checks", response_model=CheckCreated, status_code=202)
async def create_check(payload: CheckCreate, db: Db, redis: RedisClient) -> CheckCreated:
    settings = get_settings()
    accounts = await _accounts_for_check(db, payload)
    account_limit = max(1, settings.max_accounts)
    if len(accounts) > account_limit:
        raise HTTPException(status_code=422, detail=f"Забагато акаунтів. Максимум {account_limit}")

    expanded = expand_phone_inputs(payload.phones)
    if not expanded:
        raise HTTPException(status_code=422, detail="Немає номерів")
    if len(expanded) > settings.max_phones:
        raise HTTPException(status_code=422, detail=f"Забагато номерів. Максимум {settings.max_phones}")

    rows: list[PhoneCheckRow] = []
    invalid = 0
    for position, raw in enumerate(expanded):
        stored = raw if len(raw) <= 80 else raw[:80]
        e164 = normalize_phone(raw, settings.default_phone_region)
        if e164 is None:
            invalid += 1
            rows.append(
                PhoneCheckRow(
                    position=position,
                    input_phone=stored,
                    phone_e164=None,
                    status="invalid_phone",
                    error_detail="Некоректний номер",
                    premium=False,
                    verified=False,
                    bot=False,
                    scam=False,
                    fake=False,
                    enrich_pending=False,
                )
            )
        else:
            rows.append(
                PhoneCheckRow(
                    position=position,
                    input_phone=stored,
                    phone_e164=e164,
                    status=None,
                    premium=False,
                    verified=False,
                    bot=False,
                    scam=False,
                    fake=False,
                    enrich_pending=False,
                )
            )

    check = PhoneCheck(
        session_id=accounts[0].id,
        strategy="import_and_enrich",
        status="queued",
        callback_url=payload.callback_url,
        total=len(rows),
        processed=invalid,
        found_count=0,
        flood_wait_count=0,
        cleanup_phones=[],
        cleanup_attempts=0,
        transient_attempts=0,
        account_count=len(accounts),
        timeout_seconds=payload.timeout,
    )
    db.add(check)
    await db.flush()
    lanes: list[CheckLane] = []
    for position, account in enumerate(accounts):
        lane = CheckLane(
            check_id=check.id,
            session_id=account.id,
            position=position,
            status="queued",
            cleanup_phones=[],
            cleanup_attempts=0,
            transient_attempts=0,
            flood_wait_count=0,
        )
        db.add(lane)
        lanes.append(lane)
    await db.flush()
    lane_of_row = assign_lane_indexes([row.phone_e164 for row in rows], len(lanes))
    for row, lane_index in zip(rows, lane_of_row, strict=True):
        row.check_id = check.id
        if lane_index is not None:
            row.lane_id = lanes[lane_index].id
        db.add(row)
    await db.commit()
    try:
        await enqueue(redis, str(check.id))
    except Exception as exc:
        check.status = "failed"
        check.error_message = "Черга недоступна"
        for lane in lanes:
            lane.status = "failed"
            lane.error_message = "Черга недоступна"
        await db.commit()
        raise HTTPException(status_code=503, detail="Черга недоступна") from exc
    return CheckCreated(
        id=check.id,
        status=check.status,
        total=check.total,
        processed=check.processed,
        account_count=check.account_count,
        timeout=check.timeout_seconds,
    )


@router.get("/checks", response_model=list[CheckSummary])
async def list_checks(
    db: Db,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[CheckSummary]:
    result = await db.execute(select(PhoneCheck).order_by(PhoneCheck.created_at.desc()).limit(limit))
    return [summary_from_check(row) for row in result.scalars().all()]


@router.get("/checks/{check_id}", response_model=CheckDetail)
async def get_check(
    check_id: str,
    db: Db,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5000, ge=1, le=5000),
) -> CheckDetail:
    check = await _load_check(db, check_id)
    rows_result = await db.execute(
        select(PhoneCheckRow)
        .where(PhoneCheckRow.check_id == check.id)
        .order_by(PhoneCheckRow.position)
        .offset(offset)
        .limit(limit)
    )
    counts = await status_counts(db, check.id)
    summary = summary_from_check(check)
    return CheckDetail(
        **summary.model_dump(),
        counts=counts,
        flood_wait_seconds=check.flood_wait_seconds,
        callback_url=check.callback_url,
        callback_status=check.callback_status,
        started_at=check.started_at,
        finished_at=check.finished_at,
        rows=[row_to_public(row) for row in rows_result.scalars().all()],
    )


@router.get("/checks/{check_id}/export")
async def export_check(
    check_id: str,
    db: Db,
    export_format: Literal["json", "csv"] = Query(default="json", alias="format"),
) -> Response:
    check = await _load_check(db, check_id)
    rows_result = await db.execute(
        select(PhoneCheckRow).where(PhoneCheckRow.check_id == check.id).order_by(PhoneCheckRow.position)
    )
    public_rows = [row_to_public(row) for row in rows_result.scalars().all()]
    if export_format == "json":
        document = {
            "id": str(check.id),
            "status": check.status,
            "total": check.total,
            "processed": check.processed,
            "found": check.found_count,
            "account_count": check.account_count or 1,
            "timeout": check.timeout_seconds,
            "rows": [row.model_dump(mode="json") for row in public_rows],
        }
        body = check_to_json(document).encode("utf-8")
        media_type = "application/json; charset=utf-8"
        filename = f"perevirka-{check.id}.json"
    else:
        body = rows_to_csv([row.model_dump() for row in public_rows]).encode("utf-8")
        media_type = "text/csv; charset=utf-8"
        filename = f"perevirka-{check.id}.csv"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
