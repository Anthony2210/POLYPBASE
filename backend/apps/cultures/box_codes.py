"""Transactional Box namespace allocation, independent of Strain numbering."""

import re

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.utils.translation import gettext_lazy as _

from .models import Box, BoxCodeNamespace, BoxLocalIdAlias


def _reconciled_high_water(namespace, high_water):
    pattern = re.compile(rf"^{re.escape(namespace)}\.(\d+)")
    sources = (
        Box.objects.filter(global_code__startswith=f"{namespace}.").values_list("global_code", flat=True),
        Box.objects.filter(previous_global_code__startswith=f"{namespace}.").values_list("previous_global_code", flat=True),
        BoxLocalIdAlias.objects.filter(code__startswith=f"{namespace}.").values_list("code", flat=True),
    )
    for codes in sources:
        for code in codes:
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


def lock_local_id_registry(*, using=None):
    """Acquire identity coordination before any Box or namespace row lock.

    Call this at the start of an outer transaction if it explicitly locks Box
    rows before saving, allocating, or registering aliases. Statement-level SQL
    guards use the same order for ordinary raw/bulk identity writes; they cannot
    retroactively reorder a caller's earlier SELECT FOR UPDATE.
    """
    connection = connections[using or DEFAULT_DB_ALIAS]
    if not connection.in_atomic_block:
        raise RuntimeError("Local ID coordination requires an atomic transaction.")
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(192837, 19)")
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] == "repeatable read":
                raise ValidationError("Local ID writes require READ COMMITTED or SERIALIZABLE isolation.")


def _lock_namespace_row(namespace):
    row, _ = BoxCodeNamespace.objects.get_or_create(namespace=namespace)
    return BoxCodeNamespace.objects.select_for_update().get(pk=row.pk)


def locked_namespace(namespace, *, reconcile=True):
    """Create the unique lock row safely even on concurrent first use.

    get_or_create's savepoint/unique-key retry waits for an uncommitted insert.
    No Box rows are locked here. The approved CLA/CLC pair always locks in
    sorted order, including when allocation is the first namespace caller.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Box code allocation requires an atomic transaction.")
    lock_local_id_registry()
    pair = ("CLA-JKA-1", "CLC-JKA-1")
    namespaces = sorted(pair) if namespace in pair else (namespace,)
    rows = {name: _lock_namespace_row(name) for name in namespaces}
    row = rows[namespace]
    if not reconcile:
        return row
    high_water = _reconciled_high_water(namespace, row.high_water)
    if high_water != row.high_water:
        row.high_water = high_water
        row.save(update_fields=["high_water"])
    return row


def reserve_reconciled_box_identity(code):
    """Serialize the two approved aliases and prevent ordinary resurrection.

    Lock both namespaces in correction order even for target creation. Existing
    collisions remain inspectable; this guard is only for new or changed codes.
    """
    aliases = {
        "CLA-JKA-1.009": "CLC-JKA-1.009",
        "CLA-JKA-1.010": "CLC-JKA-1.010",
        "CLC-JKA-1.009": "CLA-JKA-1.009",
        "CLC-JKA-1.010": "CLA-JKA-1.010",
    }
    target = aliases.get(code)
    if target is None:
        return
    locked_namespace(code.rsplit(".", 1)[0], reconcile=False)
    # Codes are globally unique. Do not disclose the target or its institution.
    if Box.objects.filter(global_code=target).exists():
        raise ValidationError(_("This code is reserved by a historical culture identity."))


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
