"""Shared permission helpers for roles and organizations."""

from .models import OrganizationMembership
from apps.organizations.models import Organization


def get_authorized_organizations(user):
    """Return organizations the user can access."""
    if user.is_superuser:
        return Organization.objects.all()

    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        is_active=True,
    ).distinct()


def get_authorized_organization_ids(user):
    """Return authorized organization IDs as a list for queryset filters."""
    return list(get_authorized_organizations(user).values_list("id", flat=True))


def user_can_write_lab_data(user, organization):
    """Return True when the user can create or update lab records."""
    if user.is_superuser:
        return True

    return OrganizationMembership.objects.filter(
        user=user,
        organization=organization,
        is_active=True,
        role__in=[
            OrganizationMembership.Role.ADMIN,
            OrganizationMembership.Role.LAB_TECHNICIAN,
        ],
    ).exists()
