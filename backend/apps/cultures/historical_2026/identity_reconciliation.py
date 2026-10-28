"""Exact CLA/SME attribution correction, without merges or shared Strain edits.

Planning is read-only. PostgreSQL application has a separate attested execution boundary.
Source correspondence and its limits are documented in docs/cla-sme-identity-reconciliation.md.
"""

from django.apps import apps

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.models import Q

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.box_codes import register_box_code
from apps.cultures.models import Box, BoxCodeNamespace, BoxLineage, SubcultureEvent
from apps.cultures.pca_identity_unification import fingerprint, json_value, snapshot
from apps.organizations.models import Organization
from apps.taxonomy.scoping import eligible_strains
from . import identity_execution as execution
from apps.taxonomy.models import (
    LocalStrainIdentity, OrganizationProvenanceCode, OrganizationSpeciesCode, Species, Strain,
)

OPERATION = "cla_sme_identity_20261027_v2"
PRODUCT_DECISION = "Anthony 2026-10-27: V2 overrides earlier 2026 identities; proven annual continuity only."
REASON = "Reviewed historical scientific identity correction; preserve Box PK, QR and all scientific history."
SPECS = tuple(
    {"family": family, "old_code": f"{old}-JKA-1.{n:03}",
     "target_code": f"CLC-JKA-1.{n:03}" if family == "cla" and n >= 9 else f"{old}-JKA-1.{n:03}",
     "old_species": old_species, "species": species, "aaa": aaa,
     "strain_code": f"{aaa}-JKA-1", "old_strain_code": f"{old}-JKA-1"}
    for family, old, aaa, old_species, species, numbers in (
        ("cla", "CLA", "CLC", "Cyanea lamarckii", "Chrysaora lactea", range(1, 11)),
        ("sme", "SME", "SSP", "Stomolophus meleagris", "Stomolophus sp.2", (1, 4, 5, 6, 7, 8, 9, 10)),
    ) for n in numbers
)
EVIDENCE = {
    "original_sha256": "14befa67441f289153576b5c53de462eaa05b4218df6d5439a44ee432f62cb67",
    "v2_sha256": "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160",
    "cla": [
        {"cell": "Semaestomeae!B77", "original": "CLA-JKA-1.09", "v2": "CLC-JKA-1.09",
         "normalized": "CLC-JKA-1.009", "proof": "Only changed cell in the same four-row source block 77-80."},
        {"cell": "Semaestomeae!B81", "original": "CLA-JKA1.10", "v2": "CLC-JKA1.10",
         "normalized": "CLC-JKA-1.010", "proof": "Only changed cell in the same four-row source block 81-84."},
    ],
    "sme": {"decision": "D8, exact eight SME-JKA-1 histories, 2020-2023: Stomolophus sp.2",
            "successor_identity": "BLOCKED: no explicit SME to SSP cross-reference in 2024 source.",
            "2023_cells": "Rhizostomeae!B129,B133,B137,B141,B145",
            "2024_candidates": "Rhizostomae!B157,B161,B165; not an approved physical equivalence"},
}


def require_local_database():
    """Compatibility entry point: validate the explicit execution configuration."""
    return execution.require_database()


def _application_tables():
    return sorted({m._meta.db_table for m in apps.get_models(include_auto_created=True)
                   if m._meta.managed and not m._meta.proxy} | {"django_migrations"})


