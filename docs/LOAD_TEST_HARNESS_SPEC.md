# Local load-test harness spec

Specification only. Do not treat this document as a measured capacity number, and do not implement the P0/P1 code changes from a reading of it. The harness has to exist and record a baseline before any of those changes go live, including behind a flag.

Line numbers below are from `project/modularize-coordination` at `6d3052b` (Tracker v1.4.98). Re-check them if `main.py` moves.

Related:

- Soak process and the prerel host: [PRE_RELEASE_SOAK_PROCESS.md](PRE_RELEASE_SOAK_PROCESS.md)
- Hub extraction (behavior-preserving move, not a scale-out): [COORDINATION_HUB_EXTRACTION.md](COORDINATION_HUB_EXTRACTION.md)
- September 2026 source review: [MULTI_ORG_PREREL_REVIEW_2026-09-10.md](MULTI_ORG_PREREL_REVIEW_2026-09-10.md). Several of its line numbers and two of its findings (SQL echo, weather still on the event loop) are stale. Use this spec for the current hot spots.
- Temporary always-on during an incident, after a baseline exists: [CAMPAIGN_MODE_CHECKLIST.md](CAMPAIGN_MODE_CHECKLIST.md)
- Protocol rates the harness copies: [R2C_PROTOCOL.md](../R2C_PROTOCOL.md)

## Constraints this harness is for

The service stays one multi-tenant site. Do not stand up a stack per organization to make a rung pass.

Scale-to-zero stays the steady state for quiet organizations, at least for the first 10–20 orgs. An open WebSocket is a Cloud Run request, so a tablet that keeps heartbeating pins CPU for the whole process. Parking is the mechanism that lets quiet orgs scale to zero.

The realistic worst case is about three organizations running mutual-aid campaign searches at the same time, on that same single instance. The ladder past 20 orgs is characterization. It is not a promise to support 40, 80, or 160 hot tenants, and it is not a reason to raise `max-instances`.

`deploy.sh` pins `--max-instances 1` because `R2CCoordinationHub` keeps sockets, owners, and peer traffic in process memory. Two instances do not agree on live ownership. The harness always runs one Uvicorn process. A passing rung does not authorize a second instance. See [COORDINATION_HUB_EXTRACTION.md](COORDINATION_HUB_EXTRACTION.md) and the campaign checklist.

Review GCP billing before and after any live change. This harness is local and must not point at production databases, the production bucket, production secrets, or `api.open-meteo.com` / the FAA NOTAM API. Record the harness run; do not "confirm" it by loading `r2c-tracker.com`.

P0/P1 fixes land behind flags, one at a time, and only after the baseline in this spec is recorded with flags off. The baseline is the current code.

## What the harness is

A local driver that:

1. Starts one Tracker process shaped like the current Cloud Run service.
2. Simulates N organizations, for N in **10, 20, 40, 80, 160**.
3. Runs scenarios A, B, and C at each N. B has a 6-tablet primary and a 16-tablet upper bound. C-warm is the support-gate reconnect; C-cold is recorded and not scored.
4. Records the metrics below and scores them against the proposed thresholds.
5. Repeats a single rung with one flag on only after the flags-off baseline for that rung is stored.

No product file needs to change for the first baseline. Probes that read the event-loop lag, lock wait, and pool checkout live in the harness process (in-process mode) or are omitted (container mode). Do not add a public metrics route for this.

## Runtime the harness must mirror

Production, from `deploy_pilot.sh` wrapping `deploy.sh`, and from `docs/releases/v1.4.98.md`:

| Setting | Source | Value to mirror |
| --- | --- | --- |
| Service | `deploy_pilot.sh`, release note | `r2c-tracker-pilot` in `r2c-tracker-pilot`, `us-west1` |
| Min instances | `deploy.sh` `--min 0` | scale to zero when nothing is connected |
| Max instances | `deploy.sh` `--max-instances 1` | one process |
| CPU throttling | `deploy.sh` `--cpu-throttling` | CPU only while a request is active. An open `/ws/r2c` socket is a request, so heartbeats keep the CPU allocated. |
| CPU | `deploy.sh` does not pass `--cpu` | **TODO.** Mirror 1 vCPU until a live `services describe` says otherwise. Do not treat the container number as a production twin until that describe is filed. |
| Memory | `deploy_pilot.sh` `CLOUD_RUN_MEMORY=1Gi` | 1 GiB |
| Concurrency | `deploy.sh` does not pass `--concurrency` | **TODO.** Cloud Run's documented default when the flag is omitted is 80. Record the live value before comparing runs. Do not invent a tighter cap in the harness to make a rung pass. |
| Process model | `Dockerfile` `CMD` | one `uvicorn main:app`, no `--workers` |
| Flight logs | `deploy.sh` gen2 GCS volume, `BASE_LOG_DIRECTORY` | `/flightlogs-vol` |
| Databases | operational engine plus control plane | two PostgreSQL databases. SQLite is not an acceptable harness backend. |

Local container limits for the support-gate run:

