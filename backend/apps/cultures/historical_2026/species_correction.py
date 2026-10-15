"""Bounded, reviewed identity corrections; never edit a shared strain."""

import hashlib
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    LocalStrainIdentity, OrganizationProvenanceCode, OrganizationSpeciesCode, Species, Strain,
)
from apps.taxonomy.scoping import eligible_strains

OPERATION = "urgent_species_20261015_v1"
V2_SHA256 = "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160"
LEDGER_SHA256 = "92e589b519e4a54eb9d094e8a6ef3dce22c6f833028481552b4c66957fb44dc1"
REASON = "Operator-confirmed scientific identity correction, approved 2026-10-15; preserve Box and QR identity."
SPECS = tuple(
    (f"CAC-JKA-2.{n:03}", "CAC-JKA-2", 'Chrysaora chesapeakei "white"',
     "Chrysaora achlyos", "CAC", "D9; operator approval 2026-10-15",
     f"Semaestomeae!B{row}; A3:A20", f"CAC-JKA-2.{n:02}")
    for n, row in enumerate((3, 7, 11, 13, 17), 1)
) + (("OCE-TAI-1.002", "OCE-TAI-1", "Pennaria disticha", "Oceaniidae sp.",
      "OCE", "D26; operator approval 2026-10-15", "Hydrozoa!B173; A173:A174", "OCE-TAI-1.02"),)


def snapshot(obj):
    return {f.attname: getattr(obj, f.attname) for f in obj._meta.concrete_fields}


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()


def _identity(strain):
    return {
        "strain": snapshot(strain), "species": snapshot(strain.species),
        "strain_pk": strain.pk, "strain_code": strain.code,
        "species_pk": strain.species_id, "species_name": strain.species.scientific_name,
    }


def build_plan(organization_id, *, cac_strain_id=None, oce_strain_id=None):
    """Read only. Explicit PKs select references, never infer them from prefixes."""
    organization = Organization.objects.filter(pk=organization_id).first()
    entries = []
    for code, namespace, old, new, aaa, decision, cells, literal in SPECS:
        entry = {"global_code": code, "box_pk": None, "current": None, "proposed": None,
                 "classification": "missing", "conflicts": [],
                 "evidence": {"decision": decision, "workbook_cells": cells,
                              "literal_source_code": literal, "expected_species": old,
                              "required_species": new}}
        entries.append(entry)
        if organization is None:
            entry["classification"] = "invalid organization or Strain"
            entry["conflicts"].append("Organization does not exist.")
            continue
        box = Box.objects.filter(organization=organization, global_code=code).first()
        target_id = cac_strain_id if aaa == "CAC" else oce_strain_id
        target = Strain.objects.filter(pk=target_id).select_related("species").first()
        if box:
            entry.update(box_pk=box.pk, box=snapshot(box), current=_identity(box.strain))
        if target:
            entry["proposed"] = _identity(target)
        if not box:
            continue
        if not target:
            entry["classification"] = "ambiguous target/reference"
            entry["conflicts"].append("Supply an existing, independently reviewed target Strain PK.")
            continue
        assignments = list(OrganizationSpeciesCode.objects.filter(
            organization=organization).filter(Q(code=aaa) | Q(species_id=target.species_id))
            .order_by("pk"))
        entry["assignments"] = [snapshot(a) for a in assignments]
        for strain in (box.strain, target):
            if not eligible_strains(organization).filter(pk=strain.pk).exists():
                entry["conflicts"].append(f"Strain {strain.pk} is not eligible in this Organization.")
            local = LocalStrainIdentity.objects.filter(strain=strain).first()
            entry.setdefault("local_identities", []).append(snapshot(local) if local else None)
            if local:
                assignment = local.species_code_assignment
                provenance = local.provenance_code_assignment
                entry.setdefault("local_assignments", []).append({
                    "species": snapshot(assignment),
                    "provenance": snapshot(provenance) if provenance else None,
                })
                if (strain.organization_id != organization.pk
                                        or assignment.organization_id != organization.pk
                        or assignment.species_id != strain.species_id
                        or (provenance and provenance.organization_id != organization.pk)):
                    entry["conflicts"].append(f"Strain {strain.pk} has inconsistent local identity.")
        if (len(assignments) != 1 or assignments[0].code != aaa
                or assignments[0].species_id != target.species_id):
            entry["conflicts"].append(f"Require an existing consistent {aaa} Species assignment; do not rewrite it here.")
        if target.species.scientific_name != new or target.code != namespace:
            entry["conflicts"].append("Target Species or exact Strain code does not match the reviewed reference.")
        if entry["conflicts"]:
            entry["classification"] = "invalid organization or Strain"
        elif box.strain_id == target.pk:
            entry["classification"] = "already correct"
        elif box.strain.species.scientific_name != old or box.strain.code != namespace:
            entry["classification"] = "unexpected current identity"
            entry["conflicts"].append("Current Species/Strain is not the approved previous identity.")
        else:
            entry["classification"] = "correction needed"
    return {"operation": OPERATION, "organization": snapshot(organization) if organization else {"id": organization_id},
            "reason": REASON, "workbook": "Suivi_2026_actualisé_v2.xlsx", "v2_sha256": V2_SHA256,
            "ledger_sha256": LEDGER_SHA256, "entries": entries}


