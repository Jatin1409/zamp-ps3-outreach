"""Access code + rate limits, so the public demo link can't be used to run up API bills."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac

from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select

from .config import get_settings
from .db import session, utcnow
from .models import Run


def _ok(code: str | None) -> bool:
    expected = get_settings().access_code
    return bool(code) and hmac.compare_digest(code.encode(), expected.encode())


async def require_access(x_access_code: str | None = Header(default=None),
                         code: str | None = Query(default=None)):
    # `code` query param exists only because EventSource cannot send headers.
    if not _ok(x_access_code or code):
        raise HTTPException(status_code=401, detail="Access code required. Ask the person who shared this link.")


def client_id(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


async def check_run_budget(cid: str):
    s_ = get_settings()
    now = utcnow()
    async with session() as s:
        hour = (await s.execute(select(func.count()).select_from(Run).where(
            Run.client_id == cid, Run.created_at >= now - dt.timedelta(hours=1)))).scalar() or 0
        day = (await s.execute(select(func.count()).select_from(Run).where(
            Run.created_at >= now - dt.timedelta(days=1)))).scalar() or 0
        active = (await s.execute(select(func.count()).select_from(Run).where(
            Run.status.in_(("queued", "running"))))).scalar() or 0
    if hour >= s_.runs_per_hour_per_client:
        raise HTTPException(429, f"Rate limit: {s_.runs_per_hour_per_client} runs per hour from one network. "
                                 "Try again later.")
    if day >= s_.runs_per_day_global:
        raise HTTPException(429, "Daily run budget for this demo is used up. It resets in 24 hours.")
    if active >= s_.max_concurrent_runs * 3:
        raise HTTPException(429, "Too many runs in progress right now. Try again in a minute.")
