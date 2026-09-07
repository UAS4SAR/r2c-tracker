# Pre-release (`prerel.r2c-tracker.com`) Cloud Run + DNS cutover

Sketch for standing up a **long-lived** modularization soak environment for org
**NCSSAR** on iOS and Android. This is **not** the ephemeral release-gate
staging stack (`r2c-tracker-staging` / `r2c-release-staging` / `staging.invalid`),
which is IAM-only, cloned for ≤24h, and cleaned by `cleanup_pilot_staging.sh`.

Related: [PRE_RELEASE_SOAK_PROCESS.md](PRE_RELEASE_SOAK_PROCESS.md),
[PILOT_SETUP.md](../PILOT_SETUP.md), [RELEASE.md](../RELEASE.md).

**Status:** runbook only. Do not execute against production until Ken
explicitly starts the cutover. Prefer doing the work from a workstation with
`gcloud` config `r2c-tracker-pilot`, `pg_dump`/`pg_restore` 15, and IAP access
to the pilot PostgreSQL path (same prerequisites as [RELEASE.md](../RELEASE.md)
staging prep).

## Goals

| Item | Value |
|------|--------|
| Public origin | `https://prerel.r2c-tracker.com` |
| Org soak path | `https://prerel.r2c-tracker.com/ncssar` |
| Tablet override | RID2Caltopo Developer Tools → TrackerUrl = that URL |
| Source branch | `project/modularize-coordination` |
| Project / region | `r2c-tracker-pilot` / `us-west1` |
| Isolation | Separate Cloud Run service + DB clones + secrets; **no** prod write path |

## Non-goals / hard rules

1. **Never** point `CONTROL_PLANE_*` or `DATABASE_URL` for prerel at production
   databases (`r2c_pilot_tracker` / `r2c_pilot_control_plane` on the live pilot).
2. **Never** reuse `r2c-tracker-pilot` service name or overwrite
   `r2c-tracker.com` domain mapping.
3. **Do not** hijack `r2c-tracker-staging` for tablet soak — release automation
   will delete it and it uses `https://staging.invalid`.
4. Use a **separate** `CONTROL_PLANE_SIGNING_KEY` (and session `SECRET_KEY`) so
   prerel enrollment/session tokens cannot be confused with production.
5. Do not copy production flight-log objects into the prerel bucket (same rule
   as release staging).
6. Registrar for `r2c-tracker.com` is still GoDaddy per ownership notes; keep
   existing apex/`www` Google Frontend records intact while adding `prerel`.

## Recommended fixed identity (proposed names)

Mirror the staging naming style, but durable:

| Resource | Proposed name |
|----------|----------------|
| Cloud Run service | `r2c-tracker-prerel` |
| Runtime SA | `r2c-tracker-prerel@r2c-tracker-pilot.iam.gserviceaccount.com` |
| Flightlogs bucket | `r2c-tracker-prerel-flightlogs` |
| Cloud SQL (option A) | `r2c-prerel` (Postgres 15, zonal, small tier) **or** |
| DB on existing VM (option B) | new DBs `r2c_prerel_tracker` / `r2c_prerel_control_plane` + roles on the pilot Postgres path |
| Secrets | `r2c-prerel-tracker-database-url`, `r2c-prerel-control-plane-database-url`, `r2c-prerel-tracker-admin-password`, `r2c-prerel-deployment-gate-key`, `r2c-prerel-secret-key`, `r2c-prerel-control-plane-signing-key` |
| Public URLs | `CONTROL_PLANE_PUBLIC_URL=https://prerel.r2c-tracker.com`, `CONTROL_PLANE_TRACKER_BASE_URL=https://prerel.r2c-tracker.com` |
| Mode | `CONTROL_PLANE_MODE=live`, `ALLOW_UNAUTHENTICATED=1`, `RELEASE_STAGING_MODE=false` |
| Network | Same Direct VPC as pilot: `r2c-pilot-vpc` / `r2c-pilot-us-west1` |

**Option A vs B:** A matches release-staging isolation (separate Cloud SQL
instance). B is cheaper if the existing e2-micro / 5433 path has headroom.
Either is fine; pick one before cloning. Do not share tablespaces with prod
roles that can write prod DBs.

