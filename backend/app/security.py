"""Access code + rate limits, so the public demo link can't be used to run up API bills."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import unicodedata

from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select

from .config import get_settings
from .db import session, utcnow
from .models import Run


_INVISIBLE = re.compile(r"[\s\u200b-\u200f\u2060\ufeff]")
_DASHES = re.compile(r"[\u2010-\u2015\u2212\ufe58\ufe63\uff0d_]")
_LOOKALIKE = str.maketrans({"i": "1", "l": "1", "o": "0"})


def normalize_code(code: str) -> str:
    """Forgiving comparison for a code people retype or paste from chat apps: ignore case, spaces and invisible
    characters, treat any dash as "-", and treat look-alikes (I / l / 1, O / 0) as the same character.
    (Reported: the correct code was rejected - it contains both "I" and "l", and phones capitalise the first letter.)"""
    c = unicodedata.normalize("NFKC", code or "")
    c = _DASHES.sub("-", _INVISIBLE.sub("", c)).casefold()
    return c.translate(_LOOKALIKE)


def _ok(code: str | None) -> bool:
    expected = normalize_code(get_settings().access_code)
    given = normalize_code(code or "")
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())


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
