# Local load-test harness spec

Specification only. Do not treat this document as a measured capacity number, and do not implement the P0/P1 code changes from a reading of it. The harness has to exist and record a baseline before any of those changes go live, including behind a flag.

Line numbers below are from `project/modularize-coordination` at `6d3052b` (Tracker v1.4.98). Re-check them if `main.py` moves.

Related:

- Soak process and the prerel host: [PRE_RELEASE_SOAK_PROCESS.md](PRE_RELEASE_SOAK_PROCESS.md)
- Hub extraction (behavior-preserving move, not a scale-out): [COORDINATION_HUB_EXTRACTION.md](COORDINATION_HUB_EXTRACTION.md)
- September 2026 source review: [MULTI_ORG_PREREL_REVIEW_2026-09-10.md](MULTI_ORG_PREREL_REVIEW_2026-09-10.md). Several of its line numbers and two of its findings (SQL echo, weather still on the event loop) are stale. Use this spec for the current hot spots.
- Temporary always-on during an incident, after a baseline exists: [CAMPAIGN_MODE_CHECKLIST.md](CAMPAIGN_MODE_CHECKLIST.md)
- Protocol rates the harness copies: [R2C_PROTOCOL.md](../R2C_PROTOCOL.md)
- Tablet behavior below is from RID2Caltopo 2.5.8 (`origin/main` `0eac4703`), as read by the R2C team. It is not in this repository.

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
3. Runs scenarios A, B, and C at each N. B has a 6-tablet primary and a 16-tablet upper bound. C-warm is the support-gate reconnect, lockstep at about +2 s. C-cold is recorded and not scored.
4. Records the metrics below and scores them against the proposed thresholds.
5. Repeats a single rung with one flag on only after the flags-off baseline for that rung is stored.

No product file needs to change for the first baseline. Probes that read the event-loop lag, lock wait, and pool checkout live in the harness process (in-process mode) or are omitted (container mode). Do not add a public metrics route for this.

## Runtime the harness must mirror

Live production, read 2026-11-16 with `gcloud run services describe r2c-tracker-pilot --project=r2c-tracker-pilot --region=us-west1`. The Cloud Run project is `r2c-tracker-pilot`. It is not `r2c-tracker-platform`. The same project and region also contain `r2c-tracker-prerel` and `r2c-tracker`. The harness mirrors the pilot service only.

`deploy_pilot.sh` records the billing export as `r2c-tracker-platform.r2c_billing_export`. That project is **verify project**: confirm where the dataset actually lives before treating a query as the pilot's bill. See [CAMPAIGN_MODE_CHECKLIST.md](CAMPAIGN_MODE_CHECKLIST.md).

| Setting | Live value on revision `r2c-tracker-pilot-00232-soz` |
| --- | --- |
| Project / region / service | `r2c-tracker-pilot` / `us-west1` / `r2c-tracker-pilot` |
| Execution environment | gen2 |
| CPU | `1000m` (1 vCPU) |
| Memory | `1Gi` |
| Container concurrency | 80 |
| Request timeout | 3600s |
| Max instances | `maxScale` 1 |
| Min instances | no `minScale` annotation, which is min instances 0 |
| CPU throttling | `true` |
| Startup CPU boost | `true` |
| Startup probe | custom, `timeoutSeconds` 5 (`/livez` in `deploy.sh`) |
| VPC egress | direct VPC, network `r2c-pilot-vpc`, subnet `r2c-pilot-us-west1`, egress `private-ranges-only` |
| Cloud SQL connector annotation | absent |
| Runtime service account | `r2c-tracker-runtime@r2c-tracker-pilot.iam.gserviceaccount.com` |
| Process model | `Dockerfile` `CMD`: one `uvicorn main:app`, no `--workers` |
| Flight logs | `deploy.sh` gen2 GCS volume, `BASE_LOG_DIRECTORY` `/flightlogs-vol` |

CPU throttling allocates CPU only while a request is active. An open `/ws/r2c` socket is a request, so heartbeats keep the CPU allocated. Databases for the harness are two local PostgreSQL instances. SQLite is not an acceptable backend. The local process does not attach the production VPC or the runtime service account.

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

`--cpus=1` and `--memory=1g` match the live `1000m` / `1Gi` service. Docker does not enforce Cloud Run's concurrency of 80, and it cannot freeze the event loop between requests the way CPU throttling does while the container stays up. Scenario A-idle plus scenario C-cold is the local stand-in for scale-from-zero. Scenario C-warm restarts the process while clients are still connected. Do not describe a docker run as a CPU-throttling or startup-CPU-boost reproduction.

### Concurrency ceiling of 80

With `maxScale` 1 and container concurrency 80, the whole site accepts 80 in-flight requests. Each long-lived WebSocket holds one of those slots until it closes, and the request timeout is 3600s, so a tablet that stays connected can occupy a slot for an hour. HTTP uploads, NOTAM fetches, and `live-status` polls use the same 80. A quiet tablet that has parked does not hold a slot.

