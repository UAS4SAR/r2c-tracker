# ADR 0001: Pin each organization to one tracker region via base URL

- **Status:** Proposed
- **Date:** 2026-09-06
- **Context:** Multi-org hosting across US regions; avoid rewriting tenancy before modularizing `main.py`.

## Decision

**Option A — org pinned to one region.**  
Each organization is served by exactly one tracker deployment. Devices learn that host from enrollment / org config (`tracker.base_url` / `tracker_url_prefix`). There is no cross-region request router in v1.

## Consequences

- Scale-out = more regional Cloud Run (or equivalent) deployments of the **same** codebase with different env (DBs, secrets, org set).
- Moving an org between regions is an ops runbook (export/import, re-issue or update enrollment/config, drain WS) — not a transparent failover.
- Clients need **no** protocol change for multi-region; only URL/config.
- Control-plane DB may be per-region initially (simplest). A later ADR may introduce a shared global control plane if needed.

## Rejected for now

- **Option B — global control plane + regional coordination nodes:** more moving parts; revisit after coordination is extracted from `main.py`.

## References

- Architecture review §3–4 (enrollment returns `tracker.base_url`)
- Cleanup plan Phase 2
