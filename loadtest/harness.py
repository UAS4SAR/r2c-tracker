"""Start a local tracker and run simulated tablet scenarios.

Never reads production credentials and never calls Cloud Run.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from loadtest.clients import Admission, Phase, Tablet, TabletSpec
from loadtest.metrics import latency_block

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


_CGROUP = Path("/sys/fs/cgroup/r2c-loadtest")


def _sudo_write(path: Path, text: str) -> None:
    subprocess.run(
        ["sudo", "tee", str(path)],
        input=text.encode(),
        stdout=subprocess.DEVNULL,
        check=True,
    )


def _enforce_envelope(pid: int) -> bool:
    """Hard 1 GiB memory cap and one CPU quota. Local cgroup only."""
    subprocess.run(["sudo", "mkdir", "-p", str(_CGROUP)], check=True)
    _sudo_write(_CGROUP / "memory.max", "1073741824\n")
    _sudo_write(_CGROUP / "memory.swap.max", "0\n")
    _sudo_write(_CGROUP / "cpu.max", "100000 100000\n")
    _sudo_write(_CGROUP / "cgroup.procs", f"{pid}\n")
    members = (_CGROUP / "cgroup.procs").read_text().split()
    return str(pid) in members


def _oom_kills() -> int:
    try:
        for line in (_CGROUP / "memory.events").read_text().splitlines():
            if line.startswith("oom_kill "):
                return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        return 0
    return 0


def _pg_bin() -> Optional[Path]:
    if os.environ.get("PG_BIN"):
        return Path(os.environ["PG_BIN"])
    executable = shutil.which("pg_config") or shutil.which("initdb")
    if not executable:
        return None
    path = Path(executable)
    return path.parent if path.name == "initdb" else Path(
        subprocess.check_output([str(path), "--bindir"], text=True).strip()
    )


class LocalDatabase:
    def __init__(self, directory: Path, backend: str):
        self.directory = directory
        self.backend = backend
        self.tenant_url = ""
        self.control_url = ""
        self._data: Optional[Path] = None

    def start(self) -> None:
        if self.backend == "sqlite":
            self.tenant_url = f"sqlite+aiosqlite:///{self.directory / 'tracker.db'}"
            self.control_url = f"sqlite+aiosqlite:///{self.directory / 'control.db'}"
            return
        pg_bin = _pg_bin()
        if pg_bin is None:
            raise RuntimeError("PostgreSQL server tools not found. Install them or pass --backend sqlite.")
        data = self.directory / "pgdata"
        self._data = data
        port = _free_port()
        subprocess.run(
            [str(pg_bin / "initdb"), "-D", str(data), "-U", "r2c_load", "-A", "trust", "--no-locale"],
            check=True, stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(self.directory / "postgres.log"),
             "-o", f"-h 127.0.0.1 -p {port} -k /tmp", "-w", "start"],
            check=True, stdout=subprocess.DEVNULL,
        )
        for name in ("r2c_load_tracker", "r2c_load_control"):
            subprocess.run(
                [str(pg_bin / "createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "r2c_load", name],
                check=True, stdout=subprocess.DEVNULL,
            )
        prefix = f"postgresql+asyncpg://r2c_load@127.0.0.1:{port}/"
        self.tenant_url = prefix + "r2c_load_tracker"
        self.control_url = prefix + "r2c_load_control"

    def stop(self) -> None:
        if self._data is None:
            return
        pg_bin = _pg_bin()
        if pg_bin is None:
            return
        subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(self._data), "-m", "immediate", "-w", "stop"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


class TrackerProcess:
    def __init__(self, database: LocalDatabase, directory: Path, instrument: bool, hello_delay_ms: int,
                 optimizations: bool = False):
        self.database = database
        self.directory = directory
        self.instrument = instrument
        self.hello_delay_ms = hello_delay_ms
        self.optimizations = optimizations
        self.port = _free_port()
        self.process: Optional[subprocess.Popen] = None
        self.log_path = directory / "server.log"
        self.metrics_path = directory / "instrument.json"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(ROOT),
            "DATABASE_URL": self.database.tenant_url,
            "CONTROL_PLANE_DATABASE_URL": self.database.control_url,
            "SECRET_KEY": "loadtest-local-session-key",  # pragma: allowlist secret
            "CONTROL_PLANE_SIGNING_KEY": "loadtest-local-signing-key-32chars",  # pragma: allowlist secret
            "CONTROL_PLANE_MODE": "live",
            "CONTROL_PLANE_PUBLIC_URL": "https://loadtest.r2c-tracker.example",
            "SESSION_COOKIE_HTTPS_ONLY": "false",
            "R2C_LOADTEST_STUB_WEATHER": "1",
            "R2C_LOADTEST_LOG_DIR": str(self.directory / "flightlogs"),
            "PORT": str(self.port),
        }
        if self.instrument:
            env["R2C_LOADTEST_INSTRUMENT"] = "1"
            env["R2C_LOADTEST_METRICS"] = str(self.metrics_path)
        if self.hello_delay_ms > 0:
            env["R2C_LOADTEST_DELAY_HELLO_ACK_MS"] = str(self.hello_delay_ms)
        if self.optimizations:
            env.update({
                "R2C_SKIP_EMPTY_DEVICE_NAME": "1",
                "R2C_CACHE_ORG_CONFIG": "1",
                "R2C_DB_POOL_SIZE": "20",
                "R2C_DB_MAX_OVERFLOW": "40",
                "R2C_SAFE_ZONE_UPSERT": "1",
                "R2C_CAP_UPLOAD_BODY": "1",
            })
        log = self.log_path.open("ab")
        self.launched_at = time.perf_counter()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "loadtest.serve_app"],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        log.close()
        self.memory_limited = _enforce_envelope(self.process.pid)
        if not self.memory_limited:
            raise RuntimeError("Tracker process was not placed in the 1 GiB cgroup.")
        self.oom_kills_at_start = _oom_kills()
        try:
            os.sched_setaffinity(self.process.pid, {0})
            self.cpu_pinned = True
        except (AttributeError, OSError, PermissionError):
            self.cpu_pinned = False
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Tracker exited early:\n" + self.tail())
            try:
                with urllib.request.urlopen(self.base_url + "/readyz", timeout=0.5) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("Tracker did not become ready:\n" + self.tail())

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def rss_mb(self) -> Optional[float]:
        if self.process is None:
            return None
        try:
            for line in Path(f"/proc/{self.process.pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        except OSError:
            return None
        return None

    def cpu_seconds(self) -> Optional[float]:
        if self.process is None:
            return None
        try:
            fields = Path(f"/proc/{self.process.pid}/stat").read_text().rsplit(")", 1)[1].split()
            ticks = int(fields[11]) + int(fields[12])
            return ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, IndexError, ValueError):
            return None

    def tail(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")[-4000:]
        except OSError:
            return ""

    def load_instrument(self) -> dict:
        try:
            return json.loads(self.metrics_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}


async def provision(control_url: str, organizations: list[dict]) -> dict[tuple[str, str], str]:
    from control_plane import ControlPlaneStore

    store = ControlPlaneStore(control_url)
    tokens: dict[tuple[str, str], str] = {}
    try:
        await store.init()
        for org in organizations:
            designator = org["designator"]
            created = await store.create_organization(
                legal_name=f"Load Test {designator}",
                designator=designator,
                admin_name="Load Test",
                admin_email=f"loadtest-{designator.lower()}@example.invalid",
                postal_address="Local load test",
                actor_id="loadtest",
                simulation=True,
            )
            owner = await store.activate_owner(
                designator,
                f"loadtest-{designator.lower()}@example.invalid",
                "loadtest-password",
            )
            campaign = await store.create_enrollment_campaign(
                organization_id=created.id,
                label="loadtest",
                created_by_user_id=owner.id,
                expires_in_hours=4,
                max_redemptions=min(500, org["tablets"]),
            )
            for index, tablet in enumerate(org["tablet_names"]):
                credential = await store.issue_device_credential(
                    campaign_id=campaign.id,
                    organization_id=created.id,
                    device_name=tablet,
                    platform=org["platforms"][index],
                    authorized_user_id=owner.id,
                )
                tokens[(designator, tablet)] = credential.token
    finally:
        await store.dispose()
    return tokens


def _orgs(count: int, hot: int, tablets_per_hot: int, profile: str) -> list[dict]:
    organizations = []
    for index in range(count):
        designator = f"L{index:03d}"
        if index < hot:
            names = [f"hot-{n}" for n in range(tablets_per_hot)]
            platforms = ["android" if n % 2 == 0 else "ios" for n in range(tablets_per_hot)]
        else:
            names = ["quiet-android", "quiet-ios"]
            platforms = ["android", "ios"]
        organizations.append({
            "designator": designator,
            "tablets": len(names),
            "tablet_names": names,
            "platforms": platforms,
            "hot": index < hot,
            "profile": "hot" if index < hot else profile,
        })
    return organizations


def _specs(organizations: list[dict], tokens: dict[tuple[str, str], str], zones: int, drones_per_zone: int,
           upload: bool) -> list[tuple[TabletSpec, bool]]:
    specs = []
    for org in organizations:
        designator = org["designator"]
        if org["hot"]:
            upload_assigned = False
            for zone in range(zones):
                remotes = tuple(
                    f"RID{designator}{zone}{drone}" for drone in range(drones_per_zone)
                )
                lat = 39.0 + zone * 0.05
                lng = -121.0
                for drone, remote in enumerate(remotes):
                    name = f"hot-{zone * drones_per_zone + drone}"
                    do_upload = upload and not upload_assigned
                    upload_assigned = upload_assigned or do_upload
                    # RID2Caltopo sends the device guid as zoneId. One tablet, one
                    # websocket. Geographic zones stay in lat/lng and peer_remote_ids.
                    specs.append((TabletSpec(
                        designator=designator,
                        token=tokens[(designator, name)],
                        platform=org["platforms"][zone * drones_per_zone + drone],
                        role="hot",
                        zone_id=name,
                        remote_id=remote,
                        peer_remote_ids=tuple(item for item in remotes if item != remote),
                        lat=lat + drone * 0.0001,
                        lng=lng + drone * 0.0001,
                    ), do_upload))
        else:
            for name, platform in zip(org["tablet_names"], org["platforms"]):
                specs.append((TabletSpec(
                    designator=designator,
                    token=tokens[(designator, name)],
                    platform=platform,
                    role=org["profile"],
                    zone_id=name,
                    remote_id=f"RID{designator}{name}",
                    peer_remote_ids=(),
                    lat=38.0,
                    lng=-120.0,
                ), False))
    return specs


@dataclass
class RunConfig:
    name: str
    orgs: int
    hot_orgs: int
    tablets_per_hot: int
    zones: int
    drones_per_zone: int
    profile: str
    duration_s: float
    concurrency: int
    reconnect: str
    restart: bool
    hello_delay_ms: int
    upload: bool
    backend: str
    instrument: bool
    cold_start: bool = False
    optimizations: bool = False


class ResourceSampler:
    def __init__(self, server: TrackerProcess):
        self.server = server
        self.peak_rss_mb = 0.0
        self.cpu_percent: Optional[float] = None
        self.ignore_exit = False
        self._samples: list[tuple[float, Optional[float]]] = []
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="r2c-rss", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            rss = self.server.rss_mb()
            cpu = self.server.cpu_seconds()
            if rss:
                self.peak_rss_mb = max(self.peak_rss_mb, rss)
            self._samples.append((time.perf_counter(), cpu))
            process = self.server.process
            if (
                not self.ignore_exit
                and process is not None
                and process.poll() is not None
            ):
                break
            time.sleep(1.0)

    def stop(self) -> None:
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=2)
        points = [(stamp, cpu) for stamp, cpu in self._samples if cpu is not None]
        if len(points) >= 2 and points[-1][0] > points[0][0] and points[-1][1] >= points[0][1]:
            self.cpu_percent = round(
                100.0 * (points[-1][1] - points[0][1]) / (points[-1][0] - points[0][0]),
                1,
            )

    def reset_cpu_window(self) -> None:
        self._samples.clear()


def _lock_blocks(instrument: dict) -> dict:
    waits = instrument.get("lock_wait_s") or {}
    if isinstance(waits, list):
        waits = {"hub": waits}
    return {
        kind: latency_block(waits.get(kind) or [])
        for kind in ("hub", "submission", "submission_guard", "other")
    }


def summarize(config: RunConfig, tablets: list[Tablet], server: TrackerProcess, elapsed_s: float,
              notes: list[str]) -> dict:
    stats = [tablet.stats for tablet in tablets]
    instrument = server.load_instrument() if config.instrument else {}
    lags = instrument.get("event_loop_lag_s") or []
    rss = getattr(server, "peak_rss_mb", None)
    if rss is None:
        rss = server.rss_mb()
    launch = [sample for item in stats for sample in item.launch_to_hello_s]
    return {
        "scenario": config.name,
        "backend": config.backend,
        "orgs": config.orgs,
        "hot_orgs": config.hot_orgs,
        "tablets_per_hot": config.tablets_per_hot,
        "profile": config.profile,
        "reconnect": config.reconnect,
        "duration_s": round(elapsed_s, 3),
        "concurrency_limit": config.concurrency,
        "cpu_pinned_to_one_core": getattr(server, "cpu_pinned", False),
        "memory_limit": "1GiB" if getattr(server, "memory_limited", False) else None,
        "oom": bool(getattr(server, "oom", False)),
        "cpu_percent_of_one_core": getattr(server, "cpu_percent", None),
        "server_rss_mb": None if rss is None else round(rss, 1),
        "launch_to_first_hello_s": None if not launch else round(min(launch), 3),
        "connects": sum(item.connects for item in stats),
        "connect_failures": sum(item.connect_failures for item in stats),
        "hello_acks": sum(item.hello_acks for item in stats),
        "parked": sum(item.parked for item in stats),
        "ack_timeouts": sum(item.ack_timeouts for item in stats),
        "http_429": sum(item.http_429 for item in stats),
        "http_errors": sum(item.http_errors for item in stats),
        "notam_statuses": sorted({code for item in stats for code in item.notam_status}),
        "hello_ack": latency_block([sample for item in stats for sample in item.hello_ack_s]),
        "heartbeat_ack": latency_block([sample for item in stats for sample in item.heartbeat_ack_s]),
        "upload": latency_block([sample for item in stats for sample in item.upload_s]),
        "reconnect_from_close": latency_block([sample for item in stats for sample in item.reconnect_from_close_s]),
        "reconnect_from_listen": latency_block([sample for item in stats for sample in item.reconnect_from_listen_s]),
        "queue_wait": latency_block(tablets[0].admission.queue_waits_s if tablets else []),
        "admission_rejected": tablets[0].admission.rejected if tablets else 0,
        "event_loop_lag": latency_block(lags) if config.instrument else None,
        "lock_wait": _lock_blocks(instrument) if config.instrument else None,
        "spans": _span_blocks(instrument) if config.instrument else None,
        "optimizations": config.optimizations,
        "notes": notes,
        "client_closes": _client_closes(tablets),
        "tablets": _tablet_rows(tablets),
    }


async def execute(config: RunConfig, directory: Path) -> dict:
    database = LocalDatabase(directory, config.backend)
    database.start()
    server = TrackerProcess(
        database, directory, config.instrument, config.hello_delay_ms, config.optimizations,
    )
    notes = [
        "Weather is stubbed. FAA credentials are absent, so NOTAM fetches fail closed and clients retry every 15 s.",
        "The tracker process is in cgroup r2c-loadtest: memory.max=1GiB, swap max=0, cpu.max=1.",
    ]
    if config.optimizations:
        notes.append(
            "Flags on: R2C_SKIP_EMPTY_DEVICE_NAME, R2C_CACHE_ORG_CONFIG, "
            "R2C_DB_POOL_SIZE=20, R2C_DB_MAX_OVERFLOW=40, R2C_SAFE_ZONE_UPSERT, R2C_CAP_UPLOAD_BODY."
        )
    try:
        organizations = _orgs(config.orgs, config.hot_orgs, config.tablets_per_hot, config.profile)
        tokens = await provision(database.control_url, organizations)
        pairs = _specs(
            organizations, tokens, config.zones, config.drones_per_zone, config.upload,
        )
        server.start()
        sampler = ResourceSampler(server)
        sampler.start()
        notes.append(
            "Server CPU was pinned to one core."
            if server.cpu_pinned
            else "Server CPU could not be pinned; the process ran on the host's scheduler."
        )
        quiet = max(0, config.orgs - config.hot_orgs)
        planned = config.hot_orgs * config.tablets_per_hot + quiet * 2
        if config.concurrency and planned > config.concurrency:
            notes.append(
                "Planned tablets exceed the concurrency cap, so some connects are expected to queue or return 429."
            )
        phase = Phase()
        phase.listen_at = time.perf_counter()
        if config.cold_start:
            phase.launch_at = server.launched_at
        admission = Admission(config.concurrency)
        tablets = [
            Tablet(spec, server.base_url, admission, phase, config.reconnect, upload)
            for spec, upload in pairs
        ]
        started = time.perf_counter()
        tasks = [asyncio.create_task(tablet.run()) for tablet in tablets]
        if config.restart:
            await _wait_for_acks(tablets, min(12.0, config.duration_s))
            phase.generation += 1
            sampler.ignore_exit = True
            server.stop()
            await asyncio.sleep(0.3)
            server.start()
            sampler.ignore_exit = False
            sampler.reset_cpu_window()
            phase.listen_at = time.perf_counter()
        remaining = config.duration_s - (time.perf_counter() - started)
        alive = True
        if remaining > 0:
            deadline = time.monotonic() + remaining
            while time.monotonic() < deadline:
                process = server.process
                if process is not None and process.poll() is not None:
                    alive = False
                    notes.append(f"Tracker exited during the run with code {process.returncode}.")
                    break
                await asyncio.sleep(min(1.0, deadline - time.monotonic()))
        phase.stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=20)
        sampler.stop()
        server.peak_rss_mb = sampler.peak_rss_mb or server.rss_mb()
        server.cpu_percent = sampler.cpu_percent
        oom_delta = _oom_kills() - server.oom_kills_at_start
        server.oom = oom_delta > 0 or not alive
        if server.oom:
            notes.append(f"OOM kill events during this run: {oom_delta}.")
        elapsed = time.perf_counter() - started
        result = summarize(config, tablets, server, elapsed, notes)
        result["open_sockets_planned"] = len(tablets)
        result["server_log"] = _server_log_summary(server.log_path)
        return result
    finally:
        server.stop()
        database.stop()


async def _wait_for_acks(tablets: list[Tablet], timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if tablets and all(tablet.stats.hello_acks for tablet in tablets):
            return
        await asyncio.sleep(0.1)


def config_for(preset: str, **overrides) -> RunConfig:
    presets = {
        "smoke": RunConfig("smoke", 1, 1, 1, 1, 1, "hot", 4.0, 0, "lockstep", False, 0, False, "sqlite", False),
        "A": RunConfig("A", 10, 0, 6, 3, 2, "idle", 36.0, 80, "lockstep", False, 0, False, "postgres", False),
        "B": RunConfig("B", 10, 3, 6, 3, 2, "idle", 30.0, 80, "lockstep", False, 0, True, "postgres", False),
        "C": RunConfig("C", 10, 3, 6, 3, 2, "idle", 20.0, 80, "lockstep", True, 0, False, "postgres", False),
        "C-android-ack": RunConfig(
            "C-android-ack", 3, 3, 6, 3, 2, "idle", 22.0, 80, "lockstep", False, 12000, False, "postgres", False,
        ),
    }
    if preset not in presets:
        raise SystemExit(f"Unknown scenario {preset}")
    config = replace(presets[preset])
    for key, value in overrides.items():
        if value is not None:
            setattr(config, key, value)
    if config.tablets_per_hot == 16:
        config.zones = 4
        config.drones_per_zone = 4
    elif config.tablets_per_hot == 6:
        config.zones = 3
        config.drones_per_zone = 2
    elif config.tablets_per_hot == 1:
        config.zones = 1
        config.drones_per_zone = 1
    return config


def markdown_report(results: list[dict]) -> str:
    lines = [
        "# Load-test baseline",
        "",
        "Local flags-off run of the harness in `loadtest/`. Weather was stubbed. "
        "The tracker was not pointed at Cloud Run. These numbers are a local measurement, "
        "not a capacity claim.",
        "",
        "| Run | Orgs | Hot tablets | Hello ack p50/p95/p99 ms | Heartbeat ack p50 ms | Upload p50 ms | Reconnect p50 ms | 429 | Parked | RSS MiB |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        hello = result["hello_ack"]
        hello_text = _triple(hello)
        lines.append(
            "| {scenario} {profile} {reconnect} | {orgs} | {tablets} | {hello} | {hb} | {up} | {re} | {rej} | {parked} | {rss} |".format(
                scenario=result["scenario"],
                profile=result["profile"],
                reconnect=result["reconnect"],
                orgs=result["orgs"],
                tablets=result["tablets_per_hot"] if result["hot_orgs"] else 0,
                hello=hello_text,
                hb=_one(result["heartbeat_ack"]),
                up=_one(result["upload"]),
                re=_one(result["reconnect_from_listen"]),
                rej=result["admission_rejected"],
                parked=result["parked"],
                rss=result["server_rss_mb"],
            )
        )
    lines.append("")
    for result in results:
        lines.append(f"## {result['scenario']} orgs={result['orgs']} reconnect={result['reconnect']}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps({key: result[key] for key in (
            "hello_acks", "connect_failures", "ack_timeouts", "http_errors",
            "notam_statuses", "cpu_pinned_to_one_core", "notes", "lock_wait", "event_loop_lag",
        ) if key in result}, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def _span_blocks(instrument: dict) -> dict:
    spans = instrument.get("spans_s") or {}
    return {
        name: latency_block(samples)
        for name, samples in sorted(spans.items())
        if isinstance(samples, list)
    }


def _client_closes(tablets: list[Tablet]) -> dict:
    reasons: dict[str, int] = {}
    ages: list[float] = []
    before = 0
    after = 0
    for tablet in tablets:
        before += tablet.stats.closes_before_hello
        after += tablet.stats.closes_after_hello
        ages.extend(tablet.stats.close_age_s)
        for key, count in tablet.stats.close_reasons.items():
            reasons[key] = reasons.get(key, 0) + count
    return {
        "before_hello": before,
        "after_hello": after,
        "age_s": latency_block(ages),
        "reasons": dict(sorted(reasons.items(), key=lambda item: -item[1])),
    }


def _tablet_rows(tablets: list[Tablet]) -> list[dict]:
    rows = []
    for tablet in tablets:
        spec = tablet.spec
        rows.append({
            "designator": spec.designator,
            "zone": spec.zone_id,
            "platform": spec.platform,
            "role": spec.role,
            "connects": tablet.stats.connects,
            "failures": tablet.stats.connect_failures,
            "hellos": tablet.stats.hello_acks,
        })
    return rows


def _server_log_summary(path: Path) -> dict:
    retired = 0
    retired_sockets = 0
    closed: dict[str, int] = {}
    errors: dict[str, int] = {}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        if "broadcast retired" in line:
            retired += 1
            parts = line.split()
            try:
                retired_sockets += int(parts[parts.index("retired") + 1])
            except (ValueError, IndexError):
                pass
        elif "r2c websocket closed:" in line:
            code = _between(line, "code=", " reason=")
            reason = _between(line, "reason=", " map=")
            key = f"code={code} reason={reason}"
            closed[key] = closed.get(key, 0) + 1
        elif "r2c websocket error:" in line:
            error = _between(line, "error=", " map=")
            errors[error[:160]] = errors.get(error[:160], 0) + 1
    return {
        "broadcast_retired_lines": retired,
        "broadcast_retired_sockets": retired_sockets,
        "closes": dict(sorted(closed.items(), key=lambda item: -item[1])[:12]),
        "errors": dict(sorted(errors.items(), key=lambda item: -item[1])[:12]),
    }


def _between(line: str, start_label: str, end_label: str) -> str:
    start = line.find(start_label)
    if start < 0:
        return ""
    start += len(start_label)
    end = line.find(end_label, start)
    if end < 0:
        return line[start:].strip()
    return line[start:end].strip()


def _one(block: dict) -> str:
    value = block.get("p50_ms")
    return "—" if value is None else str(value)


def _triple(block: dict) -> str:
    if not block.get("count"):
        return "—"
    return f"{block.get('p50_ms')} / {block.get('p95_ms')} / {block.get('p99_ms')}"


def run_config(config: RunConfig, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    return asyncio.run(execute(config, directory))