Count open sockets plus in-flight HTTP on every run and compare the sum to 80. Populations already in this spec:

| Population | Open sockets | Against 80 |
| --- | --- | --- |
| B primary, quiet orgs parked | 18 hot | under |
| B primary opening, N = 20, before the 30 s park | 52 | under |
| B primary opening, N = 40 | 92 | over |
| B upper bound, quiet orgs parked | 48 hot | under |
| B upper bound opening, N = 20 | 82 | over |
| C-warm campaign, primary / upper | 18 / 48 | under |
| C-warm fleet, primary, N = 20 | 52 | under |
| C-warm fleet, upper bound, N = 20 | 82 | over |
| A-map, N = 40 | 80 | on the ceiling |
| A-map or A-idle opening, N = 80 | 160 | over |

The support gate for shapes that stay at or under 80 (B primary steady, C campaign, C fleet primary at N = 20, A-map at N = 20) records zero Cloud Run-style refusals. The harness also runs at the ceiling and past it: A-map at N = 40 (80 sockets), the B upper-bound opening at N = 20 (82), and A-map at N = 80 (160). Record queue delay and HTTP 429 there. Those overflows are a measurement. Raising concurrency, or raising `max-instances`, is a follow-up after that record. It is not part of the flags-off baseline. `max-instances` still stays 1 until coordination state leaves the process, even if 80 is the number that fails.

Uvicorn will accept the 81st socket unless something in front of it stops admitting. For the support-gate twin, put a local limiter in front of the container that admits 80 concurrent requests and queues or rejects the rest the way this Cloud Run service does. Run the overflow populations through that limiter, and record a second file with admission left open so the process cost above 80 is visible separately from the 429s. Do not change the live service's concurrency to make either file pass.

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
| Silence timeout | `main.py:668-671`, `13152-13191` | A socket with no text frame for `R2C_CLIENT_SILENCE_SEC` (default 60, and at least two heartbeat intervals) is closed with code 4000. The wait is `websocket.receive_text()`, so an Android OkHttp ping is not a heartbeat. Android flying heartbeats land at 45–60 s (RID2Caltopo 2.5.8), against this 60 s text timeout. |
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
| Peer-traffic schedule | 1 s when the nearest other aircraft is within 5 pad-heights; 2 / 4 / 8 / 16 s as that ratio grows; 16 s when distance or pad is missing | `R2CCoordinationHub._shadow_traffic_interval_ms`, `main.py:2078-2090`. The client starts at its own 1 s default and then follows `traffic_schedule.shadowIntervalMs` (1–16 s). |
| Peer-traffic fan-out | only sockets within 1 mile (1609.344 m) | `PEER_TRAFFIC_RADIUS_M`, `main.py:1504`, filter at `4018-4026` |
| Weather timeout | connect 3.05 s, read 10 s | `requests.get(..., timeout=(3.05, 10))`, `main.py:5243` |
| NOTAM cache | 90 s | `FAA_PROXY_CACHE_TTL_SEC`, `faa_proxy.py:81-86` |
| Streams page poll | 10 s | `activeRefreshMs`, `static/organization_streams_live.js:42` |
| Mutual-aid comment in the protocol | "10 or more zones and drones" | `R2C_PROTOCOL.md`. The harness population is Ken's 2026-11-14 shape below, not a reading of this sentence as 12 zones. |

Marked **PROPOSAL** (change only by editing this spec, not by a quiet harness tweak):

| Knob | Value | Why |
| --- | --- | --- |
| Quiet org, tablets | 2, one iOS and one Android | A truck tablet and a standby. With no map and no drones they park; they do not hold a socket. |
| Platform mix | half iOS, half Android | Support gate. Primary hot org is 3 iOS + 3 Android. Upper bound is 8 + 8. |
| `HOT_ZONES` | 3 on the support gate; allowed 3 or 4 | Ken, 2026-11-14. A hot org is a mutual-aid search of 3–4 zones. |
| `DRONES_PER_ZONE` | 2 on the support gate; allowed 2, 3, or 4 | Ken, 2026-11-14. Each zone has 2–4 drones. |
| Tablets per hot org | `HOT_ZONES × DRONES_PER_ZONE` | One tablet per drone. Primary is 3 × 2 = **6**. Upper bound is 4 × 4 = **16**. |
| Who sees a drone | the other tablets in the same zone | Same zone is one segment. `CROSS_ZONE_VISIBILITY` defaults off. |
| Who is inside 1 mile | the tablets in that drone's zone | Other zones of the same hot org are placed more than 1 mile away. |
| Owner frames while flying | ~1 `sighting` + 1 `traffic_position` per second per drone | RID2Caltopo 2.5.8. About 2 websocket text frames/s/drone, not batched. See the client section. |
| Flight-log uploads | 1 / drone / hour, 2 MiB GeoJSON | One sortie per drone. Primary is 6 / hot org / hour; upper bound is 16. Size stays inside `MAX_FLIGHT_LOG_BYTES` (16 MiB). Sortie length is still a proposal. |
| `drone_confirmed` | 1 / drone / hour | One operator save per sortie. Primary 6 / hot org / hour; upper bound 16. |
| NOTAM polls | every 1800 s, or when the tablet moves 0.25 nm | RID2Caltopo 2.5.8. The server cache TTL is 90 s and is not the client timer. |
| Org-config fetch | none on 2.5.8 | The upcoming BTRX branch adds a startup retry. It is a labeled variant, not the baseline. |
| Browser on a hot org | one visible streams tab | 1 poll / 10 s of `live-status`. No thumbnail fetch unless the optional video variant is on. |
| `RECONNECT_DELAY` | `lockstep-2s` primary; `uniform-0-5` comparison | 2.5.8 has no jitter. Backoff is 2, 4, 8, 10, 10… s. A restart herds open tablets at about +2 s. |
| Identical foreign keys | every org reuses the same `mapId` and `remoteId` strings | The review asked for this collision case. Isolation is part of the pass. |

