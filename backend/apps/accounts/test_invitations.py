import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .api_views import OrganizationInvitationResendAPIView
from .models import AccountInvitation, OrganizationMembership
from .tokens import INVITATION_TOKEN_PREFIX, INVITATION_TOKEN_TIMEOUT

ISSUED_AT = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
PASSWORD = "un-mot-de-passe-solide-42"
LINK_PATTERN = re.compile(
    rf"/reset-password/([^/\s]+)/({INVITATION_TOKEN_PREFIX}[^\s]+)"
)


def at(moment):
    return patch("apps.accounts.invitations.invitation_now", return_value=moment)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_DELIVERY_ENABLED=True,
    PUBLIC_BASE_URL="https://polypbase.example",
)
class InvitationTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.paris = Organization.objects.create(name="Paris")
        self.partner = Organization.objects.create(name="Partner")
        self.admin = self._user("admin", password="secret")
        self.responsable = self._user("responsable", password="secret")
        self.tech = self._user("tech", password="secret")
        self.viewer = self._user("viewer", password="secret")
        self.partner_admin = self._user("partner-admin", password="secret")
        self._membership(self.admin, self.paris, "admin")
        self._membership(self.responsable, self.paris, "admin", is_responsable=True)
        self._membership(self.tech, self.paris, "lab_technician")
        self._membership(self.viewer, self.paris, "viewer")
        self._membership(self.partner_admin, self.partner, "admin")
        self.members_url = reverse("api_account_members")
        self.invitations_url = reverse("api_account_invitations")
        self.user_model = user_model

    def _user(self, username, *, password=None):
        return get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.org",
            password=password,
            first_name=username.title(),
        )

    def _membership(self, user, organization, role, **extra):
        return OrganizationMembership.objects.create(
            user=user, organization=organization, role=role, **extra
        )

    def _header(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def login(self, user):
        self.client.login(username=user.username, password="secret")

    def invite(self, email, role="viewer", organization=None, moment=ISSUED_AT):
        organization = organization or self.paris
        with at(moment):
            return self.client.post(
                self.members_url,
                data={"email": email, "role": role, "organization_id": organization.pk},
                content_type="application/json",
                **self._header(organization),
            )

    def invitations(self, moment=ISSUED_AT, organization=None):
        with at(moment):
            return self.client.get(
                self.invitations_url, **self._header(organization or self.paris)
            )

    def resend(self, membership_id, moment=ISSUED_AT, organization=None):
        with at(moment):
            return self.client.post(
                reverse("api_account_invitation_resend", args=[membership_id]),
                content_type="application/json",
                **self._header(organization or self.paris),
            )

    def link_parts(self, message=None):
        message = message or mail.outbox[-1]
        match = LINK_PATTERN.search(message.body)
        self.assertIsNotNone(match)
        return match.groups()

    def confirm(self, uid, token, moment):
        with at(moment):
            return self.client.post(
                reverse("api_password_reset_confirm"),
                data={"uid": uid, "token": token, "password": PASSWORD},
                content_type="application/json",
            )

    def make_legacy_invitation(
        self, username="legacy", role="viewer", organization=None, *, audit=True, **extra
    ):
        """What the pre-change invitation API left behind: an unusable password,
        no AccountInvitation row, and its creation audit for this exact membership."""
        organization = organization or self.paris
        user = self._user(username)
        self.assertFalse(user.has_usable_password())
        membership = self._membership(user, organization, role, **extra)
        if audit:
            AuditLog.objects.create(
                organization=organization,
                user=self.admin,
                action=AuditLog.Action.CREATION,
                object_type="account",
                object_id=user.get_username(),
                description="Member access created",
                metadata={
                    "user_id": user.id,
                    "membership_id": membership.id,
                    "valeurs": {"role": role},
                },
            )
        return membership

    def invited_membership(self, email="invited@example.org"):
        response = self.invite(email)
        self.assertEqual(response.status_code, 201)
        return OrganizationMembership.objects.get(user__email=email)


class InvitationLifecycleTests(InvitationTestCase):
    def test_creation_persists_real_issue_state(self):
        self.login(self.admin)

        response = self.invite("new@example.org")

        self.assertEqual(response.status_code, 201)
        invitation = AccountInvitation.objects.get(membership__user__email="new@example.org")
        self.assertEqual(invitation.issued_at, ISSUED_AT)
        self.assertEqual(
            invitation.expires_at, ISSUED_AT + timedelta(seconds=INVITATION_TOKEN_TIMEOUT)
        )
        self.assertEqual(INVITATION_TOKEN_TIMEOUT, 24 * 60 * 60)
        body = response.json()["invitation"]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["expires_at"], "2026-09-10T08:00:00+00:00")
        self.assertFalse(body["can_resend"])

    def test_structured_identity_is_preserved_in_creation_listing_and_resend(self):
        self.login(self.admin)
        with at(ISSUED_AT):
            created = self.client.post(
                self.members_url,
                data={
                    "email": "compound@example.org",
                    "first_name": "ÉLISE-ANNE",
                    "last_name": "du  pont-müller",
                    "role": "viewer",
                    "organization_id": self.paris.pk,
                },
                content_type="application/json",
                **self._header(self.paris),
            )
        self.assertEqual(created.status_code, 201)
        old_uid, old_token = self.link_parts()
        listed = self.invitations()
        self.assertEqual(listed.status_code, 200)
        resent = self.resend(
            created.json()["membership_id"], moment=ISSUED_AT + timedelta(hours=25)
        )
        self.assertEqual(resent.status_code, 200)
        new_uid, new_token = self.link_parts()

        for response, row in (
            (created, created.json()["invitation"]),
            (listed, listed.json()["invitations"][0]),
            (resent, resent.json()["invitation"]),
        ):
            with self.subTest(endpoint=response.request["PATH_INFO"]):
                self.assertEqual(row["first_name"], "Élise-Anne")
                self.assertEqual(row["last_name"], "DU PONT-MÜLLER")
                self.assertEqual(row["full_name"], "Élise-Anne DU PONT-MÜLLER")
                self.assertEqual(row["email"], "compound@example.org")
                self.assertNotIn("username", row)
                for field in ("uid", "token", "link"):
                    self.assertNotIn(field, row)
                text = response.content.decode()
                for secret in (old_uid, old_token, new_uid, new_token, "reset-password"):
                    self.assertNotIn(secret, text)

    def test_pending_invitation_is_excluded_from_members_and_listed_as_invitation(self):
        self.login(self.admin)
        self.invite("pending@example.org")

        members = self.client.get(self.members_url, **self._header(self.paris)).json()
        invitations = self.invitations().json()

        self.assertNotIn("pending@example.org", {m["email"] for m in members["members"]})
        self.assertEqual([i["email"] for i in invitations["invitations"]], ["pending@example.org"])
        self.assertEqual(invitations["server_time"], "2026-09-09T08:00:00+00:00")

    def test_exact_expiry_boundary_matches_token_validation_and_status(self):
        self.login(self.admin)
        self.invite("boundary@example.org")
        uid, token = self.link_parts()
        expires_at = ISSUED_AT + timedelta(hours=24)

        at_boundary = self.invitations(expires_at).json()["invitations"][0]
        after_boundary = self.invitations(expires_at + timedelta(seconds=1)).json()[
            "invitations"
        ][0]

        self.assertEqual(at_boundary["status"], "pending")
        self.assertFalse(at_boundary["can_resend"])
        self.assertEqual(after_boundary["status"], "expired")
        self.assertTrue(after_boundary["can_resend"])
        late = self.confirm(uid, token, expires_at + timedelta(seconds=1))
        self.assertEqual(late.status_code, 400)
        on_time = self.confirm(uid, token, expires_at)
        self.assertEqual(on_time.status_code, 204)

    def test_accepted_invitation_leaves_invitations_and_joins_members(self):
        self.login(self.admin)
        self.invite("accepted@example.org")
        uid, token = self.link_parts()

        self.assertEqual(self.confirm(uid, token, ISSUED_AT + timedelta(hours=1)).status_code, 204)

        self.login(self.admin)
        invitations = self.invitations().json()["invitations"]
        members = self.client.get(self.members_url, **self._header(self.paris)).json()["members"]
        self.assertEqual(invitations, [])
        self.assertIn("accepted@example.org", {m["email"] for m in members})

    def test_invitation_link_cannot_be_reused_after_acceptance(self):
        self.login(self.admin)
        self.invite("once@example.org")
        uid, token = self.link_parts()
        self.assertEqual(self.confirm(uid, token, ISSUED_AT).status_code, 204)

        self.assertEqual(self.confirm(uid, token, ISSUED_AT).status_code, 400)

    def test_forgot_password_acceptance_also_moves_account_to_members(self):
        self.login(self.admin)
        membership = self.invited_membership("forgot@example.org")
        user = membership.user
        user.set_password(PASSWORD)
        user.save(update_fields=["password"])

        self.assertEqual(self.invitations().json()["invitations"], [])
        members = self.client.get(self.members_url, **self._header(self.paris)).json()["members"]
        self.assertIn("forgot@example.org", {m["email"] for m in members})

    def test_legacy_invitation_is_expired_without_fabricated_timestamp(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)

        body = self.invitations().json()["invitations"]

        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], membership.id)
        self.assertEqual(body[0]["status"], "expired")
        self.assertIsNone(body[0]["expires_at"])
        self.assertTrue(body[0]["can_resend"])
        self.assertFalse(AccountInvitation.objects.exists())
        members = self.client.get(self.members_url, **self._header(self.paris)).json()["members"]
        self.assertNotIn("legacy@example.org", {m["email"] for m in members})

    def test_legacy_invitation_can_be_resent_and_gets_real_validity(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)

        response = self.resend(membership.id)

        self.assertEqual(response.status_code, 200)
        body = response.json()["invitation"]
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["expires_at"], "2026-09-10T08:00:00+00:00")
        self.assertFalse(body["can_resend"])
        self.assertEqual(len(mail.outbox), 1)
        uid, token = self.link_parts()
        self.assertEqual(self.confirm(uid, token, ISSUED_AT + timedelta(hours=1)).status_code, 204)

    def test_resend_restarts_validity_and_invalidates_the_old_link(self):
        self.login(self.admin)
        membership = self.invited_membership("resend@example.org")
        old_uid, old_token = self.link_parts()
        later = ISSUED_AT + timedelta(hours=30)

        response = self.resend(membership.id, moment=later)

        self.assertEqual(response.status_code, 200)
        new_uid, new_token = self.link_parts()
        self.assertNotEqual(old_token, new_token)
        invitation = AccountInvitation.objects.get(membership=membership)
        self.assertEqual(invitation.issued_at, later)
        self.assertEqual(invitation.expires_at, later + timedelta(hours=24))
        self.assertEqual(invitation.generation, 2)
        check_at = later + timedelta(hours=1)
        self.assertEqual(self.confirm(old_uid, old_token, check_at).status_code, 400)
        self.assertEqual(self.confirm(new_uid, new_token, check_at).status_code, 204)

    def test_resend_rejects_a_still_valid_invitation(self):
        self.login(self.admin)
        membership = self.invited_membership("valid@example.org")
        mail.outbox.clear()

        response = self.resend(membership.id, moment=ISSUED_AT + timedelta(hours=1))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "invitation_still_valid")
        self.assertEqual(mail.outbox, [])
        self.assertEqual(AccountInvitation.objects.get(membership=membership).generation, 1)

    def test_resend_rejects_an_accepted_account(self):
        self.login(self.admin)
        membership = self.invited_membership("done@example.org")
        uid, token = self.link_parts()
        self.assertEqual(self.confirm(uid, token, ISSUED_AT).status_code, 204)
        self.login(self.admin)

        response = self.resend(membership.id, moment=ISSUED_AT + timedelta(days=3))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "invitation_not_pending")

    def test_resend_rejects_an_ordinary_member(self):
        self.login(self.admin)
        membership = OrganizationMembership.objects.get(user=self.viewer)

        self.assertEqual(self.resend(membership.id).status_code, 409)

    def test_second_sequential_resend_does_not_create_another_generation(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)
        self.assertEqual(self.resend(membership.id).status_code, 200)

        second = self.resend(membership.id)

        self.assertEqual(second.status_code, 409)
        self.assertEqual(AccountInvitation.objects.get(membership=membership).generation, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_audit_is_scoped_and_holds_no_secret(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)

        self.resend(membership.id)

        log = AuditLog.objects.get(description="Member invitation resent")
        self.assertEqual(log.organization, self.paris)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.action, AuditLog.Action.UPDATE)
        uid, token = self.link_parts()
        serialized = json.dumps(log.metadata) + log.description + log.object_id
        for secret in (uid, token, "reset-password", token.removeprefix(INVITATION_TOKEN_PREFIX)):
            self.assertNotIn(secret, serialized)

    def test_apis_never_expose_link_uid_or_token(self):
        self.login(self.admin)
        created = self.invite("secret@example.org")
        uid, token = self.link_parts()
        legacy = self.make_legacy_invitation()
        resent = self.resend(legacy.id)
        uid2, token2 = self.link_parts()
        listing = self.invitations()
        for response, link_uid, link_token in (
            (created, uid, token),
            (resent, uid2, token2),
            (listing, uid2, token2),
        ):
            text = response.content.decode()
            self.assertNotIn(link_uid, text)
            self.assertNotIn(link_token, text)
            self.assertNotIn("reset-password", text)
            self.assertNotIn("token", text.lower())

    def test_resend_email_failure_rolls_back_state_and_audit(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)

        with patch("apps.accounts.api_views._send_password_email", side_effect=OSError("smtp")):
            response = self.resend(membership.id)

        self.assertEqual(response.status_code, 503)
        self.assertFalse(AccountInvitation.objects.exists())
        self.assertFalse(AuditLog.objects.filter(description="Member invitation resent").exists())
        self.assertEqual(self.invitations().json()["invitations"][0]["status"], "expired")

    def test_resend_unavailable_when_email_delivery_is_disabled(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)

        with override_settings(EMAIL_DELIVERY_ENABLED=False):
            response = self.resend(membership.id)

        self.assertEqual(response.status_code, 503)
        self.assertFalse(AccountInvitation.objects.exists())

    def test_creation_email_failure_leaves_no_invitation_row(self):
        self.login(self.admin)

        with patch("apps.accounts.api_views._send_password_email", side_effect=OSError("smtp")):
            response = self.invite("fails@example.org")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(AccountInvitation.objects.exists())
        self.assertFalse(self.user_model.objects.filter(email="fails@example.org").exists())


