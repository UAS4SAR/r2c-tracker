# Multi-organization robustness and prerelease review

Reviewed 2026-09-10. Tracker: `project/modularize-coordination`, commit `b9b1264`. RID2Caltopo: `main`, commit `325f8abd1` (2.2.7 build 197 snapshot). Existing unrelated working files were left untouched.

This is a source review and implementation proposal, not a deployment, capacity certification, or completed mobile feature. No production database was accessed or cloned. Repository documentation was treated as design history, not as authorization to execute its cutover commands.

## Assessment

The Tracker has a credible multi-organization foundation: scoped device authorization and upload routes, organization-aware coordination keys and persistence, organization-separated flight-log paths, audit facilities, and guarded release tooling. The current regression suite passes. The next step should be a safe persistent prerelease environment, followed by targeted runtime hardening and representative load tests. Raising the Cloud Run instance count now would be premature.

Existing prerelease work should be retained and hardened, rather than replaced. `setup_pilot_prerel.sh`, `deploy_prerel.sh`, and `scripts/refresh_prerel_databases.sh` already define an independent service, SQL instance, database roles, bucket, and signing/session secrets. However, those separate resources do not yet establish a complete boundary against production side effects.

## Findings, in priority order

### 1. Prerelease can use production integrations and even append to a production secret

**Priority: fix before deploying an unattended clone.**

`deploy_prerel.sh:27–44` defaults to shared managed-request ingestion, Google OAuth, TURN, Gmail refresh-token, and App Store webhook secrets. Its Gmail token target is the production-named secret. `deploy.sh:345–373` grants the runtime service account `roles/secretmanager.secretVersionAdder` on that target. `main.py:6839` implements the write. `setup_pilot_prerel.sh:226–245` grants access to several shared secrets.

This means database separation alone does not prevent prerelease from sending real messages, consuming shared relay capacity, or changing the Gmail token used by production. `PLATFORM_BILLING_SOURCE=illustrative` is not a general external-effects switch. The billing-notification worker starts when the control plane exists (`main.py:1202`), and sends queued notifications when an email sender is configured (`main.py:418`).

**Recommendation:** default prerelease email to a capture sink; reject real-recipient sending unless explicitly enabled for an allowlisted test recipient. Use separate ingestion/webhook/TURN credentials or disabled adapters by default. Shared OAuth clients can be an intentional exception with separate redirect URIs and sessions; they should not imply shared Gmail write permissions. Verify actual IAM as well as script defaults: removing a grant from a setup script does not revoke an existing grant.

### 2. Cloning preserves production authorization and embedded external configuration

**Priority: fix before making a refreshed clone available.**

The refresh script restores both complete database dumps and stops there. Device tokens are authenticated using a plain SHA-256 hash (`control_plane.py:318`, `:879`, `:6669`); rotating the enrollment signing key does not change these hashes. Thus an active copied device credential can authenticate the same production token on the clone. Config-release/proposal JSON, account password/reset state, contact addresses, video state, pending notifications, and organization hostnames also survive the copy.

**Recommendation:** make sanitization a mandatory, transactional, versioned step on the destination only. Revoke copied device credentials and enrollment campaigns; invalidate password-reset/setup tokens; clear transient coordination, active video/signaling/download state and pending delivery queues. Preserve history only where useful for testing. Review and sanitize nested configuration snapshots as well as top-level fields: production CalTopo credentials and map destinations can let a test tablet write outside Tracker even when the Tracker databases are isolated. Use dedicated test maps and test credentials.

Record a clone manifest containing source versions, copy times, schema version, sanitizer version, row-count checks and destination identities. Never record secret values. Treat realistic incident/contact data as restricted data, with limited test access and an expiry policy. A synthetic multi-organization fixture is preferable for routine automated tests; a sanitized production clone is valuable for migration and realism testing.

### 3. Deployment guards do not fully enforce the advertised environment

**Priority: fix before routine prerelease deployment.**

The wrapper validates project, service name, public URL, live mode, and transport access. Database secret names, SQL instance, runtime service account, bucket and `CONTROL_PLANE_TRACKER_BASE_URL` remain environment-overridable without matching destination checks (`deploy_prerel.sh:9–25`, `:49`, `:60–84`). An inherited production bucket or database secret name can therefore defeat the intended isolation while passing the current checks.

