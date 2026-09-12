# Readiness changes in the Organization audit trail

The Organization audit trail now includes these events:

| Change | Audit event | Category |
| --- | --- | --- |
| Member pilot callsign or qualification edit | `member.pilot_profile_updated` | Administration |
| Operating profile edit | `organization.operating_profiles_updated` | Administration |
| Aircraft service report | `organization.aircraft_service_reported` | Administration |
| Flight readiness correction | `recording.readiness_corrected` | Recording |

Each successful write saves the existing history and its central audit summary in the same database transaction. A failed write leaves neither. Permission checks and service-report retry behavior are unchanged.

The summary records the original editor and time, affected member/aircraft/profile/flight, and a reference to the detailed history. Pilot edits include old/new callsigns and changed field names. Certificate values and full flight snapshots stay in their existing histories.

## Existing history

On startup after deployment, `backfill_readiness_audit` adds missing audit entries from the four existing history tables. It uses the same deterministic event IDs as live writes, so repeat runs and overlapping startups do not create duplicates or overwrite retention holds. Original actor IDs, recorded email addresses and server timestamps are preserved, including for editors who no longer exist. Aircraft service reports use the original server receipt time, not a client-supplied time.

The backfill processes batches of 250 records and respects the existing 365-day audit retention window; it does not resurrect expired events. Histories whose organization no longer exists are excluded. Malformed history records are skipped and counted instead of inventing attribution. Startup logs report inserted, existing, expired and invalid counts without logging history contents.

No schema migration or separate manual production data edit is required. The code takes effect, and existing production histories are backfilled, when the updated Tracker is deployed.

## Validation

- All 393 unit tests passed, including all four live audit paths, service retry deduplication, repeatable backfill, original attribution/timestamps, organization filtering, rollback, malformed/expired history, and preservation of audit retention holds.
- Local application startup, HTTP and organization-scoped coordination smoke tests passed.
- Rollback-compatible migration check passed; no schema changes are introduced.
- Static security scan and new-file secret scan passed. PostgreSQL conflict-ignore insert compiled successfully; backfill integration tests used SQLite.
- This source update has not been published to production.
