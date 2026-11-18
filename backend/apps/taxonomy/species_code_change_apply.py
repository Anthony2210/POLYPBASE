"""Internal, fail-closed application of one institutional AAA change.

Lock order: Local ID registry -> organization -> membership -> fresh User ->
PostgreSQL table fences (sorted) -> profile/assignment -> Strains -> local
identities -> Boxes (each by PK) -> old/new namespaces (sorted). PostgreSQL row
locks use NOWAIT too: an older writer may hold an organization/taxonomy row and
then wait for our registry lock. Waiting on that row would invert the order.

The registry alone cannot exclude raw Strain/assignment writers or Box updates
of strain_id alone. Sorted SHARE ROW EXCLUSIVE NOWAIT fences cover just the
identity state tables used here. The namespace table instead uses EXCLUSIVE
NOWAIT to exclude ROW SHARE holders before locked_namespace's blocking row
locks. Fences exclude DML and taxonomy phantoms until commit without disabling
any trigger. Existing conflicting holders fail closed instead of inverting locks.
PostgreSQL requires READ COMMITTED, checked before coordination or state reads;
a SERIALIZABLE snapshot established before the fences could miss a raw insert.
These brief table-wide fences also delay unrelated institutions' identity writes.
SQLite is supported for isolated functional tests, not PostgreSQL lock proof.
Call from a fresh transaction: callers must not already hold identity row locks.
Activity-only dependency counts in the advisory plan are not frozen; scientific
records, tags, snapshots and historical audit entries are never rewritten.
"""

import re
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.box_aliases import (
    canonical_box_id, register_box_local_id_alias, validate_local_id_claim,
)
from apps.cultures.box_codes import (
    lock_local_id_registry, locked_namespace, register_box_code,
)
from apps.cultures.box_identity import CANONICAL_STRAIN_CODE, strain_box_identity
from apps.cultures.models import Box, BoxCodeNamespace, BoxLocalIdAlias
from apps.organizations.models import Organization

from .institution_species_services import (
    InstitutionSpeciesConflict, InstitutionSpeciesInvalid, InstitutionSpeciesNotFound,
    require_institution_admin,
)
from .models import (
    InstitutionSpecies, LocalStrainIdentity, OrganizationProvenanceCode,
    OrganizationSpeciesCode, SpeciesCodeChangeRequest, Strain,
)
from .species_code_change import clean_proposed_code, plan_species_code_change

_CANONICAL_BOX = re.compile(r"[A-Z]{3}-[A-Z]{3}-[0-9]+\.[0-9]+")


def _require_supported_isolation():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] != "read committed":
                raise InstitutionSpeciesConflict("species_code_change_isolation_unsupported")


def _locked(queryset):
    return queryset.select_for_update(nowait=connection.vendor == "postgresql")


def _authorize(user, organization):
    """Never authorize a replay using cached user flags or membership state."""
    if (user is None or not getattr(user, "is_authenticated", False)
            or getattr(user, "pk", None) is None
            or not isinstance(organization, Organization) or organization.pk is None):
        raise PermissionDenied()
    organization = _locked(Organization.objects.all()).filter(pk=organization.pk).first()
    if organization is None:
        raise PermissionDenied()
    membership = _locked(OrganizationMembership.objects.all()).filter(
        organization=organization, user_id=user.pk,
    ).first()
    actor = _locked(get_user_model().objects.all()).filter(pk=user.pk).first()
    if actor is None or not actor.is_active or not organization.is_active:
        raise PermissionDenied()
    if not actor.is_superuser and (
        membership is None or not membership.is_active
        or membership.role != OrganizationMembership.Role.ADMIN
    ):
        raise PermissionDenied()
    require_institution_admin(actor, organization)
    return actor, organization


