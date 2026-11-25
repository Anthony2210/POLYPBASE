# Polypbase status — November 24, 2026

## Repository and production

- `main` observed in this repository: `9e00e2c` (audit changes integrated through PR #37). Git integration does not prove deployment; production was not checked here.
- **Current production release: unverified.** The latest release explicitly reported in the preserved historical log is `cb07a2c` (November 3, 2026, operator report); this is not evidence of the current state. Running data corrections is separate from shipping code.
- [Full prior status history](archive/etat-historique-2026-10-14_2026-11-16.md) and [deployment rules](operations/deployment-operations.md).

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

- Before a release, establish the SHA, date, and schema actually in service. Verify the earlier HSTS observations, probes, and backups separately; do not assume they were resolved or are still current.
- Before enabling week-only precision, decide ordering around subculture, handling of unresolved scientific states, and location and temperature assignment without inference.
- Before enabling donation acceptance, decide stock and status policies, destination materialization, and history access.
- The [one-off campaign records](archive/README.md) are historical evidence, not procedures to rerun. Removing their tools does not verify production data. Any new data recovery requires its own evidence and approval.

See the [durable decisions](DECISIONS.md) and [domain references](INDEX.md).