def _schema_state():
    """Refuse migration/schema drift without running migrations or creating rows."""
    loader = MigrationLoader(connection)
    loader.check_consistent_history(connection)
    applied = set(loader.applied_migrations)
    if applied != set(loader.disk_migrations):
        raise ValueError("Unexpected migration state; deployment and schema review are required first.")
    state = {"migrations": sorted(applied)}
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
            if tables != set(_application_tables()):
                raise ValueError("Unexpected database tables; no correction is allowed against an unreviewed schema.")
            shapes = {}
            for model in apps.get_models(include_auto_created=True):
                if not model._meta.managed or model._meta.proxy:
                    continue
                table = model._meta.db_table
                columns = connection.introspection.get_table_description(cursor, table)
                if {c.name for c in columns} != {f.column for f in model._meta.local_concrete_fields}:
                    raise ValueError("Unexpected schema columns; review the deployed model contract.")
                constraints = connection.introspection.get_constraints(cursor, table)
                for field in model._meta.local_concrete_fields:
                    if field.primary_key or field.unique:
                        if not any(c.get("unique") and c.get("columns") == [field.column]
                                   for c in constraints.values()):
                            raise ValueError("Missing required primary/unique identity constraint.")
                    if field.is_relation and getattr(field, "db_constraint", False):
                        expected_fk = (field.target_field.model._meta.db_table, field.target_field.column)
                        if not any(c.get("foreign_key") == expected_fk and c.get("columns") == [field.column]
                                   for c in constraints.values()):
                            raise ValueError("Missing required historical reference constraint.")
                for constraint in model._meta.constraints:
                    actual = constraints.get(constraint.name)
                    if not actual or not (actual.get("check") or actual.get("unique")):
                        raise ValueError("A declared database integrity constraint is missing.")
                shapes[table] = {"columns": [list(c) for c in columns], "constraints": constraints}
            state["relations"] = shapes
            # Django introspection omits CHECK expressions, FK actions and partial
            # index predicates. Bind actual PostgreSQL enforcement definitions too.
            cursor.execute("""SELECT c.relname, k.conname, k.contype, k.convalidated,
                k.conenforced, k.condeferrable, k.condeferred, pg_get_constraintdef(k.oid, true)
                FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' ORDER BY c.relname, k.conname""")
            definitions = cursor.fetchall()
            if any(not row[3] or not row[4] for row in definitions):
                raise ValueError("Unvalidated or unenforced constraints require schema review.")
            state["constraint_definitions"] = definitions
            cursor.execute("""SELECT c.relname, x.relname, i.indisvalid, i.indisready,
                i.indislive, pg_get_indexdef(i.indexrelid), pg_get_expr(i.indpred, i.indrelid)
                FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
                JOIN pg_class x ON x.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' ORDER BY c.relname, x.relname""")
            indexes = cursor.fetchall()
            if any(not all(row[2:5]) for row in indexes):
                raise ValueError("Invalid or incomplete indexes require schema review.")
            state["index_definitions"] = indexes
            cursor.execute("""SELECT c.relname, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid, true)
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' ORDER BY c.relname, t.tgname""")
            state["trigger_definitions"] = cursor.fetchall()
    return fingerprint(json_value(state))


def _lock_application_tables():
    """One-off maintenance barrier; fail immediately instead of deadlocking writers.

    EXCLUSIVE permits ordinary SELECT but excludes even SELECT FOR UPDATE. Taking
    every managed table NOWAIT before row locks prevents reverse FK/namespace lock
    orders, direct writers and phantom dependencies during these small exact batches.
    This intentionally requires an operator-arranged application maintenance window.
    """
    if connection.vendor == "postgresql":
        tables = ", ".join('"public".' + connection.ops.quote_name(t) for t in _application_tables())
        with connection.cursor() as cursor:
            cursor.execute("LOCK TABLE " + tables + " IN EXCLUSIVE MODE NOWAIT")


def _authorize(organization_id, actor):
    if actor is None or getattr(actor, "pk", None) is None:
        raise ValueError("An explicit actor is required.")
    organization = Organization.objects.select_for_update(no_key=True).get(pk=organization_id)
    memberships = list(OrganizationMembership.objects.select_for_update().filter(
        organization=organization, user_id=actor.pk).order_by("pk"))
    user = get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    if not user.is_active or not any(m.is_active and m.role == "admin" for m in memberships):
        raise PermissionDenied("An active administrator of this institution is required.")
    return organization, user


