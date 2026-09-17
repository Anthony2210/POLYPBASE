from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import OrganizationMembership

MEASUREMENT_EDIT_WINDOW = timedelta(hours=24)
_ROLE_UNSET = object()


def get_active_measurement_role(*, user, organization):
    """Return the user's active product role for one organization."""
    if not user or not user.is_authenticated:
        return None
    return (
        OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True,
        )
        .values_list("role", flat=True)
        .first()
    )


def get_measurement_editability(*, user, measurement, now=None, role=_ROLE_UNSET):
    """Return server-authoritative correction state for one measurement."""
    if role is _ROLE_UNSET:
        role = get_active_measurement_role(
            user=user,
            organization=measurement.box.organization,
        )

    if role == OrganizationMembership.Role.ADMIN:
        return {
            "can_edit": True,
            "edit_deadline": None,
            "edit_restriction": None,
        }

    if role == OrganizationMembership.Role.LAB_TECHNICIAN:
        deadline = measurement.created_at + MEASUREMENT_EDIT_WINDOW
        can_edit = (now or timezone.now()) < deadline
        return {
            "can_edit": can_edit,
            "edit_deadline": deadline,
            "edit_restriction": None if can_edit else "edit_window_expired",
        }

    return {
        "can_edit": False,
        "edit_deadline": None,
        "edit_restriction": "role_read_only",
    }
