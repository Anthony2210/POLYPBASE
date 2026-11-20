"""Organization-scoped DNA evidence mutations; URLs are stored, never fetched."""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.accounts.identity import readable_user_identity_label
from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import Box, BoxDnaValidation


def can_edit_dna_validation(*, user, organization):
    """Require a real active lab membership, including for superusers."""
    return bool(
        user and user.is_authenticated and user.is_active
        and organization and organization.is_active
        and OrganizationMembership.objects.filter(
            user=user, user__is_active=True, organization=organization, is_active=True,
            role__in=[OrganizationMembership.Role.ADMIN, OrganizationMembership.Role.LAB_TECHNICIAN],
        ).exists()
    )


def require_dna_validation_authority(*, user, organization):
    if not can_edit_dna_validation(user=user, organization=organization):
        raise PermissionDenied(_(
            "This account cannot edit DNA validations. Please contact your administrator."
        ))


class DnaValidationConflict(APIException):
    status_code = 409

    def __init__(self, code, detail):
        super().__init__({"code": code, "detail": detail})


def _snapshot(validation):
    if validation is None:
        return None
    return {
        "id": validation.pk,
        "evidence_url": validation.evidence_url,
        "revision": validation.revision,
        "created_by_id": validation.created_by_id,
        "created_by": readable_user_identity_label(validation.created_by),
        "created_at": validation.created_at.isoformat(),
        "updated_by_id": validation.updated_by_id,
        "updated_at": validation.updated_at.isoformat(),
        "revoked_by_id": validation.revoked_by_id,
        "revoked_at": validation.revoked_at.isoformat() if validation.revoked_at else None,
    }


@transaction.atomic
def mutate_dna_validation(*, user, organization, box_id, operation, data):
    """Serialize all intents on Box and commit their required audit atomically.

    Revisions use the maximum over retained rows, including revoked rows, so an
    old PATCH/revoke cannot target a replacement validation (the ABA problem).
    """
    from .serializers import (
        BoxDnaValidationCreateSerializer, BoxDnaValidationRevokeSerializer,
        BoxDnaValidationUpdateSerializer,
    )

    # Still serialize membership changes, but allow FK KEY SHARE checks by
    # Box-locked writers while this transaction waits for the same Box.
    organization = Organization.objects.select_for_update(no_key=True).filter(
        pk=organization.pk if organization else None, is_active=True,
    ).first()
    require_dna_validation_authority(user=user, organization=organization)
    try:
        box = Box.objects.select_for_update().get(
            pk=box_id, organization=organization, merged_into__isnull=True,
        )
    except Box.DoesNotExist as exc:
        raise NotFound(_("No Box matches the given query.")) from exc

    serializers = {
        "add": BoxDnaValidationCreateSerializer,
        "update": BoxDnaValidationUpdateSerializer,
        "revoke": BoxDnaValidationRevokeSerializer,
    }
    serializer = serializers[operation](data=data() if callable(data) else data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    validation = box.dna_validations.filter(revoked_at__isnull=True).select_related("created_by").first()
    if operation == "add":
        if validation is not None:
            raise DnaValidationConflict("dna_validation_exists", _("A DNA validation already exists."))
    elif validation is None or validation.revision != values["expected_revision"]:
        raise DnaValidationConflict("dna_validation_changed", _("The DNA validation has changed. Reload the box."))
    elif operation == "update" and validation.evidence_url == values["evidence_url"]:
        raise ValidationError({
            "code": "dna_validation_no_change", "detail": _("The evidence URL has not changed."),
        })

    if operation == "add":
        return _add_validation(user=user, organization=organization, box=box, evidence_url=values["evidence_url"])

    before = _snapshot(validation)
    validation.revision = _next_revision(box)
    validation.updated_by = user
    if operation == "revoke":
        validation.revoked_at = timezone.now()
        validation.revoked_by = user
    else:
        validation.evidence_url = values["evidence_url"]
    validation.save()
    _audit(user=user, organization=organization, box=box, validation=validation,
           event="revoked" if operation == "revoke" else "updated", before=before)
    return validation


def _next_revision(box):
    return (box.dna_validations.aggregate(value=Max("revision"))["value"] or 0) + 1


def _audit(*, user, organization, box, validation, event, before):
    AuditLog.objects.create(
        organization=organization, user=user,
        action=AuditLog.Action.CREATION if event == "added" else AuditLog.Action.UPDATE,
        object_type="box", object_id=box.global_code,
        description=f"Box DNA validation {event}",
        metadata={
            "event": f"box_dna_validation_{event}",
            "box_id": box.pk, "dna_validation_id": validation.pk,
            "actor_id": user.pk, "actor": readable_user_identity_label(user),
            "before": before, "after": _snapshot(validation),
        },
    )


def _add_validation(*, user, organization, box, evidence_url):
    """Create the active evidence and its audit; the caller holds the Box lock."""
    validation = BoxDnaValidation.objects.create(
        box=box, evidence_url=evidence_url,
        created_by=user, updated_by=user, revision=_next_revision(box),
    )
    _audit(user=user, organization=organization, box=box, validation=validation, event="added", before=None)
    return validation


def validate_dna_evidence_url(value):
    """Apply the DNA evidence URL rule shared with the Box page; return the stored value."""
    from .serializers import BoxDnaValidationCreateSerializer

    serializer = BoxDnaValidationCreateSerializer(data={"evidence_url": value})
    if not serializer.is_valid():
        raise ValidationError(serializer.errors["evidence_url"])
    return serializer.validated_data["evidence_url"]


def add_dna_validation_to_new_box(*, user, organization, box, evidence_url):
    """Record evidence given when a Box is created, inside the creation transaction.

    The creation service has already locked the institution and the actor's
    membership, and the Box is new, so no existing evidence can conflict. The
    write path, revision and audit event are the same as a Box page addition.
    """
    require_dna_validation_authority(user=user, organization=organization)
    return _add_validation(user=user, organization=organization, box=box, evidence_url=evidence_url)