def apply_plan(organization_id, *, actor, expected_plan_hash, cac_strain_id, oce_strain_id):
    if not expected_plan_hash or actor is None:
        raise ValueError("An explicit actor and reviewed plan hash are required.")
    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(pk=organization_id)
        memberships = list(OrganizationMembership.objects.select_for_update().filter(
            organization=organization, user_id=actor.pk))
        user = get_user_model().objects.select_for_update().get(pk=actor.pk)
        if not user.is_active or not any(m.is_active and m.role == "admin" for m in memberships):
            raise PermissionDenied("An active administrator membership in this Organization is required.")
        # Lock references before reading their identities. Existing assignment rows
        # and unique constraints prevent replacement/duplicate reference races.
        boxes = list(Box.objects.select_for_update().filter(organization=organization,
                                       global_code__in=[s[0] for s in SPECS]).order_by("pk"))
        locked_boxes = {box.global_code: box.pk for box in boxes}
        if len(boxes) != len(SPECS) or set(locked_boxes) != {spec[0] for spec in SPECS}:
            raise ValueError("Unresolved conflicts: all six Boxes must be present in the locked selection.")
        strain_ids = {b.strain_id for b in boxes} | {cac_strain_id, oce_strain_id}
        strains = list(Strain.objects.select_for_update().filter(pk__in=strain_ids).order_by("pk"))
        list(Species.objects.select_for_update().filter(pk__in=[s.species_id for s in strains]).order_by("pk"))
        list(OrganizationSpeciesCode.objects.select_for_update().filter(
            organization=organization).order_by("pk"))
        list(OrganizationProvenanceCode.objects.select_for_update().filter(
            organization=organization).order_by("pk"))
        list(LocalStrainIdentity.objects.select_for_update().filter(strain_id__in=strain_ids).order_by("pk"))
        # Include legacy eligibility witnesses, so an unowned target cannot lose
        # its last institutional Box while this batch is being applied.
        list(Box.objects.select_for_update().filter(organization=organization).filter(
            Q(global_code__in=[s[0] for s in SPECS]) | Q(strain_id__in=strain_ids)).order_by("pk"))
        plan = build_plan(organization_id, cac_strain_id=cac_strain_id, oce_strain_id=oce_strain_id)
        # Every planned write must resolve to the exact row locked above.
        planned_boxes = {entry["global_code"]: entry["box_pk"] for entry in plan["entries"]}
        if len(plan["entries"]) != len(SPECS) or planned_boxes != locked_boxes:
            raise ValueError("Unresolved conflicts: planned Box identities do not match the locked selection.")
        if fingerprint(plan) != expected_plan_hash:
            raise ValueError("Database state changed since the reviewed plan; run a new dry-run.")
        if any(e["classification"] not in {"already correct", "correction needed"} for e in plan["entries"]):
            raise ValueError("Unresolved conflicts block the entire batch.")
        for entry in plan["entries"]:
            if entry["classification"] == "already correct":
                continue
            changed = Box.objects.filter(pk=entry["box_pk"], organization=organization,
                                         strain_id=entry["current"]["strain_pk"]).update(
                                             strain_id=entry["proposed"]["strain_pk"])
            if changed != 1:
                raise ValueError("Box changed during application.")
            AuditLog.objects.create(
                organization=organization, user=user, action=AuditLog.Action.UPDATE,
                object_type="box", object_id=entry["global_code"], description=REASON,
                metadata={"operation": OPERATION, "plan_hash": expected_plan_hash,
                                          "reviewed_plan": json.loads(json.dumps(plan, default=str)),
                          "box_id": entry["box_pk"], "global_code": entry["global_code"],
                          "previous": entry["current"], "new": entry["proposed"],
                          "reason": REASON, "evidence": entry["evidence"],
                          "v2_sha256": V2_SHA256, "ledger_sha256": LEDGER_SHA256},
            )
        return plan
