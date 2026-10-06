"""Invitation lifecycle helpers shared by the API views and the tests.

Lock order for every flow touching an invitation (never take them the other way):

    Organization -> OrganizationMembership -> AccountInvitation -> User

Membership changes, invitation creation, resend and invitation confirmation all
lock the Organization first, so they serialize per institution. The forgot-password
confirmation only locks the User, the last lock of the chain, so it cannot invert
the order.
"""

from datetime import timedelta

from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db.models import CharField, Exists, OuterRef, Q
from django.db.models.fields.json import KT
from django.db.models.functions import Cast
from django.utils import timezone

from apps.audit.models import AuditLog

from .models import AccountInvitation, OrganizationMembership
from .tokens import INVITATION_TOKEN_TIMEOUT, invitation_is_expired

INVITATION_STATUS_PENDING = "pending"
INVITATION_STATUS_EXPIRED = "expired"

# Audit row written by the account invitation API for every membership it creates
# (also by the earlier temporary-password writer, whose accounts got a usable
# password and therefore never match the unusable-password condition below).
INVITATION_CREATION_AUDIT_DESCRIPTION = "Member access created"


def invitation_now():
    """Current time for invitation decisions; one seam for tests."""
    return timezone.now()


def _legacy_invitation_audit():
    # Compared as text so SQLite and PostgreSQL JSON handling agree.
    return AuditLog.objects.annotate(
        audited_membership_id=KT("metadata__membership_id"),
        audited_user_id=KT("metadata__user_id"),
    ).filter(
        organization_id=OuterRef("organization_id"),
        action=AuditLog.Action.CREATION,
        object_type="account",
        description=INVITATION_CREATION_AUDIT_DESCRIPTION,
        audited_membership_id=Cast(OuterRef("pk"), CharField()),
        audited_user_id=Cast(OuterRef("user_id"), CharField()),
    )


def unaccepted_invitation_q():
    """Memberships that are invitations not yet accepted.

    The account must still have an unusable password (no password chosen yet), and
    the membership must be a proven invitation:

    - it has an `AccountInvitation` row (written by every invitation since this
      model exists), or
    - legacy: no row, the user never logged in, and the invitation API's creation
      audit names this exact membership and user in this organization.

    An unusable password alone is not proof of an invitation, so other accounts
    with one (for example created by a technical command) stay ordinary members.
    """
    legacy = (
        Q(invitation__isnull=True)
        & Q(user__last_login__isnull=True)
        & Exists(_legacy_invitation_audit())
    )
    return Q(user__password__startswith=UNUSABLE_PASSWORD_PREFIX) & (
        Q(invitation__isnull=False) | legacy
    )


def pending_invitation_memberships(organization):
    """Active, unaccepted invitations of one organization (hidden ones included)."""
    return (
        OrganizationMembership.objects.filter(
            organization=organization,
            is_active=True,
            user__is_active=True,
        )
        .filter(unaccepted_invitation_q())
        .select_related("user", "invitation")
    )


def get_invitation(membership):
    try:
        return membership.invitation
    except AccountInvitation.DoesNotExist:
        return None


def invitation_status(invitation, now):
    if invitation_is_expired(invitation, now):
        return INVITATION_STATUS_EXPIRED
    return INVITATION_STATUS_PENDING


def issue_invitation(membership, *, now=None):
    """Start a new validity period and supersede every earlier link.

    Callers must hold the organization lock and run inside a transaction.
    """
    issued_at = (now or invitation_now()).replace(microsecond=0)
    invitation = (
        AccountInvitation.objects.select_for_update()
        .filter(membership=membership)
        .first()
    )
    if invitation is None:
        invitation = AccountInvitation(membership=membership)
    invitation.generation += 1
    invitation.issued_at = issued_at
    invitation.expires_at = issued_at + timedelta(seconds=INVITATION_TOKEN_TIMEOUT)
    invitation.save()
    invitation.membership = membership
    return invitation