```sh
docker run --rm \
  --cpus=1 \
  --memory=1g \
  --memory-swap=1g \
  -p 8080:8080 \
  -v "$HARNESS_LOG_DIR:/flightlogs-vol" \
  -e PORT=8080 \
  -e DATABASE_URL="$HARNESS_DATABASE_URL" \
  -e CONTROL_PLANE_DATABASE_URL="$HARNESS_CONTROL_PLANE_DATABASE_URL" \
  -e FLIGHTLOGS_STORAGE_REQUIRED=true \
  harness-r2c-tracker
```

`--cpus=1` matches the one vCPU the instance has while a request is active. **TODO:** confirm the live Cloud Run CPU and concurrency with `services describe` before treating this container as a production twin. `deploy.sh` omits both flags. Docker cannot freeze the event loop between requests the way Cloud Run CPU throttling does while the container stays up. Scenario A-park plus scenario C-cold is the local stand-in for scale-from-zero. Scenario C-warm restarts the process while clients are still connected, which is the heavier reconnect. Do not describe a docker run as a CPU-throttling reproduction.

Use PostgreSQL 16 for both databases (the compose file in the repo is a stub and is not the harness). Apply the app's own startup migrations by booting the app. Point weather and FAA at local stubs. Bind `/flightlogs-vol` to a local directory for the baseline. Treat those upload latencies as a lower bound: production writes go through Cloud Storage FUSE. A later pass may mount a non-production bucket with gcsfuse; that pass is not required to record the first baseline, and it must not be the production flight-log bucket.

Run two modes:

- **Container mode** (the support gate). One container, limits above. Client processes stay outside the container. This is the latency, error, memory, and restart record.
- **In-process mode** (lock wait, event-loop lag, pool checkout, submission-lock map size). The harness imports `main` after installing the probes below, still against the local Postgres, still one event loop, and still with `asyncio` default thread limits. Use the same client schedule. Do not quote in-process latency as the Cloud Run number.

## Hot spots (current code)

| Item | Where | What the code does now |
| --- | --- | --- |
| Upload body cap, live path | `main.py:6273-6306` `PUT /{designator}/upload` | `await request.body()` buffers the entire flight log with no size check. `MAX_ARCHIVE_UPLOAD_BYTES` (default 100 MiB, `main.py:212-222`) applies only to `read_upload_with_limit` (`main.py:225-241`), which the archive-import paths call (`main.py:10178`, `main.py:12693`). The live tablet upload does not. |
| Upload body cap, other path | `main.py:10793` | Recording download content allows up to 16 GiB. Out of the campaign-search model unless a hot org is also pulling video. |
| Weather call | `main.py:5220-5262`, `5451-5459`, `5581-5594`; `runtime_io.py:14-40` | `get_weather` is still synchronous `requests.get` to `api.open-meteo.com` with timeout `(3.05, 10)`. It already runs on `anyio.to_thread` through `run_bounded_sync`, behind `_weather_workers = CapacityLimiter(4)`. `create_flight_and_archive` still awaits that result before the flight row is inserted. A slow provider occupies one of four workers and holds the upload open. It does not, by itself, run on the event loop. |
| Archive write | `main.py:5452`, `5477-5498`, `6308-6310` | `_write_flight_log` does `os.makedirs` and `json.dump` on `/flightlogs-vol` (GCS FUSE in production) on `_archive_workers` (also 4). Same pattern for the geojson parse / numpy outlier filter on `_processing_workers` (`main.py:5453`, `5501-5504`). |
| Global coordination lock | `main.py:1507` `R2CCoordinationHub._lock` | One `asyncio.Lock` for every tenant. 52 `async with self._lock` sites on this class (`main.py:1545` through `4890`). `OrganizationStreamEventHub` has a separate lock (`main.py:1367-1410`) and is not the coordination hot path. Heartbeat scans `self._owners` for the whole process while holding the hub lock (`main.py:3656-3660`). `traffic_position` holds it across per-report distance math and recipient filtering (`main.py:3876-4026`). `connect` waits for the single `_load_task` before accept completes (`main.py:2307-2310`). |
| Heartbeat upserts | `main.py:3661-3684`, `4531-4563`, `4592-4631` | Every heartbeat, after the lock is released, commits a zone SELECT-then-UPDATE (`_upsert_zone_state`) and, for each drone that guid owns, its own `_upsert_owner_state` session (conditional UPDATE, maybe SELECT, maybe INSERT, commit). Zone *broadcasts* are throttled to `R2C_HEARTBEAT_ZONE_UPDATE_SEC` (default 60, `main.py:4263-4274`). The SQL is not throttled. |
| Sighting inserts (sibling of the upsert issue) | `main.py:3786-3838`, `4824-4840` | A non-owner `sighting` inserts one `r2c_recent_sighting` row and commits. An owner `sighting` updates the owner row and commits. Cleanup of stale rows is a separate 1000-row batch every `R2C_DB_CLEANUP_SEC` (default 60, `main.py:4431-4457`), not on the hot path. |
| Submission locks | `main.py:730-763`, used at `6295-6306` | Key is `(organization_id, RID-or-SAR)`. The map entry is deleted when the waiter count hits zero, so idle keys do not grow from successful uploads. The increment and the delete run **outside** `_flight_submission_locks_guard`, so two tasks can lose an update and either leak a key or briefly create a second lock for the same key. |
| Tenant engine | `main.py:940-942`, sessions at `1158-1162` | `create_async_engine(..., echo=False, pool_pre_ping=True, pool_recycle=300)`. `pool_size` and `max_overflow` are not set, so SQLAlchemy's defaults apply (5 and 10). The control-plane engine matches (`control_plane.py:1784-1789`). The September review's "echo=True" finding does not match this tree. |
| Extra DB connection | `main.py:1818-1824`, `1856+` | One dedicated PostgreSQL LISTEN connection for video notifications when the control-plane URL is Postgres. Count it in the connection budget. |
| WebSocket backpressure | `runtime_io.py:50-112` | `WebSocketWriter` serializes writes per socket, allows 16 pending messages, and closes with code 1013 after a 2 second send budget. Broadcasts fan out with `asyncio.gather` and do not hold the hub lock during the send (`main.py:4300-4306`). |
| Silence timeout | `main.py:668-671`, `13152-13191` | A socket with no client frame for `R2C_CLIENT_SILENCE_SEC` (default 60, and at least two heartbeat intervals) is closed with code 4000. The harness must heartbeat connected clients inside that window or the server will drop them. |
| Restart load | `main.py:1811-1816`, `2307-2310`, `4326-4342` | Startup loads every org's recent zones, owners, and confirmations in one query each, then every new `connect` awaits that task. A warm restart with the full live set queued behind one load is the heavier case. A cold start runs the same code with an empty or parked working set. |
| Browser "SSE" | `main.py:13046-13084`, `static/organization_streams_live.js:42` | `/{designator}/streams/events` accepts a socket, sends one message, and closes. It is not a long-lived stream. The streams page polls `GET /{designator}/streams/live-status` every 10 seconds while the tab is visible and a stream is advertised. Count HTTP poll failures here, not SSE drops. |
| FAA proxy | `faa_proxy.py:81-131`, `382` | `GET /faa/notams` (`main.py:6070-6112`). Cache TTL default 90 seconds, at most 8 concurrent upstream `requests.get` calls, 35 second timeout, run via `asyncio.to_thread`. |
| Map tiles | none on the tracker | The tracker does not proxy map or tile traffic. Tablets talk to CalTopo themselves and report `caltopoRttMs` on `hello` and `heartbeat`. Leaflet on HTML pages is a browser asset, not a coordination hot path. |

