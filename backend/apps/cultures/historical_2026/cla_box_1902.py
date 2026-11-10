"""Exact, reviewed correction of one physical Box; never a CLA batch receipt."""


import json

from django.db import connection

from apps.audit.models import AuditLog
from apps.measurements import box_1902_sql as capability
from apps.cultures.box_identity import strain_box_identity, USABLE
from apps.cultures.models import Box, BoxCodeNamespace
from apps.cultures.pca_identity_unification import json_value, snapshot
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    BiologicalProvenance, LocalStrainIdentity, OrganizationProvenanceCode,
    OrganizationSpeciesCode, Species, Strain,
)
from apps.taxonomy.scoping import eligible_strains

from . import identity_execution as execution
from . import identity_reconciliation as reconciliation
from .identity_references import _lactea_candidate

OPERATION = execution.BOX_1902_OPERATION
FAMILY = "cla-box-1902"
OLD_CODE = "CLA-JKA-1.009"
TARGET_CODE = "CLC-JKA-1.009"
LOCAL_CODE = "CLA-JKA-1.09"
RECEIPT = "cla_box_1902_correction"
REASON = capability.REASON
fingerprint = execution.fingerprint

PRESERVATION_CONTRACT = capability.OPERATION


def _require_preservation_capability():
    """Only the independently provisioned executor may use this operation."""
    capability.attest(connection, allow_retired=False)
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user")
        if cursor.fetchone() != (capability.EXECUTOR, capability.EXECUTOR):
            raise ValueError("The dedicated Box 1902 executor is required; v1 approvals are invalid.")


def _scope(organization_id, box_id, source_strain_id, source_species_id, target_strain_id):
    if any(type(v) is not int for v in (organization_id, box_id, source_strain_id, source_species_id, target_strain_id)):
        raise ValueError("Explicit integer identity PKs are required.")
    if (organization_id, box_id, source_strain_id, source_species_id) != (1, 1902, 213, 131):
        raise ValueError("Only organization 1, Box 1902, source Strain 213 and Species 131 are authorized.")
    if target_strain_id != 285:
        raise ValueError("Only target Strain 285 / Species 164 is authorized.")


def build_plan(organization_id, *, box_id, source_strain_id, source_species_id, target_strain_id):
    _scope(organization_id, box_id, source_strain_id, source_species_id, target_strain_id)
    with execution.session(operation=OPERATION) as binding:
        return _build_plan(target_strain_id, binding)


