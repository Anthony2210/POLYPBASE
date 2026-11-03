"""Reviewed correction of six Boxes to their exact colour-variant Species.

Two separate, dry-run-by-default steps:

1. ``references``: additive only. Create the exact missing Species names and,
   when no compatible one exists, an institution-owned technical Strain that
   reuses the SAME existing strain code under the target Species. Strain
   uniqueness is (species, code), so no existing Species, Strain, code
   assignment or Box is touched. No local identity or AAA assignment is created.
2. ``boxes``: relation-only. Move exactly the six listed Box PKs to explicit,
   reviewed target Strains. Codes, statuses, lineage, locations, movements,
   measurements (including zeros), QR tags, transfer records and audits stay.

Changing ``Box.strain`` changes how a Box's existing measurements are grouped
by Species in current views and exports. Raw measurements are not rewritten,
and the before/after identity is kept in the audit log.
"""

import json
import re
import unicodedata
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.box_identity import USABLE, strain_box_identity
from apps.cultures.models import (
    Box, BoxLineage, BoxTransfer, BoxTransferImport, SubcultureAllocation, TransferItem,
)
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain
from apps.taxonomy.scoping import eligible_strains

from .species_correction import fingerprint, snapshot

REFERENCE_OPERATION = "variant_species_references_20261103_v1"
BOX_OPERATION = "variant_species_boxes_20261103_v1"
REASON = ("Operator-confirmed Species correction for six reviewed Boxes; the full scientific name "
          "identifies the Species. Preserve Box, QR and Strain code identity.")
HISTORICAL_EFFECT = ("Current views and exports group this Box's existing measurements under the new "
                     "Species. Raw measurements, frozen transfer records and prior audits are unchanged.")

PINK = 'Chrysaora chesapeakei "pink"'
WHITE = 'Chrysaora chesapeakei "white"'
BROWN = 'Sanderia malayensis "brown"'
# (Box PK, global code, existing Strain code, exact target Species name).
SPECS = (
    (1854, "CCP-ACH-2.001", "CCP-ACH-2", PINK),
    (1855, "CCP-ACH-2.002", "CCP-ACH-2", PINK),
    (1865, "CCP-TPR-1.008", "CCP-TPR-1", WHITE),
    (1866, "CCP-TPR-1.009", "CCP-TPR-1", WHITE),
    (2227, "SMA-JKA-2.005", "SMA-JKA-2", BROWN),
    (2228, "SMA-JKA-2.006", "SMA-JKA-2", BROWN),
)
BOX_PKS = tuple(spec[0] for spec in SPECS)

# Reviewed source contract (production SQL diagnostics B, 2026-11-03). Any drift
# from these exact PKs, codes or names stops both steps; nothing is reinterpreted.
ORGANIZATION_PK = 1
ORGANIZATION_PORTABLE_ID = uuid.UUID("ebc09e8a-9dbc-4a86-b80d-fddba628d6fe")
SOURCE_SPECIES = {128: 'Chrysaora chesapeakei "pink striped"', 143: 'Sanderia malayensis "white"'}
# Source Strain PK -> (exact Strain code, exact source Species PK).
SOURCE_STRAINS = {201: ("CCP-ACH-2", 128), 203: ("CCP-TPR-1", 128), 242: ("SMA-JKA-2", 143)}
SOURCE_FOR_BOX = {1854: 201, 1855: 201, 1865: 203, 1866: 203, 2227: 242, 2228: 242}
GROUPS = tuple(dict.fromkeys((spec[2], spec[3]) for spec in SPECS))

READY, ALREADY_PRESENT, CONFLICT = "READY", "ALREADY_PRESENT", "CONFLICT"
NEEDED, CORRECT = "correction needed", "already correct"


def _ids(value, label):
    if (not isinstance(value, (list, tuple, set, frozenset)) or not value
            or any(type(v) is not int or v <= 0 for v in value) or len(set(value)) != len(value)):
        raise ValueError(f"Explicit, distinct, positive {label} PKs are required.")
    return sorted(value)


