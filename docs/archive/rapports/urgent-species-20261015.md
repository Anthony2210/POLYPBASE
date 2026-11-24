# Six urgent scientific identity corrections — 2026-10-15

Status: implementation prepared for independent review, **not applied to operational data**.
No commit, integration, deployment, production/Neon/VM connection or operational database execution is authorized by this document.

## Scope and evidence

Working HEAD: `4c98572b0345ec3fd0334559253849d054a489fb`, branch
`fix/urgent-species-20261015`. The working tree was clean before this change.
Main and the companion repository were inspected read-only. The registered
`species-labels` worktree had uncommitted changes in `POLYPBASE_CURRENT_STATE.md`
and `frontend/scripts/test-qr-labels.mjs`; neither was modified or copied.

Authoritative source in `POLYPBASE-ANALYSES`:
`data/raw/Suivi_2026_actualisé_v2.xlsx`.
Verified SHA-256:
`0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160`.

| Persisted global code | Literal workbook identifier / cell | Species label anchor | Decision |
|---|---|---|---|
| CAC-JKA-2.001 | CAC-JKA-2.01 / Semaestomeae!B3 | A3:A20: Chrysaora achlyos | V2 + historical sources + operator approval |
| CAC-JKA-2.002 | CAC-JKA-2.02 / Semaestomeae!B7 | A3:A20: Chrysaora achlyos | V2 + historical sources + operator approval |
| CAC-JKA-2.003 | CAC-JKA-2.03 / Semaestomeae!B11 | A3:A20: Chrysaora achlyos | V2 + historical sources + operator approval |
| CAC-JKA-2.004 | CAC-JKA-2.04 / Semaestomeae!B13 | A3:A20: Chrysaora achlyos | D9 + V2 + operator approval |
| CAC-JKA-2.005 | CAC-JKA-2.05 / Semaestomeae!B17 | A3:A20: Chrysaora achlyos | D9 + V2 + operator approval |
| OCE-TAI-1.002 | OCE-TAI-1.02 / Hydrozoa!B173 | A173:A174: Oceaniidae sp. | D26 + V2 + operator approval |

The existing `historical_2026.source.canonical_box_code` explains the source
`.01` to persisted `.001` normalization. This command does not rename any code.
Historical CAC .01/.02 are also explicitly associated with achlyos in 2023–2025;
.03 is in the original 2026 achlyos block. Earlier `Chrysaoa` spelling is literal
historical evidence, not a new species reference to create.

Ledger: `docs/SPECIES_AAA_ISSUES_AND_PROPOSED_CORRECTIONS.md` in the companion.
Verified companion HEAD: `4623fb1a9f689ce1d65d32154bbdd1306e245cc2`.
Ledger SHA-256:
`92e589b519e4a54eb9d094e8a6ef3dce22c6f833028481552b4c66957fb44dc1`.
D9 explicitly addresses CAC .04/.05 and separately reserves CCP white/pink/pink
striped for strains 1/2/3. It does not resolve CAC-JKA-1.xxx. D26 identifies
OCE-TAI-1.002 as Oceaniidae sp. D22 confirms **THY**, not THI, for
Thyroscyphus sp.2; no Thyroscyphus correction is included here.

## Cause and implementation

Box API and label metadata derive Species from `Box.strain.species`; QR targets
use the stable Box PK. The operator-confirmed wrong scientific associations
therefore require a persisted relation correction, not a label/UI workaround.
Historical source label conflicts exist, but the exact execution that introduced
the operational associations was not investigated in a live database.

The existing historical importer can create references/Boxes and import science;
it is not a safe six-relation applicator. Its immutable reviewed manifest is left
unchanged. A dedicated bounded service and management command reuse the existing
Django transaction, organization membership, strain eligibility and AuditLog
patterns. They update **only** the six selected `Box.strain_id` fields and append
one audit per real change. No Species, Strain, assignment, Box or measurement is
created, renamed or globally reclassified.

The operator must explicitly select existing target Strain PKs. They must have
exact Species names and codes `CAC-JKA-2` / `OCE-TAI-1`, be institution-eligible,
and have consistent institution Species assignments `CAC` / `OCE`. Existing
local identities must have consistent owned organization/Species/provenance
relations. An unowned legacy strain without a local identity remains eligible
only through existing institutional Box usage. No prefix guesses a species or
selects a target PK. Exact duplicate `(species, code)` references are prohibited
by the existing database constraint; ambiguous/absent references are never chosen
automatically.

