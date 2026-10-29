from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func, select

from app.accounts import profile_from_me, session_fingerprint, upsert_account
from app.api.auth import raise_login
from app.api.deps import Db, RedisClient
from app.locking import SessionLock, cooldown_ttl, session_lock_key
from app.models import AccountSession, CheckLane, PhoneCheck
from app.schemas import SessionCreate, SessionPublic, session_to_public
from app.telegram_client import open_client, user_authorized

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionPublic])
async def list_sessions(db: Db) -> list[SessionPublic]:
    result = await db.execute(select(AccountSession).order_by(AccountSession.created_at.desc()))
    return [session_to_public(row) for row in result.scalars().all()]


@router.post("", response_model=SessionPublic, status_code=201)
async def create_session(payload: SessionCreate, db: Db, redis: RedisClient) -> SessionPublic:
    fingerprint = session_fingerprint(payload.string_session)
    cooling = await cooldown_ttl(redis, fingerprint)
    if cooling > 0:
        raise HTTPException(
            status_code=429,
            detail=f"FLOOD_WAIT {cooling} с",
            headers={"Retry-After": str(cooling)},
        )
    lock = SessionLock(redis, session_lock_key(fingerprint), str(uuid4()))
    if not await lock.acquire():
        raise HTTPException(status_code=409, detail="Сесія зараз використовується")
    try:
        try:
            async with open_client(payload.api_id, payload.api_hash, payload.string_session) as client:
                if not await user_authorized(client):
                    raise HTTPException(status_code=400, detail="Сесія не авторизована")
                me = await client.get_me()
                saved = client.session.save()
        except HTTPException:
            raise
        except Exception as exc:
            raise_login(exc)
        if not isinstance(saved, str) or not saved:
            raise HTTPException(status_code=400, detail="Сесія не авторизована")
        profile = profile_from_me(me)
        account = await upsert_account(
            db,
            api_id=payload.api_id,
            api_hash=payload.api_hash,
            string_session=saved,
            label=(payload.label or "").strip() or None,
            phone=profile["phone"],
            username=profile["username"],
            first_name=profile["first_name"],
        )
        await db.commit()
        await db.refresh(account)
        return session_to_public(account)
    finally:
        await lock.release()


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, db: Db) -> Response:
    try:
        parsed = UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Сесію не знайдено") from exc
    account = await db.get(AccountSession, parsed)
    if account is None:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")
    active = await db.scalar(
        select(func.count())
        .select_from(PhoneCheck)
        .where(
            PhoneCheck.session_id == account.id,
            PhoneCheck.status.in_(("queued", "running", "paused")),
        )
    )
    active_lane = await db.scalar(
        select(func.count())
        .select_from(CheckLane)
        .join(PhoneCheck, PhoneCheck.id == CheckLane.check_id)
        .where(
            CheckLane.session_id == account.id,
            CheckLane.status.in_(("queued", "running", "paused")),
            PhoneCheck.status.in_(("queued", "running", "paused")),
        )
    )
    if active or active_lane:
        raise HTTPException(status_code=409, detail="Сесія використовується перевіркою")
    await db.delete(account)
    await db.commit()
    return Response(status_code=204)
