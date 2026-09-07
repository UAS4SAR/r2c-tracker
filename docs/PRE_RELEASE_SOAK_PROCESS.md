# Pre-release soak process (modularization)

Agreed working process while mainline continues bugfixes.

1. Land work on long-lived branch `project/modularize-coordination` (rebase/merge `main` often).
2. Deploy a **pre-release** Cloud Run (or equivalent) service from that branch with **cloned** operational + control-plane databases — never production writes from experiments.
3. Point field tablets via RID2Caltopo **Developer Tools → TrackerUrl** (non-blank must show the obvious Main Screen indicator that the default tracker is not in use).
4. Soak: enroll/coord/upload/video checklist against a chosen org on the pre-release URL.
5. Gates before merge to `main`: unit suite + `qualify_release.sh` on the branch, plus Ken’s comfort with soak results.
6. Merge to main only when ready — not on a calendar.

Hygiene/docs PRs may still land on `main` independently. Large extractionsctions stay on this branch until soak passes.

Baseline tag: `tracker-pre-modularize` (v1.4.87 / `22254cb`).
