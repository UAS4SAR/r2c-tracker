"""Latency summaries for one harness run."""

from __future__ import annotations

import math
from typing import Iterable, Optional


def percentile(values: Iterable[float], fraction: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def latency_block(samples_s: list[float]) -> dict:
    return {
        "count": len(samples_s),
        "p50_ms": _ms(percentile(samples_s, 0.50)),
        "p95_ms": _ms(percentile(samples_s, 0.95)),
        "p99_ms": _ms(percentile(samples_s, 0.99)),
    }


def _ms(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value * 1000.0, 3)