def _validate(organization_id, source_strain_ids, target_strain_ids=None):
    if type(organization_id) is not int or organization_id <= 0:
        raise ValueError("An explicit positive organization PK is required.")
    if organization_id != ORGANIZATION_PK:
        raise ValueError("Only the reviewed institution can be corrected.")
    sources = _ids(source_strain_ids, "source Strain")
    if set(sources) != set(SOURCE_STRAINS):
        # Never let a parameter substitute another Strain, even with the same code.
        raise ValueError("Source Strain PKs must be exactly the reviewed source contract.")
    targets = None if target_strain_ids is None else _ids(target_strain_ids, "target Strain")
    if targets is not None and set(sources) & set(targets):
        raise ValueError("A Strain cannot be both a source and a target.")
    return sources, targets


def _normalized(name):
    """Detect near-duplicate names (case, quotes, spacing) that need human review."""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = re.sub(r"[“”«»″']", '"', text)
    return re.sub(r"\s+", " ", text).strip()


def _identity(strain):
    return {"strain": snapshot(strain), "species": snapshot(strain.species), "strain_pk": strain.pk,
            "strain_code": strain.code, "species_pk": strain.species_id,
            "species_name": strain.species.scientific_name}


def _local_identity_problem(strain, organization):
    local = LocalStrainIdentity.objects.filter(strain=strain).select_related(
        "species_code_assignment", "provenance_code_assignment").first()
    if local is None:
        return None
    assignment, provenance = local.species_code_assignment, local.provenance_code_assignment
    if (strain.organization_id != organization.pk or assignment.organization_id != organization.pk
            or assignment.species_id != strain.species_id
            or (provenance is not None and provenance.organization_id != organization.pk)):
        return f"Strain {strain.pk} has an inconsistent local identity."
    return None


def _assignment_state(organization, species, strain_code):
    """Missing AAA assignments are allowed; contradictory ones block (box_identity rule)."""
    aaa = strain_code[:3]
    assigned = (OrganizationSpeciesCode.objects.filter(organization=organization, species=species).first()
                if species is not None else None)
    holder = OrganizationSpeciesCode.objects.filter(organization=organization, code=aaa).first()
    state = {"aaa": aaa, "target_species_assignment": snapshot(assigned) if assigned else None,
             "aaa_code_holder": snapshot(holder) if holder else None}
    conflict = None
    if assigned is not None and assigned.code != aaa:
        conflict = f"Target Species already has contradictory {assigned.code!r} assignment; never rewritten here."
        state["classification"] = CONFLICT
    else:
        state["classification"] = "consistent" if assigned else "missing (not created)"
    return state, conflict


def _box_entries(organization, sources):
    """Resolve exactly the six PKs; never select by code or prefix."""
    eligible = eligible_strains(organization)
    entries = []
    for pk, code, strain_code, species_name in SPECS:
        entry = {"box_pk": pk, "global_code": code, "strain_code": strain_code,
                 "target_species": species_name, "classification": CONFLICT, "conflicts": []}
        entries.append(entry)
        box = Box.objects.select_related("strain__species").filter(pk=pk).first()
        if box is None or organization is None or box.organization_id != organization.pk:
            entry["conflicts"].append("Box is missing from this institution.")
            continue
        entry.update(box=snapshot(box), current=_identity(box.strain))
        if box.global_code != code:
            entry["conflicts"].append("Box global code differs from the reviewed code.")
        if box.merged_into_id is not None:
            entry["conflicts"].append("A merged Box identity is read-only.")
        if box.strain.code != strain_code:
            entry["conflicts"].append("Current Strain code differs from the reviewed code.")
        if not eligible.filter(pk=box.strain_id).exists():
            entry["conflicts"].append("Current Strain is not eligible in this institution.")
        if TransferItem.objects.filter(source_box_id=pk).exists():
            entry["conflicts"].append("A frozen transfer package references this Box; separate review required.")
        if entry["conflicts"]:
            continue
        expected = SOURCE_FOR_BOX[pk]
        if box.strain_id == expected:
            if (box.strain.species_id != SOURCE_STRAINS[expected][1]
                    or box.strain.species.scientific_name != SOURCE_SPECIES[box.strain.species_id]):
                entry["conflicts"].append("Source Species differs from the reviewed source contract.")
            elif expected in sources:
                entry["classification"] = NEEDED
            else:
                entry["conflicts"].append("Current Strain is not one of the explicitly reviewed source Strains.")
        elif box.strain_id in SOURCE_STRAINS:
            entry["conflicts"].append("Box uses another reviewed source Strain than its contract.")
        elif box.strain.species.scientific_name == species_name:
            # Already moved; the box step also requires the exact explicit target PK.
            entry["classification"] = CORRECT
        else:
            entry["conflicts"].append("Current Strain differs from the reviewed source contract.")
    return entries


