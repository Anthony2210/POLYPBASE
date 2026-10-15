# Scientific anomaly diagnostic — 2026-10-15

Status: implemented and tested on synthetic data only; **never executed against an operational database**.
It writes nothing, calls no lifecycle or import service and creates no Species, Strain or Box.

## Command

Run from `backend/`, only against an environment that was separately authorized:

```text
uv run python manage.py diagnose_scientific_anomalies --organization-id <ORGANIZATION_PK> [--mode urgent|hs|all]
    [--cac-strain-id <REVIEWED_PK>] [--oce-strain-id <REVIEWED_PK>] [--summary-only]
```

`--organization-id` is required; there is no default organization. JSON (sorted keys, ASCII, no timestamps,
`report_sha256` for drift detection) goes to stdout and a short summary to stderr. A completed diagnosis exits 0
even when entries are BLOCKED. Every SQL statement runs through a guard that refuses writes and row locks.

## What it reports

`urgent`: for the six Boxes of `correct_urgent_species`, the persisted Box (PK, status), current Strain and Species,
ownership class, applicable `OrganizationSpeciesCode` rows, `LocalStrainIdentity` and provenance consistency,
`eligible_strains` membership, an AAA prefix survey (H1 evidence, not proof), exact-name candidate references
(`candidate_only`), the verdict of the real `build_plan` mechanism and each blocker with a stable code. With no
explicit target PK every entry is BLOCKED (`TARGET_NOT_SUPPLIED`); `readiness_if_candidate_supplied` is hypothetical.

`hs`: the 35 literal labels through the existing `build_deactivation_plan`, approved remaps, Box PK/status, latest
`BiologicalMeasurement` (a real `0` stays `0`; `null` strobilae stay `null`; no measurement is `latest: null`), and
`deactivation_safety` of `UNSAFE`, `UNVERIFIED` or `NOT_APPLICABLE`. It is never `SAFE` and never a proposal.

## Organization isolation

Every Strain serialized in the report is first proven eligible in the selected Organization with `eligible_strains`
(owned, or unowned legacy and already used by that Organization's Boxes). Otherwise the report only says
`available_in_organization: false`:

- A supplied target PK that is missing or inaccessible gives the same `TARGET_UNAVAILABLE_IN_ORGANIZATION` blocker;
  no foreign attribute, and no existence distinction. The planner receives "no target" for it, so READY/BLOCKED is unchanged.
- A Box is looked up only inside the Organization. There is no probe of other institutions: a Box of another
  institution is reported as `BOX_MISSING`.
- A Box referencing an inaccessible Strain reports `CURRENT_STRAIN_UNAVAILABLE_IN_ORGANIZATION` and nothing else.
- `LocalStrainIdentity` assignments and provenance outside the Organization are reduced to
  `SPECIES_ASSIGNMENT_OUTSIDE_ORGANIZATION` / `PROVENANCE_ASSIGNMENT_OUTSIDE_ORGANIZATION`, with no ID or code.
- Candidates come from `eligible_strains` only. Planner conflict text keeps a Strain PK only if that Strain is available.
- The global Species catalog is unchanged; the supplied PK echoed in the report is operator input.

## Source evidence

Static V2 facts (workbook SHA-256 `0d5cd16e…b160`, extracted read-only) are labelled `origin: v2_workbook_*` and
are separate from persisted facts. Only the five known contradictions are embedded; the workbook is not read at runtime.
A literal identifier is merged over several rows (one polyp/ephyrae pair per temperature sub-block), so each entry lists
its `merged_range`, every `blocks` entry with values, and `last_week_with_value` over the whole range.
`PCA-JKA-1.05` (`Semaestomeae!B149:B152`, canonical `PCA-JKA-1.005`) holds weeks 1-2 at 5 °C (rows 149-150) and weeks
3-40 at 10 °C (rows 151-152), so its last source observation is 2026-W40. These are source observations only: they say
nothing about the persisted Box status, its persisted measurements or whether it may be deactivated.
`CLA-JKA-1.009` → `CLC-JKA-1.009` is not an approved remap and appears only as `candidate_remap`.
