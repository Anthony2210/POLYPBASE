"""Whole-Box institutional donation proposals (Transfer v2, Phase 6 milestone 2).

A proposal is an offer, not a transfer. Issuing or cancelling one never changes
Box ownership, ``Box.organization``, status, location, measurements, lineage or
stock, never creates a destination Box or a TransferEnvelope, and never grants
the destination read access to the source history.

Authority: an active Admin membership of the explicitly selected institution,
with no superuser shortcut (an outward institutional act, as for sharing).
Institutions, Boxes and ownership are always reloaded server-side; client
identifiers only select rows inside that scope.

Lock order for writes: Organization -> OrganizationMembership -> User -> Box
(-> GlobalBoxIdentity inside enrollment) -> BoxDonationProposal. Membership
changes lock the Organization first, so a concurrent demotion is serialized.
Laboratory writers lock the Box first and never an Organization afterwards.
"""

import hashlib
import json

from django.contrib.auth import get_user_model
from django.core.validators import RegexValidator
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.identity import serialize_user_identity
from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.scientific_snapshot import SELECTORS, canonical_json
from apps.organizations.models import Organization

from .box_ownership import enroll_origin_box_as_admin
from .models import Box, BoxDonationProposal, BoxOwnershipEpisode, BoxTransferImport
from .scientific_projection import project_verified_source_box_snapshot
from .transfer_v2_protocol import StrictIntegerField, StrictSerializer, StrictStringField, StrictUUIDField

# The source history reference covers the whole scientific snapshot contract
# except the institution's own label and portable id: renaming an institution
# does not change a Box history.
HISTORY_SELECTORS = sorted(
    SELECTORS - {"source.institution_name", "source.institution_portable_id"}
)
# Provisional fail-closed scope pending a product decision on inactive and
# pending_review Boxes; this restricts proposals only, never a Box lifecycle.
ELIGIBLE_BOX_STATUSES = frozenset({Box.Status.ACTIVE})


class DonationError(Exception):
    """Expected refusal with a stable code; ``extra`` never holds a secret."""

    def __init__(self, code, status, detail, **extra):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail
        self.extra = extra


def _admin_required():
    return DonationError(
        "donation_admin_required", 403,
        "Only an active Admin of the active institution can manage Box donations.",
    )


def _box_not_found():
    # Missing, foreign and merged Boxes are indistinguishable.
    return DonationError("box_not_found", 404, "This Box does not exist.")


def _proposal_not_found():
    return DonationError("proposal_not_found", 404, "This donation proposal does not exist.")


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            raise serializers.ValidationError("Expected a boolean.")
        return data


# Exact JSON types, reusing the Transfer v2 strict fields: an integer is never
# read as a UUID, a string never as a confirmation or revision.
class PreflightSerializer(StrictSerializer):
    source_box_id = StrictIntegerField(min_value=1)
    destination_institution_id = StrictUUIDField()


class CreateSerializer(PreflightSerializer):
    request_id = StrictUUIDField()
    history_verified = StrictBooleanField()
    history_sha256 = StrictStringField(validators=[RegexValidator(r"^[0-9a-f]{64}\Z")])


class CancelSerializer(StrictSerializer):
    expected_revision = StrictIntegerField(min_value=1)


def _validated(serializer_class, data):
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        raise DonationError("invalid_request", 400, "The request is not valid.", errors=serializer.errors)
    return serializer.validated_data


# Authority --------------------------------------------------------------------


def _usable(user):
    return user is not None and getattr(user, "is_authenticated", False) and user.is_active


def _has_admin_membership(user, organization_id):
    return OrganizationMembership.objects.filter(
        user_id=user.pk, organization_id=organization_id, is_active=True,
        role=OrganizationMembership.Role.ADMIN,
    ).exists()


def require_admin(actor, organization):
    """Unlocked check for read-only endpoints; returns the reloaded institution."""
    if not _usable(actor) or organization is None:
        raise _admin_required()
    current = Organization.objects.filter(pk=organization.pk, is_active=True).first()
    if current is None or not _has_admin_membership(actor, current.pk):
        raise _admin_required()
    return current


def _locked_admin(actor, organization):
    """Lock institution, membership and account, then recheck authority."""
    if not _usable(actor) or organization is None:
        raise _admin_required()
    current = Organization.objects.select_for_update(no_key=True).filter(
        pk=organization.pk, is_active=True,
    ).first()
    if current is None:
        raise _admin_required()
    membership = OrganizationMembership.objects.select_for_update(no_key=True).filter(
        organization_id=current.pk, user_id=actor.pk,
    ).first()
    locked_actor = get_user_model().objects.select_for_update(no_key=True).filter(pk=actor.pk).first()
    if (
        locked_actor is None or not locked_actor.is_active or membership is None
        or not membership.is_active or membership.role != OrganizationMembership.Role.ADMIN
    ):
        raise _admin_required()
    return current, locked_actor