def _contract_conflicts(organization):
    """Verify the reviewed institution and source references, eligible or not."""
    conflicts = []
    if organization is None or organization.portable_id != ORGANIZATION_PORTABLE_ID:
        conflicts.append("Institution differs from the reviewed source contract.")
    species = dict(Species.objects.filter(pk__in=SOURCE_SPECIES).values_list("pk", "scientific_name"))
    for pk, name in SOURCE_SPECIES.items():
        if species.get(pk) != name:
            conflicts.append(f"Source Species {pk} differs from the reviewed source contract.")
    strains = {s.pk: s for s in Strain.objects.filter(pk__in=SOURCE_STRAINS)}
    for pk, (code, species_pk) in SOURCE_STRAINS.items():
        strain = strains.get(pk)
        if strain is None or strain.code != code or strain.species_id != species_pk:
            conflicts.append(f"Source Strain {pk} differs from the reviewed source contract.")
    return conflicts


def _sources_state(sources, organization):
    """Snapshot eligible sources only. Box entries separately require each Box
    needing correction to use an eligible, listed source. An unowned legacy
    source stops being eligible here once its last own Box has been corrected;
    it is then reported without contents and is not a conflict (idempotence)."""
    rows, conflicts = [], _contract_conflicts(organization)
    codes = {group[0] for group in GROUPS}
    found = {s.pk: s for s in Strain.objects.filter(pk__in=sources).select_related("species")}
    eligible = eligible_strains(organization) if organization else Strain.objects.none()
    for pk in sources:
        strain = found.get(pk)
        if strain is None:
            conflicts.append(f"Source Strain {pk} does not exist.")
        elif not eligible.filter(pk=pk).exists():
            rows.append({"strain_pk": pk, "eligible": False})
        else:
            rows.append({**_identity(strain), "eligible": True,
                         "remaining_institution_boxes": Box.objects.filter(
                             organization=organization, strain=strain).exclude(pk__in=BOX_PKS).count()})
            if strain.code not in codes:
                conflicts.append(f"Source Strain {pk} does not use a reviewed Strain code.")
    return rows, conflicts


def _plan_header(operation, organization, organization_id, sources):
    return {"operation": operation, "reason": REASON, "historical_effect": HISTORICAL_EFFECT,
            "organization": snapshot(organization) if organization else {"id": organization_id},
            "box_pks": list(BOX_PKS), "source_strain_ids": sources}


