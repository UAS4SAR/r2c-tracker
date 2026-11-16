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

_waits: dict[str, list[float]] = {
    "hub": [],
    "submission": [],
    "submission_guard": [],
    "other": [],
}
_lags: list[float] = []
_guard = threading.Lock()
_installed = False
_MAX_SAMPLES = 200_000


def _classify_lock() -> str:
    """Tag the waiter from the nearest caller. The hub class is not imported here."""
    import traceback

    for frame in reversed(traceback.extract_stack(limit=16)):
        line = frame.line or ""
        if frame.name == "serialized_flight_submission":
            if "guard" in line:
                return "submission_guard"
            return "submission"
        if "self._lock" in line:
            return "hub"
    return "other"


class TimingLock(asyncio.Lock):
    async def acquire(self):
        started = time.perf_counter()
        acquired = await super().acquire()
        elapsed = time.perf_counter() - started
        kind = _classify_lock()
        with _guard:
            bucket = _waits[kind]
            if len(bucket) < _MAX_SAMPLES:
                bucket.append(elapsed)
        return acquired


def install_lock_timing() -> None:
    global _installed
    if _installed:
        return
    asyncio.Lock = TimingLock  # type: ignore[misc, assignment]
    _installed = True


def snapshot() -> dict:
    with _guard:
        return {
            "lock_wait_s": {
                kind: list(samples) for kind, samples in _waits.items()
            },
            "event_loop_lag_s": list(_lags),
        }


def _write(payload: dict) -> None:
    path = os.environ.get("R2C_LOADTEST_METRICS", "").strip()
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


async def start_lag_probe() -> asyncio.Task:
    async def probe():
        try:
            next_flush = time.monotonic() + 5.0
            while True:
                started = time.perf_counter()
                await asyncio.sleep(0.05)
                excess = time.perf_counter() - started - 0.05
                with _guard:
                    if len(_lags) < _MAX_SAMPLES:
                        _lags.append(max(0.0, excess))
                if time.monotonic() >= next_flush:
                    _write(snapshot())
                    next_flush = time.monotonic() + 5.0
        except asyncio.CancelledError:
            _write(snapshot())
            raise

    return asyncio.create_task(probe(), name="r2c-loadtest-lag")
