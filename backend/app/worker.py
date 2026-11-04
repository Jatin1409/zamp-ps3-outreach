"""Standalone worker: `python -m app.worker`. Use with WORKER_ENABLED=false on the web service to scale separately."""
import asyncio
import logging

from .db import create_all, init_engine
from .jobs import recover_interrupted, worker_loop
from .seed import ensure_seed

logging.basicConfig(level=logging.INFO)


async def main():
    init_engine()
    await create_all()
    await ensure_seed()
    await recover_interrupted()
    await worker_loop(asyncio.Event())


if __name__ == "__main__":
    asyncio.run(main())