## Cutover checklist

### 0. Preconditions

- [ ] `project/modularize-coordination` builds green locally (`./qualify_release.sh`)
- [ ] RID2Caltopo project branch includes prerel App Links / Associated Domains
- [ ] Ken confirms go-ahead (this runbook is not authorization to spend or mutate DNS)
- [ ] Workstation: `./setup_pilot_local.sh` already done; `gcloud` config
  `r2c-tracker-pilot`; Postgres 15 client tools available

### 1. Provision isolate (once)

1. Create runtime SA `r2c-tracker-prerel@…` with Secret Manager accessor,
   Cloud SQL client (if A), and storage object admin on the prerel bucket only.
2. Create private bucket `r2c-tracker-prerel-flightlogs` (uniform access),
   mount path same as pilot (`/flightlogs-vol` via `deploy.sh` conventions).
3. Create Cloud SQL instance **or** prerel databases/roles on the VM.
4. Generate prerel-only secrets (do not clone prod signing/session keys).
   Store only in Secret Manager; never commit.
5. Optionally reuse existing OAuth client IDs **after** adding prerel redirect
   URIs (next section), or create a prerel-only OAuth client.

### 2. Clone databases (refreshable)

Pattern after `scripts/refresh_staging_databases.sh`:

1. Open the authenticated IAP / temporary firewall path used for staging clones.
2. `pg_dump` production pilot tracker + control-plane DBs (read-only).
3. `pg_restore` into prerel DBs.
4. Close temporary firewall / IAP hole immediately.
5. Sanity: connect as prerel roles only; confirm NCSSAR org/designator present
   in control-plane; spot-check recent flights in tracker DB.
6. Record refresh time in an ignored local note (e.g. `.release-state/prerel.json`
   — do not commit secrets). Re-clone before a major soak if prod drifted.

Full clone is preferred for v1 (same as release staging). NCSSAR-only filtering
can wait until someone needs a smaller footprint.

### 3. DNS + TLS (registrar + Cloud Run)

Keep apex and `www` unchanged.

At the `r2c-tracker.com` DNS host (GoDaddy today):

1. Add **`prerel`** as the record Cloud Run domain mapping instructs
   (typically a `CNAME` to `ghs.googlehosted.com.`, or the specific records
   printed by the mapping command — follow the command output, not a guessed
   target).
2. Map the domain to the prerel service, for example:

```bash
gcloud --configuration=r2c-tracker-pilot beta run domain-mappings create \
  --service=r2c-tracker-prerel \
  --domain=prerel.r2c-tracker.com \
  --region=us-west1 \
  --project=r2c-tracker-pilot
```

(Use whatever `gcloud run domain-mappings` / Certificate Manager flow is
current in the project; the important part is **service =
`r2c-tracker-prerel`**, never `r2c-tracker-pilot`.)

3. Wait for Google-managed certificate / HTTPS 200 on
   `https://prerel.r2c-tracker.com/livez` (and `/readyz` once DBs are wired).
4. Confirm `https://prerel.r2c-tracker.com/.well-known/assetlinks.json` and
   `/.well-known/apple-app-site-association` return the same app association
   payloads as production (host-agnostic routes in `main.py`).

### 4. OAuth / callbacks (if soak uses browser login)

Add prerel redirects alongside production (do not remove prod):

- `https://prerel.r2c-tracker.com/platform-admin/google/callback`
- `https://prerel.r2c-tracker.com/google/callback`
- Microsoft Web redirect: `https://prerel.r2c-tracker.com/microsoft/callback`
  (if Microsoft OIDC is enabled for prerel)

Tablet TrackerUrl soak may not need OAuth if devices enroll via QR / managed
enrollment against the prerel origin.

### 5. Deploy from the modularization branch

Add a thin wrapper (when implementing — not required to exist yet)
`deploy_prerel.sh` modeled on `deploy_pilot.sh` / `deploy_staging.sh`:

- Hard-refuse wrong `GCLOUD_PROJECT` / `SERVICE_NAME`
- Force `CONTROL_PLANE_PUBLIC_URL` / `CONTROL_PLANE_TRACKER_BASE_URL` to
  `https://prerel.r2c-tracker.com`
