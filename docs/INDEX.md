# Polypbase documentation

Polypbase uses Django REST Framework and React to track cultures across institutions. Read only the pages relevant to your task, then verify the implementation, migrations, and tests.

## Sources of truth

1. An explicit product decision that applies to the task, subject to the invariants in [AGENTS.md](../AGENTS.md).
2. Code, migrations, and tests for the behavior of the version under review. Observed behavior is not necessarily intended; ask for a decision when they disagree.
3. The references below for contracts, [Status](STATUS.md) for dated findings and caveats, and [Decisions](DECISIONS.md) for product and technical decisions.
4. [Archives](archive/README.md) are dated evidence, not current instructions. [Design sources](sources/) do not describe the deployed schema.

Use [Contributor conventions](CONVENTIONS.md) for coding and review standards.
Update this index when adding, moving, or retiring a current guide, and link
straight to the canonical page rather than through a compatibility stub.

## Find the relevant guide

| Task | Authoritative guide |
|---|---|
| Write or review code, commits, PRs, or documentation | [Contributor conventions](CONVENTIONS.md) |
| Understand the product, applications, and repository | [Architecture](reference/architecture.md) |
| Look up a Django route | [Generated API reference](reference/api.md) |
| Accounts, roles, invitations, scope, and external sharing | [Organizations, permissions, and sharing](reference/organizations-permissions-sharing.md) |
| Create, qualify, move, or reactivate a box; trace its identifiers, DNA, or lineage | [Box lifecycle](reference/boxes-lifecycle.md) |
| Read, simulate, or preserve historical revisions | [History and revisions](reference/history-revisions.md) |
| Readings, precision, zero, temperature, probes, or salinity | [Measurements, temperatures, and probes](reference/measurements-temperatures-probes.md) |
| CSV, planner, exports, or institutional donations | [Imports, exports, and transfers](reference/imports-exports-transfers.md) |
| Species, strains, AAA/BBB codes, and WoRMS | [Taxonomy and species](reference/taxonomy-species.md) |
| React, routing, usability, or CSS | [Frontend](reference/frontend.md) · [Style organization](../frontend/src/styles/README.md) |
| User-facing text, FR/EN/JA catalogs, and usage checks | [Terminology and i18n](reference/terminology-i18n.md) · [Application translation register](translation-review/application/README.md) |
| Django models, constraints, and traceability | [Data model](reference/data-model.md) |
| Set up a development environment | [Getting started](../README.md) |
| Windows worktrees, draft PRs, merges, Dependabot, and cleanup | [Local pull request workflow](operations/local-pr-workflow.md) |
| Tests, browser QA, and migrations in an isolated environment | [Development and QA](operations/development-qa.md) |
| Release, backups, dedicated migrator, or VM-side iMinilide collection | [Deployment and operations](operations/deployment-operations.md) |
| Demo and isolation requirements | [Isolated demo](operations/isolated-demo.md) |
| Use Polypbase in the laboratory or Administration | [User guide](user/guide.md) |

### Technical annexes (as needed)

- [Historical movements](reference/annexes/historical-movements.md)
- [Institutional species foundation](reference/annexes/institution-species-foundation.md)
- [Quantitative subculture](reference/annexes/quantitative-subculture.md)
