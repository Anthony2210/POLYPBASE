"""Read-only diagnosis of the persisted state behind historical scientific anomalies.

Two modes share one report:

* ``urgent``: why ``correct_urgent_species`` is READY or BLOCKED for the six
  reviewed Boxes. The verdict comes from the real ``build_plan`` mechanism; this
  module only explains it with persisted facts.
* ``hs``: what the existing HS deactivation plan would classify for the 35 listed
  labels, with safety warnings. A warning is never a lifecycle proposal.

Nothing here writes, locks, creates references or infers a taxonomic identity.
Candidate references are reported as candidates only. Every SQL statement runs
through a guard that refuses writes and row locks.
"""

import hashlib
import json
import re

from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models import Count, Q

from apps.cultures.models import Box
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode, Species
from apps.taxonomy.scoping import eligible_strains

from . import decisions
from .deactivation import READY, V2_CONTRADICTION, build_deactivation_plan
from .source import canonical_box_code
from .species_correction import LEDGER_SHA256, SPECS, V2_SHA256, build_plan

SCHEMA = "polypbase.scientific_anomaly_diagnostic"
SCHEMA_VERSION = 1
MODES = ("urgent", "hs", "all")

URGENT_CODES = frozenset(spec[0] for spec in SPECS)

# Source data, not persisted facts. Extracted read-only from the V2 workbook
# (SHA-256 checked) so a deployed server never needs the file. A literal identifier
# is merged over several rows (one polyp/ephyrae pair per temperature sub-block), so
# every block of the whole merged range is listed. "last_week" is the last 2026 ISO
# week holding a value in ANY row of the merged range; a source 0 counts as a value.
# Weeks come from each sheet's row-2 header, never from the column index (Hydrozoa
# has an unlabelled column AR, so there AS is W39 and AT is W40).
# Blocks without any value are omitted. Rows are 1-based worksheet rows.
V2_KNOWN_CONTRADICTIONS = decisions.HS_V2_CONTRADICTIONS
SOURCE_EVIDENCE_NOTE = (
    "Source observations only. They say nothing about the persisted Box status, its persisted "
    "measurements or whether it may be deactivated."
)

_WRITE_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|MERGE|GRANT|REVOKE|COPY|LOCK|VACUUM|REINDEX)\b"
    r"|\bFOR\s+(NO\s+KEY\s+)?UPDATE\b|\bFOR\s+(KEY\s+)?SHARE\b",
    re.IGNORECASE,
)


class WriteAttempt(Exception):
    """The diagnostic tried to run a statement that could change the database."""


def _refuse_writes(execute, sql, params, many, context):
    if _WRITE_SQL.search(sql):
        raise WriteAttempt(f"Read-only diagnostic refused a write or lock statement: {sql[:60]!r}")
    return execute(sql, params, many, context)


