# Feature freeze — modularization window

**Effective:** upon merge of this note / Ken’s go-ahead  
**Repos:** `UAS4SAR/r2c-tracker`, `UAS4SAR/RID2Caltopo` (Apple tree included)  
**Baseline:** tracker `22254cb` (v1.4.87) — unit + `qualify_release.sh` green on assistant host; image builds remain on the release Mac. Android `3ebd688` (2.2.4 build 185).

## Why

`main.py` still owns too many roles for multi-org / multi-region hosting. Android and Apple need shared contracts more than new features. We pause the feature firehose and harden boundaries.

## Freeze (default deny)

- New operator-facing features (AD modes, map toys, new video modes)
- New routes bolted onto `main.py`
- New peer/MQTT capabilities
- Server-side CalTopo writes
- Broad UI redesigns

## Allow list

- **P0 field bugs** (crash, wrong CalTopo owner, enroll/auth break, data loss)
- Security / credential revoke / Connect Key rotation runbooks
- Protocol clarifications that document or tighten existing `R2C_PROTOCOL.md`
- Test / CI / deploy hardening
- Parity deltas already listed in `apple/ANDROID_UI_PARITY.md` (continuity only)
- Apple `R2CCore` / portable contract extraction
- Tracker modularization PRs (coordination → flights → video)

## Decision rule

If a change doesn’t reduce `main.py` coupling, improve module boundaries, improve shared contracts, or fix a P0 → **defer**.

## Cadence

- Weekly freeze review
- Weekly demo: extracted module or closed parity slice — not a new feature
- P0 on-call rotation so the monolith isn’t one person’s full-time job

See also: `docs/adr/0001-org-region-pinning.md`, `docs/COORDINATION_HUB_EXTRACTION.md`.