def _build_plan(target_strain_id, binding):
    errors = []
    if connection.vendor != "postgresql":
        raise ValueError("Box 1902 preservation requires the independently provisioned PostgreSQL capability.")
    capability.attest(connection)
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.box_1902_manifest_v2()")
        manifest = cursor.fetchone()[0]
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
    organization = Organization.objects.filter(pk=1).first()
    box = Box.objects.filter(pk=1902, organization_id=1).select_related("strain__species").first()
    strains = eligible_strains(organization).select_related("species")
    source = strains.filter(pk=213).first()
    target = strains.filter(pk=target_strain_id).first()
    if organization is None or organization.name != "Aquarium de Paris" or not organization.is_active:
        errors.append("The exact active organization 1 Aquarium de Paris is required.")
    if source is None or (source.code, source.species_id, source.species.scientific_name) != (
            "CLA-JKA-1", 131, "Cyanea lamarckii"):
        errors.append("The exact source Strain 213 / Species 131 identity is required.")
    if target is None or (target.pk, target.organization_id, target.code, target.species_id,
                          target.species.scientific_name) != (
            285, 1, "CLC-JKA-1", 164, "Chrysaora lactea"):
        errors.append("MISSING_OR_INCOMPATIBLE_REFERENCE: explicit eligible target CLC-JKA-1 / Chrysaora lactea required.")
    for strain in (source, target):
        if strain:
            errors.extend(reconciliation._local_conflicts(strain, 1))
            if strain_box_identity(strain, organization)["status"] != USABLE:
                errors.append("Inconsistent Strain code components or local identity.")
    if target:
        if source and source.global_identity_id and target.global_identity_id and source.global_identity_id != target.global_identity_id:
            errors.append("Incompatible established global Strain identities.")
    candidates = [s for s in Species.objects.order_by("pk") if _lactea_candidate(s.scientific_name)]
    if target and (len(candidates) != 1 or candidates[0].pk != target.species_id):
        errors.append("Ambiguous target Species candidates require separate review.")
    assignments = reconciliation._rows(OrganizationSpeciesCode.objects.filter(organization_id=1))
    for code, species_id in (("CLA", 131), ("CLC", target.species_id if target else None)):
        if not any(a["code"] == code and a["species_id"] == species_id for a in assignments):
            errors.append(f"MISSING_OR_INCOMPATIBLE_REFERENCE: institutional {code} assignment; prepare separately.")
    receipts = reconciliation._rows(AuditLog.objects.filter(
        organization_id=1, object_type=RECEIPT, object_id="1902", metadata__operation=OPERATION))
    corrected = bool(box and box.global_code == TARGET_CODE and box.strain_id == target_strain_id)
    if box is None:
        errors.append("The exact institutional Box 1902 is missing; no adoption or creation allowed.")
    else:
        if box.local_code != LOCAL_CODE or box.status != Box.Status.INACTIVE:
            errors.append("Box local identity or inactive status differs from the approved state.")
        if box.merged_into_id or box.previous_global_code:
            errors.append("Merged or previously renamed identity requires separate review.")
        if not corrected and (box.global_code != OLD_CODE or box.strain_id != 213):
            errors.append("Box source identity differs from the exact approved state.")
        if corrected:
            proven = any(
                r["metadata"].get("before", {}).get("id") == 1902
                and r["metadata"]["before"].get("organization_id") == 1
                and r["metadata"]["before"].get("global_code") == OLD_CODE
                and r["metadata"]["before"].get("strain_id") == 213
                and r["metadata"]["before"].get("local_code") == LOCAL_CODE
                and r["metadata"]["before"].get("status") == Box.Status.INACTIVE
                and r["metadata"].get("source_species_id") == 131
                and all(r["metadata"].get("after", {}).get(k) == v for k, v in {
                    "id": 1902, "organization_id": 1, "global_code": TARGET_CODE,
                    "strain_id": target_strain_id, "local_code": LOCAL_CODE}.items())
                for r in receipts)
            if not proven:
                errors.append("Corrected identity lacks this one-Box operation's exact receipt.")
    codes = (OLD_CODE, TARGET_CODE)
    collision = Box.objects.filter(global_code__in=codes).exclude(pk=1902).exists()
    reservation = Box.objects.filter(previous_global_code__in=codes).exists()
    if collision or reservation:
        errors.append("Old or target namespace collision or historical reservation; merging is forbidden.")
    dependencies = reconciliation._dependencies([1902] if box else [], organization_id=1)
    if dependencies.get("foreign_reference_blocked"):
        errors.append("Foreign operational dependency requires separate review; contents withheld.")
    # PostgreSQL invalidates associations by the biology's owning Box, not by
    # other Box references or transitive dependency membership.
    biology_ids = {row["id"] for row in dependencies.get("measurements.biologicalmeasurement", [])
                   if row["box_id"] == 1902}
    temperature_invalidations = [row["id"] for row in dependencies.get("measurements.historicaltemperaturereading", [])
                                 if connection.vendor == "postgresql"
                                 and row["biological_measurement_id"] in biology_ids
                                 and row["association_status"] == "confirmed"]
    references = [s for s in (source, target) if s]
    provenances = reconciliation._rows(OrganizationProvenanceCode.objects.filter(organization_id=1))
    return {"operation": OPERATION, "preservation_contract": PRESERVATION_CONTRACT, "manifest": manifest,
            "execution": dict(binding), "schema_hash": reconciliation._schema_state(),
            "organization_id": 1, "box_id": 1902, "source_strain_id": 213, "source_species_id": 131,
            "target_strain_id": target_strain_id, "reason": REASON,
            "classification": "BLOCKED" if errors else "ALREADY_CORRECT" if corrected else "READY",
            "conflicts": errors, "organization": snapshot(organization) if organization else None,
            "before": snapshot(box) if box else None,
            "proposed": {"global_code": TARGET_CODE, "strain_id": target_strain_id},
            "strains": [snapshot(s) for s in references],
            "species": reconciliation._rows(Species.objects.filter(pk__in={s.species_id for s in references} | {s.pk for s in candidates})),
            "assignments": assignments, "provenances": provenances,
            "biological_provenances": reconciliation._rows(BiologicalProvenance.objects.filter(
                pk__in={p["biological_provenance_id"] for p in provenances})),
            "local_identities": reconciliation._rows(LocalStrainIdentity.objects.filter(strain_id__in=[s.pk for s in references])),
            "boxes": reconciliation._rows(Box.objects.filter(organization_id=1)),
            "dependencies": dependencies,
            "temperature_association_invalidations": [],
            "temperature_association_preservations": temperature_invalidations,
            "audits": reconciliation._rows(AuditLog.objects.filter(organization_id=1)),
            "namespaces": reconciliation._rows(BoxCodeNamespace.objects.filter(namespace__in=("CLA-JKA-1", "CLC-JKA-1"))),
            "collision": collision, "reservation": reservation}


