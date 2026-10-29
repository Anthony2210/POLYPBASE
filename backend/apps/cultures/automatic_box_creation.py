"""Ordinary box creation with atomic code allocation and durable retry intent."""

from collections.abc import Mapping
import hashlib
import hmac
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import APIException, ErrorDetail, PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain
from apps.taxonomy.scoping import eligible_strains

from .box_codes import allocate_box_codes
from .box_identity import USABLE, institution_species_codes, strain_box_identity, strain_display_name
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


class AutomaticBoxIntentKeySerializer(AutomaticBoxRequestSerializer):
    resume_token = serializers.UUIDField()


class AutomaticBoxIntentSerializer(AutomaticBoxCreateSerializer):
    resume_token = serializers.UUIDField()


def _normalized_intent(values):
    volume = values["volume_liters"]
    return {
        "strain": values["strain"], "thermal_zone": values["thermal_zone"],
        "entered_on": values["entered_on"].isoformat(),
        "volume_liters": None if volume is None else format(volume.copy_abs() if volume == 0 else volume, ".2f"),
        "notes": values["notes"],
    }


def _token_hash(token):
    return hashlib.sha256(str(token).encode("ascii")).hexdigest()


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


def _can_create_boxes(*, organization, actor):
    """Unlocked read of the same authority; writes always revalidate it under lock."""
    if actor is None or not actor.is_authenticated or not actor.is_active or not organization.is_active:
        return False
    return actor.is_superuser or OrganizationMembership.objects.filter(
        organization=organization, user_id=actor.pk, is_active=True,
        role__in=(OrganizationMembership.Role.ADMIN, OrganizationMembership.Role.LAB_TECHNICIAN),
    ).exists()



def automatic_box_strain_options(*, organization, actor):
    """Eligible strains with their verified Box prefix, for the creation form only.

    Read-only: no lock, counter or reservation. The next Box number is never
    returned; it is allocated only inside the creation transaction.
    """
    if not _can_create_boxes(organization=organization, actor=actor):
        raise PermissionDenied(_("This account cannot create boxes."))
    strains = list(
        eligible_strains(organization)
        .select_related(
            "species",
            "local_identity__species_code_assignment",
            "local_identity__provenance_code_assignment__biological_provenance",
        )
        .prefetch_related("translations")
        .annotate(active_box_count=Count(
            "boxes", filter=Q(boxes__organization=organization, boxes__status=Box.Status.ACTIVE),
        ))
        .order_by("species__scientific_name", "code", "pk")
    )
    species_codes = institution_species_codes(organization, {strain.species_id for strain in strains})
    return [
        {
            "id": strain.pk,
            "species": {"id": strain.species_id, "scientific_name": strain.species.scientific_name},
            "name": strain_display_name(strain),
            "code": strain.code,
            "active_box_count": strain.active_box_count,
            "identity": strain_box_identity(strain, organization, species_codes=species_codes),
        }
        for strain in strains
    ]


def _owned_receipt_box(receipt, organization):
    box = Box.objects.filter(pk=receipt.box_id, organization=organization).first()
    if box is None:
        raise PermissionDenied(_("This box is outside the active institution."))
    return box


@transaction.atomic
def inspect_automatic_box_request(*, organization, actor, data):
    """Inspect untrusted recovery evidence without registering or cancelling a key.

    Unknown is not terminal: a delayed create may still commit. Share the authority
    lock order so committed results and permission changes are observed coherently.
    """
    serializer = AutomaticBoxRequestSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    organization, actor = _locked_creation_authority(organization=organization, actor=actor)
    receipt = AutomaticBoxCreationRequest.objects.filter(
        organization=organization, actor=actor,
        request_id=serializer.validated_data["request_id"],
    ).first()
    if receipt is None:
        return "unknown", None
    if receipt.cancelled:
        return "cancelled", None
    if receipt.box_id is None:
        return "pending", None
    return "created", _owned_receipt_box(receipt, organization)


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
    if receipt.resume_token_hash and receipt.box_id is None:
        # A UUID alone is not authority over a registered intention.
        raise AutomaticBoxCreationConflict()
    if receipt.cancelled:
        return None
    # Resolution is not a creation retry: a committed Box survives zone/strain changes.
    return _owned_receipt_box(receipt, organization)


@transaction.atomic
def create_automatic_box(*, organization, actor, data, _registered_receipt=None):
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
    if previous is not None and previous.resume_token_hash and (
        _registered_receipt is None or _registered_receipt.pk != previous.pk
    ):
        raise AutomaticBoxCreationConflict()
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
    intent = _normalized_intent(values)
    if previous is not None:
        if previous.intent != intent:
            raise AutomaticBoxCreationConflict()
        # A registered intention has no Box until its first successful execution.
        if previous.box_id is not None:
            return _owned_receipt_box(previous, organization), False

    # A new identifier must extend a verified AAA-BBB-X prefix; never invent one.
    identity = strain_box_identity(strain, organization)
    if identity["status"] != USABLE:
        raise serializers.ValidationError({"strain": [ErrorDetail(
            _("The recorded code of this strain cannot be used to number an AAA-BBB-X box."),
            code=f"strain_identity_{identity['reason']}",
        )]})

    try:
        code, number = allocate_box_codes(identity["prefix"], 1)[0]
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
    if previous is None:
        AutomaticBoxCreationRequest.objects.create(
            organization=organization, actor=actor, request_id=values["request_id"],
            intent=intent, box=box,
        )
    else:
        previous.box = box
        previous.save(update_fields=["box"])
    return box, True


