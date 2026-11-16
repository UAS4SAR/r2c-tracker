# Local load harness

Simulated RID2Caltopo 2.5.8 tablets against one local Tracker process. The harness does not call Cloud Run, does not read production credentials, and builds its own database in a temporary directory.

Client behavior follows `docs/LOAD_TEST_HARNESS_SPEC.md`:

- A flying drone sends about one `sighting` and one `traffic_position` per second.
- Heartbeats are every 15 s. Android stretches that to 45 s while it owns a flying drone, and sends the first heartbeat immediately. iOS sends the first heartbeat 15 s after the socket opens and does not send websocket pings.
- A tablet with no map and no drones sends `idle` and closes after 30 s.
- Reconnect backoff is 2, 4, 8, 10… seconds with no jitter. `--reconnect jitter` draws each delay uniformly from 0 to 5 s instead.
- Android treats a missing ack at 10 s as a 0-delay reconnect that does not grow the backoff. `C-android-ack` delays `hello_ack` by 12 s so that path runs.
- NOTAM refresh is once at map-up, then every 1800 s, or every 15 s while the response is an error. This local process has no FAA credentials, so those calls fail closed and retry.

## Run a scenario

From the repository root, with the runtime dependencies installed:

```sh
python -m loadtest smoke
python -m loadtest A --orgs 10 --backend sqlite
python -m loadtest B --orgs 10 --tablets 6 --backend postgres
python -m loadtest B --orgs 10 --tablets 16 --backend postgres
python -m loadtest C --reconnect lockstep --tablets 6
python -m loadtest C --reconnect jitter --tablets 6
python -m loadtest C-android-ack --backend postgres
```

`A` is the quiet fleet (two tablets per org, idle park). `B` is three hot orgs plus quiet orgs. `C` connects, kills the process, and starts it again. Postgres is the default except for `smoke`, which uses SQLite so unit tests do not need a database server. Set `PG_BIN` if `pg_config` is not on `PATH`.

`--concurrency 80` is the default for A/B/C and matches the live single-instance cap. The harness admits that many in-flight sockets and HTTP calls, then counts queue time and synthetic 429s. The server process is pinned to one CPU core when the OS allows it. RSS is sampled. A 1 GiB memory cgroup is not applied on a host that does not delegate a cgroup.

`--instrument` sets `R2C_LOADTEST_INSTRUMENT=1` in the child only. That records event-loop lag and `asyncio.Lock` wait. Leave it off for the flags-off baseline.

Weather is stubbed inside the child (`R2C_LOADTEST_STUB_WEATHER=1`) so a flight upload does not call Open-Meteo. Uploads in scenario B are one per hot org so the short run still has an upload latency.

## Baseline

```sh
python -m loadtest baseline --date 2026-11-16 --backend postgres
```

This writes `docs/load-test-results/baseline-<date>.md` and JSON files next to it. It runs quiet fleets of 10, 20, 40, 80, and 160 orgs, scenario B with 6 tablets, and scenario C lockstep versus jitter.

## Smoke test

```sh
python -m unittest tests.test_loadtest_smoke
```

One tablet, SQLite, a few seconds. It is part of `python -m unittest discover -s tests -p 'test_*.py'`.
