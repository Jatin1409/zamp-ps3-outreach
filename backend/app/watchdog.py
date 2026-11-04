"""Diagnostics: logs the event-loop thread's stack whenever the loop is blocked > 1 s (LOOP_WATCHDOG=1)."""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger("watchdog")


def start(loop: asyncio.AbstractEventLoop, threshold: float = 1.0):
    loop_thread = threading.get_ident()
    state = {"beat": time.monotonic()}

    def beat():
        state["beat"] = time.monotonic()

    def run():
        reported = 0.0
        while True:
            loop.call_soon_threadsafe(beat)
            time.sleep(0.25)
            lag = time.monotonic() - state["beat"]
            if lag > threshold and time.monotonic() - reported > 2:
                frame = sys._current_frames().get(loop_thread)
                stack = "".join(traceback.format_stack(frame)[-12:]) if frame else "?"
                log.warning("event loop blocked %.1fs; loop thread is at:\n%s", lag, stack)
                reported = time.monotonic()

    threading.Thread(target=run, daemon=True, name="loop-watchdog").start()