def build_reference_plan(organization_id, *, source_strain_ids):
    """Read only. Additive preparation of exact Species and technical Strains."""
    sources, _ = _validate(organization_id, source_strain_ids)
    organization = Organization.objects.filter(pk=organization_id).first()
    plan = _plan_header(REFERENCE_OPERATION, organization, organization_id, sources)
    entries = _box_entries(organization, sources)
    source_rows, conflicts = _sources_state(sources, organization)
    conflicts += [f"{e['global_code']}: {c}" for e in entries for c in e["conflicts"]]
    groups = []
    for strain_code, species_name in GROUPS:
        members = [e for e in entries if e["strain_code"] == strain_code]
        needed = [e for e in members if e["classification"] == NEEDED]
        group = {"strain_code": strain_code, "species_name": species_name, "create_species": False,
                 "create_strain": None, "species_pk": None, "target_strain_pk": None, "conflicts": []}
        groups.append(group)
        source_ids = sorted({e["current"]["strain_pk"] for e in needed})
        if len(source_ids) > 1:
            group["conflicts"].append("Boxes of one group use several source Strains.")
        species = Species.objects.filter(scientific_name=species_name).first()
        near = [s.pk for s in Species.objects.order_by("pk")
                if s.scientific_name != species_name and _normalized(s.scientific_name) == _normalized(species_name)]
        if near:
            group["conflicts"].append(f"Near-duplicate Species {near} require separate review.")
        group["species_pk"] = species.pk if species else None
        group["species"] = snapshot(species) if species else None
        group["create_species"] = bool(needed) and species is None and not near
        group["assignment"], conflict = _assignment_state(organization, species, strain_code) if organization else ({}, None)
        if conflict:
            group["conflicts"].append(conflict)
        target = Strain.objects.filter(species=species, code=strain_code).first() if species else None
        if target is not None:
            if organization is None or not eligible_strains(organization).filter(pk=target.pk).exists():
                group["conflicts"].append("A target Strain exists outside this institution; contents withheld.")
            elif target.pk in sources:
                group["conflicts"].append("The target Strain is also listed as a source.")
            else:
                group["target_strain_pk"] = target.pk
                group["target_strain"] = snapshot(target)
                problem = _local_identity_problem(target, organization)
                if problem:
                    group["conflicts"].append(problem)
        elif needed and len(source_ids) == 1:
            source = Strain.objects.get(pk=source_ids[0])
            # Same existing code and parsed components; no biological attribute invented.
            group["create_strain"] = {"organization_id": organization_id, "species_name": species_name,
                                      "code": source.code, "number": source.number,
                                      "origin_code": source.origin_code, "copied_from_strain_pk": source.pk}
        conflicts += [f"{strain_code}: {c}" for c in group["conflicts"]]
    plan.update(entries=entries, sources=source_rows, groups=groups, conflicts=conflicts)
    creates = any(g["create_species"] or g["create_strain"] for g in groups)
    plan["classification"] = CONFLICT if conflicts else READY if creates else ALREADY_PRESENT
    return plan


def _authorize(organization_id, actor):
    if actor is None or getattr(actor, "pk", None) is None:
        raise ValueError("An explicit actor is required.")
    organization = Organization.objects.select_for_update().get(pk=organization_id)
    memberships = list(OrganizationMembership.objects.select_for_update().filter(
        organization=organization, user_id=actor.pk).order_by("pk"))
    user = get_user_model().objects.select_for_update().get(pk=actor.pk)
    if not user.is_active or not any(m.is_active and m.role == "admin" for m in memberships):
        raise PermissionDenied("An active administrator membership in this Organization is required.")
    return organization, user


def _lock(organization, strain_ids):
    """Lock the six Boxes first (blocks FK inserts to them), then references."""
    locked = list(Box.objects.select_for_update().filter(
        pk__in=BOX_PKS, organization=organization).order_by("pk"))
    strain_ids = set(strain_ids) | set(SOURCE_STRAINS) | {box.strain_id for box in locked}
    strains = list(Strain.objects.select_for_update().filter(pk__in=strain_ids).order_by("pk"))
    names = [group[1] for group in GROUPS]
    species_ids = {s.species_id for s in strains} | set(SOURCE_SPECIES) | set(
        Species.objects.filter(scientific_name__in=names).values_list("pk", flat=True))
    list(Species.objects.select_for_update().filter(pk__in=species_ids).order_by("pk"))
    list(Strain.objects.select_for_update().filter(species_id__in=species_ids,
                                                   code__in=[g[0] for g in GROUPS]).order_by("pk"))
    list(OrganizationSpeciesCode.objects.select_for_update().filter(organization=organization).order_by("pk"))
    list(LocalStrainIdentity.objects.select_for_update().filter(strain_id__in=strain_ids).order_by("pk"))
    return {box.pk: box.strain_id for box in locked}


def _check_locked(plan, locked):
    if {e["box_pk"]: (e.get("current") or {}).get("strain_pk") for e in plan["entries"]} != {
            pk: locked.get(pk) for pk in BOX_PKS}:
        raise ValueError("Planned Box identities do not match the locked selection.")