## Ladder

Run every scenario at N = 10, 20, 40, 80, 160.

Support gate: **N = 10 and N = 20**. Those are the first 10–20 orgs the site is supposed to hold, with at most three of them hot. A miss at 40, 80, or 160 is recorded and stops any claim that the single instance supports that many organizations. It is not fixed by raising `max-instances`.

Org identity: `org-000` … `org-{N-1}`, each with its own designator, organization id, and device credentials. Map ids and remote ids are the **same strings** in every org.

Steady window, scenarios A and B: 60 s warmup (covers the 60 s zone-update throttle), then 5 minutes measured. Scenario C: the reconnect, then 60 s of reconnect measurement, then 2 minutes of the parent scenario's steady traffic. The support-gate C run is the warm restart. Cold start is recorded beside it and is not the pass/fail row.

Correctness on every rung, not just the support gate: a socket may only receive coordination messages for its own organization id. A shared `mapId` or `remoteId` leaking into another org's `owner_assigned`, `zone_update`, `relay_sighting`, or `peer_traffic_position` is a fail.

## RID2Caltopo 2.5.8 client behavior

Source: RID2Caltopo released 2.5.8, `origin/main` `0eac4703`, as read by the R2C team. The harness copies these numbers. It does not retune them.

**Flying drone, both platforms.** About one `sighting` and one `traffic_position` per second per drone, about two websocket text frames per second, with no batching. Samples less than 1 s apart are dropped. Movement under 2 ft is re-sent on a 3 s keepalive instead of at 1 s. `traffic_position` starts at a 1 s default and then follows the server schedule, which this tracker clamps to 1–16 s (`main.py:2078-2090`). Non-owner `sighting`s are throttled to 3 s. Android sends these only while a CalTopo map is up. iOS sends them whenever coordination is required. A hot-org search has a map up and coordination required, so both platforms send.

**Heartbeat.** iOS is fixed at 15 s, the first one 15 s after the socket opens, and iOS sends no websocket pings. Android's base interval is 15 s, the first heartbeat goes out immediately on open, and a location update can add another heartbeat if the last one was at least 1 s ago. On 2.5.8 those location updates are every 10 s or 5 m. Android sends no heartbeat while one is still unacked, and it suppresses the interval heartbeat while it owns a drone that had a sighting in the last 30 s. While flying, that lands at an effective 45–60 s. The support gate uses 45 s, the busier end of that range. Android's OkHttp client pings every 20 s. Those pings are not text frames and do not reset `receive_text` at `main.py:13152`.

**Video presence.** Every 15 s while a managed-video incident is set. iOS piggybacks that on the heartbeat. Android sends it as its own frame. The support gate leaves video off.

**Idle park.** After 30 s with no drones and no map, the tablet sends `idle` and closes the socket. The server's `idleParkSec` (default 30 s, `main.py:674`) is the override. The tablet reconnects on the next sighting. The first connect is immediate. Quiet tablets are a connect/disconnect churn, not a standing heartbeat.

**NOTAMs.** `GET /faa/notams` every 1800 s, or when the tablet moves 0.25 nm (about 463 m). While the request is failing, Android retries every 15 s. iOS retries every 15 s until the first success. There is no jitter on that retry.

**Reconnect.** Websocket. The first connect is immediate. After a failure the backoff is 2, 4, 8, 10, 10… seconds, capped at 10 s, with no jitter. A tracker restart therefore brings every tablet that still had a socket back together at about +2 s. Android resets that backoff when the socket opens. iOS resets it only on `hello_ack`. A missing ack (10 s timeout, checked every 5 s) makes Android reconnect at 0 delay without growing the backoff; iOS stays on the normal ladder. On 401/403, Android keeps reconnecting with backoff, and iOS stops and does an HTTP re-auth. A dead tracker that does not reset the TCP connection is noticed in about 10–20 s on Android and in up to about 25 s on iOS; the +2 s herd is on top of that notice. A process kill that resets sockets is noticed immediately, so C-warm uses notice-now plus the +2 s step.