There is no clue-submission route in this repository. Ken confirmed on 2026-11-14 that clues are written only to CalTopo and never through Tracker. The harness does not invent a clue request. Operator "Save" on a drone is the websocket message `drone_confirmed` (`R2C_PROTOCOL.md`, handler `main.py:4103`). Flight logs are `PUT /{designator}/upload`.

## Rates the model is allowed to use

Pinned by this repo (use these; do not retune them to make a rung pass):

| Behavior | Value | Source |
| --- | --- | --- |
| Heartbeat period | 15 s | `R2C_HEARTBEAT_SEC` default, `main.py:666`; `R2C_PROTOCOL.md` |
| Owner lease | 45 s | `R2C_LEASE_SEC`, `main.py:667` |
| Idle park advisory | 30 s | `R2C_IDLE_PARK_SEC`, `main.py:674`, sent as `idleParkSec` on `hello_ack` (`main.py:3459-3462`). The `120` in the protocol example is an example payload, not the server default. |
| Zone-update broadcast throttle | 60 s per map unless forced | `R2C_HEARTBEAT_ZONE_UPDATE_SEC`, `main.py:673`, `4263-4274` |
| Non-owner sighting send | 1 per drone per 3 s | `R2C_PROTOCOL.md` ("currently to one update every three seconds by default") |
| Peer-traffic schedule | 1 s when the nearest other aircraft is within 5 pad-heights; 2 / 4 / 8 / 16 s as that ratio grows; 16 s when distance or pad is missing | `R2CCoordinationHub._shadow_traffic_interval_ms`, `main.py:2078-2090`. The server tells the sender; the harness must follow `traffic_schedule.shadowIntervalMs`, starting at 16 s until the first schedule arrives. |
| Peer-traffic fan-out | only sockets within 1 mile (1609.344 m) | `PEER_TRAFFIC_RADIUS_M`, `main.py:1504`, filter at `4018-4026` |
| Weather timeout | connect 3.05 s, read 10 s | `requests.get(..., timeout=(3.05, 10))`, `main.py:5243` |
| NOTAM cache | 90 s | `FAA_PROXY_CACHE_TTL_SEC`, `faa_proxy.py:81-86` |
| Streams page poll | 10 s | `activeRefreshMs`, `static/organization_streams_live.js:42` |
| Mutual-aid comment in the protocol | "10 or more zones and drones" | `R2C_PROTOCOL.md`. The harness population is Ken's 2026-11-14 shape below, not a reading of this sentence as 12 zones. |

Marked **PROPOSAL** (change only by editing this spec, not by a quiet harness tweak):