# Source checks ----------------------------------------------------------------


def _destination(source, destination_institution_id):
    destination = Organization.objects.filter(
        portable_id=destination_institution_id, is_active=True,
    ).first()
    if destination is None:
        raise DonationError(
            "destination_not_found", 404, "No active registered institution has this identifier.",
        )
    if destination.pk == source.pk:
        raise DonationError(
            "destination_is_source", 400, "A Box cannot be donated to its own institution.",
        )
    return destination


def _source_box(source, box_id, *, lock):
    boxes = Box.objects.filter(pk=box_id, organization_id=source.pk, merged_into__isnull=True)
    if lock:
        boxes = boxes.select_for_update(of=("self",))
    box = boxes.select_related("strain__species", "strain__global_identity").first()
    if box is None:
        raise _box_not_found()
    if box.status not in ELIGIBLE_BOX_STATUSES:
        raise DonationError(
            "box_status_not_eligible", 409, "Only an active Box can be proposed for donation.",
            box_status=box.status,
        )
    if BoxTransferImport.objects.filter(created_box_id=box.pk).exists():
        raise DonationError(
            "box_identity_provenance_unresolved", 409,
            "A Box created by a Transfer v1 import has no established biological identity.",
        )
    if box.strain.global_identity_id is None:
        raise DonationError(
            "strain_global_identity_required", 409, "The Box strain requires a global strain identity.",
        )
    return box


def _current_episode_problem(source, episode):
    """None when the episode is the source's current operational representation."""
    if episode is None:
        return None
    if episode.organization_id != source.pk or episode.ended_at is not None:
        return DonationError(
            "box_not_current_owner", 409,
            "This Box is not the current operational representation held by this institution.",
        )
    return None


def _pending_conflict(identity_id):
    pending = BoxDonationProposal.objects.filter(
        identity_id=identity_id, state=BoxDonationProposal.State.PENDING,
    )
    if pending.exists():
        return DonationError(
            "donation_already_pending", 409, "This Box already has a pending donation proposal.",
        )
    return None


def source_history_reference(source, box):
    """SHA-256 of the canonical scientific snapshot of the Box, as it is now.

    Reuses the versioned scientific snapshot contract (canonical measurements,
    their time, values including 0 and null, provenance, Box code and strain
    identity). It does not cover locations, movements, lineage, notes, accounts
    or zone temperatures. A non-projectable Box is refused, never repaired.
    """
    result = project_verified_source_box_snapshot(
        source=source, box_id=box.pk, selectors=HISTORY_SELECTORS,
    )
    if not result.ok:
        raise DonationError(
            "box_not_projectable", 409, "The scientific history of this Box cannot be projected.",
            diagnostics=[{"code": item.code, "path": item.path} for item in result.diagnostics],
        )
    content = canonical_json(result.snapshot)
    return {
        "snapshot_version": result.snapshot["schema_version"],
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "observation_count": len(result.snapshot["observations"]),
    }


# Preflight --------------------------------------------------------------------


def preflight_proposal(*, actor, organization, data):
    """Read-only eligibility and history reference; no enrollment, lock or write."""
    values = _validated(PreflightSerializer, data)
    source = require_admin(actor, organization)
    destination = _destination(source, values["destination_institution_id"])
    box = _source_box(source, values["source_box_id"], lock=False)
    episode = BoxOwnershipEpisode.objects.filter(local_box_id=box.pk).select_related("identity").first()
    problem = _current_episode_problem(source, episode)
    if problem is not None:
        raise problem
    if episode is not None:
        problem = _pending_conflict(episode.identity_id)
        if problem is not None:
            raise problem
    history = source_history_reference(source, box)
    return {
        "eligible": True,
        "source_box": _box_data(box),
        "destination_institution": {
            "institution_id": str(destination.portable_id), "name": destination.name,
        },
        "global_box_identity": {
            "enrolled": episode is not None,
            "global_box_id": str(episode.identity.global_box_id) if episode else None,
            "ownership_sequence": episode.sequence if episode else None,
        },
        "history": {
            **history,
            "polyp_state_revision": box.polyp_state_revision,
        },
    }


def _box_data(box):
    return {
        "id": box.pk,
        "global_code": box.global_code,
        "strain_code": box.strain.code,
        "species_scientific_name": box.strain.species.scientific_name,
        "global_strain_id": str(box.strain.global_identity.global_id),
    }