**Upcoming BTRX branch, not 2.5.8.** Location updates tighten to every 5 s or 2 m, so a map-up Android tablet can add heartbeats that often, still at least 1 s apart and still not while a heartbeat is unacked. Startup adds `GET /api/v1/organization-config/current`: three tries at 0, +3, and +8 s, then a background interval of 30 s doubling to a 300 s cap (30, 60, 120, 240, 300), no jitter, until the device is authorized. Run this only as a labeled BTRX variant.

## Scenario A — quiet fleet

No sightings in the steady window, no traffic, no uploads, no browser polls. Two tablets per org, one iOS and one Android.

**A-idle (support gate).** No map and no drones, which is the 2.5.8 park condition. At t = 0 every quiet tablet connects immediately, in lockstep, and sends `hello`. They heartbeat by the platform rules above until 30 s, send `idle`, and close. They stay down, because reconnect is on the next sighting and there is not one yet. At N = 160 that is 320 sockets open for half a minute, then zero. After the closes, hold 60 s at zero sockets. This is the scale-to-zero case.

**A-churn (support gate, N = 10 and N = 20).** Continue A-idle. At t = 180 s give each quiet org one sighting. Both tablets reconnect immediately (first connect), send the sighting, then go 30 s with no map and no drones, send `idle`, and close again. One extra connect/disconnect wave. That is the churn model. The 180 s gap is a scripted sighting, not a client timer.

**A-map (support gate).** A CalTopo map is up and no drone is owned, so the park condition is not met and the sockets stay open. iOS heartbeats every 15 s with the first at +15 s. Android heartbeats immediately and then every 15 s. Tablets are stationary, so the 5 m location gate does not add extra Android heartbeats. Android still sends an OkHttp ping every 20 s. At N = 160 that is 320 sockets and 320/15 ≈ 21.3 heartbeat text frames/s, plus 160 Android tablets pinging every 20 s (8 protocol pings/s). Each heartbeat still does a zone upsert.

NOTAM on A-map: one `GET /faa/notams` per tablet at map-up, then not again inside a 5 minute window unless the tablet moves 0.25 nm. A-idle tablets have no map and do not poll. A moving-tablet variant (location every 10 s / 5 m, and the BTRX 5 s / 2 m variant) is optional and off the support gate.

## Scenario B — three hot orgs, everyone else quiet

Exactly three orgs (`org-000`, `org-001`, `org-002`) run the hot profile. The other N−3 orgs run A-idle on the same process: they connect, park at 30 s, and stay down, so the measured window is the hot sockets plus whatever those parked orgs left in the database. At N = 10 that is 3 hot + 7 quiet. There is no N at which more than three orgs are hot. Also run **B0** once, not on the ladder: the three hot orgs and zero quiet orgs, so the hot cost can be subtracted from the N = 10 B run.

Ken, 2026-11-14: a hot org is **3–4 zones, 2–4 drones per zone, one tablet per drone**, so **6–16 tablets and the same number of drones**. The old 12-zone / 4-aircraft sketch is retired.

**B primary (the support gate).** `HOT_ZONES=3`, `DRONES_PER_ZONE=2`. That is 6 tablets, each owning one drone, in three zones on one map. Three hot orgs are 18 sockets, and that is the measured window once the quiet orgs have parked. The opening connect is larger: at N = 20 the quiet tablets add 34 sockets for the first 30 s (52 total), and at N = 160 they add 314 (332 total), then those sockets close.

**B upper bound (parameterized, required on N = 10 and N = 20).** `HOT_ZONES=4`, `DRONES_PER_ZONE=4`. That is 16 tablets and 16 drones per hot org, 48 hot sockets in the measured window, under the live concurrency of 80. The opening connect at N = 20 is 48 + 34 = 82 sockets until the quiet orgs park, which is past 80. Record queue delay and 429s for that opening. The harness must accept any pair in the allowed ranges, and the result JSON must record the pair and the socket count against 80. A miss at 16 tablets is the top of the range Ken described. It does not erase a pass at 6 tablets, and it is not fixed by raising concurrency or `max-instances` in the baseline.

Same-zone tablets see that zone's drones. Other zones of the hot org are more than a mile away, so they do not relay those drones and they do not receive that zone's `peer_traffic_position`. Leave `CROSS_ZONE_VISIBILITY` off unless a later edit of this spec turns it on.

Per hot org, primary (6 tablets, 3 iOS + 3 Android), drones moving, for the whole measured window. Counts are from RID2Caltopo 2.5.8 plus the zone shape above.