| Knob | Value | Why |
| --- | --- | --- |
| Quiet org, connected tablets | 2 | A truck tablet and a standby. Enough to heartbeat; not a search team. |
| `HOT_ZONES` | 3 on the support gate; allowed 3 or 4 | Ken, 2026-11-14. A hot org is a mutual-aid search of 3–4 zones. |
| `DRONES_PER_ZONE` | 2 on the support gate; allowed 2, 3, or 4 | Ken, 2026-11-14. Each zone has 2–4 drones. |
| Tablets per hot org | `HOT_ZONES × DRONES_PER_ZONE` | One tablet per drone. Primary is 3 × 2 = **6**. Upper bound is 4 × 4 = **16**. |
| Who sees a drone | the other tablets in the same zone | Same zone is one segment. `CROSS_ZONE_VISIBILITY` defaults off. |
| Who is inside 1 mile | the tablets in that drone's zone | Other zones of the same hot org are placed more than 1 mile away. |
| Owner sighting rate while tracking | **TODO.** Placeholder 1 / s / owned drone | Not pinned in this repo, and Ken has not confirmed it. Tag every result file `owner_sighting_rate: "TODO"`. Non-owner relays stay at 1 per 3 s. |
| Flight-log uploads | 1 / drone / hour, 2 MiB GeoJSON | One sortie per drone. Primary is 6 / hot org / hour; upper bound is 16. Size stays inside `MAX_FLIGHT_LOG_BYTES` (16 MiB). Sortie length is still a proposal. |
| `drone_confirmed` | 1 / drone / hour | One operator save per sortie. Primary 6 / hot org / hour; upper bound 16. |
| NOTAM polls | each connected hot tablet, every 90 s | Matches the cache TTL. No client poll interval is in this repo. Primary is 6 tablets; upper bound is 16. |
| Org-config fetch | once per `hello` | The server pushes `organization_config_changed`. No periodic poll is specified. |
| Browser on a hot org | one visible streams tab | 1 poll / 10 s of `live-status`. No thumbnail fetch unless the optional video variant is on. |
| `RECONNECT_DELAY` | `uniform-0-5` and `burst` | Client backoff is unknown and can be very random. Run both until RID2Caltopo's schedule is known. `uniform-0-5` draws each socket independently from 0 to 5 s. `burst` is delay 0 for every socket. |
| Identical foreign keys | every org reuses the same `mapId` and `remoteId` strings | The review asked for this collision case. Isolation is part of the pass. |

## Ladder

Run every scenario at N = 10, 20, 40, 80, 160.

Support gate: **N = 10 and N = 20**. Those are the first 10–20 orgs the site is supposed to hold, with at most three of them hot. A miss at 40, 80, or 160 is recorded and stops any claim that the single instance supports that many organizations. It is not fixed by raising `max-instances`.

Org identity: `org-000` … `org-{N-1}`, each with its own designator, organization id, and device credentials. Map ids and remote ids are the **same strings** in every org.

Steady window, scenarios A and B: 60 s warmup (covers the 60 s zone-update throttle), then 5 minutes measured. Scenario C: the reconnect, then 60 s of reconnect measurement, then 2 minutes of the parent scenario's steady traffic. The support-gate C run is the warm restart. Cold start is recorded beside it and is not the pass/fail row.

Correctness on every rung, not just the support gate: a socket may only receive coordination messages for its own organization id. A shared `mapId` or `remoteId` leaking into another org's `owner_assigned`, `zone_update`, `relay_sighting`, or `peer_traffic_position` is a fail.

## Scenario A — quiet fleet

Mostly idle organizations. No sightings, no traffic, no uploads, no NOTAMs, no browser polls.

**A2, heartbeat only (the load case).** Each of the N orgs keeps 2 zones connected for the whole window. Each zone sends `hello` once, then `heartbeat` every 15 s, and nothing else. At N = 160 that is 320 sockets and 320/15 ≈ 21.3 heartbeats/s, each doing a zone upsert.

**A-park (the scale-to-zero case).** Same population, but each zone sends `hello`, one `heartbeat`, then `idle` with `reason=no_active_drones`, then a normal close. The harness waits until `r2c_hub.connection_count()` is 0 (in-process) or the server has no `/ws/r2c` request (container logs). Hold the idle for 60 s and confirm the process is not accumulating sessions. Then stop the container. This is the local picture of "quiet orgs can scale to zero". Run A-park at N = 10 and N = 20 for the support gate, and at the higher rungs as characterization.

## Scenario B — three hot orgs, everyone else quiet

Exactly three orgs (`org-000`, `org-001`, `org-002`) run the hot profile. The other N−3 orgs run the A2 heartbeat profile on the same process. At N = 10 that is 3 hot + 7 quiet. There is no N at which more than three orgs are hot. Also run **B0** once, not on the ladder: the three hot orgs and zero quiet orgs, so the hot cost can be subtracted from the N = 10 B run.

Ken, 2026-11-14: a hot org is **3–4 zones, 2–4 drones per zone, one tablet per drone**, so **6–16 tablets and the same number of drones**. The old 12-zone / 4-aircraft sketch is retired.

