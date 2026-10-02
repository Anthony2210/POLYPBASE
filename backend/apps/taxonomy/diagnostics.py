"""Privileged, cross-organization observations; never call from institution APIs.

This scan performs ORM reads only, without row locks or a snapshot guarantee.
It is not a reservation. Authorized pre-migration execution must take place in
an operationally stable context: concurrent writes during or after this scan
can invalidate the evidence. A normal Django/SQLite transaction would not prove
that these multiple reads form a production-consistent snapshot.

The report is kept in memory (taxonomy and Box identity metadata, not histories).
Counts include inactive history. No result authorizes a merge or correction.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from django.db.models import Count

from apps.cultures.models import (
    Box, BoxLineage, BoxLocation, BoxMovement, BoxTransfer, SubcultureEvent,
    TransferItem,
)
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization

from .models import (
    BiologicalProvenance, GlobalStrainIdentity, LocalStrainIdentity, OrganizationProvenanceCode,
    OrganizationSpeciesCode, Species, Strain,
)


DIRECT = "DIRECT_CONSTRAINT_BLOCKER"
REVIEW = "INTEGRITY_REVIEW_REQUIRED"
INFO = "INFORMATIONAL_FUTURE_NAMESPACE"
SCHEMA = "SCHEMA_INTEGRITY_BLOCKER"


@dataclass(frozen=True)
class ConsolidationImpactScope:
    """Reference evidence for conflicts/corrections, not all diagnostic findings.

    Null ownership and missing local identity alone do not imply consolidation.
    Their category-specific evidence remains authoritative unless the Strain is
    also implicated in an eligible identity conflict or integrity anomaly.
    Inclusion here does not establish that a merge or correction is required.
    """

    eligible_categories: tuple[str, ...] = (
        "CANONICAL_OWNED_DUPLICATES",
        "GLOBAL_ID_MULTIPLE_SPECIES",
        "LOCAL_AAA_INCONSISTENCY",
        "LOCAL_BBB_INCONSISTENCY",
        "CURRENT_SPECIES_CODE_DUPLICATES",
    )
    not_automatically_included_categories: tuple[str, ...] = (
        "UNOWNED_GLOBAL_REPRESENTATIONS",
        "OWNED_WITHOUT_LOCAL_IDENTITY",
    )

    def to_dict(self) -> dict:
        return {
            "eligible_categories": list(self.eligible_categories),
            "not_automatically_included_categories": list(self.not_automatically_included_categories),
            "inclusion_rule": "STRAINS_IN_ANY_ELIGIBLE_CATEGORY",
            "overlap_rule": "INCLUDED_IF_ALSO_IN_ELIGIBLE_CATEGORY",
            "excluded_only_evidence": "CATEGORY_SPECIFIC_EVIDENCE",
            "implies_consolidation_required": False,
        }


@dataclass(frozen=True)
class FindingCategory:
    code: str
    classification: str
    phase_3c_relevant: bool
    reason: str
    records: tuple[dict, ...]
    scope: ConsolidationImpactScope | None = None

    @property
    def count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "classification": self.classification,
            "phase_3c_relevant": self.phase_3c_relevant, "reason": self.reason,
            "records": list(self.records), "count": self.count,
            **({"scope": self.scope.to_dict()} if self.scope is not None else {}),
        }


@dataclass(frozen=True)
class GlobalStrainDiagnosticReport:
    scan_status: str
    readiness: str
    constraint_applicable: bool
    categories: tuple[FindingCategory, ...] = ()
    errors: tuple[str, ...] = ()
    schema_version: int = 1

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "scan_status": self.scan_status,
            "readiness": self.readiness,
            "constraint_applicable": self.constraint_applicable,
            "categories": [category.to_dict() for category in self.categories],
            "errors": list(self.errors),
        }


def _duplicate_groups(rows, key_fields):
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[field] for field in key_fields)].append(row)
    return [grouped[key] for key in sorted(grouped) if len(grouped[key]) > 1]


def _index(model, *fields):
    return {row["id"]: row for row in model.objects.values("id", *fields)}


def _reference_counts(model, path, strain_ids):
    counts = {}
    ordered_ids = sorted(strain_ids)
    # Bound parameters below SQLite's conservative limit. Each strain belongs
    # to exactly one batch, so reference counts are not multiplied or combined.
    for start in range(0, len(ordered_ids), 500):
        rows = model.objects.filter(
            **{f"{path}__in": ordered_ids[start:start + 500]},
        ).order_by().values(path).annotate(count=Count("pk"))
        counts.update({row[path]: row["count"] for row in rows})
    return counts


def diagnose_global_strain_identity_state() -> GlobalStrainDiagnosticReport:
    """Return a complete observation or an explicit, sanitized incomplete result.

    No partial scan can advertise readiness. Error details are deliberately not
    serialized: database exceptions can contain operational or private data.
    """
    try:
        return _scan()
    except Exception:
        return GlobalStrainDiagnosticReport(
            scan_status="INCOMPLETE", readiness="INCOMPLETE",
            constraint_applicable=False, errors=("SCAN_FAILED",),
        )


def _scan():
    # Read FK values separately so broken required relations cannot silently
    # disappear through an inner join and produce an apparently clean scan.
    raw_strains = sorted(Strain.objects.values(
        "id", "organization_id", "global_identity_id", "species_id", "code", "number",
    ), key=lambda row: row["id"])
    organizations = _index(Organization, "portable_id")
    species = _index(Species, "scientific_name")
    globals_by_id = _index(GlobalStrainIdentity, "global_id")
    assignments = _index(OrganizationSpeciesCode, "organization_id", "species_id")
    provenance_assignments = _index(
        OrganizationProvenanceCode, "organization_id", "biological_provenance_id",
    )
    provenance_ids = set(BiologicalProvenance.objects.values_list("pk", flat=True))
    for assignment in assignments.values():
        if assignment["organization_id"] not in organizations or assignment["species_id"] not in species:
            raise ValueError("Invalid species code assignment relation")
    for assignment in provenance_assignments.values():
        if (assignment["organization_id"] not in organizations
                or assignment["biological_provenance_id"] not in provenance_ids):
            raise ValueError("Invalid provenance code assignment relation")
    locals_by_id = _index(
        LocalStrainIdentity, "strain_id", "species_code_assignment_id",
        "provenance_code_assignment_id",
    )
    locals_by_strain = {}
    strain_ids = {row["id"] for row in raw_strains}
    for local in locals_by_id.values():
        if local["strain_id"] not in strain_ids or local["strain_id"] in locals_by_strain:
            raise ValueError("Invalid local identity relation")
        locals_by_strain[local["strain_id"]] = local
        assignments[local["species_code_assignment_id"]]
        if local["provenance_code_assignment_id"] is not None:
            provenance_assignments[local["provenance_code_assignment_id"]]

    boxes = sorted(Box.objects.values(
        "id", "strain_id", "organization_id", "status", "global_code",
    ), key=lambda row: row["id"])
    boxes_by_strain = defaultdict(list)
    prefix_counts = Counter()
    for box in boxes:
        if box["strain_id"] not in strain_ids or box["organization_id"] not in organizations:
            raise ValueError("Invalid Box relation")
        boxes_by_strain[box["strain_id"]].append(box)
        # Every literal dotted prefix is counted, including codes containing dots.
        # No LIKE wildcard interpretation or assumption about suffix validity.
        for position, character in enumerate(box["global_code"]):
            if character == ".":
                prefix_counts[box["global_code"][:position + 1]] += 1

    strains = []
    for row in raw_strains:
        owner = organizations[row["organization_id"]] if row["organization_id"] is not None else None
        identity = globals_by_id[row["global_identity_id"]] if row["global_identity_id"] is not None else None
        local = locals_by_strain.get(row["id"])
        references = boxes_by_strain[row["id"]]
        strains.append({
            "strain_id": row["id"], "organization_id": row["organization_id"],
            "organization_uuid": str(owner["portable_id"]) if owner else None,
            "global_identity_id": row["global_identity_id"],
            "global_uuid": str(identity["global_id"]) if identity else None,
            "species_id": row["species_id"],
            "species_name": species[row["species_id"]]["scientific_name"],
            "code": row["code"], "number": row["number"],
            "local_identity_id": local["id"] if local else None,
            "box_count": len(references),
            "box_organization_count": len({box["organization_id"] for box in references}),
        })
    owned = [row for row in strains if row["organization_id"] is not None]
    linked = [row for row in strains if row["global_identity_id"] is not None]
    categories = []
    # Only conflict/integrity categories below contribute IDs. Legacy ownership
    # review and local-identity normalization alone intentionally do not.
    impact_ids = set()

    def add(code, classification, reason, records, relevant=True, scope=None):
        categories.append(FindingCategory(code, classification, relevant, reason, tuple(records), scope))

    def groups(rows, keys):
        return [
            {**{key: group[0][key] for key in keys}, "strains": group}
            for group in _duplicate_groups(rows, keys)
        ]

    canonical = groups([row for row in owned if row["global_identity_id"] is not None],
                       ("organization_id", "global_identity_id"))
    for group in canonical:
        group["organization_uuid"] = group["strains"][0]["organization_uuid"]
        group["global_uuid"] = group["strains"][0]["global_uuid"]
        impact_ids.update(row["strain_id"] for row in group["strains"])
    add("CANONICAL_OWNED_DUPLICATES", DIRECT, "OWNED_GLOBAL_KEY_NOT_UNIQUE", canonical)

    multi_species = []
    for group in groups(linked, ("global_identity_id",)):
        distinct = {row["species_id"]: row["species_name"] for row in group["strains"]}
        if len(distinct) > 1:
            group["global_uuid"] = group["strains"][0]["global_uuid"]
            group["species"] = [
                {"species_id": pk, "species_name": distinct[pk]} for pk in sorted(distinct)
            ]
            multi_species.append(group)
            impact_ids.update(row["strain_id"] for row in group["strains"])
    add("GLOBAL_ID_MULTIPLE_SPECIES", REVIEW, "GLOBAL_SPECIES_REQUIRES_REVIEW", multi_species)
    add("UNOWNED_GLOBAL_REPRESENTATIONS", REVIEW, "NULL_OWNER_IS_NOT_DESTINATION_OWNERSHIP",
        [row for row in linked if row["organization_id"] is None])

    aaa, bbb = [], []
    for row in strains:
        local = locals_by_strain.get(row["strain_id"])
        if local is None:
            continue
        assignment = assignments[local["species_code_assignment_id"]]
        mismatch = []
        if row["organization_id"] is None:
            mismatch.append("UNOWNED_STRAIN")
        elif assignment["organization_id"] != row["organization_id"]:
            mismatch.append("ORGANIZATION_MISMATCH")
        if assignment["species_id"] != row["species_id"]:
            mismatch.append("SPECIES_MISMATCH")
        if mismatch:
            aaa.append({**row, "assignment_id": assignment["id"],
                        "assignment_organization_id": assignment["organization_id"],
                        "assignment_species_id": assignment["species_id"], "mismatch_types": mismatch})
            impact_ids.add(row["strain_id"])
        if local["provenance_code_assignment_id"] is not None:
            assignment = provenance_assignments[local["provenance_code_assignment_id"]]
            mismatch = []
            if row["organization_id"] is None:
                mismatch.append("UNOWNED_STRAIN")
            elif assignment["organization_id"] != row["organization_id"]:
                mismatch.append("ORGANIZATION_MISMATCH")
            if mismatch:
                bbb.append({**row, "assignment_id": assignment["id"],
                            "assignment_organization_id": assignment["organization_id"],
                            "mismatch_types": mismatch})
                impact_ids.add(row["strain_id"])
    add("LOCAL_AAA_INCONSISTENCY", REVIEW, "LOCAL_SPECIES_ASSIGNMENT_MISMATCH", aaa)
    add("LOCAL_BBB_INCONSISTENCY", REVIEW, "LOCAL_PROVENANCE_OWNER_MISMATCH", bbb)
    assigned_pairs = {(row["organization_id"], row["species_id"]) for row in assignments.values()}
    add("OWNED_WITHOUT_LOCAL_IDENTITY", REVIEW, "NORMALIZATION_READINESS_NOT_BIOLOGICAL_DUPLICATE", [
        {**row, "global_id_present": row["global_identity_id"] is not None,
         "aaa_assignment_present": (row["organization_id"], row["species_id"]) in assigned_pairs}
        for row in owned if row["local_identity_id"] is None
    ])
    current = groups(strains, ("species_id", "code"))
    for group in current:
        impact_ids.update(row["strain_id"] for row in group["strains"])
    add("CURRENT_SPECIES_CODE_DUPLICATES", SCHEMA, "CURRENT_CONSTRAINED_KEY_DUPLICATED", current)
    add("FUTURE_ORGANIZATION_CODE_COLLISIONS", INFO, "UNAPPROVED_NAMESPACE_EVIDENCE",
        groups(owned, ("organization_id", "code")), False)
    add("FUTURE_ORGANIZATION_SPECIES_CODE_COLLISIONS", INFO, "UNAPPROVED_NAMESPACE_EVIDENCE",
        groups(owned, ("organization_id", "species_id", "code")), False)
    # SQL-style TRIM removes ordinary spaces. Unicode uppercasing here is an
    # explicit observational convention, not a DB collation or future rule.
    normalized = [{**row, "normalized_code": row["code"].strip(" ").upper()} for row in owned]
    add("NORMALIZED_ORGANIZATION_CODE_COLLISIONS", INFO, "OBSERVED_SPACE_TRIM_UNICODE_UPPER",
        groups(normalized, ("organization_id", "normalized_code")), False)

    by_code = defaultdict(list)
    for row in strains:
        by_code[row["code"]].append(row)
    coupling = []
    for code in sorted(by_code):
        members = by_code[code]
        prefix = code + "."
        references = [box for row in members for box in boxes_by_strain[row["strain_id"]]]
        matching_references = sum(box["global_code"].startswith(prefix) for box in references)
        if len(members) > 1 or matching_references != len(references) or prefix_counts[prefix] != matching_references:
            coupling.append({
                "code": code, "prefix": prefix, "strains": members,
                "box_prefix_count": prefix_counts[prefix], "referenced_box_count": len(references),
                "matching_referenced_box_count": matching_references,
            })
    add("BOX_PREFIX_COUPLING", INFO, "TEXTUAL_PREFIX_NOT_IDENTITY_OR_OWNERSHIP", coupling, False)
    box_duplicates = [
        {"global_code": group[0]["global_code"], "box_ids": [row["id"] for row in group]}
        for group in _duplicate_groups(boxes, ("global_code",))
    ]
    add("BOX_GLOBAL_CODE_DUPLICATES", SCHEMA, "CURRENT_BOX_UNIQUENESS_VIOLATED", box_duplicates)

    impacts = []
    if impact_ids:
        paths = (
            ("measurement_count", BiologicalMeasurement, "box__strain_id"),
            ("box_location_count", BoxLocation, "box__strain_id"),
            ("movement_count", BoxMovement, "box__strain_id"),
            ("parent_lineage_count", BoxLineage, "parent_box__strain_id"),
            ("child_lineage_count", BoxLineage, "child_box__strain_id"),
            ("subculture_event_count", SubcultureEvent, "parent_box__strain_id"),
            ("transfer_item_source_count", TransferItem, "source_box__strain_id"),
            ("box_transfer_count", BoxTransfer, "box__strain_id"),
        )
        counts = {name: _reference_counts(model, path, impact_ids) for name, model, path in paths}
        for row in strains:
            pk = row["strain_id"]
            if pk not in impact_ids:
                continue
            statuses = Counter(box["status"] for box in boxes_by_strain[pk])
            impacts.append({
                **row, "active_box_count": statuses[Box.Status.ACTIVE],
                "inactive_box_count": statuses[Box.Status.INACTIVE],
                "pending_review_box_count": statuses[Box.Status.PENDING_REVIEW],
                **{name: values.get(pk, 0) for name, values in counts.items()},
            })
    add("CONSOLIDATION_IMPACT", INFO, "REFERENCE_COUNTS_NOT_A_MERGE_PLAN", impacts,
        scope=ConsolidationImpactScope())
    blocked = any(category.count and category.classification in (DIRECT, SCHEMA) for category in categories)
    review = any(category.count and category.classification == REVIEW for category in categories)
    return GlobalStrainDiagnosticReport(
        scan_status="COMPLETE", readiness="BLOCKED" if blocked else "REVIEW_REQUIRED" if review else "READY",
        constraint_applicable=not blocked, categories=tuple(categories),
    )
