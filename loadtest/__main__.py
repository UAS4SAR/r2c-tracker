"""Run a local load scenario. See loadtest/README.md."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from loadtest.harness import ROOT, config_for, markdown_report, run_config


def _run(config, directory: Path) -> dict:
    print(f"running {config.name} orgs={config.orgs} hot={config.hot_orgs}x{config.tablets_per_hot} "
          f"reconnect={config.reconnect} backend={config.backend}", flush=True)
    result = run_config(config, directory)
    print(json.dumps({
        "scenario": result["scenario"],
        "hello_acks": result["hello_acks"],
        "hello_ack_p50_ms": result["hello_ack"]["p50_ms"],
        "heartbeat_ack_p50_ms": result["heartbeat_ack"]["p50_ms"],
        "upload_p50_ms": result["upload"]["p50_ms"],
        "reconnect_p50_ms": result["reconnect_from_listen"]["p50_ms"],
        "admission_rejected": result["admission_rejected"],
        "parked": result["parked"],
        "server_rss_mb": result["server_rss_mb"],
    }), flush=True)
    return result


def baseline(date: str, out: Path, backend: str, instrument: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    json_dir = out.parent / f"baseline-{date}"
    json_dir.mkdir(parents=True, exist_ok=True)
    results = []
    jobs = []
    for orgs in (10, 20, 40, 80, 160):
        jobs.append(config_for("A", orgs=orgs, backend=backend, instrument=instrument, name=f"A-{orgs}"))
    jobs.append(config_for("B", orgs=10, tablets_per_hot=6, backend=backend, instrument=instrument, name="B-6"))
    jobs.append(config_for(
        "C", orgs=10, tablets_per_hot=6, reconnect="lockstep",
        backend=backend, instrument=instrument, name="C-lockstep",
    ))
    jobs.append(config_for(
        "C", orgs=10, tablets_per_hot=6, reconnect="jitter",
        backend=backend, instrument=False, name="C-jitter",
    ))
    jobs.append(config_for(
        "C-android-ack", orgs=3, backend=backend, instrument=False, name="C-android-ack",
    ))
    for config in jobs:
        try:
            with tempfile.TemporaryDirectory(prefix=f"r2c-{config.name}-") as scratch:
                result = _run(config, Path(scratch))
        except Exception as exc:
            result = {
                "scenario": config.name,
                "profile": config.profile,
                "reconnect": config.reconnect,
                "orgs": config.orgs,
                "hot_orgs": config.hot_orgs,
                "tablets_per_hot": config.tablets_per_hot,
                "hello_ack": {"count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None},
                "heartbeat_ack": {"count": 0, "p50_ms": None},
                "upload": {"count": 0, "p50_ms": None},
                "reconnect_from_listen": {"count": 0, "p50_ms": None},
                "admission_rejected": None,
                "parked": None,
                "server_rss_mb": None,
                "hello_acks": 0,
                "connect_failures": None,
                "ack_timeouts": None,
                "http_errors": None,
                "notam_statuses": [],
                "cpu_pinned_to_one_core": None,
                "notes": [f"Run failed: {exc}"],
                "lock_wait": None,
                "event_loop_lag": None,
            }
            print(f"FAILED {config.name}: {exc}", flush=True)
        results.append(result)
        (json_dir / f"{config.name}.json").write_text(json.dumps(result, indent=2) + "\n")
    body = markdown_report(results)
    body = f"Date: {date}. Backend: {backend}. Instrumentation: {instrument}.\n\n" + body
    out.write_text(body)
    print(f"wrote {out}", flush=True)


def full(date: str, backend: str) -> None:
    """Spec steady window: 60 s warmup plus 5 measured minutes."""
    out_dir = ROOT / "docs" / "load-test-results" / f"full-{date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    steady = 360.0
    jobs = []
    for orgs in (10, 20, 40, 80, 160):
        jobs.append(config_for(
            "A", orgs=orgs, duration_s=steady, backend=backend, instrument=True, name=f"A-{orgs}",
        ))
    jobs.append(config_for(
        "B", orgs=10, tablets_per_hot=6, duration_s=steady, backend=backend,
        instrument=True, name="B-6",
    ))
    jobs.append(config_for(
        "B", orgs=10, tablets_per_hot=16, duration_s=steady, backend=backend,
        instrument=True, name="B-16",
    ))
    jobs.append(config_for(
        "C", orgs=10, tablets_per_hot=16, reconnect="lockstep", duration_s=steady,
        backend=backend, instrument=True, name="C-lockstep-16",
    ))
    jobs.append(config_for(
        "C", orgs=10, tablets_per_hot=16, reconnect="jitter", duration_s=steady,
        backend=backend, instrument=True, name="C-jitter-16",
    ))
    jobs.append(config_for(
        "C-android-ack", orgs=10, tablets_per_hot=16, duration_s=300.0,
        backend=backend, instrument=True, name="C-android-ack-16",
    ))
    for trial in (1, 2, 3):
        jobs.append(config_for(
            "B", orgs=10, tablets_per_hot=16, duration_s=60.0, backend=backend,
            instrument=True, cold_start=True, name=f"cold-{trial}",
        ))
    for config in jobs:
        target = out_dir / f"{config.name}.json"
        if target.exists():
            print(f"skip existing {target.name}", flush=True)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix=f"r2c-{config.name}-") as scratch:
                result = _run(config, Path(scratch))
        except Exception as exc:
            result = {"scenario": config.name, "error": str(exc), "oom": True}
            print(f"FAILED {config.name}: {exc}", flush=True)
        target.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {target}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local r2c-tracker load harness")
    parser.add_argument("scenario", choices=["smoke", "A", "B", "C", "C-android-ack", "baseline", "full"])
    parser.add_argument("--orgs", type=int)
    parser.add_argument("--tablets", type=int, dest="tablets_per_hot")
    parser.add_argument("--reconnect", choices=["lockstep", "jitter"])
    parser.add_argument("--backend", choices=["postgres", "sqlite"])
    parser.add_argument("--duration", type=float, dest="duration_s")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--instrument", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--date", default="2026-11-16")
    args = parser.parse_args()
    if args.scenario == "full":
        full(args.date, args.backend or "postgres")
        return
    if args.scenario == "baseline":
        backend = args.backend or "postgres"
        out = args.out or (ROOT / "docs" / "load-test-results" / f"baseline-{args.date}.md")
        baseline(args.date, out, backend, args.instrument)
        return
    overrides = {
        "orgs": args.orgs,
        "tablets_per_hot": args.tablets_per_hot,
        "reconnect": args.reconnect,
        "backend": args.backend,
        "duration_s": args.duration_s,
        "concurrency": args.concurrency,
        "instrument": True if args.instrument else None,
    }
    config = config_for(args.scenario, **overrides)
    if args.scenario == "smoke" and args.backend is None:
        config.backend = "sqlite"
    directory = args.out or Path(tempfile.mkdtemp(prefix="r2c-loadtest-"))
    result = _run(config, directory)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if result["hello_acks"] < 1 and not result["ack_timeouts"]:
        raise SystemExit("no hello_ack received")


if __name__ == "__main__":
    main()