def apply_plan(organization_id, *, box_id, source_strain_id, source_species_id, target_strain_id,
               actor, expected_plan_hash):
    _scope(organization_id, box_id, source_strain_id, source_species_id, target_strain_id)
    if actor is None or not getattr(actor, "pk", None) or not expected_plan_hash:
        raise ValueError("An explicit actor and reviewed plan hash are required.")
    with execution.session(apply=True, organization_id=1, family=FAMILY, target_strain_id=target_strain_id,
                           actor_id=actor.pk, expected_plan_hash=expected_plan_hash, operation=OPERATION) as binding:
        _require_preservation_capability()
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.box_1902_lock_v2(%s)", [actor.pk])
        # The SQL maintenance barrier already locks these rows and authorizes the
        # active institutional Admin. Recheck through Django without DML grants.
        from django.contrib.auth import get_user_model
        from apps.accounts.models import OrganizationMembership
        from django.core.exceptions import PermissionDenied
        if (not get_user_model().objects.filter(pk=actor.pk, is_active=True).exists()
                or not OrganizationMembership.objects.filter(
                    user_id=actor.pk, organization_id=1, is_active=True, role="admin").exists()):
            raise PermissionDenied("An active administrator of this institution is required.")
        plan = _build_plan(target_strain_id, binding)
        if fingerprint(plan) != expected_plan_hash:
            raise ValueError("Persisted state changed since review; run a new dry-run.")
        if plan["classification"] == "BLOCKED":
            raise ValueError("Unresolved conflicts block the one-Box correction.")
        if plan["classification"] == "ALREADY_CORRECT":
            return plan
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.box_1902_apply_v2(%s,%s)", [actor.pk, expected_plan_hash])
        expected_boxes = json_value(plan["boxes"])
        for row in expected_boxes:
            if row["id"] == 1902:
                row.update(plan["proposed"])
        expected_dependencies = json_value(plan["dependencies"])
        for row in expected_dependencies.get("cultures.box", []):
            if row["id"] == 1902:
                row.update(plan["proposed"])

        if (reconciliation._rows(Box.objects.filter(organization_id=1)) != expected_boxes
                or reconciliation._dependencies([1902], organization_id=1) != expected_dependencies):
            raise ValueError("Scientific or historical state changed during correction.")
        after = snapshot(Box.objects.get(pk=1902, organization_id=1))
        receipts = AuditLog.objects.filter(
            organization_id=1, user_id=actor.pk, object_type=RECEIPT, object_id="1902",
            metadata__operation=OPERATION, metadata__plan_hash=expected_plan_hash)
        if receipts.count() != 1:
            raise ValueError("The protected correction receipt is missing or ambiguous.")
        if (not receipts.first().metadata.get("after", {}).get("global_code") == after["global_code"]
                or [row for row in reconciliation._rows(AuditLog.objects.filter(organization_id=1))
                    if row["metadata"].get("operation") != OPERATION]
                != [row for row in plan["audits"] if row["metadata"].get("operation") != OPERATION]):
            raise ValueError("Correction audit postconditions failed.")
        return plan


def summary(plan):
    """Public operator output excludes notes, measurements and prior audit contents."""
    return {key: plan[key] for key in ("operation", "execution", "organization_id", "box_id",
            "source_strain_id", "source_species_id", "target_strain_id", "classification", "conflicts")} | {
                "old_code": OLD_CODE, "target_code": TARGET_CODE, "local_code": LOCAL_CODE,
                "plan_hash": fingerprint(plan), "preserved_state_hash": fingerprint(plan["dependencies"])}
