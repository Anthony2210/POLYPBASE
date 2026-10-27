"""Ordinary box creation with atomic code allocation and durable retry intent."""

from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain
from apps.taxonomy.scoping import eligible_strains

from .box_codes import allocate_box_codes
from .models import AutomaticBoxCreationRequest, Box, BoxLocation, ThermalZone


class AutomaticBoxRequestSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({
                    key: _("This field is not accepted.") for key in sorted(unknown)
                })
        return super().to_internal_value(data)


class AutomaticBoxCreateSerializer(AutomaticBoxRequestSerializer):
    strain = serializers.IntegerField(min_value=1)
    thermal_zone = serializers.IntegerField(min_value=1)
    entered_on = serializers.DateField()
    volume_liters = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True, default=None,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class AutomaticBoxCreationConflict(APIException):
    status_code = 409
    default_code = "box_creation_request_conflict"
    default_detail = _("This request ID was already used for a different box creation.")

    def __init__(self):
        super().__init__({"code": self.default_code, "detail": self.default_detail})


class AutomaticBoxCreationCancelled(AutomaticBoxCreationConflict):
    default_code = "box_creation_request_cancelled"
    default_detail = _("This box creation request was cancelled. Use a new request ID to create a box.")


def _locked_creation_authority(*, organization, actor):
    """Share organization -> membership -> user locks for creation and resolution."""
    if actor is None or not actor.is_authenticated:
        raise PermissionDenied(_("This account cannot create boxes."))
    organization = Organization.objects.select_for_update(no_key=True).filter(
        pk=organization.pk, is_active=True,
    ).first()
    if organization is None:
        raise PermissionDenied(_("This institution is not active."))
    membership = OrganizationMembership.objects.select_for_update(no_key=True).filter(
        organization=organization, user_id=actor.pk,
    ).first()
    actor = get_user_model().objects.select_for_update(no_key=True).filter(pk=actor.pk).first()
    if (actor is None or not actor.is_active or not (
        actor.is_superuser or (
            membership is not None and membership.is_active
            and membership.role in (OrganizationMembership.Role.ADMIN, OrganizationMembership.Role.LAB_TECHNICIAN)
        )
    )):
        raise PermissionDenied(_("This account cannot create boxes."))
    return organization, actor


def _owned_receipt_box(receipt, organization):
    box = Box.objects.filter(pk=receipt.box_id, organization=organization).first()
    if box is None:
        raise PermissionDenied(_("This box is outside the active institution."))
    return box


@transaction.atomic
def resolve_automatic_box_request(*, organization, actor, data):
    """Return a committed owned Box or permanently cancel an absent scoped key."""
    serializer = AutomaticBoxRequestSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    organization, actor = _locked_creation_authority(organization=organization, actor=actor)
    receipt, _created = AutomaticBoxCreationRequest.objects.get_or_create(
        organization=organization, actor=actor,
        request_id=serializer.validated_data["request_id"],
        defaults={"cancelled": True},
    )
    if receipt.cancelled:
        return None
    # Resolution is not a creation retry: a committed Box survives zone/strain changes.
    return _owned_receipt_box(receipt, organization)


@transaction.atomic
def create_automatic_box(*, organization, actor, data):
    """Revalidate locked authority and eligibility before creation or replay.

    Lock order: organization -> membership -> actor -> strain -> zone -> namespace.
    Never lock existing Boxes after the namespace: subculture owns its parent first.
    NO KEY UPDATE keeps FK references compatible with concurrent subculture inserts.
    The organization lock serializes creation and resolution of an idempotency key.
    """
    key_serializer = AutomaticBoxRequestSerializer(
        data={key: value for key, value in data.items() if key == "request_id"} if isinstance(data, Mapping) else data,
    )
    key_serializer.is_valid(raise_exception=True)
    organization, actor = _locked_creation_authority(organization=organization, actor=actor)
    previous = AutomaticBoxCreationRequest.objects.filter(
        organization=organization, actor=actor,
        request_id=key_serializer.validated_data["request_id"],
    ).first()
    if previous is not None and previous.cancelled:
        raise AutomaticBoxCreationCancelled()

    serializer = AutomaticBoxCreateSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    strain = Strain.objects.select_for_update(no_key=True).filter(pk=values["strain"]).first()
    if strain is None or not eligible_strains(organization).filter(pk=strain.pk).exists():
        raise serializers.ValidationError({"strain": _("Select an eligible strain in the active institution.")})
    zone = ThermalZone.objects.select_for_update(no_key=True).filter(
        pk=values["thermal_zone"], organization=organization, is_active=True,
    ).first()
    if zone is None:
        raise serializers.ValidationError({"thermal_zone": _("Select an active zone in the active institution.")})

    volume = values["volume_liters"]
    normalized_volume = None if volume is None else format(volume.copy_abs() if volume == 0 else volume, ".2f")
    intent = {
        "strain": strain.pk, "thermal_zone": zone.pk,
        "entered_on": values["entered_on"].isoformat(),
        "volume_liters": normalized_volume,
        "notes": values["notes"],
    }
    if previous is not None:
        if previous.intent != intent:
            raise AutomaticBoxCreationConflict()
        # The receipt is not a capability to read an institution's reassigned Box.
        return _owned_receipt_box(previous, organization), False

    try:
        code, number = allocate_box_codes(strain.code, 1)[0]
        box = Box.objects.create(
            organization=organization, strain=strain, thermal_zone=zone,
            global_code=code, box_number=number, local_code="", status=Box.Status.ACTIVE,
            entered_on=values["entered_on"], volume_liters=volume, notes=values["notes"],
        )
    except DjangoValidationError as error:
        raise serializers.ValidationError({"global_code": error.messages}) from error
    BoxLocation.objects.create(
        box=box, thermal_zone=zone, starts_at=timezone.now(),
        notes="Initial location after automatic ordinary creation.",
    )
    AuditLog.objects.create(
        organization=organization, user=actor, action=AuditLog.Action.CREATION,
        object_type="box", object_id=box.global_code,
        description=f"Box created automatically: {box.global_code}",
        metadata={
            "box_id": box.pk, "request_id": str(values["request_id"]),
            "valeurs": {
                "code_global": box.global_code, "numero_boite": box.box_number,
                "souche": strain.code, "espece": strain.species.scientific_name,
                "emplacement": zone.name, "date_entree": intent["entered_on"],
                "volume_litres": intent["volume_liters"], "note": box.notes,
            },
        },
    )
    AutomaticBoxCreationRequest.objects.create(
        organization=organization, actor=actor, request_id=values["request_id"],
        intent=intent, box=box,
    )
    return box, True