def _specs(family):
    if family not in {"cla", "sme"}:
        raise ValueError("Select the exact cla or sme family.")
    return [s for s in SPECS if s["family"] == family]


def _codes(specs):
    return {s[key] for s in specs for key in ("old_code", "target_code")} | {
        s["old_code"].replace("SME-", "SSP-", 1) for s in specs if s["family"] == "sme"
    }


def _rows(queryset, lock=False):
    if lock:
        queryset = queryset.select_for_update()
    return [snapshot(row) for row in queryset.order_by("pk")]


def _dependencies(box_ids, *, organization_id, lock=False):
    """Inventory all incoming Box FKs, including through tables and future models.

    Also cover incoming measurement/event references. Nothing is reassigned.
    Ordinary laboratory writers serialize through the Box lock.
    """
    result = {}
    # A child can reference an event whose parent is outside the selected batch.
    event_ids = set(BoxLineage.objects.filter(
        Q(parent_box_id__in=box_ids) | Q(child_box_id__in=box_ids)
    ).exclude(subculture_event_id=None).values_list("subculture_event_id", flat=True))
    event_rows = _rows(SubcultureEvent.objects.filter(pk__in=event_ids), lock)
    if event_rows:
        result[SubcultureEvent._meta.label_lower] = event_rows
    frontier = {Box: set(box_ids), SubcultureEvent: event_ids}
    seen = {Box, SubcultureEvent}
    while frontier:
        following = {}
        for model in sorted(apps.get_models(include_auto_created=True), key=lambda m: (
                m._meta.model_name != "boxmovement", m._meta.label_lower)):
            query = Q(pk__in=[])
            for field in model._meta.concrete_fields:
                if field.is_relation and field.remote_field.model in frontier:
                    query |= Q(**{f"{field.attname}__in": frontier[field.remote_field.model]})
            rows = _rows(model._base_manager.filter(query), lock)
            if rows:
                existing = {r["id"]: r for r in result.get(model._meta.label_lower, [])}
                existing.update({r["id"]: r for r in rows})
                result[model._meta.label_lower] = sorted(existing.values(), key=lambda r: r["id"])
                if model not in seen:
                    following[model] = {r["id"] for r in rows}
                    seen.add(model)
        frontier = following
    # A relationship is evidence, not authorization to disclose foreign rows.
    # Invalid cross-institution links block the batch without exporting contents.
    for label, rows in list(result.items()):
        model = apps.get_model(label)
        safe = []
        for row in rows:
            foreign = row.get("organization_id", organization_id) != organization_id
            for field in model._meta.concrete_fields:
                if field.is_relation and field.remote_field.model is Box and row[field.attname] is not None:
                    foreign |= Box.objects.filter(pk=row[field.attname]).exclude(organization_id=organization_id).exists()
            if foreign:
                result["foreign_reference_blocked"] = True
            else:
                safe.append(row)
        result[label] = safe
    return result


def _local_conflicts(strain, organization_id):
    errors = []
    local = LocalStrainIdentity.objects.filter(strain=strain).select_related(
        "species_code_assignment", "provenance_code_assignment").first()
    if strain.organization_id not in (None, organization_id):
        errors.append("Current Strain belongs to another institution.")
    if local:
        assignment = local.species_code_assignment
        provenance = local.provenance_code_assignment
        if (strain.organization_id != organization_id
                or assignment.organization_id != organization_id
                or assignment.species_id != strain.species_id
                or (provenance and provenance.organization_id != organization_id)):
            errors.append("Inconsistent local Strain identity.")
    return errors


def build_plan(organization_id, *, family, target_strain_id):
    with execution.session() as binding:
        return _build_plan(organization_id, family=family, target_strain_id=target_strain_id, binding=binding)


