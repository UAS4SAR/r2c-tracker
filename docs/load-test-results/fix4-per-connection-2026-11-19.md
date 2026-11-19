# Fix 4: per-connection hello and heartbeat cost, 2026-11-19

Local only. Postgres on this VM, weather stubbed, no FAA credentials, no Cloud Run. Tracker process in cgroup `r2c-loadtest` with `memory.max=1GiB`, swap max 0, and one CPU. `R2C_LOADTEST_INSTRUMENT=1`. The zone id fix from fix 1 is in effect, so each tablet has its own device guid.

## Profile, before any behavior change

A 90 s B-16 run with timing spans and no new flags. Client hello p50/p99 was 363/723 ms, heartbeat 27/1372 ms, upload p50 659 ms. Server handler time:

| Span | n | p50 / p99 ms | What it is |
| --- | ---: | --- | --- |
| `hello.device_name` | 62 | 118 / 464 | Control-plane read on every hello. The harness sends no `deviceModel`, and that function returns `""` after one credential SELECT. |
| `hello.config` | 62 | 65 / 336 | `get_organization_config_version_ms`, one query per hello. |
| `hello.zone_upsert` | 62 | 81 / 272 | Tenant SELECT + commit of `r2c_zone_state`. |
| `ws.hello` | 62 | 504 / 708 | Whole hello handler. The ack is sent before the zone broadcast, so most of the client hello time is these three queries. |
| `pool.connect` burst | 199 | 26 / 236 | Wait to check a connection out of the default pool (5 + 10 overflow). |
| `heartbeat.zone_upsert` steady | 120 | 22 / 65 | The heartbeat handler after the first 30 s. Owner upserts were 0 ms because this harness does not send `first_sighting`. |
| `ws.heartbeat` steady | 120 | 22 / 66 | Whole heartbeat handler. Ack send is 0.1 ms. |
| `traffic.broadcast` steady | 2861 | 0.8 / 4.1 | Fan-out to same-zone peers. |
| `ws.sighting` | 5613 | 0.05 / 0.17 | Sightings return immediately when no owner lease exists. |
| `hello.ack_send` | 62 | 0.13 / 0.24 | Writing the ack is not the wait. |
| `upload.create` | 3 | 177 / 193 | Weather stub 21, overlap query 34, archive 14, flush 12. |

Fan-out is not the tail. A slow peer does not hold the sender for hundreds of milliseconds at this load. The client heartbeat p99 is the opening herd: Android sends its first heartbeat immediately after `hello`, and the server does not read it until the hello handler returns. Steady heartbeats are tens of milliseconds.

## Flags

All default off. The "on" column below sets every one of them.

| Flag | Default | On |
| --- | --- | --- |
| `R2C_SKIP_EMPTY_DEVICE_NAME` | Call `assign_operational_device_name` on every hello. | Skip that call when `deviceModel` is blank. Auth already accepted the socket, and an empty model only re-reads the credential and returns `""`. |
| `R2C_CACHE_ORG_CONFIG` | Query the config version on every hello. | One in-flight lookup per organization, then reuse it for 5 s. |
| `R2C_DB_POOL_SIZE` / `R2C_DB_MAX_OVERFLOW` | SQLAlchemy default, 5 and 10. | On runs used 20 and 40. Unset leaves the engine constructor arguments unchanged. |
| `R2C_SAFE_ZONE_UPSERT` | SELECT-then-INSERT. Two hellos with the same `(org, map, zone_id)` can insert two rows. The next `scalar_one_or_none()` raises `Multiple rows were found when one or none was required` and the handler drops the socket with no close frame. | `INSERT ... ON CONFLICT DO UPDATE` on `(organization_id, map_id, zone_id)`. |
| `R2C_CAP_UPLOAD_BODY` | `PUT /{designator}/upload` buffers the whole body. | Reject above 100 MiB (`MAX_ARCHIVE_UPLOAD_BYTES`) with HTTP 413, including a `Content-Length` check before the buffer. |

`R2C_SAFE_ZONE_UPSERT=1` is also what creates the schema change. Startup then deletes older duplicate `r2c_zone_state` rows (keeping `MAX(id)` per scope) and runs `CREATE UNIQUE INDEX IF NOT EXISTS uq_r2c_zone_state_scope`. With the flag unset, startup does not touch that index. `tests/test_zone_upsert_race.py` seeds two rows, shows the old path raises, then runs eight concurrent conflict upserts and leaves one row.

## Before / after

360 s, instrumentation on, 1 GiB and one CPU. Off and on are the same build. "Before" is every flag unset.

| Run | Hello p50 / p95 / p99 ms | Heartbeat p50 / p95 / p99 ms | Upload p50 / p99 ms | 429 | Connects / failures | CPU | RSS |
| --- | --- | --- | --- | ---: | --- | ---: | ---: |
| B-6 off | 175 / 195 / 197 | 4.8 / 190 / 292 | 230 / 277 | 0 | 32 / 0 | 3.0% | 166 |
| B-6 on | 101 / 143 / 146 | 5.1 / 215 / 270 | 221 / 235 | 0 | 32 / 0 | 2.9% | 168 |
| B-16 off | 303 / 442 / 515 | 5.9 / 174 / 1047 | 352 / 410 | 0 | 62 / 0 | 7.7% | 177 |
| B-16 on | 186 / 300 / 308 | 5.7 / 119 / 885 | 502 / 571 | 0 | 62 / 0 | 7.4% | 180 |
| C-16 lockstep off | 137 / 427 / 591 | 5.2 / 139 / 753 | — | 0 | 124 / 62 | 7.5% | 174 |
| C-16 lockstep on | 97 / 171 / 189 | 6.0 / 200 / 359 | — | 0 | 124 / 62 | 7.3% | 176 |
| A-80 off | 270 / 372 / 405 | 96 / 449 / 489 | — | 320 | 160 / 320 | 1.1% | 170 |
| A-80 on | 175 / 243 / 354 | 9.7 / 417 / 461 | — | 320 | 160 / 320 | 1.2% | 174 |

No OOM. Hub-lock p99 stayed at 0.002–0.003 ms. Event-loop lag p99 stayed about 2 ms. Traffic broadcast steady p99 stayed about 5 ms, on and off.

The steady heartbeat handler, after the first 30 s, is the number that is not the connect herd. B-16 zone-upsert p99 there moved from 63 ms to 27 ms. A-80 heartbeat p50 moved from 96 ms to 10 ms, which is the quiet herd once 80 sockets are actually admitted. The client heartbeat p99 on B-16 (1047 → 885 ms) is still the Android heartbeats that sit behind hello during the opening second. Hello itself moved, which is why that tail moved, and why C's post-restart hello p99 moved from 591 ms to 189 ms.

C's 62 failures are the restart: 124 connects and 124 hello-acks, and close-to-ack p50 stayed about 2.1 s, the 2 s backoff. A-80 still returns 320 synthetic 429s and then parks all 160 tablets. The concurrency cap of 80 is unchanged.

Upload at 16 tablets did not get faster in the client samples (352 → 502 ms, three uploads). The server `upload.create` span stayed near 120–130 ms p50. Weather, archive, and commit are each under 60 ms p99. The client gap is the request waiting out the hello herd, not the archive write. B-6's create span did drop, from 110 ms to 29 ms p50, and the client p50 stayed near 220 ms.

## What was not changed

Broadcasts are still awaited. The spans say that wait is about 5 ms p99, so detaching it would not move the heartbeat tail. Heartbeat upserts are still one commit each. Coalescing them would not move the client p99, because that p99 is the first heartbeat of the run, and a coalesce cannot skip the first write.
