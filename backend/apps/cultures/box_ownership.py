"""Global biological Box identity and institutional ownership episodes.

Internal foundation (Transfer v2 milestone 1). There is no ownership
transition, no destination materialization and no cross-institution read
access here. The donation proposal workflow (milestone 2) enrolls origin Boxes
through ``enroll_origin_box_as_admin``; future acceptance consumes the rest.
"""

from django.db import connection, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog

from .models import Box, BoxOwnershipEpisode, BoxTransferImport, GlobalBoxIdentity


def _refuse(message, code="box_ownership_conflict"):
    raise ValidationError({"box": message}, code=code)


def enroll_origin_box(*, organization, box):
    """Give an existing local Box its global identity, lazily and idempotently.

    Forward-only legacy policy: nothing is backfilled by migration. The Box is
    recorded as the origin representation (sequence 1) of a new identity, with an
    unknown start. Callers must already have authorized ``organization`` as the
    active institution. No Box field, location, measurement, lineage, author or
    status is read for inference or written.

    Boxes whose biological provenance is unresolved are refused rather than
    given a possibly false new identity:
    - a Transfer v1 import may continue a Box held elsewhere;
    - a merged identity alias is not a living representation.
    """
    episode, _created = _enroll_origin_box(organization=organization, box=box)
    return episode


@transaction.atomic
def _enroll_origin_box(*, organization, box):
    """Return ``(episode, created)``; ``created`` is decided under the Box lock.

    The Box row lock serializes every enroller of this Box (and PCA alias
    conversion), so exactly one caller observes "no episode" and creates it.
    """
    locked = Box.objects.select_for_update().filter(
        pk=box.pk, organization_id=organization.pk,
    ).only("id", "organization_id", "merged_into_id").first()
    if locked is None:
        _refuse("Box is not in the active institution.", code="box_not_in_organization")
    existing = BoxOwnershipEpisode.objects.filter(local_box_id=locked.pk).first()
    if existing is not None:
        if existing.organization_id != organization.pk:
            _refuse("Box ownership history contradicts its institution.")
        return existing, False
    if locked.merged_into_id is not None:
        _refuse("A merged identity alias cannot receive a global Box identity.")
    if BoxTransferImport.objects.filter(created_box_id=locked.pk).exists():
        _refuse(
            "Transfer v1 provenance is unresolved; no global Box identity is inferred.",
            code="box_identity_provenance_unresolved",
        )
    identity = GlobalBoxIdentity.objects.create()
    return BoxOwnershipEpisode.objects.create(
        identity=identity, sequence=1, organization_id=organization.pk, local_box=locked,
    ), True


def enroll_origin_box_as_admin(*, actor, organization, box):
    """Operational entry to ``enroll_origin_box``: authorized, attributed, audited.

    Returns ``(episode, created)``. Must run inside the caller's transaction,
    after the caller locked the institution and rechecked authority, so that
    the enrollment, its audit and the caller's own write commit or roll back
    together. Authority is checked again here: an active Admin membership of
    ``organization`` (no superuser shortcut). An already enrolled Box keeps
    its identity and writes no audit; a new identity is never allocated for it.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Origin enrollment must run inside the caller's transaction.")
    if (
        actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active
        or not OrganizationMembership.objects.filter(
            user_id=actor.pk, organization_id=organization.pk, is_active=True,
            role=OrganizationMembership.Role.ADMIN, organization__is_active=True,
        ).exists()
    ):
        raise PermissionDenied("Source organization Admin authority is required.")
    # "New" must come from the state observed under the Box lock, never from a
    # read before it: two concurrent callers would both see no episode.
    episode, created = _enroll_origin_box(organization=organization, box=box)
    if not created:
        return episode, False
    AuditLog.objects.create(
        organization_id=organization.pk,
        user=actor,
        action=AuditLog.Action.CREATION,
        object_type="global_box_identity",
        object_id=str(episode.identity.global_box_id),
        description="Global Box identity enrolled for origin Box",
        metadata={
            "box_id": episode.local_box_id,
            "box_code": episode.local_box.global_code,
            "ownership_sequence": episode.sequence,
        },
    )
    return episode, True


def boxes_with_ownership_history(box_ids):
    """Return the given Box PKs that represent any ownership episode, past or current.

    Such a Box must never become a merged alias. Writers converting a Box into
    an alias must call this after locking the Box row (SELECT ... FOR UPDATE,
    READ COMMITTED), the same row ``enroll_origin_box`` and episode creation
    lock first, so enrollment and alias conversion cannot both succeed.
    """
    return set(
        BoxOwnershipEpisode.objects.filter(local_box_id__in=list(box_ids))
        .values_list("local_box_id", flat=True)
    )


def verify_ownership_chain(identity):
    """Return integrity problems of one chain, read-only; an empty list is valid.

    Checks cross-row invariants a single-row CHECK cannot express. It repairs
    nothing and must not be exposed across institutions.
    """
    episodes = list(
        BoxOwnershipEpisode.objects.filter(identity=identity)
        .select_related("local_box").order_by("sequence")
    )
    problems = []
    if not episodes:
        return ["identity_without_episode"]
    if [episode.sequence for episode in episodes] != list(range(1, len(episodes) + 1)):
        problems.append("sequence_gap")
    current = [episode for episode in episodes if episode.ended_at is None]
    if len(current) != 1 or current[0] is not episodes[-1]:
        problems.append("current_owner_not_last")
    for previous, following in zip(episodes, episodes[1:]):
        if previous.organization_id == following.organization_id:
            problems.append("same_institution_succession")
        if previous.ended_at is None or previous.ended_at != following.started_at:
            problems.append("discontinuous_succession")
    for episode in episodes:
        if episode.local_box.organization_id != episode.organization_id:
            problems.append("representation_institution_mismatch")
    return problems
