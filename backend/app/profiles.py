"""Seller profiles: create, edit, duplicate, delete and switch between them.

The *selected* profile travels with each request (header `X-Seller-Profile`, set by the UI from the viewer's own
choice), so two reviewers can work in different profiles at the same time without affecting each other. A request
with no (or an unknown) profile id uses the default profile. Every run stores the id of the profile it was created
under plus a snapshot of the profile's content, so editing or deleting a profile never rewrites history.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select

from .db import session, utcnow
from .models import Run, SellerProfile, new_id
from .security import require_access
from .seed import LEDGERLINE, ensure_seed

router = APIRouter(prefix="/api", dependencies=[Depends(require_access)])
DEFAULT_ID = "default"
MAX_PROFILES = 25


def _clean_list(v: list[str]) -> list[str]:
    return [x.strip() for x in v if isinstance(x, str) and x.strip()]


class SellerIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)
    product: str = Field(min_length=10, max_length=2000)
    ideal_customer: str = Field(min_length=5, max_length=2000)
    problems_solved: list[str] = Field(max_length=12)
    proof_points: list[str] = Field(default_factory=list, max_length=12)
    tone: str = Field(default="Plain, direct and peer-to-peer.", max_length=500)
    sender_name: str = Field(min_length=1, max_length=120)
    fictional_notice: str | None = Field(default=None, max_length=300)

    @field_validator("company_name", "product", "ideal_customer", "tone", "sender_name", "fictional_notice")
    @classmethod
    def strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("problems_solved", "proof_points")
    @classmethod
    def lists(cls, v):
        v = _clean_list(v)
        if any(len(x) > 300 for x in v):
            raise ValueError("each item must be at most 300 characters")
        return v

    @field_validator("problems_solved")
    @classmethod
    def need_problems(cls, v):
        if not v:
            raise ValueError("add at least one problem the product solves (relevance scoring uses it)")
        return v


class ProfileCreate(BaseModel):
    data: SellerIn | None = None
    copy_from: str | None = None   # duplicate an existing profile instead


def profile_json(p: SellerProfile, run_count: int | None = None) -> dict:
    return {"id": p.id, "name": p.name, "data": p.data, "is_default": p.id == DEFAULT_ID,
            "created_at": p.created_at, "updated_at": p.updated_at, "run_count": run_count}


def runs_of_profile_clause(profile_id: str):
    """Runs made before multi-profile support have no profile id; they were all made with the default profile."""
    if profile_id == DEFAULT_ID:
        return or_(Run.seller_profile_id == DEFAULT_ID, Run.seller_profile_id.is_(None))
    return Run.seller_profile_id == profile_id


async def resolve_profile(request: Request) -> SellerProfile:
    await ensure_seed()
    pid = request.headers.get("x-seller-profile") or request.query_params.get("seller_profile")
    async with session() as s:
        row = await s.get(SellerProfile, pid) if pid else None
        if row is None:
            row = await s.get(SellerProfile, DEFAULT_ID)
        if row is None:  # default deleted earlier: oldest remaining
            row = (await s.execute(select(SellerProfile).order_by(SellerProfile.created_at))).scalars().first()
        return row


async def _name_taken(name: str, exclude: str | None = None) -> bool:
    async with session() as s:
        q = select(SellerProfile).where(func.lower(SellerProfile.name) == name.strip().lower())
        if exclude:
            q = q.where(SellerProfile.id != exclude)
        return (await s.execute(q)).scalars().first() is not None


@router.get("/seller-profiles")
async def list_profiles(request: Request):
    await ensure_seed()
    current = await resolve_profile(request)
    async with session() as s:
        rows = (await s.execute(select(SellerProfile).order_by(SellerProfile.created_at))).scalars().all()
        counts = {}
        for p in rows:
            counts[p.id] = (await s.execute(select(func.count()).select_from(Run)
                                            .where(runs_of_profile_clause(p.id)))).scalar() or 0
    return {"current_id": current.id if current else None,
            "items": [profile_json(p, counts[p.id]) for p in rows]}


@router.post("/seller-profiles")
async def create_profile(body: ProfileCreate):
    async with session() as s:
        n = (await s.execute(select(func.count()).select_from(SellerProfile))).scalar() or 0
    if n >= MAX_PROFILES:
        raise HTTPException(409, f"You can have at most {MAX_PROFILES} seller profiles.")
    if body.copy_from:
        async with session() as s:
            src = await s.get(SellerProfile, body.copy_from)
        if src is None:
            raise HTTPException(404, "The profile to duplicate no longer exists.")
        data = dict(src.data)
        base = f"{src.name} (copy)"
        name, i = base, 2
        while await _name_taken(name):
            name, i = f"{base} {i}", i + 1
        data["company_name"] = name
    elif body.data is not None:
        data = body.data.model_dump()
        if await _name_taken(data["company_name"]):
            raise HTTPException(409, f"A profile called “{data['company_name']}” already exists.")
    else:
        raise HTTPException(422, "Send the profile data, or the id of a profile to duplicate.")
    async with session() as s:
        row = SellerProfile(id=new_id(), name=data["company_name"], data=data, created_at=utcnow())
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return profile_json(row, 0)


@router.get("/seller-profiles/{pid}")
async def get_profile_by_id(pid: str):
    async with session() as s:
        row = await s.get(SellerProfile, pid)
    if row is None:
        raise HTTPException(404, "Seller profile not found.")
    return profile_json(row)


@router.put("/seller-profiles/{pid}")
async def update_profile(pid: str, body: SellerIn):
    if await _name_taken(body.company_name, exclude=pid):
        raise HTTPException(409, f"A profile called “{body.company_name}” already exists.")
    async with session() as s:
        row = await s.get(SellerProfile, pid)
        if row is None:
            raise HTTPException(404, "Seller profile not found.")
        row.data, row.name = body.model_dump(), body.company_name
        await s.commit()
        await s.refresh(row)
        return profile_json(row)


@router.delete("/seller-profiles/{pid}")
async def delete_profile(pid: str):
    async with session() as s:
        row = await s.get(SellerProfile, pid)
        if row is None:
            raise HTTPException(404, "Seller profile not found.")
        n = (await s.execute(select(func.count()).select_from(SellerProfile))).scalar() or 0
        if n <= 1:
            raise HTTPException(409, "You need at least one seller profile. Create another before deleting this one.")
        active = (await s.execute(select(func.count()).select_from(Run).where(
            runs_of_profile_clause(pid), Run.status.in_(("queued", "running"))))).scalar() or 0
        if active:
            raise HTTPException(409, "A run using this profile is in progress. Wait for it to finish or stop it.")
        runs = (await s.execute(select(func.count()).select_from(Run)
                                .where(runs_of_profile_clause(pid)))).scalar() or 0
        await s.delete(row)
        await s.commit()
    # Runs keep their own snapshot of the profile, so history stays readable after deletion.
    return {"ok": True, "deleted": pid, "runs_kept": runs}


@router.post("/seller-profiles/{pid}/reset")
async def reset_profile(pid: str):
    if pid != DEFAULT_ID:
        raise HTTPException(400, "Only the default profile can be reset to the Ledgerline example.")
    async with session() as s:
        row = await s.get(SellerProfile, pid)
        if row is None:
            raise HTTPException(404, "Seller profile not found.")
        row.data, row.name = dict(LEDGERLINE), LEDGERLINE["company_name"]
        await s.commit()
        await s.refresh(row)
        return profile_json(row)
