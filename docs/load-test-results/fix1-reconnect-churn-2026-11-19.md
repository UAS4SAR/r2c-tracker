# Fix 1: campaign-tablet reconnect churn, 2026-11-19

Local only. The earlier 16-tablet Scenario B run opened 692 sockets for 62 planned tablets and recorded 630 connect failures. That churn was a harness artifact. The tracker was not changed.

## Root cause

RID2Caltopo 2.5.8 sends one websocket per tablet, and the protocol `zoneId` is that tablet's device guid (`app/TrackerCoordination Protocol.md` in RID2Caltopo: "a single outbound websocket per zone", with `"zoneId": "device-guid"`). The harness instead gave every tablet in a geographic zone the same id (`z0`, `z1`, …) and the same guid.

The hub keeps one `r2c_zone_state` row per `(organization, map, zone_id)` and loads it with `scalar_one_or_none()` (`main.py` `_upsert_zone_state`). Two tablets helloing on the same id both observe no row and both insert. The next upsert then raises `Multiple rows were found when one or none was required`. The websocket handler logs that and drops the socket without a close frame (`serve_r2c_websocket`). The client counts a connect failure and dials again. The new pair races the same row, so the loop continues for the whole window.

Evidence from a 50 s probe of the old harness, 16 tablets, instrumentation on, 1 GiB / 1 CPU:

- 473 connects, 439 failures, 420 of them before `hello_ack`. Failure age p50 was 9 ms.
- Every client failure was `ConnectionClosedError` with no close frame. None were 1013, 1008, 4000, or a synthetic 429. Admission rejected 0.
- The server log contained that multiple-row error 439 times, once per failure. A shorter trace logged two `r2c hello received` lines for the same `zone=z0 guid=z0` (and the same for `z1` and `z2`) and then the error at a connection age of about 38 ms.
- Quiet tablets, which already used a unique id, did not reconnect.

`broadcast retired` lines were present and were not the driver. After the fix they still show up when the 14 quiet tablets park into an in-flight zone broadcast. Those sockets close on purpose. They do not reconnect.

## Change

Harness only. Each hot tablet now sends its own device name as `zoneId` and `guid`. Tablets in one geographic zone still share a latitude cluster and still list each other's drones in `peer_remote_ids`, which is how same-zone relay works. `tests/test_loadtest_smoke.py` checks that 16 hot tablets get 16 zone ids and that the first four still share a location and a peer set.

## Before / after

Same shape as `full-2026-11-16.md`: 10 orgs, 3 hot, 7 quiet, 360 s, Postgres, weather stubbed, instrumentation on, cgroup `memory.max=1GiB` and one CPU. Before is the shared zone id. After is one zone id per tablet.

| Run | Connects (planned) | Failures | Hello p50 / p95 / p99 ms | Heartbeat p50 / p95 / p99 ms | Upload p50 / p99 ms | 429 | CPU | RSS | OOM |
| --- | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | --- |
| B-6 before | 180 (32) | 148 | 168 / 188 / 204 | 5.1 / 33 / 341 | 220 / 270 | 0 | 2.6% | 166 MiB | no |
| B-6 after | 32 (32) | 0 | 185 / 203 / 210 | 4.9 / 219 / 292 | 213 / 255 | 0 | 2.5% | 165 MiB | no |
| B-16 before | 692 (62) | 630 | 216 / 350 / 364 | 5.4 / 31 / 456 | 682 / 734 | 0 | 6.5% | 172 MiB | no |
| B-16 after | 62 (62) | 0 | 304 / 538 / 640 | 6.9 / 44 / 931 | 445 / 461 | 0 | 6.8% | 174 MiB | no |

After the fix, hello-acks equal the planned sockets (32 and 62) and the 14 quiet tablets park. Client close reasons are empty. Server closes at the end of the window are code 1000. The multiple-row error is gone. HTTP errors are the expected NOTAM 503 retries (1,152 at 16 tablets is 48 tablets × 360 s / 15 s).

Hub-lock wait is unchanged and still not the cost: B-16 after is p99 0.003 ms across 41,579 acquires. Event-loop lag p99 stays about 2 ms.

Hello p99 and heartbeat p99 at 16 tablets are higher than the churning run. Those earlier samples were taken while sockets were dying every few seconds, so most tablets were not connected long enough to take a full fan-out. The steady population is the one this fix measures. Upload p50 on that population moved from 682 ms to 445 ms. The heartbeat tail is the per-connection work left for a later fix, not another reconnect loop.
