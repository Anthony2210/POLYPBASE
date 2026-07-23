"""Shared permission helpers for roles and organizations."""

from rest_framework.exceptions import PermissionDenied

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


def get_active_organization_from_request(request):
    """Return the organization selected by the user for the current session."""
    organizations = get_authorized_organizations(request.user).filter(is_active=True)
    raw_organization_id = request.headers.get("X-Organization-Id")

    if not raw_organization_id:
        return organizations.order_by("name").first()

    try:
        organization_id = int(raw_organization_id)
    except (TypeError, ValueError) as error:
        raise PermissionDenied("Invalid organization context.") from error

    try:
        return organizations.get(id=organization_id)
    except Organization.DoesNotExist as error:
        raise PermissionDenied("This account cannot access this organization.") from error


def get_active_organization_ids(request):
    """Return the currently selected organization ID as a list for filters."""
    organization = get_active_organization_from_request(request)
    return [organization.id] if organization else []


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


def get_admin_organizations(user):
    """Return organizations where the user can manage accounts and roles."""
    if user.is_superuser:
        return Organization.objects.filter(is_active=True)

    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        memberships__role=OrganizationMembership.Role.ADMIN,
        is_active=True,
    ).distinct()


def get_admin_organization_ids(user):
    """Return IDs of the organizations the user administers."""
    return list(get_admin_organizations(user).values_list("id", flat=True))


def get_active_admin_organization_ids(request):
    """Return the selected organization if the user can administer it."""
    organization = get_active_organization_from_request(request)
    if organization is None:
        return []
    if request.user.is_superuser:
        return [organization.id]
    if OrganizationMembership.objects.filter(
        user=request.user,
        organization=organization,
        is_active=True,
        role=OrganizationMembership.Role.ADMIN,
    ).exists():
        return [organization.id]
    return []


def user_is_org_admin(user):
    """Return True when the user administers at least one organization."""
    if user.is_superuser:
        return True

    return OrganizationMembership.objects.filter(
        user=user,
        is_active=True,
        role=OrganizationMembership.Role.ADMIN,
        organization__is_active=True,
    ).exists()
