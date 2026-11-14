# Campaign mode checklist

Temporary always-on for a real mutual-aid incident. This changes Cloud Run scaling on the already-serving revision's template. It does not deploy code, and it does not raise the instance cap.

Use it only when a big incident needs the single Tracker process to stay warm and to keep CPU between requests. Revert to scale-to-zero when that incident is over. Quiet organizations share this one site; campaign mode makes **every** org pay for a warm instance, which is why it is temporary.

Related:

- Why `max-instances` stays 1: [COORDINATION_HUB_EXTRACTION.md](COORDINATION_HUB_EXTRACTION.md) and `R2CCoordinationHub` in `main.py` (in-process sockets and ownership).
- Local baseline that should already exist before anyone leans on this for capacity decisions: [LOAD_TEST_HARNESS_SPEC.md](LOAD_TEST_HARNESS_SPEC.md).
- Steady-state deploy flags: `deploy.sh` (`--min 0`, `--max-instances 1`, `--cpu-throttling`). Production wrapper: `deploy_pilot.sh`.
- Soak host, which is a different service: [PRE_RELEASE_SOAK_PROCESS.md](PRE_RELEASE_SOAK_PROCESS.md).

## What "always-on" means here

`deploy.sh` deploys with:

```text
--min 0
--max-instances 1
--cpu-throttling
```

CPU throttling allocates CPU only while a request is active. An open `/ws/r2c` socket counts as a request. With `min-instances=0`, the instance can scale to zero once the last socket closes. That is the steady state for quiet orgs.

Campaign mode, on the production service only:

| Knob | Steady state (`deploy.sh`) | Campaign mode | After revert |
| --- | --- | --- | --- |
| Min instances | 0 | 1 | 0 |
| CPU throttling | on | off (CPU always allocated) | on |
| Max instances | 1 | **1, unchanged** | 1 |
| CPU / memory / concurrency | whatever is live | unchanged | unchanged |

`deploy.sh` does not pass `--cpu` or `--concurrency`. `deploy_pilot.sh` sets memory to `1Gi`. Do not "tidy" CPU, memory, or concurrency while enabling campaign mode. Record them and leave them.

Production identity, from `deploy_pilot.sh` and `docs/releases/v1.4.98.md`:

| Item | Value |
| --- | --- |
| gcloud configuration | `r2c-tracker-pilot` |
| Project | `r2c-tracker-pilot` |
| Region | `us-west1` |
| Service | `r2c-tracker-pilot` |
| Public checks | `https://r2c-tracker.com/livez` and `/readyz` |

`r2c-tracker-prerel` is not this checklist. Do not set min-instances on prerel as part of a production incident.

## Hard rules

1. `max-instances` stays 1 until coordination state is extracted from the process. Two instances do not share live ownership. A new revision and an old revision can also overlap while sockets drain. After every command below, traffic must be 100% on exactly one revision.
2. Do not run `deploy.sh` or `deploy_pilot.sh` to flip these knobs. Those build and deploy. `gcloud run services update` keeps the serving image if you do not pass a new one. Confirm the image digest did not change.
3. Do not enable campaign mode to compensate for a missing load-test baseline, and do not treat a warm instance as evidence the P0/P1 flags are safe. Flags still wait on [LOAD_TEST_HARNESS_SPEC.md](LOAD_TEST_HARNESS_SPEC.md).
4. Take the billing snapshot before the enable command and again after the revert. The export lags the incident; "cost unchanged" in the first hour does not mean the window was free.
5. Revert when the mutual-aid searches are done. The first 10–20 orgs are supposed to scale to zero when they are quiet. Campaign mode is the exception for the incident, not the new default.

## 0. Who and where

Run this from a workstation already on the `r2c-tracker-pilot` gcloud configuration, as an account that can update Cloud Run and read billing. The examples assume that configuration is active. If it is not:

```sh
gcloud config configurations activate r2c-tracker-pilot
gcloud config get-value project
gcloud config get-value account
```

Stop if the project is not `r2c-tracker-pilot` or the account is not one you expect to operate production with.

## 1. Record the serving revision before changing anything

```sh
gcloud run services describe r2c-tracker-pilot \
  --project=r2c-tracker-pilot \
  --region=us-west1 \
  --format='yaml(status.traffic,status.latestReadyRevisionName,spec.template.metadata.annotations,spec.template.spec.containerConcurrency,spec.template.spec.containers[0].image,spec.template.spec.containers[0].resources)'
```

Write down, with a UTC timestamp:

- Serving revision name (the one at 100% traffic) and its image digest.
- `autoscaling.knative.dev/minScale` (expect `0`).
- `autoscaling.knative.dev/maxScale` (expect `1`).
- `run.googleapis.com/cpu-throttling` (expect `true` while `deploy.sh` is the source of the template).
- CPU, memory, and container concurrency.
- `TRACKER` version from the health check below.

