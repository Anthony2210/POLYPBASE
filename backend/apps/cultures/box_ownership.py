"""Global biological Box identity and institutional ownership episodes.

Internal foundation only (Transfer v2 milestone 1). There is no endpoint, no
ownership transition, no destination materialization and no cross-institution
read access here. Future donation acceptance consumes these primitives.
"""

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import Box, BoxOwnershipEpisode, BoxTransferImport, GlobalBoxIdentity


def _refuse(message, code="box_ownership_conflict"):
    raise ValidationError({"box": message}, code=code)


@transaction.atomic
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
    locked = Box.objects.select_for_update().filter(
        pk=box.pk, organization_id=organization.pk,
    ).only("id", "organization_id", "merged_into_id").first()
    if locked is None:
        _refuse("Box is not in the active institution.", code="box_not_in_organization")
    existing = BoxOwnershipEpisode.objects.filter(local_box_id=locked.pk).first()
    if existing is not None:
        if existing.organization_id != organization.pk:
            _refuse("Box ownership history contradicts its institution.")
        return existing
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
    )


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