A simulation reports all six entries, organization, complete Box snapshots,
current/proposed Strain and Species identities, local assignments, classification,
source cells, fingerprints and plan SHA-256. Missing Boxes/references, unexpected
current identities, foreign strains or inconsistent assignments block application.

Application requires explicit flags and an active user with an active Admin
membership in the selected organization, reloaded under lock (superuser alone is
not sufficient). One atomic transaction locks the organization, membership, actor,
six Boxes, current/target Strains, Species, organization assignments, local
identities and legacy eligibility witness Boxes. All six must exist in the initial
locked selection. It rebuilds the complete plan and requires an exact hash match
before writing. Any conflict, stale state, write or audit failure rolls back the
whole batch. Database deadlock/lock errors also roll back; do not bypass a failure.

Each correction audit retains old/new references, actor/organization, Box PK/code,
reason, decision, source cells, V2/ledger fingerprints, operation ID, plan hash and
the complete JSON-normalized reviewed plan. A fresh plan after correction reports
`already correct`; applying that reviewed plan changes nothing and adds no audits.
Reusing the pre-correction hash is stale and is rejected.

## Future authorized operator workflow — not executed here

Run from `backend/` only after separately authorizing the intended environment.
The angle-bracket values below are required substitutions, **not known production
IDs**; no operational references were queried during implementation.

Initial read-only inventory of the six entries, without choosing target references:

```text
uv run python manage.py correct_urgent_species --organization-id <AQUARIUM_ORGANIZATION_PK>
```

Reviewed read-only plan with independently verified existing target references:

```text
uv run python manage.py correct_urgent_species --organization-id <AQUARIUM_ORGANIZATION_PK> --cac-strain-id <ACHLYOS_CAC_JKA_2_STRAIN_PK> --oce-strain-id <OCEANIIDAE_OCE_TAI_1_STRAIN_PK>
```

A separately authorized application would use the exact same scope/reference
arguments, plus `--apply --actor <ACTIVE_ADMIN_USERNAME> --expected-plan-hash
<REVIEWED_PLAN_SHA256>`. Do not use `config.test_settings` for an intended
operational action: it deliberately points at an isolated in-memory database.

Prerequisites before any operational application:

1. Independent code review and separately authorized integration/deployment.
2. Verify transaction/row-lock behavior on a disposable isolated PostgreSQL QA
   database; SQLite validation does not prove real PostgreSQL concurrency.
3. Separately authorize access to the intended institution/environment and obtain
   its actual organization, administrator and target Strain identities.
4. Confirm both target references and CAC/OCE assignments already exist and are
   consistent. If absent/conflicting, stop: resolve references through a separately
   reviewed and authorized operation; this command will not create or rewrite them.
5. Capture and independently review a fresh full plan. All six entries must be
   `correction needed` or `already correct`; inspect source evidence and exact PKs.
6. Authorize the mutation explicitly, following the repository operational and
   backup procedures. Use its exact reviewed hash; any changed state needs a new
   simulation and review, never a force option.
7. Verify persisted API/label metadata and audits after a separately authorized
   application. Do not infer correction success from this implementation report.

## Validation

Only `config.test_settings`, with `POSTGRES_DB=''` and
`POLYPBASE_TEST_POSTGRES=0`, was used. Configuration was explicitly confirmed as
SQLite `:memory:` before validation. All written test data are synthetic.

- 30 focused correction tests passed, including all-table immutability, real zero
  measurements, complete audit evidence, stale hashes, rollback on the sixth
  audit failure, active Admin permissions, legacy eligibility and serializer/QR
  consistency. The first run exceeded 120 seconds; the rerun completed successfully.
- 55 existing historical-import and organization strain-scoping tests passed.
- Django system checks passed; migration drift check reported no changes.
- `git diff --check` passed (new files also checked separately for whitespace).
- PostgreSQL concurrency not executed: local Docker availability check timed out.
  No shared or operational database was substituted.
- No frontend contract changed; no frontend build or browser QA was needed.

Implementation is ready for independent review, **not approved for operational
application**. Actual target PKs, their availability and operational plan remain
unverified by design.