**B primary (the support gate).** `HOT_ZONES=3`, `DRONES_PER_ZONE=2`. That is 6 tablets, each owning one drone, in three zones on one map. Three hot orgs are 18 sockets. With quiet orgs at N = 20 that is 18 + 34 = 52 sockets. At N = 160 it is 18 + 314 = 332 sockets.

**B upper bound (parameterized, required on N = 10 and N = 20).** `HOT_ZONES=4`, `DRONES_PER_ZONE=4`. That is 16 tablets and 16 drones per hot org, 48 hot sockets. At N = 20 with quiet orgs that is 48 + 34 = 82 sockets. The harness must accept any pair in the allowed ranges, and the result JSON must record the pair. A miss at 16 tablets is the top of the range Ken described. It does not erase a pass at 6 tablets, and it is not a reason to raise `max-instances`.

Same-zone tablets see that zone's drones. Other zones of the hot org are more than a mile away, so they do not relay those drones and they do not receive that zone's `peer_traffic_position`. Leave `CROSS_ZONE_VISIBILITY` off unless a later edit of this spec turns it on.

Per hot org, primary (6 tablets), for the whole measured window:

| Stream | Schedule | Server work |
| --- | --- | --- |
| 6 tablets | `hello`, then heartbeat / 15 s | zone upsert each heartbeat; that tablet's heartbeat extends the lease of the one drone it owns |
| 6 owner sightings | **TODO** placeholder 1 / s each | `_upsert_owner_state`. Retag this row when the owner rate is known. |
| 6 non-owner relays | each drone is seen by the other tablet in its zone: 6 / 3 s = 2 / s | `_record_sighting` insert |
| 6 `traffic_position` | follow `shadowIntervalMs`. Drones in a zone sit within one pad-height, so expect ~1 / s each once `padFt` is set | global lock + distance math + fan-out to the other tablet in the zone |
| `drone_confirmed` | 6 / hour, spread across the window | claim persist + broadcast |
| `PUT /{designator}/upload` | 6 / hour, 2 MiB | full-body buffer, weather worker, archive worker, submission lock |
| `GET /faa/notams` | 6 tablets / 90 s | mostly cache hits after the first fetch per grid cell; stub still has to take the cache lock |
| `GET /{designator}/api/v1/organization-config/current` | once per hello | control-plane read |
| `GET /{designator}/streams/live-status` | 1 browser / 10 s | control-plane read, no long-lived socket |

Upper bound (16 tablets) scales the same way: heartbeats 16/15 ≈ 1.07 / s, owner sightings 16 / s at the TODO placeholder, non-owner relays 16 drones × 3 other tablets / 3 s = 16 / s, traffic 16 / s with fan-out to the other 3 tablets in the zone, uploads and `drone_confirmed` 16 / hour, NOTAM polls 16 / 90 s.

Place the drones of one zone within one pad-height of each other and set `padFt`, so the server's own scheduler settles at 1000 ms inside the zone. Place the zones more than 1 mile apart.

**Functional checks inside B, each hot org, once per rung:**

- Two overlapping uploads of the same remote id in one org: the second waits on `serialized_flight_submission` and then returns 409 on the overlap, not a second flight row.
- The same remote id uploaded concurrently by two orgs: both proceed. They must not share a submission lock. (The key includes `organization_id`.)
- After the window, `len(_flight_submission_locks)` is 0. Growth means the prune lost the race or a waiter was stranded.

**Stall injection, support gate only (N = 10 and N = 20), after the steady B window:** point the weather stub at a 10 s response (inside the read timeout, not a hang) and start one upload in each hot org at the same instant. Quiet-org `heartbeat_ack` latency during those 10 s is the cross-tenant number. Repeat with the archive stub delaying the `/flightlogs-vol` write by 2 s if the harness has a slow-write stand-in; otherwise record "archive delay not injected" and do not claim FUSE behavior.

**Body-cap probe, once, not every rung:** one `PUT` of 100 MiB and one `PUT` of 120 MiB on a hot org. Record RSS delta, whether the request was rejected, and how long quiet-org heartbeats stalled. Today the live path has no cap, so a 120 MiB accept is a measurement, not a harness bug. A later flag that rejects above the cap is a separate run.

Optional, off by default: one hot org advertising a video stream and fetching `GET /r2c-thumbnail/{tablet_code}/{session_id}.jpg` (`main.py:10230`) on the 10 s poll. Keep it out of the support-gate totals so a search without video is the baseline.

## Scenario C — reconnect stampede

Ken, 2026-11-14: field cold-start timing is unknown and can be very random. A **warm restart** (a new revision, or an instance restart, while clients are already connected) is the load he expects to hurt. Warm restart is the primary scenario C. Cold start stays as a secondary variant. Both use the same delay parameter until the client schedule is known.

Population is scenario B's sockets at that N. Primary hot shape: 3 × 6 hot tablets plus (N−3) × 2 quiet tablets. At N = 20 that is 18 + 34 = 52 sockets. At N = 160 it is 18 + 314 = 332 sockets. The upper-bound shape on the support gate replaces 6 with 16 (48 hot tablets): 82 sockets at N = 20.