def _check_reviewed(plan, expected_plan_hash, allowed):
    if fingerprint(plan) != expected_plan_hash:
        raise ValueError("Database state changed since the reviewed plan; run a new dry-run.")
    if plan["classification"] not in allowed:
        raise ValueError("Unresolved conflicts block the entire batch.")


def apply_reference_plan(organization_id, *, source_strain_ids, actor, expected_plan_hash):
    _validate(organization_id, source_strain_ids)
    if actor is None or not expected_plan_hash:
        raise ValueError("An explicit actor and reviewed plan hash are required.")
    with transaction.atomic():
        organization, user = _authorize(organization_id, actor)
        locked = _lock(organization, source_strain_ids)
        plan = build_reference_plan(organization_id, source_strain_ids=source_strain_ids)
        _check_locked(plan, locked)
        _check_reviewed(plan, expected_plan_hash, {READY, ALREADY_PRESENT})
        created, prepared = [], {}
        for group in plan["groups"]:
            species = Species.objects.filter(pk=group["species_pk"]).first()
            if group["create_species"]:
                species = Species.objects.create(scientific_name=group["species_name"])
                created.append(species)
            strain_pk = group["target_strain_pk"]
            if group["create_strain"]:
                spec = group["create_strain"]
                strain = Strain.objects.create(organization=organization, species=species, code=spec["code"],
                                               number=spec["number"], origin_code=spec["origin_code"])
                created.append(strain)
                strain_pk = strain.pk
            if strain_pk is not None:
                prepared[group["strain_code"]] = strain_pk
        reviewed = json.loads(json.dumps(plan, default=str))
        for row in created:
            AuditLog.objects.create(
                organization=organization, user=user, action=AuditLog.Action.CREATION,
                object_type=row._meta.model_name, object_id=str(row.pk),
                description="Reviewed variant Species reference preparation",
                metadata={"operation": REFERENCE_OPERATION, "plan_hash": expected_plan_hash,
                          "created": json.loads(json.dumps(snapshot(row), default=str)), "reason": REASON})
        if created:
            AuditLog.objects.create(
                organization=organization, user=user, action=AuditLog.Action.CREATION,
                object_type="variant_species_reference_preparation", object_id=REFERENCE_OPERATION,
                description="Variant Species reference preparation receipt",
                metadata={"operation": REFERENCE_OPERATION, "plan_hash": expected_plan_hash,
                          "reviewed_plan": reviewed, "prepared_target_strain_ids": prepared,
                          "created": [{"model": r._meta.label_lower, "pk": r.pk} for r in created]})
        return {**plan, "prepared_target_strain_ids": prepared}