# Creation ---------------------------------------------------------------------


def _fingerprint(values):
    normalized = {
        "source_box_id": values["source_box_id"],
        "destination_institution_id": str(values["destination_institution_id"]),
        "history_verified": values["history_verified"],
        "history_sha256": values["history_sha256"],
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode("ascii")).hexdigest()


def create_proposal(*, actor, organization, data):
    """Issue one pending proposal; return ``(proposal, created)``.

    Idempotent per (source institution, actor, request_id): an identical replay
    returns the recorded proposal whatever its later state, a different body
    under the same key is refused. Authority is rechecked before any replay.
    Enrollment, proposal and audits commit together or not at all.
    """
    values = _validated(CreateSerializer, data)
    if values["history_verified"] is not True:
        raise DonationError(
            "history_verification_required", 400,
            "Confirm that the Box history was reviewed before proposing the donation.",
        )
    fingerprint = _fingerprint(values)
    with transaction.atomic():
        source, actor = _locked_admin(actor, organization)
        previous = BoxDonationProposal.objects.filter(
            source_organization_id=source.pk, created_by_id=actor.pk, request_id=values["request_id"],
        ).first()
        if previous is not None:
            if previous.request_fingerprint != fingerprint:
                raise DonationError(
                    "proposal_request_conflict", 409,
                    "This request ID was already used for a different donation proposal.",
                )
            return previous, False

        destination = _destination(source, values["destination_institution_id"])
        box = _source_box(source, values["source_box_id"], lock=True)
        existing = BoxOwnershipEpisode.objects.filter(local_box_id=box.pk).first()
        problem = _current_episode_problem(source, existing)
        if problem is not None:
            raise problem
        # Read under the Box lock: measurement and Box writers take it first.
        history = source_history_reference(source, box)
        if history["sha256"] != values["history_sha256"]:
            raise DonationError(
                "source_history_changed", 409,
                "The Box history changed since it was reviewed. Review it again.",
            )
        episode, _enrolled = enroll_origin_box_as_admin(actor=actor, organization=source, box=box)
        problem = _current_episode_problem(source, episode) or _pending_conflict(episode.identity_id)
        if problem is not None:
            raise problem
        now = timezone.now()
        try:
            with transaction.atomic():
                proposal = BoxDonationProposal.objects.create(
                    source_organization=source,
                    destination_organization=destination,
                    source_box=box,
                    identity_id=episode.identity_id,
                    source_episode=episode,
                    source_ownership_sequence=episode.sequence,
                    source_institution_name=source.name,
                    destination_institution_name=destination.name,
                    source_box_code=box.global_code,
                    source_strain_code=box.strain.code,
                    species_scientific_name=box.strain.species.scientific_name,
                    global_strain_id=box.strain.global_identity.global_id,
                    history_verified_by=actor,
                    history_verified_at=now,
                    history_snapshot_version=history["snapshot_version"],
                    history_sha256=history["sha256"],
                    source_polyp_state_revision=box.polyp_state_revision,
                    created_by=actor,
                    created_at=now,
                    request_id=values["request_id"],
                    request_fingerprint=fingerprint,
                )
        except IntegrityError as error:
            # Database backstop of the locked checks above (pending uniqueness,
            # request key); not expected while the lock protocol is respected.
            raise DonationError(
                "proposal_conflict", 409, "A conflicting donation proposal was recorded concurrently.",
            ) from error
        AuditLog.objects.create(
            organization=source,
            user=actor,
            action=AuditLog.Action.TRANSFER,
            object_type="box_donation_proposal",
            object_id=str(proposal.proposal_id),
            description="Box donation proposed",
            metadata={
                "box_id": box.pk,
                "box_code": box.global_code,
                "global_box_id": str(episode.identity.global_box_id),
                "ownership_sequence": episode.sequence,
                "destination_institution_id": str(destination.portable_id),
                "destination_institution_name": destination.name,
                "history_snapshot_version": history["snapshot_version"],
                "history_sha256": history["sha256"],
                "request_id": str(values["request_id"]),
            },
        )
    return proposal, True


# Cancellation -----------------------------------------------------------------