`RECONNECT_DELAY`, run as two separate result files:

- `uniform-0-5`. Each socket draws one delay uniformly from 0 to 5 seconds.
- `burst`. Every socket uses delay 0. This is the worst case, not a guess at the client.

The delay starts when the client observes the socket close. The client connects at the later of "process is listening" and "close time plus delay", then sends `hello` and resumes its A2 or B schedule. Record which distribution the file used.

**C-warm (primary).** This is a revision deploy or a process restart with the working set still live.

1. Run B until every connected socket has persisted at least one heartbeat.
2. Kill the process without an `idle` close and without a graceful drain. Container stop, or cancel the loop in-process. Clients see a drop while they still have work to send.
3. Start the process again.
4. Reconnect the same population under `RECONNECT_DELAY`.
5. `connect` awaits `_load_task` (`main.py:2307-2310`) while `_load_state` reads every org's recent zones, owners, and confirmations (`main.py:4326-4337`). On a warm restart that load is the live working set, and the clients are all trying to come back.

Run `uniform-0-5` and `burst` at every support-gate rung for the primary shape. Run both again at N = 10 and N = 20 for the 16-tablet upper bound.

**C-cold (secondary).** Scale-from-zero. Stop after A-park (no sockets left), start the process, then connect the B population under the same two delay distributions. The database may still hold the rows from the earlier B run; that is the cold process with leftover coordination rows, which is what scale-from-zero looks like after a search. Field tablets will not actually arrive on this schedule. Keep the files, and do not score them as the support gate until reconnect timing is known.

Record, for every C file: time from listen to each `hello_ack`, the delay each socket drew, sockets closed during the burst (codes 1008, 1013, 4000, and anything other than an intentional client close), and the first 2 minutes of steady traffic afterward.

A-park's single-tablet follow-on stays: after the idle, start the process and connect one quiet tablet. That is the ordinary scale-from-zero tax. Record it at N = 10. It is not the stampede.

## Metrics

Collect per rung, per scenario, and for B also split **hot orgs** vs **quiet orgs**.

| Metric | How |
| --- | --- |
| Latency p50 / p95 / p99 | `hello_ack`, `heartbeat_ack`, `owner_assigned`, `relay_sighting` delivery, `peer_traffic_position` delivery, `PUT /{designator}/upload`, `GET /faa/notams`, `GET .../live-status`, `GET .../organization-config/current`. Clock starts when the client finishes sending and stops when the matching response is complete. |
| Event-loop lag | In-process only. A probe task sleeps 50 ms and records `actual - 50 ms`. Report p50 / p95 / p99 of that excess. |
| Lock wait | In-process only. Probe wraps `asyncio.Lock.acquire` for the hub lock, the submission-lock guard, and each per-key submission lock. Report p50 / p95 / p99 wait, and the fraction of waits over 50 ms, tagged with which lock. |
| DB connections / pool | In-process: `main.engine.pool.status()` and the control-plane engine's `pool.status()` sampled 1 / s. Record checked out, overflow, and checkout wait if the pool exposes it. Container: Postgres `pg_stat_activity` grouped by database and state. Include the LISTEN connection. |
| Memory | Container: `docker stats` RSS max and RSS at the end of the 5 minutes minus RSS at the end of warmup. In-process: `VmRSS` from `/proc/self/status` on the same interval. Also sample `len(r2c_hub._connections)`, `len(r2c_hub._owners)`, and `len(main._flight_submission_locks)`. |
| Error rate | HTTP responses ≥ 500, unexpected 4xx, websocket closes other than the scenario's own idle close, and `WebSocketWriter` 1013 retires. Split by org and route. |
| Drop counts | 1013 retires, 4000 silence timeouts, failed `broadcast` recipient counts from the server log line `broadcast retired`, and failed `live-status` polls. There is no long-lived SSE stream to drop. |

Write one JSON file per (mode, N, scenario, and for B and C the hot shape and `RECONNECT_DELAY`) plus a one-page table. Store the git SHA, the flag set (empty on the baseline), `HOT_ZONES`, `DRONES_PER_ZONE`, `RECONNECT_DELAY`, and `owner_sighting_rate: "TODO"` until that rate is filled in.

## Proposed pass/fail thresholds

These are **proposals**. They are not measured, and they are not operator requirements Ken has signed. A miss on the support gate means "do not claim the capacity and do not turn on a fix flag in production until the baseline is understood". A miss on N ≥ 40 means "do not quote that rung". It does not mean the production service is out of its design.

Support gate is N = 10 and N = 20, container mode, flags off, unless the row says in-process.

