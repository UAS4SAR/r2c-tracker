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
_spans: dict[str, list[float]] = {}
_guard = threading.Lock()
_installed = False
_MAX_SAMPLES = 200_000
_started = time.perf_counter()
_BURST_S = 30.0


class span:
    """Record wall time, including awaits inside the block."""

    def __init__(self, name: str):
        self.name = name
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, *_exc):
        elapsed = time.perf_counter() - self.started
        phase = "burst" if self.started - _started < _BURST_S else "steady"
        with _guard:
            for key in (self.name, f"{self.name}.{phase}"):
                bucket = _spans.setdefault(key, [])
                if len(bucket) < _MAX_SAMPLES:
                    bucket.append(elapsed)
        return False


def record(name: str, seconds: float) -> None:
    phase = "burst" if time.perf_counter() - _started < _BURST_S else "steady"
    with _guard:
        for key in (name, f"{name}.{phase}"):
            bucket = _spans.setdefault(key, [])
            if len(bucket) < _MAX_SAMPLES:
                bucket.append(seconds)


def install_pool_timing(async_engine) -> None:
    """Time QueuePool.connect, which is the wait for a free connection."""
    pool = async_engine.sync_engine.pool
    original = pool.connect

    def timed_connect():
        started = time.perf_counter()
        connection = original()
        record("pool.connect", time.perf_counter() - started)
        return connection

    pool.connect = timed_connect


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
            "spans_s": {name: list(samples) for name, samples in _spans.items()},
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