def _build_plan(organization_id, *, family, target_strain_id, binding):
    schema_hash = _schema_state()
    specs = _specs(family)
    organization = Organization.objects.filter(pk=organization_id).first()
    # Never expose foreign Box or reference snapshots in a plan.
    target = eligible_strains(organization).filter(pk=target_strain_id).select_related("species").first()
    codes = _codes(specs)
    boxes = list(Box.objects.filter(organization_id=organization_id,
            global_code__in={s[k] for s in specs for k in ("old_code", "target_code")}).select_related("strain__species"))
    own = {b.global_code: b for b in boxes}
    occupied = set(Box.objects.filter(global_code__in=codes).values_list("global_code", flat=True))
    reserved = set(Box.objects.filter(previous_global_code__in=codes).values_list("previous_global_code", flat=True))
    receipts = list(AuditLog.objects.filter(organization_id=organization_id,
        object_type="identity_reconciliation", metadata__operation=OPERATION).order_by("pk"))
    assignments = list(OrganizationSpeciesCode.objects.filter(organization_id=organization_id).select_related("species"))
    entries = []
    for spec in specs:
        old, new = spec["old_code"], spec["target_code"]
        box = own.get(old)
        renamed = old != new and box is None
        if renamed:
            box = own.get(new)
        errors = []
        if organization is None:
            errors.append("Organization is missing.")
        if not box:
            errors.append("Exact institutional Box is missing; no creation or adoption is allowed.")
        if not target:
            errors.append("MISSING_REFERENCE: supply an existing institution-eligible target Strain PK.")
        elif target.code != spec["strain_code"] or target.species.scientific_name != spec["species"]:
            errors.append("Target Strain/Species is not the exact approved reference.")
        for aaa, name in ((spec["aaa"], spec["species"]), (spec["old_strain_code"][:3], spec["old_species"])):
            if not any(a.code == aaa and a.species.scientific_name == name for a in assignments):
                errors.append(f"MISSING_OR_INCOMPATIBLE_REFERENCE: institutional {aaa} Species assignment; use explicit preparation first.")
        if old in reserved or new in reserved:
            errors.append("A code is reserved by another historical identity; separate review required.")
        if old != new and old in occupied and new in occupied:
            errors.append("Both original and corrected identities exist; merging is forbidden.")
        # A separate SSP Box is not an attribution conflict: D8 corrects the SME
        # observations without adopting, renaming, merging or touching that Box.
        if target:
            errors.extend(_local_conflicts(target, organization_id))
        if box:
            errors.extend(_local_conflicts(box.strain, organization_id))
            if box.merged_into_id or box.previous_global_code:
                errors.append("Merged or previously renamed identities require separate review.")
            if (box.strain.global_identity_id is not None and target
                    and target.global_identity_id is not None
                    and box.strain.global_identity_id != target.global_identity_id):
                errors.append("Incompatible established global Strain identities; separate review required.")
            if renamed or box.strain_id == target_strain_id:
                proven = any(
                    e.get("box_pk") == box.pk and e.get("old_code") == old and e.get("target_code") == new
                    and e.get("after", {}).get("id") == box.pk
                    and e.get("after", {}).get("global_code") == new
                    and e.get("after", {}).get("strain_id") == target_strain_id
                    for receipt in receipts for e in receipt.metadata.get("entries", [])
                )
                if not proven or box.strain_id != target_strain_id:
                    errors.append("Pre-existing corrected Box lacks this operation's exact PK/identity receipt.")
            elif box.strain_id != target_strain_id and (
                    box.strain.code != spec["old_strain_code"]
                    or box.strain.species.scientific_name not in {spec["old_species"], spec["species"]}):
                errors.append("Unexpected persisted old Strain/Species.")
        proposed = {"strain_id": target_strain_id, "global_code": new}
        entries.append({**spec, "box_pk": box.pk if box else None,
            "box": snapshot(box) if box else None, "proposed": proposed,
                        "corresponding_ssp_present": family == "sme" and old.replace("SME-", "SSP-", 1) in occupied,
                        "physical_continuity": "NOT_ASSERTED",
            "classification": "BLOCKED" if errors else (
                "ALREADY_CORRECT" if box.strain_id == target_strain_id and box.global_code == new else "READY"),
            "conflicts": errors})
    strain_ids = {b.strain_id for b in boxes} | ({target.pk} if target else set())
    strains = list(eligible_strains(organization).filter(pk__in=strain_ids).order_by("pk"))
    visible_strain_ids = {s.pk for s in strains}
    namespaces = {c.rsplit(".", 1)[0] for c in codes}
    dependencies = _dependencies([b.pk for b in boxes], organization_id=organization_id)
    if dependencies.get("foreign_reference_blocked"):
        for entry in entries:
            entry["classification"] = "BLOCKED"
            entry["conflicts"].append("Foreign operational dependency requires separate review; contents withheld.")
    return {"operation": OPERATION, "execution": binding, "schema_hash": schema_hash,
            "product_decision": PRODUCT_DECISION, "evidence": EVIDENCE,
        "organization": snapshot(organization) if organization else {"id": organization_id},
        "family": family, "target_strain_id": target_strain_id, "entries": entries,
        "strains": [snapshot(s) for s in strains],
        "species": _rows(Species.objects.filter(pk__in={s.species_id for s in strains})),
        "assignments": [snapshot(a) for a in sorted(assignments, key=lambda a: a.pk)],
        "local_identities": _rows(LocalStrainIdentity.objects.filter(strain_id__in=visible_strain_ids)),
        "provenances": _rows(OrganizationProvenanceCode.objects.filter(organization_id=organization_id)),
        "dependencies": dependencies,
        "audits": _rows(AuditLog.objects.filter(organization_id=organization_id).filter(
                    Q(object_type="box", object_id__in=codes)
                    | Q(object_type="identity_reconciliation", metadata__operation=OPERATION)
                    | Q(metadata__box_id__in=[b.pk for b in boxes]))),
                "target_eligibility_witnesses": _rows(Box.objects.filter(
                    organization_id=organization_id, strain_id=target.pk)) if target and target.organization_id is None else [],
        "namespaces": _rows(BoxCodeNamespace.objects.filter(namespace__in=namespaces)),
        "coexisting_ssp_boxes": _rows(Box.objects.filter(organization_id=organization_id,
                    global_code__in={c for c in codes if c.startswith("SSP-")})) if family == "sme" else [],
                "occupied_codes": sorted(occupied), "reserved_codes": sorted(reserved)}