| Check | Proposal |
| --- | --- |
| A2 `heartbeat_ack` | p50 < 50 ms, p95 < 200 ms, p99 < 500 ms |
| B quiet-org `heartbeat_ack`, including during the 10 s weather stall | p50 < 50 ms, p95 < 250 ms, p99 < 500 ms |
| B hot-org `heartbeat_ack` | p95 < 500 ms, p99 < 1000 ms |
| B hot-org `peer_traffic_position` delivery and `relay_sighting` delivery | p95 < 500 ms, p99 < 1000 ms |
| `PUT` upload, weather stub < 100 ms, 2 MiB, local disk | p95 < 3 s. This will not hold once weather or FUSE is slow; that case is judged by the quiet-org heartbeat row, not by the upload row. |
| `GET /faa/notams` cache hit | p95 < 100 ms |
| `GET /livez` during B | p99 < 100 ms, no 5xx |
| Event-loop lag, in-process, A2 | p99 excess < 50 ms |
| Event-loop lag, in-process, B steady | p99 excess < 100 ms |
| Event-loop lag during the 10 s weather stall | p99 excess < 100 ms. A miss here means the worker handoff is still stalling the loop, which is a stronger finding than "the upload is slow". |
| Hub lock wait, in-process, A2 | p99 < 20 ms |
| Hub lock wait, in-process, B | p99 < 100 ms. Quiet-org waits over 100 ms while only the hot orgs are inside the lock are the signal for per-org sharding. |
| Pool checkout | no timeout; checked out stays ≤ pool size + max overflow (defaults 5 + 10 on each engine, plus the one LISTEN connection on the control plane). Checkout wait p99 < 50 ms on the support gate. |
| Memory, container, B at N = 20 | RSS growth over the measured 5 minutes < 50 MiB, and peak RSS < 70% of 1 GiB |
| Submission-lock map | size 0 within 5 s after the last in-flight upload, at every rung |
| Error rate | HTTP 5xx < 0.1% of responses. Abnormal websocket closes = 0 on the support gate. 1013 retires = 0 unless the harness injected a stalled reader. |
| Cross-org message | 0 at every rung |
| A-park | zero coordination sockets after the idle closes, at N = 10 and N = 20 |
| C-warm, primary shape, both `uniform-0-5` and `burst`, `hello_ack` | p95 < 2 s, p99 < 5 s from the moment the process is listening. Zero 1013 during the burst. This is the scenario C support gate. |
| C-warm, 16-tablet upper bound, N = 10 and N = 20 | same numbers, filed as the upper bound. A miss here does not fail the 6-tablet gate. |
| C-cold, either delay | record only. Client timing is unknown. No pass/fail until that schedule exists. |
| C, single tablet after A-park | record only. No threshold until the first baseline exists. |

N = 40, 80, and 160 use the same table as a **recording grid**, not as a fail of the service. Write "above support gate" next to any miss so it cannot be quoted as a production SLO.

## How a result maps to a P0/P1 flag

Turn on at most one flag per comparison run, on the support-gate rungs, after the flags-off JSON is stored. None of these flags exist yet. Do not add them in the harness PR.

| If the baseline shows | Flag to consider | Code the flag would wrap | Already true, so do not rebuild it |
| --- | --- | --- | --- |
| 100 MiB or 120 MiB `PUT /{designator}/upload` moves RSS by a large fraction of the 1 GiB cap, or quiet-org heartbeats miss their p99 during that `PUT` | `R2C_UPLOAD_BODY_CAP_BYTES`, defaulting to the existing `MAX_ARCHIVE_UPLOAD_BYTES` (100 MiB) and applied to `request.body()` on the live upload, not only to archive import | `main.py:6273-6281` | The archive-import cap and `read_upload_with_limit` already exist (`main.py:212-241`, `10178`, `12693`). |
| Event-loop lag or quiet-org `heartbeat_ack` misses its target while the weather stub is slow, **or** upload p95 is dominated by the weather wait even when the loop is fine | `R2C_DEFER_FLIGHT_WEATHER`: commit the flight first, fill weather later, and keep the upload response from waiting on Open-Meteo | `create_flight_and_archive` await at `main.py:5593-5594` | The HTTP call is already off the event loop (`lookup_flight_weather`, `main.py:5456-5459`, `runtime_io.py:14-40`). A flag that only moves `requests.get` onto a thread would be a no-op. The limiter of 4 is still a capacity knob; do not raise it until the baseline shows workers queued while the loop and the quiet orgs are otherwise healthy. |
| Quiet-org hub-lock wait p99 > 100 ms in B while hot orgs are in `traffic_position` or the owner scan | `R2C_COORDINATION_LOCK_PER_ORG`: shard `self._lock` by organization id | `main.py:1507`, and every site that holds it across more than one org. The heartbeat scan at `3656-3660` and the traffic section at `3876-4026` are the first to re-check. | Broadcast already drops the lock before socket writes (`4300-4306`). Sharding does not make a second Cloud Run instance correct. `max-instances` stays 1. |
| Zone-upsert or owner-upsert commits dominate heartbeat_ack time, or pool checkout waits while heartbeat QPS is the bulk of the statements | `R2C_HEARTBEAT_UPSERT_BATCH`: coalesce zone and owner writes per org on a short interval instead of a commit per heartbeat and per owned drone | `main.py:3661-3684`, `4531-4563`, `4592-4631` | Zone *broadcasts* are already throttled to 60 s (`4263-4274`). Do not "fix" the broadcast throttle. The sighting insert (`4824-4840`) is the same class of write; include it in the batch flag only if its rate, not the heartbeat rate, is the pool consumer. |
| `_flight_submission_locks` is non-empty after uploads drain, or same-key uploads in one org fail to serialize | A bound-and-prune flag only if the baseline shows growth or lost serialization. First fix the counter so it updates inside `_flight_submission_locks_guard`. | `main.py:730-763` | Pruning on a zero waiter count is already implemented (`760-763`). The September review's "never removed" note is stale. Org is already part of the key (`748`). |
| SQL log volume is large, or checkout wait is high while checked-out count sits at the default 5 + 10 | `R2C_DB_POOL_SIZE` and `R2C_DB_MAX_OVERFLOW` (tenant and control plane separately). Echo stays off. | `main.py:940-942`, `control_plane.py:1784-1789` | `echo=False`, `pool_pre_ping=True`, and `pool_recycle=300` are already set on both engines. Do not schedule an "turn echo off" change against this tree. Budget the LISTEN connection (`main.py:1856`) inside the control-plane total. Cloud SQL's max connections are shared with migrations and any overlapping revision; the flag must not size the pool as if this process were alone on the server. |

