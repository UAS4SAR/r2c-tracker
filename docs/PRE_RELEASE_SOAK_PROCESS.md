# Pre-release soak process (modularization)

Agreed working process while mainline continues bugfixes.

1. Land work on long-lived branch `project/modularize-coordination` (rebase/merge `main` often). Matching branch exists on RID2Caltopo.
2. Deploy a **pre-release** Cloud Run (or equivalent) service from that branch with **cloned** operational + control-plane databases — never production writes from experiments.
3. Preferred host shape: `https://prerel.r2c-tracker.com/<org>` (same path layout as production).
4. Point field tablets via RID2Caltopo **Developer Tools → TrackerUrl** (non-blank must show the obvious Main Screen indicator that the default tracker is not in use). Example: `https://prerel.r2c-tracker.com/ncssar`.
5. Soak: enroll/coord/upload/video checklist against the chosen org on the pre-release URL.
6. Gates before merge to `main`: unit suite + `qualify_release.sh` on the branch, plus Ken’s comfort with soak results.
7. Merge to main only when ready — not on a calendar.

## Current soak targets

| Item | Value |
|------|--------|
| Org | **NCSSAR** (`ncssar`) |
| Platforms | **iOS and Android** |
| TrackerUrl | `https://prerel.r2c-tracker.com/ncssar` |
| Tracker branch | `project/modularize-coordination` |
| App branch | RID2Caltopo `project/modularize-coordination` |

## `prerel.r2c-tracker.com` feasibility

**App code: low effort.** Enrollment and API host checks already allow the apex and any subdomain:

- Android `TrackerEnrollmentClient.trustedHost`: `r2c-tracker.com` or `*.r2c-tracker.com`
- Apple `R2CCore` / org importer / NOTAM paths: same rule

So `https://prerel.r2c-tracker.com/ncssar` works for TrackerUrl override, HTTPS API, and WS without an allowlist change.

**Deep links / App Links: app host added.** RID2Caltopo `project/modularize-coordination` registers `prerel.r2c-tracker.com` in Android App Links and Apple Associated Domains (alongside apex). Once prerel is deployed, the same `/.well-known/assetlinks.json` and `apple-app-site-association` routes on the tracker serve verification for that host. Until DNS/TLS for prerel exists, soak can still use Developer Tools TrackerUrl.

**Ops: moderate.** Separate Cloud Run (or revision) from prod; DNS + custom domain mapping + TLS for `prerel.r2c-tracker.com`; clone ops + control-plane DBs (at least NCSSAR-scoped); point control-plane / MediaMTX / env URLs at the prerel host. Do not share prod write paths.

Hygiene/docs PRs may still land on `main` independently. Large extractions stay on this branch until soak passes.

Baseline tag: `tracker-pre-modularize` (v1.4.87 / `22254cb`). App baseline: `android-pre-modularize` (2.2.4 build 185 / `3ebd688`).