| Stream | Schedule | Server work |
| --- | --- | --- |
| 6 tablets on a map | `hello` at connect. iOS heartbeat every 15 s, first at +15 s (3/15 = 0.2 / s). Android flying heartbeat every 45 s, first immediately (3/45 ≈ 0.067 / s). Android sends none while one is unacked. | zone upsert each heartbeat; that tablet's heartbeat extends the lease of the one drone it owns |
| Android pings | OkHttp ping every 20 s on the 3 Android tablets | protocol frames only. They do not reset the text silence timer |
| 6 owner sightings | ~1 / s each while moving. Samples under 1 s are dropped. Under 2 ft of movement the client keeps a 3 s keepalive instead | `_upsert_owner_state` |
| 6 non-owner relays | each drone is seen by the other tablet in its zone, throttled to 3 s: 6 / 3 = 2 / s | `_record_sighting` insert |
| 6 `traffic_position` | client default 1 / s, no batching, then `shadowIntervalMs`. Drones in a zone sit within one pad-height with `padFt` set, so the server stays at 1 s | global lock + distance math + fan-out to the other tablet in the zone. Together with the owner sighting this is ~2 text frames/s/drone |
| `drone_confirmed` | 6 / hour, spread across the window | claim persist + broadcast |
| `PUT /{designator}/upload` | 6 / hour, 2 MiB | full-body buffer, weather worker, archive worker, submission lock |
| `GET /faa/notams` | one per tablet at map-up, then every 1800 s or 0.25 nm. Stationary segments do not move 0.25 nm during the 5 minute window | one lockstep burst of 6 requests per hot org at the start, then cache hits if anything repeats inside 90 s |
| `GET /{designator}/streams/live-status` | 1 browser / 10 s | control-plane read, no long-lived socket |

There is no organization-config poll on 2.5.8. The BTRX variant, off the support gate, adds the 0 / +3 / +8 s tries and the 30 s doubling schedule, and it tightens Android location updates to 5 s / 2 m.

Upper bound (16 tablets, 8 iOS + 8 Android) scales the same way: iOS heartbeats 8/15 ≈ 0.53 / s, Android flying heartbeats 8/45 ≈ 0.18 / s, Android pings 8/20 = 0.4 / s, owner sightings 16 / s, `traffic_position` 16 / s, non-owner relays 16 drones × 3 other tablets / 3 s = 16 / s, uploads and `drone_confirmed` 16 / hour, NOTAM 16 requests at map-up.

Place the drones of one zone within one pad-height of each other and set `padFt`, so the server schedule stays at 1000 ms inside the zone. Place the zones more than 1 mile apart. The support gate uses the moving-drone path (about 1 sighting/s). A stationary-drone run, where the under-2 ft keepalive stretches sightings to 3 s, is optional.

Android flying heartbeats at the 60 s end of the 45–60 s range sit on the server's 60 s text silence timeout. Record code 4000 on those Android sockets separately. iOS, at 15 s, should not see that close. Do not treat an OkHttp ping as proof the silence timer was reset.

**Functional checks inside B, each hot org, once per rung:**

- Two overlapping uploads of the same remote id in one org: the second waits on `serialized_flight_submission` and then returns 409 on the overlap, not a second flight row.
- The same remote id uploaded concurrently by two orgs: both proceed. They must not share a submission lock. (The key includes `organization_id`.)
- After the window, `len(_flight_submission_locks)` is 0. Growth means the prune lost the race or a waiter was stranded.

**Stall injection, support gate only (N = 10 and N = 20), after the steady B window:** hold the quiet orgs on A-map for this run so they have a heartbeat to measure. Point the weather stub at a 10 s response (inside the read timeout, not a hang) and start one upload in each hot org at the same instant. Those quiet-org `heartbeat_ack` times during the 10 s are the cross-tenant number. Repeat with the archive stub delaying the `/flightlogs-vol` write by 2 s if the harness has a slow-write stand-in; otherwise record "archive delay not injected" and do not claim FUSE behavior.

**Body-cap probe, once, not every rung:** one `PUT` of 100 MiB and one `PUT` of 120 MiB on a hot org. Record RSS delta, whether the request was rejected, and how long quiet-org heartbeats stalled. Today the live path has no cap, so a 120 MiB accept is a measurement, not a harness bug. A later flag that rejects above the cap is a separate run.

Optional, off by default: one hot org with a managed-video incident set. Video presence is every 15 s (RID2Caltopo 2.5.8). iOS piggybacks it on the heartbeat. Android sends a separate frame. The browser still polls `live-status` every 10 s and may fetch `GET /r2c-thumbnail/{tablet_code}/{session_id}.jpg` (`main.py:10230`). Keep this out of the support-gate totals.

**NOTAM error retry, support gate only, once after steady B.** Fail the stub for 45 s. Android retries every 15 s for the whole failure. iOS retries every 15 s until the first success. No jitter, so the hot tablets retry in lockstep. Then return success. Record how the FAA upstream cap of 8 (`faa_proxy.py:117-121`) behaves under that herd.