Comparison rule: the flagged run has to improve the specific row that justified it and must not push any other support-gate row from pass to fail. One flag per run. Leave the flag off in production until that comparison is stored next to the baseline.

## In-process probes (harness only)

Install these before `import main`, in the harness virtualenv, not in the image that `deploy.sh` builds.

- Replace `asyncio.Lock` with a subclass that times `acquire`. The hub lock is created at `R2CCoordinationHub.__init__` during import (`r2c_hub = R2CCoordinationHub()` at `main.py:4894`), so the patch has to be in place first.
- Schedule a 50 ms lag probe on the loop the app uses.
- Sample `main.engine.pool.status()` and the control-plane pool once a second. Confirm the attribute names against the installed SQLAlchemy when the harness is written; this spec does not freeze a private pool API.
- Sample the submission-lock map and the hub connection count.

If a probe cannot be installed without editing `main.py`, skip that metric and say so in the JSON. Do not add a production endpoint to get the number.

Container mode still records client latency, docker RSS, Postgres activity, and close codes. Those are the support-gate numbers. In-process numbers explain them.

## Local safety

- Local Postgres, local filesystem, local weather stub, local FAA stub.
- Device tokens are harness-minted `r2c_dev_` credentials in the local control plane.
- Do not export `DATABASE_URL` or `CONTROL_PLANE_DATABASE_URL` from a developer shell that has used `deploy_pilot.sh`.
- Do not set `PLATFORM_EMAIL_*`, `GOOGLE_OAUTH_*`, or the production secret names.
- The weather stub must be the only host `get_weather` can reach. Give the process no route to `api.open-meteo.com` on the baseline, and a forced blackhole on the stall run, so a stub miss becomes an error instead of a silent live call.

## Order

1. Flags-off container baseline at N = 10 and N = 20 for A2, A-park, B primary, B upper bound, C-warm primary (both delay distributions), and C-warm upper bound (both delay distributions), plus B0. Record C-cold on those rungs without scoring it.
2. Flags-off in-process baseline for the same support-gate set, for lock, lag, and pool.
3. Characterization rungs, container, flags off, primary hot shape only. Stop early only if the process is OOM-killed; record that as the characterization result.
4. Then, and only then, one flag at a time on the support gate, aimed at whichever row actually missed.

Campaign mode (`min-instances=1`, CPU always allocated) is not a substitute for step 1 and is not a harness result. See [CAMPAIGN_MODE_CHECKLIST.md](CAMPAIGN_MODE_CHECKLIST.md).

## Decisions (Ken, 2026-11-14)

- A hot org is 3–4 zones, 2–4 drones per zone, one tablet per drone (about 6–16 tablets). Scenario B primary is 3 × 2 = 6. The parameterized upper bound is 4 × 4 = 16.
- Clues go only to CalTopo. Tracker has no clue workload.
- Warm restart with clients already connected is the primary reconnect stampede (scenario C-warm). Cold start is secondary. Reconnect delay stays parameterized as `uniform-0-5` and `burst` until the client schedule is known.

## TODO

1. **Owner sighting rate.** Not in this repo, and not yet confirmed. The harness uses 1 / s / owned drone as a placeholder and labels it `TODO` on every result. Non-owner relays stay at the documented 1 per 3 s. When Ken has a number, replace the placeholder in the scenario B table and re-run the support gate. Do not quietly retune earlier files.
2. **Live Cloud Run CPU and concurrency.** `deploy.sh` passes neither `--cpu` nor `--concurrency`. The harness mirrors 1 vCPU and leaves concurrency uncapped, which matches Cloud Run's documented default of 80 only if the live service was never changed. File `gcloud run services describe r2c-tracker-pilot --project=r2c-tracker-pilot --region=us-west1` before quoting a container run as a production twin. Memory is set to 1 GiB by `deploy_pilot.sh` and is not part of this TODO.
