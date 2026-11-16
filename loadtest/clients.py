"""Simulated RID2Caltopo 2.5.8 tablets. Local harness only."""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
import websockets

BACKOFF_S = (2.0, 4.0, 8.0, 10.0)


@dataclass
class TabletSpec:
    designator: str
    token: str
    platform: str  # android | ios
    role: str  # hot | idle | map
    zone_id: str
    remote_id: str
    peer_remote_ids: tuple[str, ...]
    lat: float
    lng: float


@dataclass
class TabletStats:
    connects: int = 0
    connect_failures: int = 0
    hello_acks: int = 0
    parked: int = 0
    ack_timeouts: int = 0
    http_429: int = 0
    http_errors: int = 0
    queue_waits_s: list[float] = field(default_factory=list)
    hello_ack_s: list[float] = field(default_factory=list)
    heartbeat_ack_s: list[float] = field(default_factory=list)
    upload_s: list[float] = field(default_factory=list)
    reconnect_from_close_s: list[float] = field(default_factory=list)
    reconnect_from_listen_s: list[float] = field(default_factory=list)
    launch_to_hello_s: list[float] = field(default_factory=list)
    notam_status: list[int] = field(default_factory=list)


class Admission:
    """Local stand-in for Cloud Run's single-instance concurrency cap."""

    def __init__(self, limit: int, queue_s: float = 2.0):
        self.limit = limit
        self.queue_s = queue_s
        self._sem = asyncio.Semaphore(limit) if limit > 0 else None
        self.rejected = 0
        self.admitted = 0
        self.queue_waits_s: list[float] = []

    async def acquire(self) -> bool:
        if self._sem is None:
            self.admitted += 1
            return True
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._sem.acquire(), self.queue_s)
        except asyncio.TimeoutError:
            waited = time.perf_counter() - started
            self.queue_waits_s.append(waited)
            self.rejected += 1
            return False
        waited = time.perf_counter() - started
        if waited > 0.001:
            self.queue_waits_s.append(waited)
        self.admitted += 1
        return True

    def release(self) -> None:
        if self._sem is not None:
            self._sem.release()


class Phase:
    def __init__(self):
        self.generation = 0
        self.listen_at: Optional[float] = None
        self.launch_at: Optional[float] = None
        self.stop = asyncio.Event()