def fingerprint(report):
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def diagnose(organization_id, *, mode="all", cac_strain_id=None, oce_strain_id=None):
    """Return a deterministic, JSON-ready report. Raises LookupError for an unknown organization."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}.")
    with connections[DEFAULT_DB_ALIAS].execute_wrapper(_refuse_writes):
        organization = Organization.objects.filter(pk=organization_id).first()
        if organization is None:
            raise LookupError("Organization not found.")
        report = {
            "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "mode": mode,
            "read_only": True,
            "organization": {"id": organization.pk, "is_active": organization.is_active},
            "source": {
                "v2_workbook_sha256": V2_SHA256, "ledger_sha256": LEDGER_SHA256,
                "workbook_required_at_runtime": False,
                "note": "source_evidence values are workbook data, never persisted facts.",
            },
        }
        if mode in ("urgent", "all"):
            report["urgent_species"] = _urgent(organization, cac_strain_id, oce_strain_id)
        if mode in ("hs", "all"):
            report["hs_deactivation"] = _hs(organization)
        report["report_sha256"] = fingerprint(report)
        return report


UNAVAILABLE = {"available_in_organization": False}
_STRAIN_PK_IN_TEXT = re.compile(r"Strain (\d+)")


def _available_strain_ids(organization, strain_ids):
    """Only Strains the repository rules make eligible for this Organization may be serialized."""
    ids = {pk for pk in strain_ids if pk is not None}
    return set(eligible_strains(organization).filter(pk__in=ids).values_list("pk", flat=True))


def _strain_ref(strain, organization):
    """Serialize only a Strain already proven eligible in this Organization."""
    return {
        "available_in_organization": True,
        "id": strain.pk, "code": strain.code,
        "ownership": "unowned_legacy" if strain.organization_id is None else "owned_by_organization",
        "species_id": strain.species_id, "species_name": strain.species.scientific_name,
    }


def _redact_conflict(text, available_ids):
    """Planner conflict text may name a Strain PK; keep only PKs available in the Organization."""
    return _STRAIN_PK_IN_TEXT.sub(
        lambda m: m.group(0) if int(m.group(1)) in available_ids else "Strain <unavailable>", text)


def _local_identity(strain, organization):
    """Describe the identity of an eligible Strain; foreign rows are never serialized."""
    local = (
        LocalStrainIdentity.objects.select_related("species_code_assignment", "provenance_code_assignment")
        .filter(strain=strain).first()
    )
    if local is None:
        return {"present": False, "problems": [], "observations": []}
    assignment = local.species_code_assignment
    provenance = local.provenance_code_assignment
    assignment_ok = assignment.organization_id == organization.pk
    provenance_ok = provenance is not None and provenance.organization_id == organization.pk
    problems, observations = [], []
    if strain.organization_id != organization.pk:
        problems.append("STRAIN_NOT_OWNED_BY_ORGANIZATION")
    if not assignment_ok:
        problems.append("SPECIES_ASSIGNMENT_OUTSIDE_ORGANIZATION")
    elif assignment.species_id != strain.species_id:
        problems.append("SPECIES_ASSIGNMENT_SPECIES_MISMATCH")
    if provenance is not None and not provenance_ok:
        problems.append("PROVENANCE_ASSIGNMENT_OUTSIDE_ORGANIZATION")
    # Observations are evidence for a reviewer, not blockers in the real mechanism.
    if assignment_ok and assignment.code != strain.code[:3]:
        observations.append("STRAIN_CODE_PREFIX_DIFFERS_FROM_SPECIES_ASSIGNMENT_CODE")
    segments = strain.code.split("-")
    if provenance_ok and len(segments) > 1 and provenance.code != segments[1]:
        observations.append("PROVENANCE_CODE_DIFFERS_FROM_STRAIN_CODE_SEGMENT")
    return {
        "present": True, "problems": problems, "observations": observations,
        "species_code_assignment": {
            "id": assignment.pk, "code": assignment.code, "species_id": assignment.species_id,
        } if assignment_ok else UNAVAILABLE,
        "provenance_code_assignment": None if provenance is None else {
            "id": provenance.pk, "code": provenance.code,
            "biological_provenance_id": provenance.biological_provenance_id,
        } if provenance_ok else UNAVAILABLE,
    }


def _assignment_rows(organization, aaa, species_ids):
    rows = (
        OrganizationSpeciesCode.objects.select_related("species")
        .filter(organization=organization).filter(Q(code=aaa) | Q(species_id__in=species_ids))
        .order_by("pk")
    )
    return [{"id": r.pk, "code": r.code, "species_id": r.species_id,
             "species_name": r.species.scientific_name} for r in rows]


def _candidates(organization, namespace, required_name):
    """Exact reviewed (species name, strain code) pair; never chosen from a prefix."""
    required = Species.objects.filter(scientific_name=required_name).first()
    # Scoped query: an exact reference outside the Organization is indistinguishable from none.
    exact = (
        eligible_strains(organization).select_related("species")
        .filter(species__scientific_name=required_name, code=namespace).first()
    )
    result = {
        "candidate_only": True, "required_species_id": required.pk if required else None,
        "exact_reference": None, "same_code_other_species": [],
    }
    if exact is not None:
        result["exact_reference"] = {**_strain_ref(exact, organization),
                                     "local_identity": _local_identity(exact, organization)}
    others = (
        eligible_strains(organization).select_related("species").filter(code=namespace)
        .exclude(species__scientific_name=required_name).order_by("pk")
    )
    result["same_code_other_species"] = [_strain_ref(s, organization) for s in others]
    return result


def _aaa_survey(organization, aaa):
    """H1 evidence: which Species and Strains hold Boxes under one AAA prefix."""
    groups = (
        Box.objects.filter(organization=organization, global_code__startswith=f"{aaa}-")
        .values("strain_id", "strain__code", "strain__species_id", "strain__species__scientific_name")
        .annotate(box_count=Count("pk")).order_by("strain__species_id", "strain_id")
    )
    groups = list(groups)
    available = _available_strain_ids(organization, [g["strain_id"] for g in groups])
    box_groups = [{
        "available_in_organization": True,
        "strain_id": g["strain_id"], "strain_code": g["strain__code"],
        "species_id": g["strain__species_id"], "species_name": g["strain__species__scientific_name"],
        "box_count": g["box_count"],
    } for g in groups if g["strain_id"] in available]
    unavailable_boxes = sum(g["box_count"] for g in groups if g["strain_id"] not in available)
    if unavailable_boxes:  # Boxes of this Organization pointing at a Strain it cannot access
        box_groups.append({**UNAVAILABLE, "box_count": unavailable_boxes})
    code_rows = OrganizationSpeciesCode.objects.select_related("species").filter(
        organization=organization, code=aaa).order_by("pk")
    return {
        "aaa": aaa,
        "species_code_assignments": [{"id": r.pk, "species_id": r.species_id,
                                      "species_name": r.species.scientific_name} for r in code_rows],
        "box_groups": box_groups,
        "distinct_species_under_prefix": len({g["species_id"] for g in box_groups if "species_id" in g}),
        "h1_indicator_multiple_species_under_code":
            len({g["species_id"] for g in box_groups if "species_id" in g}) > 1,
        "note": "H1 is an unproven hypothesis; this only shows what is persisted.",
    }


def _urgent(organization, cac_strain_id, oce_strain_id):
    explicit = {"CAC": cac_strain_id, "OCE": oce_strain_id}
    families = {}
    for _code, namespace, _old, new, aaa, *_rest in SPECS:
        families.setdefault(aaa, (namespace, new))
    candidates = {aaa: _candidates(organization, ns, new) for aaa, (ns, new) in families.items()}
    surveys = {aaa: _aaa_survey(organization, aaa) for aaa in families}

    # A supplied PK outside the Organization is handed to the planner as "no target": it is
    # BLOCKED either way, and its conflict text can no longer describe a foreign Strain.
    available = _available_strain_ids(organization, explicit.values())
    scoped = {aaa: pk if pk in available else None for aaa, pk in explicit.items()}
    mechanism = build_plan(organization.pk, cac_strain_id=scoped["CAC"], oce_strain_id=scoped["OCE"])
    hypothetical_ids = {
        aaa: (candidates[aaa]["exact_reference"] or {}).get("id") if explicit[aaa] is None else None
        for aaa in families
    }
    hypothetical = None
    if any(hypothetical_ids.values()):
        hypothetical = build_plan(
            organization.pk, cac_strain_id=hypothetical_ids["CAC"], oce_strain_id=hypothetical_ids["OCE"],
        )

    entries = []
    for index, spec in enumerate(SPECS):
        entries.append(_urgent_entry(
            organization, spec, explicit[spec[4]], scoped[spec[4]], candidates[spec[4]], mechanism["entries"][index],
            None if hypothetical is None or hypothetical_ids[spec[4]] is None else hypothetical["entries"][index],
            hypothetical_ids[spec[4]],
        ))
    ready = sum(e["verdict"]["correct_urgent_species"] == "READY" for e in entries)
    return {
        "operation": mechanism["operation"],
        "explicit_target_strain_ids": explicit,
        "aaa_code_survey": surveys,
        "entries": entries,
        "summary": {"ready": ready, "blocked": len(entries) - ready,
                    "batch_ready": ready == len(entries)},
    }


def _urgent_entry(organization, spec, explicit_id, scoped_id, candidates, mech, hypo, hypo_id):
    """`scoped_id` is `explicit_id` only when that Strain is eligible in the Organization."""
    code, namespace, old_name, new_name, aaa, decision, cells, literal = spec
    blockers = []
    available_ids = {pk for pk in (scoped_id, hypo_id) if pk is not None}

    def block(reason, detail):
        blockers.append({"code": reason, "detail": detail})

    # Scoped by Organization: a Box of another institution is indistinguishable from no Box.
    box = Box.objects.select_related("strain__species").filter(organization=organization, global_code=code).first()
    persisted = {"box": {"exists": box is not None, "global_code": code, "organization_id": organization.pk}}
    species_ids = [candidates["required_species_id"]]
    if box is None:
        block("BOX_MISSING", "No Box with this global_code is available in the selected organization.")
    else:
        current = box.strain
        persisted["box"].update(pk=box.pk, status=box.status)
        if current.pk not in _available_strain_ids(organization, [current.pk]):
            # The Box references a Strain this Organization cannot access: describe nothing about it.
            persisted["current_strain"] = dict(UNAVAILABLE)
            block("CURRENT_STRAIN_UNAVAILABLE_IN_ORGANIZATION",
                  "The Box references a Strain that is not available in the selected organization.")
        else:
            available_ids.add(current.pk)
            species_ids.append(current.species_id)
            persisted["current_strain"] = {
                **_strain_ref(current, organization), "local_identity": _local_identity(current, organization),
            }
            problems = persisted["current_strain"]["local_identity"]["problems"]
            if problems:
                block("CURRENT_LOCAL_IDENTITY_INCONSISTENT", ", ".join(problems))
            matches_required = current.species.scientific_name == new_name and current.code == namespace
            persisted["current_matches_required_reference"] = matches_required
            if not matches_required and (current.species.scientific_name != old_name or current.code != namespace):
                block("CURRENT_IDENTITY_UNEXPECTED",
                      f"Current Species/Strain is {current.species.scientific_name!r}/{current.code!r}; "
                      f"expected {old_name!r}/{namespace!r}.")

    assignments = _assignment_rows(organization, aaa, [s for s in species_ids if s is not None])
    persisted["species_code_assignments"] = assignments
    if candidates["required_species_id"] is None:
        block("REQUIRED_SPECIES_ABSENT", f"No Species named {new_name!r} exists.")
    by_code = [a for a in assignments if a["code"] == aaa]
    required_rows = [a for a in assignments if a["species_id"] == candidates["required_species_id"]]
    if not by_code:
        block("SPECIES_CODE_ASSIGNMENT_ABSENT", f"Organization has no OrganizationSpeciesCode {aaa}.")
    elif candidates["required_species_id"] is not None and by_code[0]["species_id"] != candidates["required_species_id"]:
        block("SPECIES_CODE_POINTS_TO_OTHER_SPECIES",
              f"Code {aaa} is assigned to {by_code[0]['species_name']!r}, not {new_name!r}.")
    if required_rows and required_rows[0]["code"] != aaa:
        block("REQUIRED_SPECIES_HAS_OTHER_CODE",
              f"{new_name!r} is assigned code {required_rows[0]['code']!r}, not {aaa!r}.")

    target = {"supplied_strain_id": explicit_id}
    if explicit_id is None:
        block("TARGET_NOT_SUPPLIED", "No explicit target Strain PK was supplied; none is ever chosen automatically.")
        if candidates["exact_reference"] is None:
            block("NO_ELIGIBLE_CANDIDATE_TARGET",
                  f"No eligible Strain exists for exactly {new_name!r} with code {namespace!r}.")
    elif scoped_id is None:
        # Nonexistent and inaccessible PKs are deliberately reported identically.
        target["availability"] = "UNAVAILABLE_IN_ORGANIZATION"
        block("TARGET_UNAVAILABLE_IN_ORGANIZATION",
              "The supplied target Strain PK is not available in the selected organization.")
    else:
        strain = eligible_strains(organization).select_related("species").get(pk=scoped_id)
        target["availability"] = "AVAILABLE"
        target["strain"] = {**_strain_ref(strain, organization),
                            "local_identity": _local_identity(strain, organization)}
        if target["strain"]["local_identity"]["problems"]:
            block("TARGET_LOCAL_IDENTITY_INCONSISTENT", ", ".join(target["strain"]["local_identity"]["problems"]))
        if strain.species.scientific_name != new_name or strain.code != namespace:
            block("TARGET_REFERENCE_MISMATCH",
                  f"Target is {strain.species.scientific_name!r}/{strain.code!r}; required {new_name!r}/{namespace!r}.")

    conflicts = [_redact_conflict(c, available_ids) for c in mech["conflicts"]]
    ready = mech["classification"] in {"already correct", "correction needed"}
    if not ready and not blockers:
        block("UNEXPLAINED_BY_DIAGNOSTIC", "; ".join(conflicts) or mech["classification"])
    entry = {
        "global_code": code,
        "persisted": persisted,
        "target": target,
        "candidate_references": {**candidates, "note": "Candidates only: never selected or applied automatically."},
        "source_evidence": {
            "origin": "v2_workbook_and_ledger_static", "decision": decision, "workbook_cells": cells,
            "literal_source_code": literal, "expected_species": old_name, "required_species": new_name,
        },
        "verdict": {
            "correct_urgent_species": "READY" if ready else "BLOCKED",
            "mechanism_classification": mech["classification"],
            "mechanism_conflicts": conflicts,
            "blockers": blockers,
            "diagnostic_consistent_with_mechanism": (
                (ready and not blockers)
                or (not ready and bool(blockers)
                    and all(b["code"] != "UNEXPLAINED_BY_DIAGNOSTIC" for b in blockers))
            ),
        },
    }
    if hypo is not None:
        entry["readiness_if_candidate_supplied"] = {
            "candidate_only": True, "candidate_strain_id": hypo_id,
            "correct_urgent_species": "READY" if hypo["classification"] in {"already correct", "correction needed"} else "BLOCKED",
            "mechanism_classification": hypo["classification"],
            "mechanism_conflicts": [_redact_conflict(c, available_ids) for c in hypo["conflicts"]],
            "note": "Hypothetical. The operator must still supply and independently review the PK.",
        }
    return entry


def _measurements(box):
    queryset = BiologicalMeasurement.objects.filter(box=box)
    latest = queryset.order_by("-measured_on", "-pk").first()
    year, week, _ = (latest.measured_on.isocalendar() if latest else (None, None, None))
    return {
        "count": queryset.count(),
        "latest": None if latest is None else {
            "measured_on": latest.measured_on.isoformat(), "iso_year": year, "iso_week": week,
            "polyp_count": latest.polyp_count, "ephyrae_count": latest.ephyrae_count,
            "strobila_count": latest.strobila_count,  # None means not measured; 0 is a real zero
            "temporal_precision": latest.temporal_precision,
        },
    }


def _box_summary(box):
    return {
        "exists": box is not None,
        **({} if box is None else {
            "pk": box.pk, "global_code": box.global_code, "status": box.status,
            "has_current_thermal_zone": box.thermal_zone_id is not None,
            "deactivated_on": box.deactivated_on.isoformat() if box.deactivated_on else None,
            "measurements": _measurements(box),
        }),
    }


def _hs(organization):
    plan = build_deactivation_plan(organization)  # reuse: read only, no lock
    approved = dict(decisions.HS_CODE_CORRECTIONS)
    canonical = {}
    for listed, item in V2_KNOWN_CONTRADICTIONS.items():
        canonical[listed] = canonical_box_code(item["literal"])[0]
    wanted = {e["listed_code"] for e in plan} | {e["resolved_code"] for e in plan} | set(canonical.values())
    boxes = {b.global_code: b for b in Box.objects.filter(organization=organization, global_code__in=wanted)}
    entries = []
    for item in plan:
        listed, resolved = item["listed_code"], item["resolved_code"]
        warnings = []
        entry = {
            "listed_code": listed, "resolved_code": resolved,
            "approved_remap": listed in approved,
            "deactivation_plan_status": item["status"], "deactivation_plan_detail": item["detail"],
            "listed_box": _box_summary(boxes.get(listed)),
            "resolved_box": _box_summary(boxes.get(resolved)) if resolved != listed else None,
            "in_urgent_species_set": resolved in URGENT_CODES or listed in URGENT_CODES,
            "source_evidence": None, "candidate_remap": None,
        }
        known = V2_KNOWN_CONTRADICTIONS.get(listed)
        if known:
            v2_code = canonical[listed]
            entry["source_evidence"] = {
                "origin": "v2_workbook_static", "v2_literal_code": known["literal"],
                "v2_canonical_code": v2_code, "cell": known["cell"], "merged_range": known["merged_range"],
                "blocks": [dict(block) for block in known["blocks"]],
                "last_week_with_value": known["last_week"], "last_iso_week": f"2026-W{known['last_week']:02d}",
                "note": SOURCE_EVIDENCE_NOTE,
            }
            if v2_code not in (listed, resolved):
                entry["candidate_remap"] = {
                    "candidate_only": True, "approved": False, "code": v2_code,
                    "persisted_box": _box_summary(boxes.get(v2_code)),
                }
            warnings.append({
                "code": "V2_ROW_FOR_LISTED_IDENTITY", "severity": "UNSAFE",
                "message": f"V2 holds source observations under {known['literal']} up to 2026 week "
                           f"{known['last_week']}, which contradicts the HS label. Source data only: "
                           "persisted status and measurements must be checked separately.",
            })
        if entry["in_urgent_species_set"]:
            warnings.append({
                "code": "IDENTITY_UNDER_URGENT_CORRECTION", "severity": "UNVERIFIED",
                "message": "This Box belongs to the six reviewed identity corrections; verify its state first.",
            })
        if item["status"] == V2_CONTRADICTION:
            entry["deactivation_safety"] = "UNSAFE"
        elif item["status"] == READY:
            warnings.append({
                "code": "HS_LABEL_NOT_VERIFIED_AGAINST_SOURCES", "severity": "UNVERIFIED",
                "message": "The HS list is not an authorization derived from persisted state; "
                           "absence of later readings does not prove the culture is out of service.",
            })
            severities = {w["severity"] for w in warnings}
            entry["deactivation_safety"] = "UNSAFE" if "UNSAFE" in severities else "UNVERIFIED"
        else:
            entry["deactivation_safety"] = "NOT_APPLICABLE"
        entry["warnings"] = warnings
        entry["diagnostic_only"] = True
        entries.append(entry)
    by_status, by_safety = {}, {}
    for e in entries:
        by_status[e["deactivation_plan_status"]] = by_status.get(e["deactivation_plan_status"], 0) + 1
        by_safety[e["deactivation_safety"]] = by_safety.get(e["deactivation_safety"], 0) + 1
    return {
        "listed_code_count": len(entries),
        "approved_remaps": approved,
        "entries": entries,
        "summary": {"plan_status": by_status, "deactivation_safety": by_safety,
                    "note": "No entry is a proposed lifecycle mutation."},
    }


def render_summary(report):
    lines = [f"Organization {report['organization']['id']} | mode {report['mode']} | read-only | "
             f"report {report['report_sha256'][:12]}"]
    urgent = report.get("urgent_species")
    if urgent:
        lines.append(f"Urgent species corrections: {urgent['summary']['ready']} READY, "
                     f"{urgent['summary']['blocked']} BLOCKED")
        for entry in urgent["entries"]:
            reasons = ", ".join(b["code"] for b in entry["verdict"]["blockers"]) or "-"
            lines.append(f"  {entry['global_code']}: {entry['verdict']['correct_urgent_species']} [{reasons}]")
    hs = report.get("hs_deactivation")
    if hs:
        lines.append(f"HS labels ({hs['listed_code_count']}):")
        for label, counts in (("plan", hs["summary"]["plan_status"]),
                              ("deactivation safety", hs["summary"]["deactivation_safety"])):
            lines.append(f"  {label}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for entry in hs["entries"]:
            if entry["deactivation_safety"] == "UNSAFE":
                lines.append(f"  WARNING {entry['listed_code']} -> {entry['resolved_code']}: "
                             f"plan {entry['deactivation_plan_status']}; UNSAFE (diagnostic only)")
    return "\n".join(lines)