def _fence_identity_writers():
    if connection.vendor == "sqlite":
        return
    if connection.vendor != "postgresql":
        raise InstitutionSpeciesConflict("species_code_change_backend_unsupported")
    models = (
        InstitutionSpecies, OrganizationSpeciesCode, OrganizationProvenanceCode,
        Strain, LocalStrainIdentity, Box, BoxCodeNamespace,
    )
    with connection.cursor() as cursor:
        # Keep one global table order even though the namespace mode is stronger.
        # NOWAIT is essential: legacy writers may already wait for our registry.
        for name in sorted(model._meta.db_table for model in models):
            mode = "EXCLUSIVE" if name == BoxCodeNamespace._meta.db_table else "SHARE ROW EXCLUSIVE"
            table = connection.ops.quote_name(name)
            cursor.execute(f"LOCK TABLE {table} IN {mode} MODE NOWAIT")


def _affected_rows(profile, organization, assignment):
    boxes = Box.objects.filter(organization=organization, strain__species_id=profile.species_id)
    strain_query = Strain.objects.filter(species_id=profile.species_id).filter(
        Q(organization=organization) | Q(pk__in=boxes.values("strain_id")),
    )
    strains = list(_locked(strain_query).order_by("pk"))
    identity_query = LocalStrainIdentity.objects.filter(
        Q(strain_id__in=[row.pk for row in strains])
        | (Q(species_code_assignment=assignment) if assignment else Q(pk__in=[])),
    )
    identities = list(_locked(identity_query).order_by("pk"))
    # Do not lock joined taxonomic rows in an unspecified database join order.
    box_ids = list(boxes.order_by("pk").values_list("pk", flat=True))
    boxes = list(_locked(Box.objects.filter(pk__in=box_ids)).order_by("pk"))
    return strains, identities, boxes


def _unsupported_blockers(strains, identities, boxes, organization, assignment):
    """Conservative mutation support, stricter than the advisory planner.

    Missing optional identity/reference records are not biological contradictions.
    Existing records must agree; nothing here creates or claims such records.
    """
    blockers = []

    def block(code, **details):
        blockers.append({"code": code, "category": "unsupported", **details})

    used_elsewhere = set(Box.objects.filter(
        strain_id__in=[row.pk for row in strains],
    ).exclude(organization=organization).values_list("strain_id", flat=True))
    for strain in strains:
        # Do not name foreign assignment/provenance records in an error.
        if strain.organization_id != organization.pk or strain.pk in used_elsewhere:
            block("strain_not_exclusively_owned", strain_id=strain.pk)
        if CANONICAL_STRAIN_CODE.fullmatch(strain.code or "") is None:
            block("noncanonical_strain_unsupported", strain_id=strain.pk)
        elif strain_box_identity(strain, organization)["status"] != "usable":
            block("strain_identity_inconsistent", strain_id=strain.pk)
    ids = {row.pk for row in strains if row.organization_id == organization.pk}
    for identity in identities:
        if (identity.strain_id not in ids or assignment is None
                or identity.species_code_assignment_id != assignment.pk):
            block("local_identity_inconsistent")
    for box in boxes:
        if box.merged_into_id is None and _CANONICAL_BOX.fullmatch(box.global_code or "") is None:
            block("noncanonical_box_unsupported", box_id=box.pk)
        if box.global_code in Box._reviewed_identity_codes:
            block("reviewed_identity_protected", box_id=box.pk)
    return blockers


def _verify_full_plan(plan, strains, boxes):
    strain_items = {row["id"]: row for row in plan["strains"]}
    box_items = {row["id"]: row for row in plan["boxes"]}
    if set(strain_items) != {row.pk for row in strains} or set(box_items) != {row.pk for row in boxes}:
        raise InstitutionSpeciesConflict("species_code_change_plan_changed")
    proposed = plan["proposed_code"]
    for strain in strains:
        item = strain_items[strain.pk]
        if (item["action"] != "rename" or item["current_code"] != strain.code
                or item["proposed_code"] != proposed + strain.code[3:]):
            raise InstitutionSpeciesConflict("species_code_change_plan_incomplete")
    codes = {row.pk: row.code for row in strains}
    for box in boxes:
        item = box_items[box.pk]
        if box.merged_into_id is not None:
            if item["action"] != "unchanged" or item["proposed_local_id"] is not None:
                raise InstitutionSpeciesConflict("species_code_change_plan_incomplete")
            continue
        if (item["action"] != "rename" or item["current_local_id"] != box.global_code
                or not box.global_code.startswith(codes[box.strain_id] + ".")
                or item["proposed_local_id"] != proposed + box.global_code[3:]):
            raise InstitutionSpeciesConflict("species_code_change_plan_incomplete")


