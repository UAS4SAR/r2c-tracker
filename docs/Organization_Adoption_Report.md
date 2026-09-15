# Organization adoption report

Platform administrators can open **Adoption report** from Costs & Organizations,
or visit `/platform-admin/adoption`. Organization users and anonymous visitors
cannot access the report or either export.

Choose daily, weekly, or monthly periods and a reference date. The report compares
the last completed calendar period with the preceding one in the selected IANA
time zone (default America/Los_Angeles). Weeks start Monday. For example,
monthly/as-of September 14 compares August with July. To inspect September,
choose October 1. Period ends are exclusive. Calendar months have different lengths.

Organizations remain named. The table ranks by absolute change in flights, with
current flight count as a tie-breaker. Percentage changes have no numeric value
when the previous count is zero. Review both change and volume rather than
treating percentage growth from a small base as evidence of strong adoption.

- New: first flight in retained history falls within the current period.
- Retained: at least one recorded flight in both periods.
- Reactivated: current activity, none in the previous period, older history exists.
- Quiet: activity in the previous period only; this is not a churn designation.
- Organization retention: retained / previously active organizations; unavailable
  if there were none. Unassigned/legacy activity is excluded from these totals.

Flight totals include uploaded/imported records and zero-duration observations.
This measures recorded use, not installations, signups, payments, or confirmed
mission counts. Duration is the saved observation interval, not necessarily full
airborne time. Deleted records, missing uploads, late uploads, and corrections
affect historical comparisons. No persistent historical confirmation audit is
inferred from these records. Old imported duplicates, if present in the database,
remain separate records; this report does not guess at merging flights.

Pilot counts use matched historical member identities from readiness records.
Legacy callsigns and unresolved identities remain unknown. Pilot numbers in the
flight CSV are HMAC pseudonyms scoped to the organization, using a domain-separated
server signing key. They remain consistent across reports until that key changes.
Names, emails, callsigns, Remote IDs, coordinates, incident names, and precise
flight timestamps are not selected for output. Model descriptions retain stored
shorthand; missing models and model values equal to the flight Remote ID become
unknown. As organization names remain and model labels are historical free text,
exports are internal reports, not anonymized public datasets.

The CSV contains one row per flight in the two compared periods, with organization,
designator, pilot number, model, and duration in minutes. The JSON export includes
the period boundaries, generation time, organization summaries, model counts,
unknown-data counts, and flight rows. Both exports use the same administrator
authentication as the page and disable caching.

## Recurring runs

The report is read-only and accepts reproducible parameters:

`/platform-admin/adoption?period=weekly&as_of=2026-09-14&format=json`

No recurring job or email delivery is enabled by this change. Once cadence and
delivery are selected, scheduled execution should call the shared `load_report`
service with authorized database access and save a dated JSON snapshot. Preserve
the snapshot to compare what was reported at the time; rerunning an old period
can incorporate late uploads. Do not create a public unauthenticated report URL
or reuse browser session cookies as scheduler credentials.

No schema migration, mobile update, or production deployment is included.

## Reading the summary

The first card counts recorded flights, including unassigned rows where present. Every adoption card explicitly counts organizations. “Organizations now inactive” counts organizations with flights in the comparison period but none in the displayed period; it is not a flight total and does not include organizations with no retained history. Named period headings make the completed-period comparison explicit. For example, a monthly reference date of September 15 displays August compared with July, not September month to date. Pilot counts are labeled “Identified pilots” because unmatched historical flight records do not establish distinct pilot identities.