**Recommendation:** validate all destination identities before any cloud mutation. Add an immutable environment marker in each database and check it against the application's configured environment at startup/readiness. Inspect resolved database identities without logging credentials. Fail closed when the two database markers, public origin, bucket, or credentials disagree. Test that production overrides are refused before any deployment command runs.

### 4. Refresh failure can remove the working test environment

**Priority: fix before frequent database refreshes.**

`scripts/refresh_prerel_databases.sh:56` deletes the prerelease service before tunnels, credentials, or dumps are verified. It later deletes and recreates both destination databases before restoring them. A failure can leave the test host absent or the database pair partially restored. The script also changes a firewall rule on the source database network during copying.

**Recommendation:** build a new versioned pair of destination databases first; restore, sanitize, migrate, and validate them before switching the prerelease service to that pair. Retain the previous pair for rollback, then remove it after a successful soak. Acquire a refresh lock to prevent overlapping refreshes. Use a read-only source backup role and a controlled existing export path where possible. No automated reverse replication or test-data merge into production.

The two parallel `pg_dump` processes each produce a consistent database snapshot, but the script does not establish one atomic snapshot across both databases. Record both copy times and reconcile cross-database references after restore. For tests that require a matching point in time, use a coordinated backup strategy. PostgreSQL documents that `pg_dump` backs up one database and is consistent while that database is in use: <https://www.postgresql.org/docs/15/app-pgdump.html>.

### 5. Live coordination is still tied to a single process

**Priority: resolve before horizontal scale-out or traffic-split releases serving the same organizations.**

`R2CCoordinationHub` stores sockets, zones, owners, peer traffic and thumbnails in memory (`main.py:1441–1470`). PostgreSQL notifications cover selected video operations, not all ownership/peer coordination. `deploy.sh:566` specifies `--max-instances 1`; the Dockerfile starts a single Uvicorn process. Persistence and replay do not make two simultaneously running hubs agree on live ownership. A new revision and an old revision can also overlap while sockets drain.

**Recommendation:** retain the single-process constraint for now. Before adding instances, choose either explicit organization routing to one coordination owner, with leases and failover fencing, or shared coordination state with atomic ownership and cross-instance delivery. The proposed organization/region pinning ADR is a useful starting point, but does not by itself solve multiple instances within a region. Avoid splitting one organization's devices between live revisions during canary testing.

Google documents that WebSocket affinity is best effort and instances must synchronize shared state: <https://docs.cloud.google.com/run/docs/triggering/websockets>. A session-affinity setting alone is not a correctness solution.

### 6. One upload can stall work for other organizations

**Priority: near-term runtime hardening.**

`create_flight_and_archive` calls synchronous `get_weather()` inside its async execution (`main.py:5345`). That function performs `requests.get` (`main.py:5008`). Connection/read timeouts bound parts of the request but do not make it asynchronous. `archive_flight_log` also performs synchronous directory creation, JSON serialization and file writes (`main.py:5216–5235`), potentially against the mounted object store.

**Recommendation:** move weather and storage operations off the event loop using bounded worker capacity, or replace them with suitable async clients. Add an overall deadline and a circuit breaker/cache for weather. Longer term, commit the essential flight record first and enrich it through a durable job, with explicit pending/unavailable weather state. Preserve upload idempotency and recover orphaned files or rows when storage and database commits fail separately.

Test with a deliberately slow weather provider and slow storage while another organization's heartbeats, ownership changes and health requests continue. Passing fast mocked HTTP tests cannot establish this property.

### 7. Additional contention and recovery limits deserve focused work