def cancel_proposal(*, actor, organization, proposal_id, data):
    """Source-only cancellation of a pending proposal at the expected revision."""
    values = _validated(CancelSerializer, data)
    with transaction.atomic():
        source, actor = _locked_admin(actor, organization)
        proposal = (
            BoxDonationProposal.objects.select_for_update(of=("self",))
            .filter(proposal_id=proposal_id, source_organization_id=source.pk)
            .first()
        )
        if proposal is None:
            raise _proposal_not_found()
        if proposal.state != BoxDonationProposal.State.PENDING:
            raise DonationError(
                "proposal_not_pending", 409, "Only a pending donation proposal can be cancelled.",
                state=proposal.state, revision=proposal.revision,
            )
        if proposal.revision != values["expected_revision"]:
            raise DonationError(
                "stale_proposal_revision", 409, "The donation proposal changed. Reload it.",
                revision=proposal.revision,
            )
        proposal.state = BoxDonationProposal.State.CANCELLED
        proposal.revision += 1
        proposal.cancelled_by = actor
        proposal.cancelled_at = timezone.now()
        proposal.save(update_fields=["state", "revision", "cancelled_by", "cancelled_at"])
        AuditLog.objects.create(
            organization=source,
            user=actor,
            action=AuditLog.Action.ARCHIVE,
            object_type="box_donation_proposal",
            object_id=str(proposal.proposal_id),
            description="Box donation proposal cancelled",
            metadata={"box_id": proposal.source_box_id, "revision": proposal.revision},
        )
    return proposal


# Reads ------------------------------------------------------------------------


def _proposals():
    return BoxDonationProposal.objects.select_related(
        "identity", "source_organization", "created_by", "history_verified_by", "cancelled_by",
    )


def outgoing_proposals(*, actor, organization):
    source = require_admin(actor, organization)
    return list(_proposals().filter(source_organization_id=source.pk).order_by("-created_at", "-pk"))


def incoming_proposals(*, actor, organization):
    destination = require_admin(actor, organization)
    return list(_proposals().filter(destination_organization_id=destination.pk).order_by("-created_at", "-pk"))


def proposal_for_institution(*, actor, organization, proposal_id):
    """Return ``(proposal, side)`` for a source or destination Admin, else 404."""
    current = require_admin(actor, organization)
    proposal = _proposals().filter(proposal_id=proposal_id, source_organization_id=current.pk).first()
    if proposal is not None:
        return proposal, "source"
    proposal = _proposals().filter(proposal_id=proposal_id, destination_organization_id=current.pk).first()
    if proposal is not None:
        return proposal, "destination"
    raise _proposal_not_found()


def history_verification_current(proposal):
    """Whether the source history still matches the reviewed reference (source view).

    An observation, not a reservation: any later change to the scientific
    snapshot (new, corrected or deleted measurement, code or strain identity)
    makes the verification stale; future acceptance must recheck under lock.
    """
    if proposal.state != BoxDonationProposal.State.PENDING:
        return None
    box = Box.objects.filter(
        pk=proposal.source_box_id, organization_id=proposal.source_organization_id,
    ).first()
    if box is None:
        return False
    try:
        reference = source_history_reference(proposal.source_organization, box)
    except DonationError:
        return False
    return (
        reference["snapshot_version"] == proposal.history_snapshot_version
        and reference["sha256"] == proposal.history_sha256
    )


def _common_data(proposal):
    return {
        "proposal_id": str(proposal.proposal_id),
        "state": proposal.state,
        "revision": proposal.revision,
        "created_at": proposal.created_at.isoformat(),
        "cancelled_at": proposal.cancelled_at.isoformat() if proposal.cancelled_at else None,
        "source_institution": {"name": proposal.source_institution_name},
        "destination_institution": {"name": proposal.destination_institution_name},
        "box": {
            "global_code": proposal.source_box_code,
            "strain_code": proposal.source_strain_code,
            "species_scientific_name": proposal.species_scientific_name,
            "global_strain_id": str(proposal.global_strain_id),
            "global_box_id": str(proposal.identity.global_box_id),
        },
        "history_verified_at": proposal.history_verified_at.isoformat(),
    }


def source_data(proposal, *, include_current=False):
    data = _common_data(proposal)
    data["box"]["id"] = proposal.source_box_id
    data["box"]["ownership_sequence"] = proposal.source_ownership_sequence
    data["created_by"] = serialize_user_identity(proposal.created_by)
    data["cancelled_by"] = serialize_user_identity(proposal.cancelled_by)
    data["history_verification"] = {
        "verified_by": serialize_user_identity(proposal.history_verified_by),
        "verified_at": proposal.history_verified_at.isoformat(),
        "snapshot_version": proposal.history_snapshot_version,
        "sha256": proposal.history_sha256,
        "polyp_state_revision": proposal.source_polyp_state_revision,
    }
    if include_current:
        data["history_verification"]["current"] = history_verification_current(proposal)
    return data


def destination_data(proposal):
    """Summary only: no source accounts, local Box id or history content."""
    return _common_data(proposal)
