"""Default-off timings for the local load harness.

Imported by main only when R2C_LOADTEST_INSTRUMENT=1. This module must not
import main.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

_waits: list[float] = []
_lags: list[float] = []
_guard = threading.Lock()
_installed = False


class TimingLock(asyncio.Lock):
    async def acquire(self):
        started = time.perf_counter()
        acquired = await super().acquire()
        elapsed = time.perf_counter() - started
        with _guard:
            if len(_waits) < 200_000:
                _waits.append(elapsed)
        return acquired


def install_lock_timing() -> None:
    global _installed
    if _installed:
        return
    asyncio.Lock = TimingLock  # type: ignore[misc, assignment]
    _installed = True


def snapshot() -> dict:
    with _guard:
        return {"lock_wait_s": list(_waits), "event_loop_lag_s": list(_lags)}


def _write(payload: dict) -> None:
    path = os.environ.get("R2C_LOADTEST_METRICS", "").strip()
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


async def start_lag_probe() -> asyncio.Task:
    async def probe():
        try:
            while True:
                started = time.perf_counter()
                await asyncio.sleep(0.05)
                excess = time.perf_counter() - started - 0.05
                with _guard:
                    if len(_lags) < 200_000:
                        _lags.append(max(0.0, excess))
        except asyncio.CancelledError:
            _write(snapshot())
            raise

    return asyncio.create_task(probe(), name="r2c-loadtest-lag")