def _retain_source_alias(box, organization):
    """Keep the caller-confirmed current ID, without moving an existing alias.

    A donor may already register this exact code for the same canonical Box.
    register_box_local_id_alias is intentionally idempotent only for the same
    source, so that legitimate case is validated here and retained as-is.
    """
    code = box.global_code
    existing = BoxLocalIdAlias.objects.filter(code=code).select_related("box").first()
    if existing is None:
        register_box_local_id_alias(box=box, code=code, organization_id=organization.pk)
        return
    validate_local_id_claim(code, box=box)
    if (existing.box.organization_id != organization.pk
            or canonical_box_id(existing.box) != canonical_box_id(box)):
        raise InstitutionSpeciesConflict("species_code_change_identity_conflict")
    register_box_code(code, namespace=box.strain.code)


def _apply(*, user, organization, profile_pk, proposed, fingerprint, request_id):
    with transaction.atomic():
        _require_supported_isolation()
        lock_local_id_registry()
        actor, organization = _authorize(user, organization)
        intent = {"profile": profile_pk, "proposed_code": proposed, "expected_plan_fingerprint": fingerprint}
        previous = SpeciesCodeChangeRequest.objects.filter(
            organization=organization, actor=actor, request_id=request_id,
        ).first()
        if previous is not None:
            if previous.intent != intent:
                raise InstitutionSpeciesConflict("species_code_change_request_conflict")
            return previous, False
        _fence_identity_writers()
        profile = _locked(InstitutionSpecies.objects.for_organization(organization)).filter(pk=profile_pk).first()
        if profile is None:
            raise InstitutionSpeciesNotFound()
        assignment = _locked(OrganizationSpeciesCode.objects.all()).filter(
            organization=organization, species_id=profile.species_id,
        ).first()
        strains, identities, boxes = _affected_rows(profile, organization, assignment)
        plan = plan_species_code_change(
            user=actor, organization=organization, profile_pk=profile.pk, proposed_code=proposed,
        )
        if plan["plan_fingerprint"] != fingerprint:
            raise InstitutionSpeciesConflict("species_code_change_plan_changed")
        blockers = plan["blockers"] + _unsupported_blockers(
            strains, identities, boxes, organization, assignment,
        )
        if blockers:
            raise InstitutionSpeciesConflict("species_code_change_blocked", blockers=blockers)
        _verify_full_plan(plan, strains, boxes)

        # Plan first: creating target namespace rows would itself block the plan.
        pairs = [(row.code, proposed + row.code[3:]) for row in strains]
        names = {name for pair in pairs for name in pair}
        # locked_namespace also locks the reviewed correction pair together.
        if names.intersection({"CLA-JKA-1", "CLC-JKA-1"}):
            names.update({"CLA-JKA-1", "CLC-JKA-1"})
        namespaces = {name: locked_namespace(name) for name in sorted(names)}
        for old, new in pairs:
            target = namespaces[new]
            floor = max(target.high_water, namespaces[old].high_water)
            if floor != target.high_water:
                target.high_water = floor
                target.save(update_fields=["high_water"])
        # Capture every source ID before any rename. Merged records stay read-only.
        for box in boxes:
            if box.merged_into_id is None:
                _retain_source_alias(box, organization)
        old_code, old_revision = assignment.code, profile.revision
        assignment.code = proposed
        assignment.save(update_fields=["code"])
        strain_mapping = []
        for strain in strains:
            old = strain.code
            strain.code = proposed + old[3:]
            strain.save(update_fields=["code"])
            strain_mapping.append({"id": strain.pk, "old_code": old, "new_code": strain.code})
        strains_by_id = {row.pk: row for row in strains}
        box_mapping = []
        for box in boxes:
            if box.merged_into_id is not None:
                continue
            old = box.global_code
            # Box.save registers the code against its now-renamed Strain.
            box.strain = strains_by_id[box.strain_id]
            box.global_code = proposed + old[3:]
            box.save(update_fields=["global_code"])
            box_mapping.append({"id": box.pk, "old_local_id": old, "new_local_id": box.global_code})
        profile.revision += 1
        profile.save(update_fields=["revision"])
        result = {
            "operation": "species_code_change", "batch_id": str(uuid.uuid4()),
            "request_id": str(request_id),
            "organization_id": organization.pk, "profile_id": profile.pk,
            "species_id": profile.species_id, "assignment_id": assignment.pk,
            "old_code": old_code, "new_code": proposed,
            "old_revision": old_revision, "new_revision": profile.revision,
            "plan_fingerprint": fingerprint, "strains": strain_mapping, "boxes": box_mapping,
            "retained_merged_box_ids": [box.pk for box in boxes if box.merged_into_id is not None],
        }
        AuditLog.objects.create(
            organization=organization, user=actor, action=AuditLog.Action.UPDATE,
            object_type="institution_species", object_id=str(profile.pk),
            description="Institutional species code changed", metadata=result,
        )
        # Local identities still point at this SAME assignment; no fake history,
        # no code snapshot rewrite, no allocation counter or scientific writes.
        receipt = SpeciesCodeChangeRequest.objects.create(
            organization=organization, actor=actor, profile=profile,
            request_id=request_id, intent=intent, result=result,
        )
        return receipt, True