## Scenario C — reconnect stampede

Warm restart is the primary case: a new revision or an instance restart while sockets are still open. RID2Caltopo 2.5.8 makes that a lockstep herd. The backoff after a failure is 2, 4, 8, 10, 10… seconds, capped at 10, with no jitter, so every tablet that saw the same close comes back together at about +2 s. The first connect of a fresh app is immediate; a restart is a failure retry, so the first step is 2 s, not 0. Cold start stays secondary.

Two populations:

- **Campaign.** Quiet orgs have already parked (A-idle). The open sockets are the hot tablets only: 18 at the primary shape, 48 at the 16-tablet upper bound. N still matters because `_load_state` reads every org.
- **Fleet.** Quiet orgs are on A-map, so every tablet is connected. Primary shape at N = 20 is 18 + 34 = 52 sockets, under the concurrency ceiling. At N = 40 that sum is 92, and at N = 160 it is 332. Upper bound at N = 20 is 48 + 34 = 82, which is past 80. This is the "all clients connected" restart. The N = 20 upper-bound herd is one of the required at-and-beyond-80 measurements.

`RECONNECT_DELAY`, each as its own result file:

- `lockstep-2s`. Every open socket reconnects at +2 s after it observes the close. No jitter. If that attempt fails, the next herd is at +4 s, then +8 s, then every 10 s, still together. This is the 2.5.8 client, and it is the support-gate stampede.
- `uniform-0-5`. Each socket draws a delay uniformly from 0 to 5 s. This is the **with client jitter** comparison. The 2.5.8 client does not do this. Use it when judging the recommendation below.

The client connects at the later of "the process is listening" and "close time plus delay", sends `hello`, and resumes its schedule. Android resets backoff when the socket opens. iOS resets it only when `hello_ack` arrives. Record which delay the file used. Do not answer these reconnects with 401 or 403: Android would keep backing off, and iOS would stop and re-auth over HTTP, which is a different test.

**C-warm (primary).** Revision deploy or process restart with the working set still live.

1. Run the parent population until every open socket has persisted at least one heartbeat. Campaign uses B after the quiet orgs have parked. Fleet uses B with quiet orgs held on A-map.
2. Kill the process without an `idle` close and without a graceful drain. Container stop, or cancel the loop in-process. Clients see a TCP close immediately.
3. Start the process again.
4. Reconnect the sockets that were open, under `RECONNECT_DELAY`. Parked tablets stay parked.
5. `connect` awaits `_load_task` (`main.py:2307-2310`) while `_load_state` reads every org's recent zones, owners, and confirmations (`main.py:4326-4337`).

Support gate: `lockstep-2s` for campaign and fleet, primary shape, at N = 10 and N = 20, and `lockstep-2s` for the 16-tablet upper bound at those same rungs (campaign and fleet). `uniform-0-5` for the fleet population at N = 10 and N = 20, primary shape, as the jitter comparison.

**C-android-ack (risk case, support gate, N = 10 and N = 20, campaign population, `lockstep-2s`).** After the herd connects, withhold `hello_ack` for more than 10 s. Android checks every 5 s and force-reconnects at 0 delay, and that path does not grow the backoff, so the Android tablets loop in lockstep. iOS stays on 2, 4, 8, 10. Android will also reset its backoff on each socket open, including an open that never produces `hello_ack`. Record reconnects per second and whether `hello_ack` latency for the iOS tablets stays inside the C-warm threshold. This case is a finding about the client, not a tracker flag.

A blackhole that never resets TCP is not this run. Android would notice in about 10–20 s and iOS in up to about 25 s, and only then start the same backoff. Mention it in the result notes if a run is killed without a socket close.

**C-cold (secondary).** Scale-from-zero. Stop after A-idle (no sockets left), start the process, then bring the hot tablets back under `lockstep-2s` and, separately, under `uniform-0-5`. The database may still hold rows from the earlier run. Parked quiet tablets reconnect only when the scenario gives them a sighting. Record these files and do not score them as the support gate.

Record, for every C file: time from listen to each `hello_ack`, the delay each socket used, sockets closed during the herd (codes 1008, 1013, 4000, and anything other than an intentional client close), and the first 2 minutes of steady traffic afterward.

The single-tablet follow-on stays: after A-idle, start the process and connect one quiet tablet, as a first connect (immediate, no 2 s backoff). That is the ordinary scale-from-zero tax. Record it at N = 10. It is not the stampede.

## Metrics

Collect per rung, per scenario, and for B also split **hot orgs** vs **quiet orgs**.