If `maxScale` is anything other than `1`, stop. Do not continue this checklist, and do not raise it.

```sh
curl -fsS https://r2c-tracker.com/livez
curl -fsS https://r2c-tracker.com/readyz
```

`/livez` is process-only (`main.py:6698`). `/readyz` checks both databases (`main.py:6706`). Expect HTTP 200, `"status": "ok"` / `"ready"`, and the version you just deployed. Startup and liveness probes in `deploy.sh` both hit `/livez` (startup: timeout 5 s, period 5 s, 24 failures; liveness: period 30 s, 3 failures).

## 2. Billing snapshot, before

Do this in the same sitting as the enable, before the `services update`.

### 2a. Which account is actually billed

```sh
gcloud billing projects describe r2c-tracker-pilot \
  --format='yaml(billingAccountName,billingEnabled)'
```

`docs/UAS4SAR_CLOUD_OWNERSHIP_MIGRATION.md` recorded billing account `01B1DE-5A235D-3DA7F7` for this project. If `describe` returns a different account, record the command's answer and use that account for the rest of the snapshot. Do not "fix" the link as part of campaign mode.

Also note project `r2c-tracker-platform` (the export project in `deploy_pilot.sh`) and `shaped-splicer-482602-v1` only if their costs move when this Cloud Run service stays warm. The service you are changing is `r2c-tracker-pilot`.

### 2b. Console

Billing → Reports, for the account from step 2a:

- Time range: month to date, and a second view of the last 24 hours.
- Filter: project `r2c-tracker-pilot`.
- Group by SKU.
- Save or screenshot the total, the Cloud Run CPU and memory SKUs, Cloud SQL, Cloud Storage, and egress.

Cloud Run → service `r2c-tracker-pilot` → Metrics, same project and region: instance count, billable instance time, CPU allocation, memory, request latency, and startup latency. Note the current instance count (it may be 0 if every tablet is parked).

### 2c. BigQuery export, if the table is there

`deploy_pilot.sh` sets `PLATFORM_BILLING_SOURCE=bigquery`, project `r2c-tracker-platform`, dataset `r2c_billing_export`. The app looks for tables prefixed `gcp_billing_export_resource_v1_` or `gcp_billing_export_v1_` (`platform_admin.py`). List them; do not guess the billing-account suffix:

```sh
bq ls --project_id=r2c-tracker-platform r2c_billing_export
```

Prefer the `gcp_billing_export_resource_v1_` table when both exist. Then, with `TABLE` set to that name:

```sql
SELECT
  project.id AS project_id,
  service.description AS service,
  sku.description AS sku,
  ROUND(SUM(cost), 6) AS cost,
  ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)), 6) AS credits,
  MIN(usage_start_time) AS usage_from,
  MAX(usage_end_time) AS usage_through
FROM `r2c-tracker-platform.r2c_billing_export.TABLE`
WHERE project.id = 'r2c-tracker-pilot'
  AND usage_start_time >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH)
GROUP BY project_id, service, sku
ORDER BY cost DESC
```

Record `usage_through`. The export is hours behind the incident. A later snapshot that still ends before the enable timestamp does not include campaign mode.

If `bq ls` fails or the dataset is empty, record that and keep the console report. Do not block the incident on the export, and do not switch the app's `PLATFORM_BILLING_SOURCE`. Platform-admin numbers with an illustrative source are not this snapshot. Production's deploy wrapper uses the BigQuery source; prerel is pinned to illustrative and is the wrong screen.

### 2d. File the snapshot

One note, next to the revision record from step 1: UTC time, billing account, revision, image digest, min/max, CPU throttling, CPU, memory, concurrency, `/livez` version, console month-to-date total, and the BigQuery `usage_through` plus the Cloud Run SKU rows. No secret values.

## 3. Enable campaign mode

```sh
gcloud run services update r2c-tracker-pilot \
  --project=r2c-tracker-pilot \
  --region=us-west1 \
  --min-instances=1 \
  --max-instances=1 \
  --no-cpu-throttling
```

`--max-instances=1` is repeated on purpose so the update cannot leave the cap implicit. Do not add `--cpu`, `--memory`, `--concurrency`, or a new `--image`.

This updates the template and usually creates a new revision. Cloud Run will not send it traffic until the startup probe (`/livez`) passes. The probe's failure budget is 24 × 5 s.

## 4. Health check immediately after

```sh
gcloud run services describe r2c-tracker-pilot \
  --project=r2c-tracker-pilot \
  --region=us-west1 \
  --format='yaml(status.traffic,status.latestReadyRevisionName,spec.template.metadata.annotations,spec.template.spec.containers[0].image)'

curl -fsS https://r2c-tracker.com/livez
curl -fsS https://r2c-tracker.com/readyz
```

Pass:

- Exactly one revision has traffic, at 100%.
- That revision's image digest equals the digest recorded in step 1.
- `minScale` is `1`, `maxScale` is `1`, `cpu-throttling` is `false` (or the annotation is absent in the way `--no-cpu-throttling` writes it; the describe output is the record).
- `/livez` and `/readyz` return 200 with the same version as step 1.
- Cloud Run metrics show instance count 1, not 2.

If two revisions hold traffic, pin the new revision only when it is Ready and the digest matches, otherwise pin the step-1 revision:

```sh
gcloud run services update-traffic r2c-tracker-pilot \
  --project=r2c-tracker-pilot \
  --region=us-west1 \
  --to-revisions=REVISION=100
```

If the new revision never becomes Ready, route 100% back to the step-1 revision and stop. Do not raise `max-instances` to keep both.

A tablet that is already connected may drop when the revision changes and will reconnect. That is the scenario C case in the harness spec, on a live incident. Watch `/readyz` and the Cloud Run error log for a few minutes. Do not start a synthetic load against production to "prove" the checklist.

## 5. While it is on

- Leave `max-instances` at 1. Extraction of `R2CCoordinationHub` out of the process is what has to happen before that cap moves. Campaign mode does not extract it.
- Quiet orgs are on this warm instance too. Do not clone the service per organization.
- If you need a code change during the incident, use the normal release path. Do not combine it with this scaling update.
- P0/P1 flags (upload cap, deferred weather, per-org lock, heartbeat batching, submission-lock prune, pool size) stay off unless a recorded harness baseline already justified that one flag. This checklist does not turn them on.

## 6. Revert to scale-to-zero

When the incident's concurrent searches are finished:

```sh
gcloud run services update r2c-tracker-pilot \
  --project=r2c-tracker-pilot \
  --region=us-west1 \
  --min-instances=0 \
  --max-instances=1 \
  --cpu-throttling
```

Then repeat the describe and both curls from step 4.

Pass:

- One revision at 100% traffic.
- Image digest still the step-1 digest, unless a normal release landed during the incident and you recorded that release on purpose.
- `minScale` is `0`, `maxScale` is `1`, CPU throttling is back on.
- `/livez` and `/readyz` are 200.
- Instance count returns to 0 after the last tablet parks or disconnects. An instance that stays at 1 with throttling on and no sockets is a client that did not park, not a failed revert. Check for an open `/ws/r2c` before flipping min-instances again.

Scaling fields live on the revision template. If the revert update itself misbehaves, put traffic back on the revision you recorded as healthy:

```sh
gcloud run services update-traffic r2c-tracker-pilot \
  --project=r2c-tracker-pilot \
  --region=us-west1 \
  --to-revisions=RECORDED_REVISION=100
```

Describe that revision's annotations before calling the incident over. Restoring traffic restores that revision's min-instances and CPU setting. If the revision you restored is still `minScale: 1`, run the revert `services update` against the service again and confirm the new serving revision is `minScale: 0` with `maxScale: 1`.

Leave the campaign revision in the history at 0% traffic until the billing window is filed. Do not delete revisions as part of this checklist.

## 7. Billing snapshot, after

Repeat section 2 after the revert, not only after the enable.

- Console report for the same project, month to date and the incident window (enable timestamp through revert timestamp, UTC).
- The same BigQuery query, plus a query restricted to that window:

```sql
SELECT
  service.description AS service,
  sku.description AS sku,
  ROUND(SUM(cost), 6) AS cost,
  ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)), 6) AS credits
FROM `r2c-tracker-platform.r2c_billing_export.TABLE`
WHERE project.id = 'r2c-tracker-pilot'
  AND usage_start_time >= TIMESTAMP('ENABLE_UTC')
  AND usage_start_time < TIMESTAMP('REVERT_UTC')
GROUP BY service, sku
ORDER BY cost DESC
```

Replace `ENABLE_UTC` and `REVERT_UTC` with the timestamps from steps 3 and 6. If `usage_through` is still earlier than `ENABLE_UTC`, the export has not caught up. Say that in the note and run the query again once `usage_through` is past `REVERT_UTC`. Do not enter a zero and call it the incident cost.

File the delta next to the before-snapshot: Cloud Run CPU and memory in particular. Always-allocated CPU on one 1 vCPU / 1 GiB instance is the expected change versus scale-to-zero. Cloud SQL and the flight-log bucket should be in the same note so a storage or connection-pool surprise is visible. This checklist does not quote a dollar budget; the snapshot is the record.

## 8. What this does not change

- One site, many orgs. No per-org Cloud Run service.
- `max-instances` remains 1 until coordination state is not only inside this process.
- Quiet orgs go back to scale-to-zero after the revert. The first 10–20 orgs are not a reason to leave min-instances at 1.
- Upload caps, weather, the global lock, heartbeat batching, submission locks, and pool size are code, gated by the harness spec. They are not flags you flip with `gcloud run services update`.