@transaction.atomic
def register_automatic_box_intent(*, organization, actor, data):
    """Bind a recoverable intention before execution, without allocating a Box.

    The token is independent of the request key and globally unique. Rebinding it
    to another key or scope cannot create a second intention after storage damage.
    """
    key = AutomaticBoxIntentKeySerializer(data={
        field: data[field] for field in ("request_id", "resume_token") if field in data
    } if isinstance(data, Mapping) else data)
    key.is_valid(raise_exception=True)
    organization, actor = _locked_creation_authority(organization=organization, actor=actor)
    token_hash = _token_hash(key.validated_data["resume_token"])
    serializer = AutomaticBoxIntentSerializer(data=data)
    failure = None
    if serializer.is_valid():
        intent = _normalized_intent(serializer.validated_data)
    else:
        # Invalid parameters cannot execute, but their rejection must also survive
        # a lost response. Preserve them immutably without retaining the raw token.
        intent = {"invalid_parameters": json.loads(json.dumps({
            name: value for name, value in data.items() if name not in ("request_id", "resume_token")
        }, cls=DjangoJSONEncoder))}
        failure = json.loads(json.dumps(serializer.errors))
    previous = AutomaticBoxCreationRequest.objects.filter(
        organization=organization, actor=actor, request_id=key.validated_data["request_id"],
    ).first()
    if previous is not None:
        if (not previous.resume_token_hash
            or not hmac.compare_digest(previous.resume_token_hash, token_hash)
            or previous.intent != intent):
            raise AutomaticBoxCreationConflict()
        if previous.box_id is not None:
            _owned_receipt_box(previous, organization)
        return previous
    try:
        with transaction.atomic():
            return AutomaticBoxCreationRequest.objects.create(
                organization=organization, actor=actor, request_id=key.validated_data["request_id"],
                resume_token_hash=token_hash, intent=intent, cancelled=failure is not None, failure=failure,
            )
    except IntegrityError:
        # A token reused in a different scope races on the global unique constraint.
        # Other database failures remain retryable infrastructure errors.
        if AutomaticBoxCreationRequest.objects.filter(resume_token_hash=token_hash).exists():
            raise AutomaticBoxCreationConflict() from None
        raise


def _locked_registered_intent(*, organization, actor, data):
    serializer = AutomaticBoxIntentKeySerializer(data=data)
    serializer.is_valid(raise_exception=True)
    organization, actor = _locked_creation_authority(organization=organization, actor=actor)
    receipt = AutomaticBoxCreationRequest.objects.filter(
        organization=organization, actor=actor, request_id=serializer.validated_data["request_id"],
    ).first()
    if (receipt is None or not receipt.resume_token_hash or not hmac.compare_digest(
        receipt.resume_token_hash, _token_hash(serializer.validated_data["resume_token"]),
    )):
        raise AutomaticBoxCreationConflict()
    return organization, actor, receipt


@transaction.atomic
def execute_automatic_box_intent(*, organization, actor, data):
    """Execute only the stored immutable parameters, or return its terminal result."""
    organization, actor, receipt = _locked_registered_intent(organization=organization, actor=actor, data=data)
    if receipt.box_id is not None:
        _owned_receipt_box(receipt, organization)
        return receipt
    if receipt.cancelled:
        return receipt
    try:
        # A business refusal must roll back allocation/location/audit before the
        # rejection is committed. Infrastructure failures leave the intent pending.
        with transaction.atomic():
            box, _created = create_automatic_box(
                organization=organization, actor=actor,
                data={**receipt.intent, "request_id": str(receipt.request_id)},
                _registered_receipt=receipt,
            )
    except serializers.ValidationError as error:
        receipt.cancelled = True
        receipt.failure = json.loads(json.dumps(error.detail))
        receipt.save(update_fields=["cancelled", "failure"])
    else:
        receipt.box = box
    return receipt


@transaction.atomic
def cancel_automatic_box_intent(*, organization, actor, data):
    """Fence a registered pending intent only with its matching recovery token."""
    organization, actor, receipt = _locked_registered_intent(organization=organization, actor=actor, data=data)
    if receipt.box_id is not None:
        _owned_receipt_box(receipt, organization)
    elif not receipt.cancelled:
        receipt.cancelled = True
        receipt.save(update_fields=["cancelled"])
    return receipt
