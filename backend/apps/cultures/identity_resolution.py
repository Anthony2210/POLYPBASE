"""Read-only operational Box identity resolution, never scientific inference."""

from collections import defaultdict

from django.db.models import Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Box, BoxLocalIdAlias


_IDENTITY_FIELDS = ("id", "organization_id", "merged_into_id")


def _canonical_ids(queryset, sources):
    """Resolve a batch, loading only missing merge hops inside the caller's scope."""
    nodes = {row["id"]: row for row in queryset.filter(pk__in=sources).values(*_IDENTITY_FIELDS)}
    frontier = set(nodes)
    while frontier:
        missing = {nodes[pk]["merged_into_id"] for pk in frontier} - {None} - nodes.keys()
        if not missing:
            break
        loaded = {row["id"]: row for row in queryset.filter(pk__in=missing).values(*_IDENTITY_FIELDS)}
        nodes.update(loaded)
        frontier = set(loaded)

    result = {}
    for source in sources:
        current = source
        seen = set()
        organization_id = nodes[source]["organization_id"] if source in nodes else None
        while current in nodes and current not in seen:
            seen.add(current)
            row = nodes[current]
            if row["organization_id"] != organization_id:
                break
            if row["merged_into_id"] is None:
                result[source] = current
                break
            current = row["merged_into_id"]
    return result


def resolve_box_identity(queryset, pk):
    """Resolve a numeric Box/QR identity through explicit same-institution merges.

    The caller must authorize the queryset and include retired records. Every
    hop reuses that scope; missing, foreign and cyclic paths are indistinguishable.
    """
    source = get_object_or_404(queryset, pk=pk)
    if source.merged_into_id is None:
        return source
    targets = _canonical_ids(queryset, {pk})
    if pk not in targets:
        raise Http404("No Box matches the given query.")
    return get_object_or_404(queryset, pk=targets[pk])


def _resolve_box_local_id_targets(queryset, codes):
    """Map exact current/previous/registered Local IDs to authorized terminal IDs.

    Claims are checked globally before authorization: an inaccessible conflicting
    claim must not turn an ambiguous identifier into a seemingly unique one.
    Multiple claims are valid only when all explicitly reach the same terminal.
    No historical code, alias source or audit row is rewritten.
    """
    codes = {code for code in codes if isinstance(code, str) and code and code == code.strip()}
    if not codes:
        return {}
    aliases = list(BoxLocalIdAlias.objects.filter(code__in=codes).values_list("code", "box_id"))
    claims = defaultdict(set)
    for code, source in aliases:
        claims[code].add(source)
    rows = Box.objects.filter(
        Q(global_code__in=codes) | Q(previous_global_code__in=codes)
    ).values_list("pk", "global_code", "previous_global_code")
    for source, current, previous in rows:
        for code in (current, previous):
            if code in codes:
                claims[code].add(source)
    sources = {source for owners in claims.values() for source in owners}
    canonical = _canonical_ids(queryset, sources)
    resolved = {}
    for code, owners in claims.items():
        targets = {canonical.get(source) for source in owners}
        if len(targets) == 1 and None not in targets:
            resolved[code] = targets.pop()
    return resolved


def resolve_box_local_ids(queryset, codes):
    """Hydrate each authorized canonical Box once for a batch of Local IDs."""
    resolved = _resolve_box_local_id_targets(queryset, codes)
    if not resolved:
        return {}
    boxes = {box.pk: box for box in queryset.filter(pk__in=set(resolved.values()))}
    return {code: boxes[pk] for code, pk in resolved.items() if pk in boxes}


def resolve_box_local_id(queryset, code):
    """Resolve one exact Local ID, failing closed with the usual generic 404."""
    box = resolve_box_local_ids(queryset, [code]).get(code)
    if box is None:
        raise Http404("No Box matches the given query.")
    return box


def box_local_id_search_ids(queryset, search):
    """Return canonical IDs for matching Local IDs, without multiplying rows.

    Alias candidates use a subquery rather than a reverse join. Only matching
    claims and their explicit merge paths are loaded; unrelated Boxes are not.
    The final list/inventory queryset still owns operational and status filters.
    """
    aliases = BoxLocalIdAlias.objects.filter(code__icontains=search)
    candidates = queryset.filter(
        Q(global_code__icontains=search) | Q(previous_global_code__icontains=search)
        | Q(pk__in=Subquery(aliases.values("box_id")))
    )
    codes = set()
    for current, previous in candidates.values_list("global_code", "previous_global_code"):
        for code in (current, previous):
            if code and search.casefold() in code.casefold():
                codes.add(code)
    codes.update(aliases.filter(box_id__in=Subquery(queryset.values("pk"))).values_list("code", flat=True))
    return set(_resolve_box_local_id_targets(queryset, codes).values())