class InvitationPermissionTests(InvitationTestCase):
    def test_roles_other_than_admin_and_anonymous_are_refused(self):
        membership = self.make_legacy_invitation()
        self.assertIn(self.client.get(self.invitations_url).status_code, (401, 403))
        self.assertIn(
            self.client.post(reverse("api_account_invitation_resend", args=[membership.id])).status_code,
            (401, 403),
        )
        for user in (self.tech, self.viewer):
            with self.subTest(user=user.username):
                self.login(user)
                self.assertEqual(self.invitations().status_code, 403)
                self.assertEqual(self.resend(membership.id).status_code, 403)
        self.assertFalse(AccountInvitation.objects.exists())

    def test_admin_cannot_resend_an_admin_invitation_but_responsable_can(self):
        membership = self.make_legacy_invitation("admin-legacy", role="admin")
        self.login(self.admin)

        listed = self.invitations().json()["invitations"][0]
        refused = self.resend(membership.id)

        self.assertFalse(listed["can_resend"])
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["code"], "responsable_required")
        self.login(self.responsable)
        self.assertTrue(self.invitations().json()["invitations"][0]["can_resend"])
        self.assertEqual(self.resend(membership.id).status_code, 200)

    def test_admin_cannot_invite_admin_role_but_responsable_can(self):
        self.login(self.admin)
        self.assertEqual(self.invite("a1@example.org", role="admin").status_code, 403)
        self.login(self.responsable)
        self.assertEqual(self.invite("a2@example.org", role="admin").status_code, 201)

    def test_invitations_are_isolated_between_organizations(self):
        paris_membership = self.make_legacy_invitation("paris-legacy")
        partner_membership = self.make_legacy_invitation(
            "partner-legacy", organization=self.partner
        )
        self.login(self.admin)

        listed = self.invitations().json()["invitations"]
        foreign_resend = self.resend(partner_membership.id)

        self.assertEqual([i["id"] for i in listed], [paris_membership.id])
        self.assertEqual(foreign_resend.status_code, 404)
        self.assertFalse(AccountInvitation.objects.exists())
        self.assertEqual(
            self.client.get(
                self.invitations_url, **self._header(self.partner)
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("api_account_invitation_resend", args=[paris_membership.id]),
                content_type="application/json",
                **self._header(self.partner),
            ).status_code,
            403,
        )

    def test_partner_admin_sees_only_partner_invitations(self):
        self.make_legacy_invitation("paris-legacy")
        partner_membership = self.make_legacy_invitation("partner-legacy", organization=self.partner)
        self.login(self.partner_admin)

        listed = self.invitations(organization=self.partner).json()["invitations"]

        self.assertEqual([i["id"] for i in listed], [partner_membership.id])