| Metric | How |
| --- | --- |
| Latency p50 / p95 / p99 | `hello_ack`, `heartbeat_ack`, `owner_assigned`, `relay_sighting` delivery, `peer_traffic_position` delivery, `PUT /{designator}/upload`, `GET /faa/notams`, `GET .../live-status`, `GET .../organization-config/current`. Clock starts when the client finishes sending and stops when the matching response is complete. |
| Event-loop lag | In-process only. A probe task sleeps 50 ms and records `actual - 50 ms`. Report p50 / p95 / p99 of that excess. |
| Lock wait | In-process only. Probe wraps `asyncio.Lock.acquire` for the hub lock, the submission-lock guard, and each per-key submission lock. Report p50 / p95 / p99 wait, and the fraction of waits over 50 ms, tagged with which lock. |
| DB connections / pool | In-process: `main.engine.pool.status()` and the control-plane engine's `pool.status()` sampled 1 / s. Record checked out, overflow, and checkout wait if the pool exposes it. Container: Postgres `pg_stat_activity` grouped by database and state. Include the LISTEN connection. |
| Memory | Container: `docker stats` RSS max and RSS at the end of the 5 minutes minus RSS at the end of warmup. In-process: `VmRSS` from `/proc/self/status` on the same interval. Also sample `len(r2c_hub._connections)`, `len(r2c_hub._owners)`, and `len(main._flight_submission_locks)`. |
| Error rate | HTTP responses ≥ 500, HTTP 429, unexpected 4xx, websocket closes other than the scenario's own idle close, and `WebSocketWriter` 1013 retires. Split by org and route. Also record in-flight requests against the live concurrency of 80. |
| Drop counts | 1013 retires, 4000 silence timeouts, failed `broadcast` recipient counts from the server log line `broadcast retired`, and failed `live-status` polls. There is no long-lived SSE stream to drop. |

Write one JSON file per (mode, N, scenario, hot shape, platform mix, and `RECONNECT_DELAY`) plus a one-page table. Store the git SHA, the flag set (empty on the baseline), `HOT_ZONES`, `DRONES_PER_ZONE`, `RECONNECT_DELAY`, and `client: "RID2Caltopo 2.5.8 0eac4703"`. A BTRX file says so in the name.

## Proposed pass/fail thresholds

These are **proposals**. They are not measured, and they are not operator requirements Ken has signed. A miss on the support gate means "do not claim the capacity and do not turn on a fix flag in production until the baseline is understood". A miss on N ≥ 40 means "do not quote that rung". It does not mean the production service is out of its design.

Support gate is N = 10 and N = 20, container mode, flags off, unless the row says in-process.

| Check | Proposal |
| --- | --- |
| A-map `heartbeat_ack` | p50 < 50 ms, p95 < 200 ms, p99 < 500 ms |
| A-map quiet tablets during B, including the 10 s weather stall | p50 < 50 ms, p95 < 250 ms, p99 < 500 ms |
| B hot-org `heartbeat_ack` | p95 < 500 ms, p99 < 1000 ms |
| B hot-org `peer_traffic_position` delivery and `relay_sighting` delivery | p95 < 500 ms, p99 < 1000 ms |
| `PUT` upload, weather stub < 100 ms, 2 MiB, local disk | p95 < 3 s. This will not hold once weather or FUSE is slow; that case is judged by the quiet-org heartbeat row, not by the upload row. |
| `GET /faa/notams` cache hit | p95 < 100 ms |
| `GET /livez` during B | p99 < 100 ms, no 5xx |
| Event-loop lag, in-process, A-map | p99 excess < 50 ms |
| Event-loop lag, in-process, B steady | p99 excess < 100 ms |
| Event-loop lag during the 10 s weather stall | p99 excess < 100 ms. A miss here means the worker handoff is still stalling the loop, which is a stronger finding than "the upload is slow". |
| Hub lock wait, in-process, A-map | p99 < 20 ms |
| Hub lock wait, in-process, B | p99 < 100 ms. Quiet-org waits over 100 ms while only the hot orgs are inside the lock are the signal for per-org sharding. |
| Pool checkout | no timeout; checked out stays ≤ pool size + max overflow (defaults 5 + 10 on each engine, plus the one LISTEN connection on the control plane). Checkout wait p99 < 50 ms on the support gate. |
| Memory, container, B at N = 20 | RSS growth over the measured 5 minutes < 50 MiB, and peak RSS < 70% of 1 GiB |
| Submission-lock map | size 0 within 5 s after the last in-flight upload, at every rung |
| Error rate | HTTP 5xx < 0.1% of responses. On populations at or under 80 in-flight requests, HTTP 429 = 0. iOS abnormal websocket closes = 0 on the support gate. 1013 retires = 0 unless the harness injected a stalled reader. Android flying code-4000 closes are recorded on their own and are not an automatic fail while the 45–60 s heartbeat sits on the 60 s text timeout. |
| At and beyond 80 in-flight | A-map at N = 40, B upper-bound opening at N = 20, C-warm fleet upper bound at N = 20, and A-map at N = 80. Record queue delay and 429s. A 429 here is the concurrency measurement, not a baseline failure to be patched by raising the live cap. |
| Cross-org message | 0 at every rung |
| A-idle | zero coordination sockets after the idle closes, and again after the A-churn wave closes, at N = 10 and N = 20 |
| C-warm `lockstep-2s`, campaign and fleet, primary shape, `hello_ack` | p95 < 2 s, p99 < 5 s from the moment the process is listening. Zero 1013 during the herd. This is the scenario C support gate. |
| C-warm `lockstep-2s`, 16-tablet upper bound, N = 10 and N = 20 | same numbers, filed as the upper bound. A miss here does not fail the 6-tablet gate. |
| C-warm `uniform-0-5` | comparison only, the with-jitter picture. Same numbers, not the 2.5.8 client. |
| C-android-ack | record the Android reconnect loop. iOS `hello_ack` still meets the C-warm row. A loop that stays at 0 delay is the client-jitter finding below. |
| C-cold | record only. |
| C, single tablet after A-idle | record only. No threshold until the first baseline exists. |

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
| In-flight requests sit on 80 and clients queue or receive 429 while the process itself is healthy | A later change to Cloud Run container concurrency, still on one instance. `max-instances` does not move with it. | Live service: concurrency 80, `maxScale` 1, timeout 3600s, revision `r2c-tracker-pilot-00232-soz` | The baseline keeps both knobs. Raising concurrency is a follow-up after the at-and-beyond-80 files exist. Raising `max-instances` waits until coordination state is out of the process. Neither is a flag to flip so a rung goes green. |

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

