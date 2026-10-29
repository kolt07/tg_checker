import logging
from typing import Any

import httpx

from app.models import PhoneCheck

logger = logging.getLogger("tgchecker.callback")


async def notify_callback(check: PhoneCheck, counts: dict[str, int]) -> None:
    if not check.callback_url:
        return
    payload: dict[str, Any] = {
        "id": str(check.id),
        "status": check.status,
        "total": check.total,
        "processed": check.processed,
        "found": check.found_count,
        "counts": counts,
        "flood_wait_count": check.flood_wait_count or 0,
        "last_flood_wait_seconds": check.last_flood_wait_seconds,
        "result_path": f"/api/v1/checks/{check.id}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                check.callback_url,
                json=payload,
                headers={"X-Tg-Checker-Event": "check.finished"},
            )
        if response.status_code >= 400:
            check.callback_status = "failed"
            check.callback_error = f"HTTP {response.status_code}"
        else:
            check.callback_status = "ok"
            check.callback_error = None
    except Exception as exc:
        logger.info("callback failed %s", type(exc).__name__)
        check.callback_status = "failed"
        check.callback_error = type(exc).__name__