def summary(plan):
    """Operator output: identities and state digests, never notes or personal data."""
    result = {key: plan[key] for key in ("operation", "execution", "schema_hash", "family", "target_strain_id")}
    result["organization_id"] = plan["organization"]["id"]
    result["entries"] = []
    for entry in plan["entries"]:
        current = entry["box"] or {}
        strain = next((s for s in plan["strains"] if s["id"] == current.get("strain_id")), {})
        species = next((s for s in plan["species"] if s["id"] == strain.get("species_id")), {})
        row = {key: entry[key] for key in ("old_code", "target_code", "box_pk", "classification",
                "conflicts", "corresponding_ssp_present", "physical_continuity")}
        row.update(current_strain_id=current.get("strain_id"), current_strain_code=strain.get("code"),
                   current_species=species.get("scientific_name"))
        result["entries"].append(row)
    result["plan_hash"] = fingerprint(plan)
    result["preserved_state_hash"] = fingerprint(plan["dependencies"])
    return result


def apply_plan(organization_id, *, family, target_strain_id, actor, expected_plan_hash):
    if actor is None or not expected_plan_hash:
        raise ValueError("An explicit actor and reviewed plan hash are required.")
    specs = _specs(family)
    codes = _codes(specs)
    with execution.session(apply=True, organization_id=organization_id, family=family,
            target_strain_id=target_strain_id, actor_id=actor.pk,
            expected_plan_hash=expected_plan_hash) as binding:
        _lock_application_tables()
        boxes = list(Box.objects.select_for_update().filter(
            organization_id=organization_id,
            global_code__in={s[k] for s in specs for k in ("old_code", "target_code")}).order_by("pk"))
        organization, user = _authorize(organization_id, actor)
        strain_ids = {b.strain_id for b in boxes} | {target_strain_id}
        strains = list(Strain.objects.select_for_update().filter(pk__in=strain_ids).order_by("pk"))
        _rows(Species.objects.filter(pk__in={s.species_id for s in strains}), True)
        _rows(OrganizationSpeciesCode.objects.filter(organization=organization), True)
        _rows(OrganizationProvenanceCode.objects.filter(organization=organization), True)
        _rows(LocalStrainIdentity.objects.filter(strain_id__in=strain_ids), True)
        _dependencies([b.pk for b in boxes], organization_id=organization_id, lock=True)
        _rows(AuditLog.objects.filter(organization=organization), True)
        _rows(BoxCodeNamespace.objects.filter(namespace__in={c.rsplit('.', 1)[0] for c in codes}), True)
        plan = _build_plan(organization_id, family=family, target_strain_id=target_strain_id, binding=binding)
        if {e["box_pk"] for e in plan["entries"]} != {b.pk for b in boxes}:
            raise ValueError("The exact planned and locked Box sets differ.")
        if fingerprint(plan) != expected_plan_hash:
            raise ValueError("Persisted state changed since review; run a new dry-run.")
        if any(e["classification"] == "BLOCKED" for e in plan["entries"]):
            raise ValueError("Unresolved conflicts block this entire family.")
        changed = [e for e in plan["entries"] if e["classification"] == "READY"]
        if not changed:
            return plan
        before_dependencies = plan["dependencies"]
        for entry in changed:
            # Register prescribed codes, never allocate a new physical Box.
            if entry["old_code"] != entry["target_code"]:
                for code in (entry["old_code"], entry["target_code"]):
                    register_box_code(code, namespace=code.rsplit(".", 1)[0])
            updated = Box.objects.filter(pk=entry["box_pk"], organization=organization,
                global_code=entry["box"]["global_code"], strain_id=entry["box"]["strain_id"]).update(**entry["proposed"])
            if updated != 1:
                raise ValueError("Box changed during correction.")
            AuditLog.objects.create(organization=organization, user=user, action=AuditLog.Action.UPDATE,
                object_type="box", object_id=entry["target_code"], description=REASON,
                metadata={"operation": OPERATION, "plan_hash": expected_plan_hash,
                          "previous": entry["box"], "new": snapshot(Box.objects.get(pk=entry["box_pk"])),
                          "evidence": EVIDENCE, "product_decision": PRODUCT_DECISION})
        # The inventory may contain Box itself through lineage references. Only
        # those exact approved Box deltas may differ; every other row stays exact.
        after_dependencies = _dependencies([b.pk for b in boxes], organization_id=organization_id)
        expected_dependencies = json_value(before_dependencies)
        for row in expected_dependencies.get("cultures.box", []):
            for entry in changed:
                if row["id"] == entry["box_pk"]:
                    row.update(entry["proposed"])
        if after_dependencies != expected_dependencies:
            raise ValueError("A scientific or historical dependency changed during correction.")
        receipt_entries = [{"box_pk": e["box_pk"], "old_code": e["old_code"], "target_code": e["target_code"],
                            "after": snapshot(Box.objects.get(pk=e["box_pk"]))} for e in plan["entries"]]
        AuditLog.objects.create(organization=organization, user=user, action=AuditLog.Action.UPDATE,
            object_type="identity_reconciliation", object_id=family, description=REASON,
            metadata={"operation": OPERATION, "plan_hash": expected_plan_hash, "reviewed_plan": plan,
                      "entries": receipt_entries, "product_decision": PRODUCT_DECISION, "evidence": EVIDENCE})
        return plan
