"""Real overlapping transactions for invitation locking (PostgreSQL only).

Each scenario pauses the first request while it holds a row lock, starts the
second request, waits until that one is about to request the same lock, and
only then lets the first one commit.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .api_views import (
    OrganizationInvitationResendAPIView,
    OrganizationMembershipDetailAPIView,
    PasswordResetConfirmAPIView,
)
from .invitations import issue_invitation
from .models import AccountInvitation, OrganizationMembership
from .tokens import INVITATION_TOKEN_PREFIX, invitation_token_generator

ISSUED_AT = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
AFTER_EXPIRY = ISSUED_AT + timedelta(hours=25)
PASSWORD = "un-mot-de-passe-solide-42"
ORGANIZATION_TABLE = "ORGANIZATIONS_ORGANIZATION"
USER_TABLE = "AUTH_USER"
_request_role = threading.local()


def _is_first_request():
    return getattr(_request_role, "name", None) == "first"


def _locks(sql, table):
    normalized = sql.upper()
    return "FOR UPDATE" in normalized and re.search(rf'\b"?{table}"?\b', normalized)


@skipUnless(
    connection.vendor == "postgresql",
    "Invitation concurrency requires PostgreSQL row locks.",
)
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_DELIVERY_ENABLED=True,
    PUBLIC_BASE_URL="https://polypbase.example",
)
class InvitationLockingTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(name="Invitation locking QA")
        self.responsable = user_model.objects.create_user(
            username="lock-responsable", email="lock-responsable@example.test", password="x"
        )
        self.admin = user_model.objects.create_user(
            username="lock-admin", email="lock-admin@example.test", password="x"
        )
        OrganizationMembership.objects.create(
            user=self.responsable, organization=self.organization, role="admin", is_responsable=True
        )
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.organization, role="admin"
        )
        self.invitee = user_model.objects.create_user(
            username="lock-invitee", email="lock-invitee@example.test"
        )
        self.membership = OrganizationMembership.objects.create(
            user=self.invitee, organization=self.organization, role="viewer"
        )
        with transaction.atomic():
            self.invitation = issue_invitation(self.membership, now=ISSUED_AT)
        mail.outbox = []

    # -- request helpers (each runs on its own thread and DB connection) --

    def _call(self, view, request, **kwargs):
        response = view(request, **kwargs)
        response.render()
        return response

    def resend(self, actor):
        request = APIRequestFactory().post(
            "/qa/resend/", {}, format="json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=actor)
        return self._call(
            OrganizationInvitationResendAPIView.as_view(), request, pk=self.membership.pk
        )

    def promote_invitee_to_admin(self):
        request = APIRequestFactory().patch(
            "/qa/member/", {"role": "admin"}, format="json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.responsable)
        return self._call(
            OrganizationMembershipDetailAPIView.as_view(), request, pk=self.membership.pk
        )

    def confirm(self, token):
        request = APIRequestFactory().post(
            "/qa/confirm/",
            {
                "uid": urlsafe_base64_encode(force_bytes(self.invitee.pk)),
                "token": token,
                "password": PASSWORD,
            },
            format="json",
        )
        return self._call(PasswordResetConfirmAPIView.as_view(), request)

    def invitation_link_token(self):
        self.invitation.membership.user = get_user_model().objects.get(pk=self.invitee.pk)
        return f"{INVITATION_TOKEN_PREFIX}{invitation_token_generator.make_token(self.invitation)}"

    # -- overlap harness --

    def overlap(self, first, second, *, table, second_table=None):
        """Run `first` until it holds a FOR UPDATE lock on `table`, start `second`,
        wait until `second` requests its lock, then let `first` finish."""
        second_table = second_table or table
        held = threading.Event()
        release = threading.Event()
        second_waiting = threading.Event()

        def hold(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if _locks(sql, table) and not held.is_set():
                held.set()
                assert release.wait(30), "first request was never released"
            return result

        def observe(execute, sql, params, many, context):
            if _locks(sql, second_table):
                second_waiting.set()
            return execute(sql, params, many, context)

        def run(name, action, wrapper):
            _request_role.name = name
            close_old_connections()
            try:
                with connection.execute_wrapper(wrapper):
                    return action()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(run, "first", first, hold)
            self.assertTrue(held.wait(30), "first request never took its lock")
            second_future = pool.submit(run, "second", second, observe)
            self.assertTrue(second_waiting.wait(30), "second request never asked for the lock")
            # Give PostgreSQL time to actually block the second request.
            time.sleep(0.5)
            self.assertFalse(second_future.done(), "second request was not blocked")
            release.set()
            return first_future.result(timeout=30), second_future.result(timeout=30)

    # -- scenarios --

    def test_simultaneous_resends_create_one_generation(self):
        with patch("apps.accounts.invitations.invitation_now", return_value=AFTER_EXPIRY):
            first, second = self.overlap(
                lambda: self.resend(self.admin),
                lambda: self.resend(self.responsable),
                table=ORGANIZATION_TABLE,
            )

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(second.data["code"], "invitation_still_valid")
        self.assertEqual(AccountInvitation.objects.get(pk=self.invitation.pk).generation, 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AuditLog.objects.filter(description="Member invitation resent").count(), 1)

    def test_resend_sees_promotion_committed_while_waiting(self):
        with patch("apps.accounts.invitations.invitation_now", return_value=AFTER_EXPIRY):
            promotion, resend = self.overlap(
                self.promote_invitee_to_admin,
                lambda: self.resend(self.admin),
                table=ORGANIZATION_TABLE,
            )

        self.assertEqual(promotion.status_code, 200, promotion.content)
        self.assertEqual(resend.status_code, 403, resend.content)
        self.assertEqual(resend.data["code"], "responsable_required")
        self.assertEqual(AccountInvitation.objects.get(pk=self.invitation.pk).generation, 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_old_link_waiting_on_a_resend_is_refused(self):
        old_token = self.invitation_link_token()
        # The confirmation runs just before expiry, the resend just after, so
        # only the generation change can refuse the old link.
        before_expiry = ISSUED_AT + timedelta(hours=23)

        def clock():
            return AFTER_EXPIRY if _is_first_request() else before_expiry

        with patch("apps.accounts.invitations.invitation_now", side_effect=clock):
            resend, confirmation = self.overlap(
                lambda: self.resend(self.admin),
                lambda: self.confirm(old_token),
                table=ORGANIZATION_TABLE,
            )

        self.assertEqual(resend.status_code, 200, resend.content)
        self.assertEqual(confirmation.status_code, 400, confirmation.content)
        self.invitee.refresh_from_db()
        self.assertFalse(self.invitee.has_usable_password())
        self.assertEqual(AccountInvitation.objects.get(pk=self.invitation.pk).generation, 2)

    def test_confirmation_holding_the_lock_wins_and_resend_sees_acceptance(self):
        token = self.invitation_link_token()
        before_expiry = ISSUED_AT + timedelta(hours=23)

        def clock():
            return before_expiry if _is_first_request() else AFTER_EXPIRY

        with patch("apps.accounts.invitations.invitation_now", side_effect=clock):
            confirmation, resend = self.overlap(
                lambda: self.confirm(token),
                lambda: self.resend(self.admin),
                table=ORGANIZATION_TABLE,
            )

        self.assertEqual(confirmation.status_code, 204, confirmation.content)
        self.assertEqual(resend.status_code, 409, resend.content)
        self.assertEqual(resend.data["code"], "invitation_not_pending")
        self.invitee.refresh_from_db()
        self.assertTrue(self.invitee.check_password(PASSWORD))
        self.assertEqual(len(mail.outbox), 0)

    def test_forgot_password_waits_for_resend_then_accepts(self):
        reset_token = default_token_generator.make_token(self.invitee)

        with patch("apps.accounts.invitations.invitation_now", return_value=AFTER_EXPIRY):
            resend, reset = self.overlap(
                lambda: self.resend(self.admin),
                lambda: self.confirm(reset_token),
                table=USER_TABLE,
            )

        self.assertEqual(resend.status_code, 200, resend.content)
        self.assertEqual(reset.status_code, 204, reset.content)
        self.invitee.refresh_from_db()
        self.assertTrue(self.invitee.check_password(PASSWORD))
        # The freshly resent link is spent: it covers the old password hash.
        invitation = AccountInvitation.objects.select_related("membership__user").get(
            pk=self.invitation.pk
        )
        resent_token = mail.outbox[-1].body.split(INVITATION_TOKEN_PREFIX)[1].split()[0]
        self.assertFalse(
            invitation_token_generator.check_token(invitation, resent_token, now=AFTER_EXPIRY)
        )