def apply_species_code_change(*, user, organization, profile_pk, proposed_code,
                              expected_plan_fingerprint, request_id):
    """Return (SpeciesCodeChangeRequest, created), or raise a domain error.

    InstitutionSpeciesInvalid.errors: field -> stable reason codes.
    InstitutionSpeciesConflict.code/.details: request conflict, changed/incomplete
    plan, blocked (details.blockers), identity/validation/database conflict, busy,
    or unsupported backend/isolation. PostgreSQL isolation failures use
    species_code_change_isolation_unsupported. No raw DB/foreign data is returned.
    PermissionDenied and InstitutionSpeciesNotFound retain existing semantics.
    Replays reauthorize but return the original result, even after later changes.
    Only successful requests persist; every failure rolls back aliases and audit.
    """
    proposed = clean_proposed_code(proposed_code)
    errors = {}
    if type(profile_pk) is not int or profile_pk <= 0:
        errors["profile_pk"] = ["invalid"]
    if (not isinstance(expected_plan_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_plan_fingerprint) is None):
        errors["expected_plan_fingerprint"] = ["invalid"]
    try:
        if not isinstance(request_id, (str, uuid.UUID)):
            raise ValueError
        request_id = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError):
        errors["request_id"] = ["invalid"]
    if errors:
        raise InstitutionSpeciesInvalid(errors)
    try:
        return _apply(
            user=user, organization=organization, profile_pk=profile_pk, proposed=proposed,
            fingerprint=expected_plan_fingerprint, request_id=request_id,
        )
    except IntegrityError as error:
        raise InstitutionSpeciesConflict("species_code_change_database_conflict") from error
    except ValidationError as error:
        raise InstitutionSpeciesConflict("species_code_change_validation_conflict") from error
    except DatabaseError as error:
        cause = error.__cause__
        sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        if (sqlstate in {"55P03", "40P01", "40001"}
                or (connection.vendor == "sqlite" and "locked" in str(error).lower())):
            raise InstitutionSpeciesConflict("species_code_change_busy") from error
        raise
