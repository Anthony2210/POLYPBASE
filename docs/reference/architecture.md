# Product and architecture

## Purpose and users

Polypbase tracks cultures of jellyfish and other organisms: boxes, biological reference data, records, thermal locations, temperatures, lineages, transfers, and exports. The product originated at the Aquarium de Paris, but its accounts and domain data are structured for multiple institutions.

The main users are laboratory technicians and animal care staff, institution administrators, and people consulting the data for scientific purposes. In the lab, landscape tablets and phones are mainly used to find or scan a box, view its details, and enter data. In the office, desktop supports dense views, charts, exports, and administration. Detailed interface rules are in [`frontend.md`](frontend.md).

## Technical overview

| Layer | Location | Responsibility |
|---|---|---|
| Backend | `backend/` | Django, Django REST Framework, sessions, permissions, business rules, ORM, transactions, auditing, exports, and QR codes |
| Frontend | `frontend/` | React, TypeScript, Vite, navigation, data entry, charts, and device-specific layouts |
| API | `/api/` | JSON between React and Django, session authentication, and CSRF protection |
| Data | PostgreSQL in production | Persistence and constraints; SQLite is used for standard isolated tests |
| Deployment | `deploy/` | Preflight, backup, update, build, schema checks, dedicated migrator, and service checks |

The frontend does not use React Router. `frontend/src/App.tsx` maps the URL to route state and uses the History API. Reusable calls go through `frontend/src/api/client.ts`, which handles the organization context and CSRF tokens for mutations, among other things. `PilotageView`, `CreateBoxPanel`, `QrSearchModal`, and `BoxPage` are now separate modules; App retains orchestration, mutations, and caches. The institution guard does not guarantee freshness within an institution; see [`frontend.md`](frontend.md).

## Backend domains

- `accounts`: sign-in, password recovery, invitations, memberships, and roles.
- `organizations`: institutions and partnership models.
- `taxonomy`: taxa, species, provenance, strains, and translations.
- `cultures`: boxes, zones, locations, movements, transplants, lineages, and transfers.
- `measurements`: biological records, observations, probes, temperatures, and salinity.
- `exports`: selection, preview, CSV generation, and import/export logs.
- `sharing`: external read-only access to a box, invitations, and frozen captures.
- `audit`: action log (`AuditLog`); the historical `Alert` model and storage are retained but inactive.

This breakdown shows where the code lives; it does not replace the domain boundaries documented in the other references.

`InstitutionSpecies` stores the local scientific name and AphiaID alongside the shared `Species` reference. Migration `taxonomy.0012` does not convert any historical data or change strain foreign keys or codes. Administration > Espèces uses institution-scoped APIs. Historical entries without profiles are read-only, and the resolution POST has been retired (`410` after authorization). This does not switch all consumers to the institution-specific name. Model, read-only readiness diagnostic, guarded rollback, and list of gates: [`../../backend/INSTITUTION_SPECIES_FOUNDATION.md`](taxonomy-species.md).

## Product decision: retirement of alerts

Alertes is no longer an active feature: no workflow for viewing or resolving alerts, and no automatic alert generation or synchronization, is part of the product contract. Measurements remain factual; changes in counts, departures from a setpoint, or missing data must not produce inferred operational or biological warnings.

`AuditLog` and the Actions feature remain active and must preserve traceability, including historical entries related to former alerts. The `Alert` model, its table, data, and migrations are retained but inactive. Any change to their status requires a separate, explicitly authorized, non-destructive decision; retiring the feature does not authorize deleting data or rewriting migrations.

## Core vocabulary

| Term | Meaning |
|---|---|
| `Organization` | Institution or aquarium that owns a workspace |
| `OrganizationMembership` | A user's membership in an institution, with its own role |
| `Box` | The central culture unit, identified by a global code |
| `Species` / `Strain` | Shared species and operational strain normally scoped to an institution; legacy cases are explicitly bounded |
| `BiologicalMeasurement` | A box record, unique per ISO week, with a stored date, precision, and distinct observer and origin fields |
| `ThermalZone` | A thermal location in an institution |
| `BoxLocation` / `BoxMovement` | A location period and a recorded event with a type; a period boundary is not proof of a physical move |
| `DailyTemperature` | Daily temperature aggregate for a zone |
| `SubcultureEvent` / `BoxLineage` | Transplant and parent-child relationship |
| `AuditLog` | Record of an action, its author, and its institution |

Do not expand biological definitions without domain approval.

## Configuration and languages

Environment-specific settings and secrets are supplied through environment variables. Invitations and password recovery use the backend email configuration. Never read or reproduce the contents of an `.env` file.

The interface defaults to French; Japanese is available with partial coverage and an English fallback. Content languages are separate. See [terminology and i18n](terminology-i18n.md) for provenance and fallback rules.

## Feature boundaries

The presence of a module or route does not mean that a workflow is mounted in React or that the feature is deployed. See the [dated project status](../STATUS.md) for release information and the [API map](api.md) for routes. The domain references distinguish active workflows, internal foundations, and retired interfaces.

## Further reading

- Institution security: [`organizations-permissions.md`](organizations-permissions-sharing.md).
- Culture lifecycle: [`boxes-lifecycle-locations.md`](boxes-lifecycle.md).
- [Data model and traceability](data-model.md).
- General setup: [`../../README.md`](../../README.md).