- Force prerel secret names and bucket
- `CONTROL_PLANE_MODE=live`, `ALLOW_UNAUTHENTICATED=1`
- Pass Android (and iOS) recommended build numbers consistent with the soak apps

Deploy source: checkout `project/modularize-coordination`, clean tree,
`./qualify_release.sh`, then:

```bash
# after deploy_prerel.sh exists:
./deploy_prerel.sh "${R2C_MINIMUM_ANDROID_BUILD}"
```

Until the wrapper exists, an explicit env-exported call to `deploy.sh` with the
table above is acceptable for a one-off, but refuse to run it against
`SERVICE_NAME=r2c-tracker-pilot`.

### 6. Smoke (before tablets)

```bash
curl -fsS https://prerel.r2c-tracker.com/livez
curl -fsS https://prerel.r2c-tracker.com/readyz
curl -fsS https://prerel.r2c-tracker.com/.well-known/assetlinks.json | head
curl -fsS https://prerel.r2c-tracker.com/.well-known/apple-app-site-association | head
# org path should render / accept the designator
curl -fsSI https://prerel.r2c-tracker.com/ncssar | head
```

Then: enroll a throwaway device on prerel, WS hello, upload, video if in scope.
Confirm prod `https://r2c-tracker.com/ncssar` still healthy and untouched.

### 7. Tablet soak

1. Install RID2Caltopo builds that include prerel App Links (project branch or
   later main merge).
2. Developer Tools → TrackerUrl =
   `https://prerel.r2c-tracker.com/ncssar` (Main Screen indicator must show).
3. Run enroll / coord / upload / video checklist on **iOS and Android**.
4. Optional: verify `https://prerel.r2c-tracker.com/<org>/…/enroll` App Link /
   Universal Link handoff after association files + DNS are live.

### 8. Ongoing hygiene

- Re-clone DBs when soak data is stale or corrupted; never reverse-sync to prod.
- Redeploy prerel from `project/modularize-coordination` after each meaningful
  modularization land; keep rebasing/merging `main`.
- Tear down or pause Cloud Run + Cloud SQL when soak pauses for weeks (cost).
- When modularization merges to main and soak ends, remove DNS mapping and
  prerel resources deliberately — do not leave a half-forgotten prod-adjacent
  host.

## Effort estimate

| Workstream | Effort | Notes |
|------------|--------|-------|
| SA / secrets / bucket / DB provision | ~half day | Mostly copy staging patterns |
| First DB clone | ~1–2 hours | Same tooling as release staging; IAP/firewall discipline |
| DNS + domain mapping + cert | ~1 hour + DNS propagation | Registrar change is the fragile part |
| OAuth redirect updates | ~30 min | Skip if QR-only soak |
| `deploy_prerel.sh` wrapper | ~1 hour | Hard-guards against prod service name |
| Smoke + first tablet enroll | ~1–2 hours | App Links verification may need a second pass |

## Scripts (on `project/modularize-coordination`)

```bash
./setup_pilot_prerel.sh          # once: Cloud SQL, secrets, SA, bucket
./scripts/refresh_prerel_databases.sh   # clone prod pilot DBs into prerel
./deploy_prerel.sh 185           # deploy branch; use soak Android build code
gcloud --configuration=r2c-tracker-pilot beta run domain-mappings create \
  --service=r2c-tracker-prerel --domain=prerel.r2c-tracker.com \
  --region=us-west1 --project=r2c-tracker-pilot
```

## Explicitly out of scope for this sketch

- Automating prerel inside `release_guard.py` / `deploy_candidate.sh`
- Changing production domain or pilot service
- MediaMTX / TURN redesign (reuse Cloudflare TURN JSON like pilot unless video
  soak proves otherwise)
- Standing up prerel from this assistant host (no `gcloud` pilot credentials /
  IAP here)

## First implementation PR (when Ken says go)

1. Add `deploy_prerel.sh` with hard service/project guards.
2. Optionally add `scripts/refresh_prerel_databases.sh` cloned from staging
   refresh with prerel instance/DB names.
3. Keep this runbook updated with the exact names chosen and the live domain
   mapping command that worked.