1. Flags-off container baseline at N = 10 and N = 20 for A-idle, A-churn, A-map, B primary, B upper bound, C-warm `lockstep-2s` (campaign and fleet, primary and upper bound), C-warm `uniform-0-5` (fleet, primary shape), and C-android-ack, plus B0 and the NOTAM-error retry. Record C-cold on those rungs without scoring it.
2. Flags-off in-process baseline for the same support-gate set, for lock, lag, and pool.
3. Characterization rungs, container, flags off, primary hot shape: A-idle, A-map, B, and C-warm `lockstep-2s` for the fleet population. Stop early only if the process is OOM-killed; record that as the characterization result.
4. Then, and only then, one flag at a time on the support gate, aimed at whichever row actually missed.

Campaign mode (`min-instances=1`, CPU always allocated) is not a substitute for step 1 and is not a harness result. See [CAMPAIGN_MODE_CHECKLIST.md](CAMPAIGN_MODE_CHECKLIST.md).

## Recommendation: jitter client reconnect (RID2Caltopo, not this repo)

2.5.8 reconnects on 2, 4, 8, 10, 10… seconds with no jitter, so a tracker restart returns every open tablet together at about +2 s, and a failed attempt returns them together again at +4 s, +8 s, and then every 10 s. Android's missing-ack path is tighter: after 10 s with no ack it reconnects at 0 delay and does not grow the backoff, and it clears the backoff on socket open even when `hello_ack` never arrives. iOS clears the backoff only on `hello_ack`, so a slow tracker keeps iOS on the ladder.

Jitter on that backoff is a small RID2Caltopo change and the highest-leverage fix for this herd. It does not belong in r2c-tracker, and it is not a reason to shard the hub lock or raise `max-instances`. The shape to take to the app team: draw each step from a range (0 through the step, or the step through twice the step), keep the 10 s cap, and apply the same jitter to the Android 0-delay ack-timeout reconnect so that path cannot stay at 0. The harness file `uniform-0-5` is the with-jitter picture of the same stampede. Compare it to `lockstep-2s` before spending tracker complexity on the herd.

## Decisions

- Ken, 2026-11-14: a hot org is 3–4 zones, 2–4 drones per zone, one tablet per drone (about 6–16 tablets). Scenario B primary is 3 × 2 = 6. The parameterized upper bound is 4 × 4 = 16.
- Ken, 2026-11-14: clues go only to CalTopo. Tracker has no clue workload.
- Ken, 2026-11-14: a warm restart with clients already connected is the primary reconnect case. Cold start is secondary.
- RID2Caltopo 2.5.8 (`0eac4703`): owner traffic is about 1 `sighting` + 1 `traffic_position` per second per flying drone. Heartbeats, idle park, NOTAMs, and reconnect are the client section above. The restart herd is lockstep at about +2 s. `uniform-0-5` is a jitter comparison, not the shipped client.
- Live describe, 2026-11-16, project `r2c-tracker-pilot`, revision `r2c-tracker-pilot-00232-soz`: 1 vCPU (`1000m`), 1 GiB, container concurrency 80, request timeout 3600s, `maxScale` 1, no `minScale`, CPU throttling on, startup CPU boost on. One instance times 80 in-flight requests is the site ceiling. Open sockets in scenarios B and C are compared with that 80. Behavior at and beyond 80 is recorded. Raising concurrency or `max-instances` is not part of the baseline.
