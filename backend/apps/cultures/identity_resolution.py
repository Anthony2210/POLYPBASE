"""Resolve one-hop Box identities within an already authorized queryset."""

from django.shortcuts import get_object_or_404


def resolve_box_identity(queryset, pk):
    """Return the requested Box or its same-organization terminal target.

    Callers must scope the queryset to the authorized organization, including
    retired records. Reuse that scope for both lookups; never follow a chain.
    """
    source = get_object_or_404(queryset, pk=pk)
    if source.merged_into_id is None:
        return source
    return get_object_or_404(
        queryset.filter(
            organization_id=source.organization_id,
            merged_into__isnull=True,
        ),
        pk=source.merged_into_id,
    )