class HiddenTeamMembershipTests(InvitationTestCase):
    def setUp(self):
        super().setUp()
        self.hidden_user = self._user("hidden", password="secret")
        self.hidden = self._membership(
            self.hidden_user, self.paris, "admin", is_hidden_from_team=True
        )

    def members(self):
        return self.client.get(self.members_url, **self._header(self.paris)).json()["members"]

    def test_hidden_membership_defaults_to_visible(self):
        self.assertFalse(OrganizationMembership.objects.get(user=self.tech).is_hidden_from_team)

    def test_hidden_membership_is_excluded_and_ordinary_ones_remain(self):
        self.login(self.admin)

        emails = {member["email"] for member in self.members()}

        self.assertNotIn("hidden@example.org", emails)
        self.assertEqual(
            emails,
            {"admin@example.org", "responsable@example.org", "tech@example.org", "viewer@example.org"},
        )

    def test_member_counters_source_excludes_hidden_membership(self):
        self.login(self.admin)

        admins = [m for m in self.members() if m["role"] == "admin"]

        self.assertEqual(len(admins), 2)

    def test_hidden_membership_still_counts_for_last_admin_protection(self):
        solo = Organization.objects.create(name="Solo")
        superuser = get_user_model().objects.create_superuser(
            username="solo-root", email="solo-root@example.org", password="secret"
        )
        visible = self._membership(self._user("solo-visible", password="secret"), solo, "admin")
        hidden = self._membership(
            self._user("solo-hidden", password="secret"), solo, "admin", is_hidden_from_team=True
        )
        self.client.force_login(superuser)

        def deactivate(membership):
            return self.client.patch(
                reverse("api_account_member_detail", args=[membership.id]),
                data={"is_active": False},
                content_type="application/json",
                **self._header(solo),
            )

        # The hidden Admin is another active Admin, so the visible one may go.
        self.assertEqual(deactivate(visible).status_code, 200)
        # Now the hidden Admin is the last active one and stays protected.
        self.assertEqual(deactivate(hidden).status_code, 403)
        hidden.refresh_from_db()
        self.assertTrue(hidden.is_active)

    def test_hidden_membership_keeps_its_role_and_backend_access(self):
        self.assertTrue(self.client.login(username="hidden", password="secret"))

        response = self.client.get(self.members_url, **self._header(self.paris))

        self.assertEqual(response.status_code, 200)
        self.hidden.refresh_from_db()
        self.assertEqual(self.hidden.role, "admin")
        self.assertTrue(self.hidden.is_active)


