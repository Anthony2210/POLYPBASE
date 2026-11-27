# Polypbase status — November 27, 2026

## Repository and production

- Production runs `68ca288` since November 25, 2026 (last deploy, reported by the product owner). The deployed schema and service state were not rechecked here.
- `main` has moved since then. Merged and not deployed yet: PRs #53–#56 and #58–#63 (#57 was closed unmerged), plus Dependabot PRs #29, #30, #44, and #46.
  - #53: agent skills for review, UI, and debugging.
  - #54: CI tests on Python 3.13, matching production.
  - #55: backup broker decoupled from per-commit release approval.
  - #56: Node 22 pinned for local tools; test cleanups.
  - #58: versioned local PR helpers and their documentation.
  - #59: malformed dates rejected in ISO week helpers; TypeScript strict mode enabled.
  - #60: first names removed from the repository.
  - #61: labels include active boxes and recorded strain names.
  - #62: sharing concurrency test expects a timeout.
  - #63: i18n catalogs split into reviewable sections; legacy translation workflow retired.
  - Dependabot: setup-uv, upload-artifact, Python, and npm dependency bumps.
- Backup broker: the code no longer requires a per-commit approval (PR #55), but the one-time root cutover on the VM is not done. Until it is done, the old per-deploy approval step still applies.
- CI and local Python follow production: 3.13. Node 22. TypeScript strict mode is on.
- Local PR helpers: `scripts/pr_tools.ps1`, documented in the [local pull request workflow](operations/local-pr-workflow.md).
- [Full prior status history](archive/etat-historique-2026-10-14_2026-11-16.md) and [deployment rules](operations/deployment-operations.md).

## Work in progress

- Weekly record tracking backend: planned weekday, anomalies, and Overview counters. Being built; not available in the app.
- Translation follow-ups: AI-written EN and JA text is marked for review.
- Backup retention: in progress.
- Review-sheet sync: planned; not built yet.

## Code reviewed for PR4

| Domain | Availability and limits |
|---|---|
| Administration | Team, Inventory, Species, Locations, Exchanges, and History on desktop. The former Shared References and Institutions sections are no longer part of the workflow. |
| Boxes | DNA validation via a declared, revocable link without inheritance; old Local IDs and historical revisions have separate registries. |
| Exchanges | Propose and view a donation in React, without accepting it or executing a transfer. External sharing is read-only via the API, with no React screen; CSV v1 was retired. |
| Species | Institutional catalog and detail pages are available. Existing species are managed explicitly even without a profile; implicit resolution was retired (`410` after authorization). AAA preview via the API; application is internal only. |
| Measurements | Restricted API applies exact-date imports; no React workflow. A week without an exact day cannot be persisted. |
| Probes | iMinilide collection and the four-hour check exist in code; physical connectivity and the production timer are unverified. |
| Demo | Isolated foundation only; no authorization to use a real snapshot or open access. |

## Decisions or checks still needed

- The schema actually in service was not rechecked. Verify the earlier HSTS observations, probes, and backups separately; do not assume they were resolved or are still current.
- Before enabling week-only precision, decide ordering around subculture, handling of unresolved scientific states, and location and temperature assignment without inference.
- Before enabling donation acceptance, decide stock and status policies, destination materialization, and history access.
- The [one-off campaign records](archive/README.md) are historical evidence, not procedures to rerun. Removing their tools does not verify production data. Any new data recovery requires its own evidence and approval.

See the [durable decisions](DECISIONS.md) and [domain references](INDEX.md).