- `main.py:720–745`: upload locks are keyed by Remote ID or callsign without organization identity and are never removed. Independent organizations can unnecessarily serialize, and unique identities grow the dictionary. Make lock scope organization-aware and bound its lifetime; use database-backed idempotency/constraints before multiple processes.
- `main.py:4590–4614`: every sighting reads all expired sightings and deletes them individually, across the table. This is global retention work on a high-frequency path, not evidence of a data leak. Move cleanup to a bounded scheduled batch; index retention time and verify the query plan on PostgreSQL.
- `main.py:4161–4169`: broadcasts await recipients sequentially without an application send deadline. A slow recipient can hold up later recipients and callers, including the shared expiry loop. Use bounded per-connection output queues, a single writer per socket, explicit overflow policy and stalled-client timeouts.
- `main.py:918`: the operational engine enables SQL echo and lacks the control plane's `pool_pre_ping`/recycle settings (`control_plane.py:1756`). Disable routine SQL parameter logging; apply suitable stale-connection recovery to both pools. Budget total connections, including notification listeners, across processes and deployments.
- Startup runs schema initialization/migrations (`main.py:1188`). Move migrations toward a separate serialized step using expand/contract changes, with previous-version compatibility verified before promotion. Startup DDL is particularly risky when scaling or overlapping revisions.

These are source-based risks; no throughput or resource-exhaustion benchmark was performed.

## RID2Caltopo environment setting

Both platforms already persist a manually editable Tracker URL and credential. That is not the requested environment selector with empty meaning production. Android's existing fields are in `CaltopoSettingsScreen.kt:217` and persisted through `AppConfigStore.kt`/the app-config proto. Apple stores the active URL in defaults and the active token in Keychain (`AppleOrgConfigImporter.swift:314–401`).

The current documentation says the Developer Tools override and prerelease App Links have landed. That does not match these checkouts: Android's manifest and Apple's associated-domains entitlement only register the apex host, and neither Developer Tools implementation contains the described override. The named app branch is not present in the local branch list. Do not infer shipped behavior from the older runbook.

### Proposed user-facing behavior

| Stored selection | Display | Host |
| --- | --- | --- |
| Empty string | Production (default) | `r2c-tracker.com` |
| `prerel` | Prerelease | `prerel.r2c-tracker.com` |

Place the selector in Developer Tools. Prefer these known choices initially; an arbitrary URL field needs additional origin validation and credential rules. Keep organization path separate from environment, so `/ncssar` is retained without making the selector organization-specific. Display a persistent, conspicuous `PRERELEASE` indicator on the main screen and include environment/server build in support diagnostics. The environment choice is local and must not be overwritten by a downloaded organization configuration or exported as a default for other tablets.

Recommended behavior is separate saved enrollment per environment, indexed by canonical HTTPS origin plus organization identity. Restore production credentials when returning to production; do not rewrite the host on a production token and send it elsewhere. If the selected environment has no enrollment, show an enrollment-required state. A revoked prerelease token after a clone refresh must not clear production authorization. The user's preference about retaining enrollments versus enrolling after every switch was requested during this review; this recommendation is not an implemented or confirmed preference.

Switching must disconnect the old coordination socket, stop or explicitly finish active uploads/video, clear environment-specific live peer/session state, and reconnect only after the selected enrollment is ready. Never automatically fall back to production when prerelease is unavailable. Keep pending uploads and their destination environment together so a later switch cannot reroute queued work. Preserve offline maps and unrelated local recordings.

Apply the chosen environment coherently to enrollment/reconciliation, API credentials, coordination WebSockets, uploads/resubmission, FAA proxy, managed config, video control/signaling, thumbnails, browser links and reauthentication. Validate enrollment-returned endpoints against the intended origin; check redirects and old cached links. Region/custom hosts already learned through enrollment need an explicit compatibility rule rather than blindly forcing every organization onto the apex.

Add `prerel.r2c-tracker.com` to Android App Links and Apple Associated Domains, then verify the host's Android association file and Apple association file against the actual signing identities. Source edits alone do not prove OS link handling.

### Required mobile tests

For Android and Apple equally: empty default; selected setting surviving process death/restart; first enrollment in each environment; return to saved production enrollment; wrong-origin QR/token rejection; clone-refresh token invalidation; stale config attempting to change environment; no automatic fallback; queued uploads retaining their destination; switching during an active socket/video/upload; mutual-aid profile handling; and visible prerelease status. Follow unit/build checks with physical tablet switching and server-log verification on both environments.

## Test and release environments

Use three distinct purposes:

1. **Automated integration environment:** disposable PostgreSQL databases plus synthetic organizations; migrations, authorization matrix, cross-tenant collisions, external-adapter failure tests and rollback checks on every candidate.
2. **Persistent prerelease:** `prerel.r2c-tracker.com`, sanitized versioned clone, separate bucket and external-effect controls; real Android/Apple devices, multi-day soak and realistic mixed-load tests.
3. **Production:** only a qualified immutable artifact, following the existing candidate/promotion workflow. Production-facing tagged candidates can still touch production data; they do not replace an isolated test service. Promote code/artifact and forward-compatible migrations, never the prerelease database.

Record the exact commit/image digest, schema and sanitizer versions, clone generation, enabled flags, mobile builds and test results for each soak. Promote the same artifact where configuration is runtime-only; otherwise document and verify the rebuild. Existing guarded-release scripts provide a useful foundation and should remain in the flow.

Feature flags should be server-enforced, scoped by environment and organization, default off for unfinished features, audited and removable. They supplement database/external-effect isolation; they do not provide it. Use short feature branches and frequent integration into a testable candidate to reduce divergence in the current long-lived branch.

## Load, failure and recovery qualification

Measure capacity in workload units, not organization count alone. Begin with 1, 5, 10 and 20 simultaneously active organizations and vary devices, viewers, sightings, upload size/rate and relay traffic. Include identical map/Remote IDs in different organizations and one intentionally noisy organization.

Measure p50/p95/p99 acknowledgement and upload latency, event-loop lag, reconnect time, dropped/queued messages, per-organization errors, DB pool wait/CPU/queries, memory growth and relay bytes. Define acceptable thresholds from operator requirements and a single-organization baseline before claiming a supported capacity. Configure Cloud Run concurrency explicitly from measurements, reserving room for admin/health/upload requests alongside long-lived sockets.

Inject slow/disconnected clients, weather timeout, database restart, object-storage failure, abrupt process termination, expired credentials, interrupted restore, and revision transitions. Include a sustained soak for leak detection and a restore drill with measured recovery time and data loss. Verify that email, billing/webhook and CalTopo writes reach only test destinations.

No defensible current organization-capacity number can be derived from the passing unit suite. Older planning estimates should not be presented as measured capacity.

## Suggested implementation order

1. **Prerelease isolation and recovery:** strict deployment guards, disabled/captured outbound effects, least-privilege IAM, sanitizer, versioned database pair and verified refresh manifest. Acceptance: intentionally supplying production destinations is refused; copied production tokens fail; a failed refresh preserves the previous working environment.
2. **Mobile environment selection:** paired Android/Apple implementation with separate environment state, visible indicator and tested enrollment/link/switch behavior. Acceptance: field devices can move safely between production and prerelease without cross-environment credentials or writes.
3. **Runtime hardening:** bounded weather/storage work, output queues, scoped upload locks, retention batches and DB recovery/logging. Acceptance: injected slow dependencies and noisy organizations do not stop other organizations' coordination.
4. **Qualification and manageability:** PostgreSQL integration tests, mixed-load harness, durable background jobs, environment manifest/banner, feature-flag inventory, operational dashboards and recovery drill. Extract the coordination hub and external adapters incrementally behind existing behavior tests; `main.py` and `control_plane.py` currently total 22,864 lines.
5. **Scale-out decision:** use measured demand to choose more capacity on one deployment, explicit organization partitioning, or distributed coordination. Establish ownership fencing and cross-instance correctness before raising instance count.

## Validation and limitations

- Full current Tracker suite: **352 tests passed**, 33.348 seconds (`python -m unittest discover -s tests -p 'test_*.py'`).
- Focused coordination suite: **52 passed**; tenant schema/persistence suite: **2 passed**. These are included in the full-suite count, not additional distinct coverage.
- Tests include SQLite/mocked and extracted-helper coverage; they are not a production PostgreSQL load test or multi-instance deployment proof.
- No mobile source changes, mobile builds, device installs, database refreshes, deployments or cloud/IAM changes were made in this review.
- Live Cloud Run inspection was attempted but Google Cloud reported reauthentication required. A public HTTPS probe from this machine failed with `SSL_ERROR_SYSCALL`. These failures do not establish that the host is globally down or reveal its current database/IAM bindings. Live service, DNS/TLS, SQL and IAM verification remain necessary before cutover.