def build_box_plan(organization_id, *, source_strain_ids, target_strain_ids):
    """Read only. Explicit target PKs are verified, never inferred from codes."""
    sources, targets = _validate(organization_id, source_strain_ids, target_strain_ids)
    organization = Organization.objects.filter(pk=organization_id).first()
    plan = _plan_header(BOX_OPERATION, organization, organization_id, sources)
    plan["target_strain_ids"] = targets
    entries = _box_entries(organization, sources)
    source_rows, conflicts = _sources_state(sources, organization)
    by_group, target_rows = {}, []
    eligible = eligible_strains(organization) if organization else Strain.objects.none()
    for strain in eligible.filter(pk__in=targets).select_related("species").order_by("pk"):
        key = (strain.code, strain.species.scientific_name)
        if key not in GROUPS:
            conflicts.append(f"Target Strain {strain.pk} does not match a reviewed code and Species.")
            continue
        if key in by_group:
            conflicts.append(f"Several target Strains supplied for {key[0]}.")
        by_group[key] = strain
        identity = strain_box_identity(strain, organization)
        assignment, conflict = _assignment_state(organization, strain.species, strain.code)
        problem = _local_identity_problem(strain, organization)
        conflicts += [c for c in (conflict, problem) if c]
        if identity["status"] != USABLE:
            conflicts.append(f"Target Strain {strain.pk} identity is unusable: {identity['reason']}.")
        target_rows.append({**_identity(strain), "box_identity": identity, "assignment": assignment})
    if len(target_rows) != len(targets):
        conflicts.append("A target Strain is missing or ineligible in this institution.")
    for entry in entries:
        target = by_group.get((entry["strain_code"], entry["target_species"]))
        entry["proposed"] = _identity(target) if target else None
        if entry["classification"] == CONFLICT:
            conflicts += [f"{entry['global_code']}: {c}" for c in entry["conflicts"]]
            continue
        if target is None:
            entry["classification"] = CONFLICT
            conflicts.append(f"{entry['global_code']}: no explicit reviewed target Strain.")
            continue
        if entry["current"]["strain_pk"] == target.pk:
            entry["classification"] = CORRECT
        elif entry["classification"] == CORRECT:
            entry["classification"] = CONFLICT
            conflicts.append(f"{entry['global_code']}: Box uses another Strain of the target Species.")
        box_pk = entry["box_pk"]
        # Evidence only: these rows are preserved; other Boxes are never changed.
        entry["evidence"] = {
            "measurement_count": BiologicalMeasurement.objects.filter(box_id=box_pk).count(),
            "lineage": [snapshot(row) for row in BoxLineage.objects.filter(
                Q(parent_box_id=box_pk) | Q(child_box_id=box_pk)).order_by("pk")],
            "subculture_allocation": [snapshot(row) for row in SubcultureAllocation.objects.filter(
                child_box_id=box_pk).order_by("pk")],
            "box_transfers": list(BoxTransfer.objects.filter(box_id=box_pk).order_by("pk")
                                  .values_list("pk", flat=True)),
            "transfer_import": list(BoxTransferImport.objects.filter(created_box_id=box_pk)
                                    .values_list("pk", flat=True)),
        }
    plan.update(entries=entries, sources=source_rows, targets=target_rows, conflicts=conflicts)
    plan["classification"] = CONFLICT if conflicts else (
        READY if any(e["classification"] == NEEDED for e in entries) else ALREADY_PRESENT)
    return plan


def apply_box_plan(organization_id, *, source_strain_ids, target_strain_ids, actor, expected_plan_hash):
    _validate(organization_id, source_strain_ids, target_strain_ids)
    if actor is None or not expected_plan_hash:
        raise ValueError("An explicit actor and reviewed plan hash are required.")
    with transaction.atomic():
        organization, user = _authorize(organization_id, actor)
        locked = _lock(organization, set(source_strain_ids) | set(target_strain_ids))
        plan = build_box_plan(organization_id, source_strain_ids=source_strain_ids,
                              target_strain_ids=target_strain_ids)
        _check_locked(plan, locked)
        _check_reviewed(plan, expected_plan_hash, {READY, ALREADY_PRESENT})
        reviewed = json.loads(json.dumps(plan, default=str))
        for entry in plan["entries"]:
            if entry["classification"] != NEEDED:
                continue
            changed = Box.objects.filter(pk=entry["box_pk"], organization=organization,
                                         strain_id=entry["current"]["strain_pk"]).update(
                                             strain_id=entry["proposed"]["strain_pk"])
            if changed != 1:
                raise ValueError("Box changed during application.")
            after = Box.objects.select_related("strain__species").get(pk=entry["box_pk"])
            AuditLog.objects.create(
                organization=organization, user=user, action=AuditLog.Action.UPDATE,
                object_type="box", object_id=entry["global_code"], description=REASON,
                metadata=json.loads(json.dumps({
                    "operation": BOX_OPERATION, "plan_hash": expected_plan_hash, "reviewed_plan": reviewed,
                    "box_id": entry["box_pk"], "global_code": entry["global_code"],
                    "before": {"box": entry["box"], "identity": entry["current"]},
                    "after": {"box": snapshot(after), "identity": _identity(after.strain)},
                    "evidence": entry["evidence"], "reason": REASON, "historical_effect": HISTORICAL_EFFECT,
                }, default=str)),
            )
        return plan