class TeamVisibilityCommandTests(InvitationTestCase):
    def setUp(self):
        super().setUp()
        self.superuser = get_user_model().objects.create_superuser(
            username="root", email="root@example.org", password="secret"
        )
        self.target = OrganizationMembership.objects.get(user=self.tech)

    def run_command(self, *extra, membership=None):
        output = StringIO()
        call_command(
            "set_team_membership_visibility",
            "--organization-id", str(self.paris.pk),
            "--membership-id", str((membership or self.target).pk),
            "--actor-user-id", str(self.superuser.pk),
            *extra,
            stdout=output,
        )
        return output.getvalue()

    def test_dry_run_changes_nothing(self):
        output = self.run_command("--hide")

        self.assertIn("DRY RUN", output)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_hidden_from_team)
        self.assertFalse(AuditLog.objects.filter(description="Member team visibility updated").exists())

    def test_apply_hides_audits_and_keeps_role(self):
        self.run_command("--hide", "--apply")

        self.target.refresh_from_db()
        self.assertTrue(self.target.is_hidden_from_team)
        self.assertEqual(self.target.role, "lab_technician")
        self.assertTrue(self.target.is_active)
        log = AuditLog.objects.get(description="Member team visibility updated")
        self.assertEqual(log.organization, self.paris)
        self.assertEqual(log.user, self.superuser)

    def test_show_restores_visibility(self):
        self.run_command("--hide", "--apply")
        self.run_command("--show", "--apply")

        self.target.refresh_from_db()
        self.assertFalse(self.target.is_hidden_from_team)

    def test_foreign_membership_and_non_superuser_are_rejected(self):
        foreign = OrganizationMembership.objects.get(user=self.partner_admin)
        with self.assertRaises(CommandError):
            self.run_command("--hide", "--apply", membership=foreign)
        with self.assertRaises(CommandError):
            call_command(
                "set_team_membership_visibility",
                "--organization-id", str(self.paris.pk),
                "--membership-id", str(self.target.pk),
                "--actor-user-id", str(self.admin.pk),
                "--hide",
                "--apply",
                stdout=StringIO(),
            )
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_hidden_from_team)
