# Product and technical decisions

Each entry records a decision and its scope. It does not prove deployment or authorize changes to production. Dates refer to the recorded decision or the milestone that provides evidence for it.

| Date or milestone | Decision | Scope and reference |
|---|---|---|
| Project invariant | Zero is a measurement, never missing data. Django decides permissions and transitions for the active institution; preserve authors, lineage, and audit records. | [AGENTS.md](../AGENTS.md) |
| October 14, 2026 status | One canonical reading per box and ISO week. A conflicting POST does not correct the existing reading; correction is explicit and audited. | [Measurements](reference/measurements-temperatures-probes.md) |
| October 14, 2026 status | Week-only target: Monday is the ISO key, the day is unknown; do not invent a day. If an intervening subculture makes ordering ambiguous, ask for the actual day or cancel; no override. | Target, not current storage: [imports](reference/imports-exports-transfers.md) |
| October 14, 2026 status | One ordinary movement per ISO week in Europe/Paris. Correct the existing event in the Origin → Destination banner. A period boundary is not proof of movement. | [Boxes](reference/boxes-lifecycle.md) |
| October 14, 2026 status | Administration is desktop-only. Retire alerts and the large shared catalog; preserve the previous alert records. | [Frontend](reference/frontend.md) · [Architecture](reference/architecture.md) |
| October 29, 2026 | Condense after 14 days by default in 15-minute buckets. Condensed points are deleted irreversibly. Preserve the last exact instant without reconstructing an unknown value. | [Probes](reference/measurements-temperatures-probes.md) |
| November 4 and 17, 2026 | Base thermal checks on actual measurements over a four-hour raw window. Do not interpret silence or a deviation as an alert. | [Thermal checks](reference/measurements-temperatures-probes.md) |
| November 12, 2026 | Separate deployment, backup, and migration authority. A pending schema change blocks the release. | [Operations](operations/deployment-operations.md) |
| PR #19, November 17, 2026 | DNA evidence is declared and revocable for each box, without inheritance or automatic identification. | [Boxes](reference/boxes-lifecycle.md) |
| PR #24, November 19, 2026 | Do not resolve historical species implicitly. Manage the institutional profile explicitly without rewriting historical data. | [Taxonomy](reference/taxonomy-species.md) |
| Donation proposal milestone | A proposal is not a transfer: the recipient gains neither access to source history nor ownership. | [Donations](reference/imports-exports-transfers.md) |
| Isolated demo milestone | Plan for an independent instance; do not use a real snapshot or open access before approval and verification. | [Isolated demo](operations/isolated-demo.md) |
| November 24, 2026 | Remove the Organizations, References, and Taxonomy administration sections. | Keep the Species workflow and models; remove only the APIs specific to the retired sections. |
| November 24, 2026 | Anthony confirms production execution of the one-off operations: CCA-EOB V3 and reassignment of five measurements, Box 1902, PCA, CLA-SME, urgent and variant species corrections, test-data cleanup, HS deactivation, historical temperature batches, the 2026 import, and movement reconciliation. | Operator confirmation; archive one-off tooling, not data protections or live dependencies. |
| November 24, 2026 | Remove `'unsafe-inline'` from `script-src` only after confirming there are no inline scripts. | Check the frontend build and served HTML templates; leave `style-src` unchanged. |
| November 25, 2026 | Use English for code, comments, docstrings, documentation (including the user guide), agent instructions, skills, commit messages, and PR descriptions. Use FR/EN/JA i18n for all user-facing text. Leave the root `README.md` and archived documents unchanged. | Keep documentation clear for both agents and developers. |
| November 25, 2026 | Use one comment standard for agents and people. Reuse and extend existing code rather than duplicating it. | Applies to new and changed code. |

See [project status](STATUS.md) for open questions and the [archives](archive/README.md) for dated evidence.
