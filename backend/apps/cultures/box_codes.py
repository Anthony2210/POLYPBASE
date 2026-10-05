"""Transactional Box namespace allocation, independent of Strain numbering."""

import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models import Box, BoxCodeNamespace


def _reconciled_high_water(namespace, high_water):
    pattern = re.compile(rf"^{re.escape(namespace)}\.(\d+)")
    for code in Box.objects.filter(global_code__startswith=f"{namespace}.").values_list("global_code", flat=True):
        match = pattern.match(code)
        if match:
            high_water = max(high_water, int(match.group(1)))
    return high_water


def _formatted_box_codes(namespace, high_water, count):
    if type(count) is not int or count < 1:
        raise ValidationError(_("A positive number of box codes is required."))
    numbers = [str(number).zfill(3) for number in range(high_water + 1, high_water + count + 1)]
    codes = [(f"{namespace}.{number}", number) for number in numbers]
    if any(len(code) > 100 for code, _ in codes):
        raise ValidationError(_("The generated box code is too long."))
    return codes


def preview_box_codes(namespace, count):
    """Read advisory candidates without creating, locking or updating a counter.

    Namespace numbering is global. Concurrent writers may change the candidates
    before creation; only allocate_box_codes reserves authoritative identities.
    """
    high_water = BoxCodeNamespace.objects.filter(namespace=namespace).values_list("high_water", flat=True).first()
    return _formatted_box_codes(namespace, _reconciled_high_water(namespace, high_water or 0), count)


def locked_namespace(namespace, *, reconcile=True):
    """Create the unique lock row safely even on concurrent first use.

    get_or_create's savepoint/unique-key retry waits for an uncommitted insert.
    No Box rows are locked here: parent -> namespace is the only lock order.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Box code allocation requires an atomic transaction.")
    row, _ = BoxCodeNamespace.objects.get_or_create(namespace=namespace)
    row = BoxCodeNamespace.objects.select_for_update().get(pk=row.pk)
    if not reconcile:
        return row
    high_water = _reconciled_high_water(namespace, row.high_water)
    if high_water != row.high_water:
        row.high_water = high_water
        row.save(update_fields=["high_water"])
    return row


def register_box_code(code, *, namespace):
    """Make manual creation, imports, and renames cooperate with allocation."""
    match = re.match(rf"^{re.escape(namespace)}\.(\d+)", code)
    if match is not None:
        number = int(match.group(1))
    else:
        # Preserve pre-canonical namespaces without treating numeric trailing
        # decorations as another namespace. Canonical dotted Strain codes use
        # the explicit namespace above rather than this historical fallback.
        match = re.fullmatch(r"(.+?)\.(\d+)(.*)", code)
        if match is None:
            return
        namespace, number = match.group(1), int(match.group(2))
    row = locked_namespace(namespace, reconcile=False)
    if number > row.high_water:
        row.high_water = number
        row.save(update_fields=["high_water"])


def allocate_box_codes(namespace, count):
    """Reserve precisely the next N consecutive suffixes, never fill old holes."""
    if type(count) is not int or count < 1:
        raise ValidationError(_("A positive number of box codes is required."))
    row = locked_namespace(namespace)
    codes = _formatted_box_codes(namespace, row.high_water, count)
    row.high_water += count
    row.save(update_fields=["high_water"])
    return codes