class Tablet:
    def __init__(self, spec: TabletSpec, base_http: str, admission: Admission, phase: Phase,
                 reconnect: str, upload_once: bool):
        self.spec = spec
        self.base_http = base_http.rstrip("/")
        self.admission = admission
        self.phase = phase
        self.reconnect = reconnect
        self.upload_once = upload_once
        self.stats = TabletStats()
        self._step = 0
        self._uploaded = False
        self._held = False

    def _ws_url(self) -> str:
        return self.base_http.replace("http://", "ws://").replace("https://", "wss://") + (
            f"/{self.spec.designator.lower()}/ws/r2c"
        )

    def _next_delay(self) -> float:
        if self.reconnect == "jitter":
            return random.uniform(0.0, 5.0)
        delay = BACKOFF_S[min(self._step, len(BACKOFF_S) - 1)]
        self._step += 1
        return delay

    async def run(self) -> None:
        first = True
        while not self.phase.stop.is_set():
            if not first:
                delay = self._next_delay()
                try:
                    await asyncio.wait_for(self.phase.stop.wait(), delay)
                    return
                except asyncio.TimeoutError:
                    pass
            first = False
            if self.phase.stop.is_set():
                return
            if not await self.admission.acquire():
                self.stats.http_429 += 1
                self.stats.connect_failures += 1
                if self.reconnect != "jitter":
                    # A refused admission is a failed attempt, so the ladder advances.
                    pass
                continue
            self._held = True
            closed_at = None
            try:
                outcome = await self._one_connection()
                closed_at = time.perf_counter()
                if outcome == "parked":
                    self.stats.parked += 1
                    return
                if outcome == "ack-timeout" and self.spec.platform == "android":
                    # 2.5.8 Android: 0 delay and the backoff does not grow.
                    self.stats.ack_timeouts += 1
                    first = True
                    continue
                if outcome == "ack-timeout":
                    self.stats.ack_timeouts += 1
            except Exception:
                self.stats.connect_failures += 1
                closed_at = time.perf_counter()
            finally:
                if self._held:
                    self.admission.release()
                    self._held = False
            if closed_at is not None and self.stats.hello_acks:
                # Recorded when the next hello_ack arrives.
                self._closed_at = closed_at

    async def _one_connection(self) -> str:
        spec = self.spec
        ping = 20 if spec.platform == "android" else None
        async with websockets.connect(
            self._ws_url(),
            additional_headers={"X-SAR-Token": spec.token},
            proxy=None,
            ping_interval=ping,
            ping_timeout=20 if ping else None,
            open_timeout=10,
            close_timeout=2,
            reconnect_delays=lambda: iter(()),
            max_size=2**20,
        ) as ws:
            self.stats.connects += 1
            opened = time.perf_counter()
            generation = self.phase.generation
            if spec.platform == "android":
                self._step = 0
            await ws.send(json.dumps({
                "type": "hello",
                "mapId": "MAP1",
                "zoneId": spec.zone_id,
                "guid": spec.zone_id,
                "name": spec.zone_id,
                "lat": spec.lat,
                "lng": spec.lng,
                "appPlatform": "ios" if spec.platform == "ios" else "android",
            }))
            hello_sent = time.perf_counter()
            if spec.platform == "android":
                await self._send_heartbeat(ws, 1)
                heartbeat_sent = {1: time.perf_counter()}
                next_heartbeat = opened + (45.0 if spec.role == "hot" else 15.0)
                seq = 2
                pending = True
            else:
                heartbeat_sent = {}
                next_heartbeat = opened + 15.0
                seq = 1
                pending = False
            hello_ok = False
            ack_deadline = time.monotonic() + 10.0
            next_owner = time.monotonic() + 0.2
            next_relay = time.monotonic() + 3.0
            next_traffic = time.monotonic() + 0.2
            next_notam = time.monotonic() + 0.5
            park_at = time.monotonic() + 30.0 if spec.role == "idle" else None
            traffic_seq = 0
            drift = 0.0
            while not self.phase.stop.is_set():
                timeout = 0.05
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout)
                except asyncio.TimeoutError:
                    raw = None
                now_m = time.monotonic()
                now_p = time.perf_counter()
                if raw:
                    message = json.loads(raw)
                    kind = message.get("type")
                    if kind == "hello_ack" and not hello_ok:
                        hello_ok = True
                        self.stats.hello_acks += 1
                        self.stats.hello_ack_s.append(now_p - hello_sent)
                        if spec.platform == "ios":
                            self._step = 0
                        if self.phase.launch_at and not self.stats.launch_to_hello_s:
                            self.stats.launch_to_hello_s.append(now_p - self.phase.launch_at)
                        if generation > 0 and self.phase.listen_at:
                            self.stats.reconnect_from_listen_s.append(now_p - self.phase.listen_at)
                        closed_at = getattr(self, "_closed_at", None)
                        if generation > 0 and closed_at is not None:
                            self.stats.reconnect_from_close_s.append(now_p - closed_at)
                            self._closed_at = None
                    elif kind == "heartbeat_ack":
                        pending = False
                        sent = heartbeat_sent.pop(message.get("clientSeq"), None)
                        if sent is not None:
                            self.stats.heartbeat_ack_s.append(now_p - sent)
                if not hello_ok and now_m >= ack_deadline:
                    await ws.close()
                    return "ack-timeout"
                if hello_ok and park_at is not None and now_m >= park_at:
                    await ws.send(json.dumps({"type": "idle", "reason": "no_active_drones"}))
                    await ws.close()
                    return "parked"
                if hello_ok and now_m >= _to_mono(next_heartbeat, opened, now_m, now_p):
                    if not (spec.platform == "android" and pending):
                        await self._send_heartbeat(ws, seq)
                        heartbeat_sent[seq] = time.perf_counter()
                        pending = spec.platform == "android"
                        seq += 1
                        interval = 45.0 if spec.role == "hot" and spec.platform == "android" else 15.0
                        next_heartbeat = time.perf_counter() + interval
                if hello_ok and spec.role == "hot" and now_m >= next_owner:
                    drift += 0.00003
                    await ws.send(json.dumps(self._sighting(spec.remote_id, drift)))
                    next_owner = now_m + 1.0
                if hello_ok and spec.role == "hot" and spec.peer_remote_ids and now_m >= next_relay:
                    await ws.send(json.dumps(self._sighting(spec.peer_remote_ids[0], drift)))
                    next_relay = now_m + 3.0
                if hello_ok and spec.role == "hot" and now_m >= next_traffic:
                    traffic_seq += 1
                    await ws.send(json.dumps(self._traffic(traffic_seq, drift)))
                    next_traffic = now_m + 1.0
                if hello_ok and spec.role != "idle" and now_m >= next_notam:
                    await self._notam()
                    next_notam = now_m + (15.0 if self.stats.notam_status and self.stats.notam_status[-1] >= 400 else 1800.0)
                if hello_ok and self.upload_once and spec.role == "hot" and not self._uploaded:
                    self._uploaded = True
                    await self._upload()
        return "closed"

    async def _send_heartbeat(self, ws, seq: int) -> None:
        await ws.send(json.dumps({
            "type": "heartbeat",
            "seq": seq,
            "lat": self.spec.lat,
            "lng": self.spec.lng,
        }))

    def _sighting(self, remote_id: str, drift: float) -> dict:
        now_ms = int(time.time() * 1000)
        return {
            "type": "sighting",
            "mapId": "MAP1",
            "remoteId": remote_id,
            "zoneId": self.spec.zone_id,
            "guid": self.spec.zone_id,
            "droneTs": now_ms,
            "lat": self.spec.lat + drift,
            "lng": self.spec.lng + drift,
            "altM": 40.0,
        }

    def _traffic(self, seq: int, drift: float) -> dict:
        now_ms = int(time.time() * 1000)
        return {
            "type": "traffic_position",
            "remoteId": self.spec.remote_id,
            "source": "rid",
            "sourceEpoch": f"{self.spec.zone_id}-epoch",
            "seq": seq,
            "sampleTs": now_ms,
            "lat": self.spec.lat + drift,
            "lng": self.spec.lng + drift,
            "padFt": 100.0,
        }

    async def _notam(self) -> None:
        if not await self.admission.acquire():
            self.stats.http_429 += 1
            self.stats.notam_status.append(429)
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self.base_http}/faa/notams",
                    params={"latitude": self.spec.lat, "longitude": self.spec.lng, "radius": 10},
                    headers={"X-SAR-Token": self.spec.token},
                )
            self.stats.notam_status.append(response.status_code)
            if response.status_code >= 500:
                self.stats.http_errors += 1
            if response.status_code == 429:
                self.stats.http_429 += 1
        except httpx.HTTPError:
            self.stats.http_errors += 1
            self.stats.notam_status.append(599)
        finally:
            self.admission.release()

    async def _upload(self) -> None:
        if not await self.admission.acquire():
            self.stats.http_429 += 1
            return
        stamp = int(time.time() * 1000)
        payload = {"features": [{"type": "Feature", "properties": {
            "title": f"{self.spec.remote_id}_loadtest",
            "r2c_prop": {
                "rid": self.spec.remote_id, "mid": "1SAR7", "model": "loadtest",
                "incident": "Load test", "op_period": "1", "map_id": "MAP1",
                "distance_mi": 1.0,
            },
        }, "geometry": {"type": "LineString", "coordinates": [
            [self.spec.lng, self.spec.lat, 20, stamp],
            [self.spec.lng + 0.002, self.spec.lat + 0.002, 20, stamp + 120000],
        ]}}]}
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{self.base_http}/{self.spec.designator.lower()}/upload",
                    headers={"X-SAR-Token": self.spec.token},
                    json=payload,
                )
            if response.status_code == 200:
                self.stats.upload_s.append(time.perf_counter() - started)
            elif response.status_code == 429:
                self.stats.http_429 += 1
            else:
                self.stats.http_errors += 1
        except httpx.HTTPError:
            self.stats.http_errors += 1
        finally:
            self.admission.release()


def _to_mono(perf_deadline: float, opened_perf: float, now_m: float, now_p: float) -> float:
    """Compare a perf_counter deadline with monotonic 'now' by converting."""
    return now_m + (perf_deadline - now_p)
