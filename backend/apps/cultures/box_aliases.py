"""Internal explicit alias registration; no inferred associations or renames."""

import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from .models import Box, BoxLocalIdAlias


_CONFLICT = "This Local ID is reserved or its source identity is unresolved."


def canonical_box_id(box):
    """Follow only explicit same-organization merges; reject cycles/foreign links."""
    seen = set()
    organization_id = box.organization_id
    while box is not None:
        if box.pk in seen or box.organization_id != organization_id:
            raise ValidationError(_CONFLICT)
        seen.add(box.pk)
        if box.merged_into_id is None:
            return box.pk
        box = Box.objects.filter(pk=box.merged_into_id).first()
    raise ValidationError(_CONFLICT)


def validate_local_id_claim(code, *, box, check_current=True):
    """Check a proposed claim without exposing any other institution's record.

    Advisory only: SQL guards are authoritative, including for bulk/raw writers.
    The provided source may be unsaved for ordinary Box creation.
    """
    query = Q(previous_global_code=code)
    if check_current:
        query |= Q(global_code=code)
    claims = list(Box.objects.filter(query))
    claims += [alias.box for alias in BoxLocalIdAlias.objects.filter(code=code).select_related("box")]
    if not claims:
        return
    canonical = canonical_box_id(box)
    for source in claims:
        if source.organization_id != box.organization_id or canonical_box_id(source) != canonical:
            raise ValidationError(_CONFLICT)


@transaction.atomic
def register_box_local_id_alias(*, box, code, organization_id):
    """Register one caller-confirmed exact historical code and return its row.

    Idempotent only for the exact same source, never reassigns to a canonical
    Box. The caller owns confirmation, authorization and audit. No code is
    normalized and no scientific, identity or merge field is changed. Numeric
    legacy suffixes reserve their original namespace, including retired names.
    """
    from .box_codes import lock_local_id_registry, register_box_code

    if not isinstance(code, str) or not code.strip() or len(code) > 100 or code != code.strip():
        raise ValidationError("A nonblank Local ID of at most 100 characters is required.")
    if box.pk is None or box.organization_id != organization_id:
        raise ValidationError(_CONFLICT)
    # Coordinate before namespace or source locks, including nonnumeric codes.
    lock_local_id_registry()
    # Use persisted state, not a potentially stale caller's organization/merge.
    source = Box.objects.filter(pk=box.pk, organization_id=organization_id).first()
    if source is None:
        raise ValidationError(_CONFLICT)
    match = re.fullmatch(r"(.+?)\.(\d+)(.*)", code)
    if match:
        register_box_code(code, namespace=match.group(1))
    source = Box.objects.select_for_update().get(pk=source.pk)
    if source.organization_id != organization_id:
        raise ValidationError(_CONFLICT)
    validate_local_id_claim(code, box=source)
    try:
        with transaction.atomic():
            alias, _ = BoxLocalIdAlias.objects.get_or_create(code=code, defaults={"box": source})
            if alias.box_id != source.pk:
                raise ValidationError(_CONFLICT)
            return alias
    except IntegrityError as exc:
        raise ValidationError(_CONFLICT) from exc
