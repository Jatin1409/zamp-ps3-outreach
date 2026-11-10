"""A small durable job queue on the main database.

Jobs are rows in `jobs`. A worker loop (started with the web app, or standalone via `python -m app.worker`)
claims them with `FOR UPDATE SKIP LOCKED` on Postgres, so several workers can run safely. Concurrency inside one
worker is bounded by a semaphore. Runs interrupted by a restart are marked failed on boot - never left looking
like they are still running.
"""
from __future__ import annotations

import asyncio
import logging
import traceback

from sqlalchemy import or_, select, text, update

from .config import get_settings
from .db import session, utcnow
from .models import Job, Run, Stage
from .pipeline.context import emit

log = logging.getLogger("jobs")
_wakeup = asyncio.Event()
FIXTURES: dict[str, dict] = {}  # run_id -> fixture (tests / offline demo only)


async def enqueue(run_id: str, kind: str, payload: dict | None = None) -> int:
    async with session() as s:
        j = Job(run_id=run_id, kind=kind, payload=payload or {})
        s.add(j)
        await s.commit()
        jid = j.id
    _wakeup.set()
    return jid


async def claim() -> Job | None:
    s_ = get_settings()
    async with session() as s:
        if s_.is_postgres:
            row = (await s.execute(text(
                "UPDATE jobs SET status='running', started_at=now(), attempts=attempts+1 WHERE id = ("
                "SELECT id FROM jobs WHERE status='queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
                "RETURNING id"))).first()
            await s.commit()
            if not row:
                return None
            return await s.get(Job, row[0])
        job = (await s.execute(select(Job).where(Job.status == "queued").order_by(Job.id).limit(1))).scalars().first()
        if not job:
            return None
        res = await s.execute(update(Job).where(Job.id == job.id, Job.status == "queued")
                              .values(status="running", started_at=utcnow(), attempts=Job.attempts + 1))
        await s.commit()
        if res.rowcount != 1:
            return None
        await s.refresh(job)
        return job


async def execute(job: Job):
    from .pipeline.runner import fail_run, run_job

    try:
        await run_job(job.run_id, job.kind, job.payload or {}, fixture=FIXTURES.get(job.run_id))
        status, err = "done", None
    except Exception as e:  # noqa: BLE001 - any crash must fail the run visibly
        log.error("job %s crashed: %s", job.id, traceback.format_exc())
        status, err = "failed", f"{type(e).__name__}: {e}"
        await fail_run(job.run_id, f"Internal error: {err}")
    async with session() as s:
        await s.execute(update(Job).where(Job.id == job.id).values(status=status, finished_at=utcnow(), error=err))
        await s.commit()


async def poll_cancellations():
    """A Stop recorded in the database (e.g. by another web process) reaches the job running it here."""
    from .pipeline.runner import RUNNING

    if not RUNNING:
        return
    try:
        async with session() as s:
            ids = set((await s.execute(select(Run.id).where(Run.id.in_(list(RUNNING)),
                                                            Run.cancel_requested.is_(True)))).scalars())
    except Exception:  # noqa: BLE001
        return
    for rid in ids:
        ctx = RUNNING.get(rid)
        if ctx:
            ctx.interrupt("user")


async def recover_interrupted():
    """On boot: anything 'running' was interrupted by a restart."""
    async with session() as s:
        await s.execute(update(Job).where(Job.status == "running")
                        .values(status="failed", error="interrupted by server restart", finished_at=utcnow()))
        queued_runs = set((await s.execute(select(Job.run_id).where(Job.status == "queued"))).scalars())
        stuck = (await s.execute(select(Run).where(Run.status.in_(("running", "queued"))))).scalars().all()
        ids = []
        for r in stuck:
            if r.id in queued_runs:
                continue
            r.status, r.outcome, r.error = "failed", "failed", "Interrupted by a server restart - use Retry."
            ids.append(r.id)
        if ids:
            await s.execute(update(Stage).where(Stage.run_id.in_(ids), Stage.status == "running")
                            .values(status="failed", error="Interrupted by a server restart", finished_at=utcnow()))
        await s.commit()
    for rid in ids:
        await emit(rid, "run_status", {"status": "failed", "error": "interrupted"})


async def worker_loop(stop: asyncio.Event):
    sem = asyncio.Semaphore(get_settings().max_concurrent_runs)
    tasks: set[asyncio.Task] = set()
    log.info("worker started")
    while not stop.is_set():
        await sem.acquire()
        job = None
        try:
            job = await claim()
        except Exception:  # noqa: BLE001
            log.exception("claim failed")
        if job is None:
            sem.release()
            await poll_cancellations()
            _wakeup.clear()
            try:
                await asyncio.wait_for(_wakeup.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            continue

        async def _run(j=job):
            try:
                await execute(j)
            finally:
                sem.release()

        t = asyncio.create_task(_run())
        tasks.add(t)
        t.add_done_callback(tasks.discard)
    for t in tasks:
        t.cancel()


async def drain(timeout: float = 60.0):
    """Tests: process queued jobs until none remain."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        job = await claim()
        if job is None:
            async with session() as s:
                left = (await s.execute(select(Job).where(or_(Job.status == "queued", Job.status == "running"))
                                        )).scalars().first()
            if left is None:
                return
            await asyncio.sleep(0.05)
            continue
        await execute(job)
