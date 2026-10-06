"""Regression tests for the invitation review findings (locking, token binding,
legacy classification). Real overlapping transactions live in
test_invitation_concurrency.py and need PostgreSQL."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import int_to_base36, urlsafe_base64_encode

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from . import api_views
from .invitations import issue_invitation
from .models import AccountInvitation, OrganizationMembership
from .test_invitations import ISSUED_AT, PASSWORD, InvitationTestCase, at
from .tokens import INVITATION_TOKEN_PREFIX, invitation_token_generator


class ResendAuthorizationUsesLockedStateTests(InvitationTestCase):
    def test_role_changed_while_waiting_for_the_lock_is_enforced(self):
        """F1: a promotion committed just before the lock is granted must count."""
        membership = self.make_legacy_invitation()
        real_lock = api_views._lock_invitation_organization

        def promote_then_lock(actor, organization, *, role):
            # Stands for a Responsable's promotion committing while we wait.
            OrganizationMembership.objects.filter(pk=membership.pk).update(role="admin")
            return real_lock(actor, organization, role=role)

        self.login(self.admin)
        with patch.object(api_views, "_lock_invitation_organization", promote_then_lock):
            response = self.resend(membership.id)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "responsable_required")
        self.assertFalse(AccountInvitation.objects.exists())
        self.assertEqual(mail.outbox, [])
        self.assertFalse(AuditLog.objects.filter(description="Member invitation resent").exists())

    def test_admin_loses_admin_role_before_resend(self):
        membership = self.make_legacy_invitation()
        OrganizationMembership.objects.filter(user=self.admin).update(role="viewer")
        self.login(self.admin)

        self.assertEqual(self.resend(membership.id).status_code, 403)
        self.assertFalse(AccountInvitation.objects.exists())


class ConfirmationRevalidatesUnderLockTests(InvitationTestCase):
    def test_link_superseded_while_waiting_for_the_lock_is_refused(self):
        """F2: the token passes the early check, then a resend commits first."""
        self.login(self.admin)
        membership = self.invited_membership("superseded@example.org")
        uid, token = self.link_parts()
        self.client.logout()
        real_valid_user = api_views.PasswordResetConfirmAPIView._valid_user

        def resend_then_validate(view, user, token_value, *, lock):
            if lock:
                issue_invitation(membership, now=ISSUED_AT + timedelta(hours=1))
            return real_valid_user(view, user, token_value, lock=lock)

        with patch.object(
            api_views.PasswordResetConfirmAPIView, "_valid_user", resend_then_validate
        ):
            response = self.confirm(uid, token, ISSUED_AT + timedelta(hours=1))

        self.assertEqual(response.status_code, 400)
        membership.user.refresh_from_db()
        self.assertFalse(membership.user.has_usable_password())
        self.assertFalse(
            AuditLog.objects.filter(description="Password reset from the login page").exists()
        )

    def test_link_used_by_a_concurrent_confirmation_is_refused(self):
        self.login(self.admin)
        membership = self.invited_membership("double@example.org")
        uid, token = self.link_parts()
        self.client.logout()
        real_valid_user = api_views.PasswordResetConfirmAPIView._valid_user

        def accept_then_validate(view, user, token_value, *, lock):
            if lock:
                accepted = get_user_model().objects.get(pk=user.pk)
                accepted.set_password("someone-else-was-faster-77")
                accepted.save(update_fields=["password"])
            return real_valid_user(view, user, token_value, lock=lock)

        with patch.object(
            api_views.PasswordResetConfirmAPIView, "_valid_user", accept_then_validate
        ):
            response = self.confirm(uid, token, ISSUED_AT + timedelta(minutes=5))

        self.assertEqual(response.status_code, 400)
        membership.user.refresh_from_db()
        self.assertTrue(membership.user.check_password("someone-else-was-faster-77"))

    def test_forgot_password_reset_does_not_need_an_invitation_row(self):
        user = self.viewer
        token = default_token_generator.make_token(user)

        response = self.client.post(
            reverse("api_password_reset_confirm"),
            data={
                "uid": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": token,
                "password": PASSWORD,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(AccountInvitation.objects.exists())

    def test_forgot_password_on_a_pending_invitation_accepts_it(self):
        self.login(self.admin)
        membership = self.invited_membership("forgot-api@example.org")
        _uid, invitation_token = self.link_parts()
        self.client.logout()
        user = membership.user
        uid = urlsafe_base64_encode(force_bytes(user.pk))

        reset = self.client.post(
            reverse("api_password_reset_confirm"),
            data={"uid": uid, "token": default_token_generator.make_token(user), "password": PASSWORD},
            content_type="application/json",
        )

        self.assertEqual(reset.status_code, 204)
        # The invitation link is spent too: the password hash it covers changed.
        self.assertEqual(self.confirm(uid, invitation_token, ISSUED_AT).status_code, 400)
        self.login(self.admin)
        self.assertEqual(self.invitations().json()["invitations"], [])

    def test_malformed_and_legacy_shaped_tokens_fail_cleanly(self):
        self.login(self.admin)
        membership = self.invited_membership("malformed@example.org")
        uid, _token = self.link_parts()
        self.client.logout()
        for token in (
            "invitation-",
            "invitation-abc",
            "invitation-abc-def",
            "invitation-0-1-deadbeef",
            "invitation-!!-1-deadbeef",
            "invitation-zzzzzzzzzzzzzzzzzzzz-1-deadbeef",
            "invitation-1--",
            f"invitation-{int_to_base36(membership.invitation.pk)}-1-{'0' * 32}",
        ):
            with self.subTest(token=token):
                self.assertEqual(self.confirm(uid, token, ISSUED_AT).status_code, 400)


class TokenBindingTests(InvitationTestCase):
    def _two_invitations_for_one_user(self):
        user = self._user("two-orgs")
        first = self._membership(user, self.paris, "viewer")
        second = self._membership(user, self.partner, "viewer")
        first_invitation = issue_invitation(first, now=ISSUED_AT)
        second_invitation = issue_invitation(second, now=ISSUED_AT)
        self.assertEqual(first_invitation.generation, second_invitation.generation)
        self.assertEqual(first_invitation.issued_at, second_invitation.issued_at)
        return user, first_invitation, second_invitation

    def test_same_user_generation_and_second_do_not_give_interchangeable_tokens(self):
        _user, first, second = self._two_invitations_for_one_user()
        first_token = invitation_token_generator.make_token(first)
        second_token = invitation_token_generator.make_token(second)

        self.assertNotEqual(first_token.split("-")[-1], second_token.split("-")[-1])
        self.assertTrue(invitation_token_generator.check_token(first, first_token, now=ISSUED_AT))
        self.assertFalse(invitation_token_generator.check_token(second, first_token, now=ISSUED_AT))
        self.assertFalse(invitation_token_generator.check_token(first, second_token, now=ISSUED_AT))

    def test_digest_of_one_invitation_cannot_be_relabelled_for_another(self):
        user, first, second = self._two_invitations_for_one_user()
        digest = invitation_token_generator.make_token(first).split("-")[-1]
        relabelled = f"{int_to_base36(second.pk)}-{int_to_base36(second.generation)}-{digest}"
        uid = urlsafe_base64_encode(force_bytes(user.pk))

        response = self.confirm(uid, f"{INVITATION_TOKEN_PREFIX}{relabelled}", ISSUED_AT)

        self.assertEqual(response.status_code, 400)

    def test_token_is_bound_to_membership_and_organization_context(self):
        _user, first, _second = self._two_invitations_for_one_user()
        token = invitation_token_generator.make_token(first)
        foreign = Organization.objects.create(name="Foreign")
        first.membership.organization_id = foreign.pk

        self.assertFalse(invitation_token_generator.check_token(first, token, now=ISSUED_AT))

    def test_token_of_one_user_does_not_work_with_another_uid(self):
        self.login(self.admin)
        self.invited_membership("owner@example.org")
        _uid, token = self.link_parts()
        other = self.invited_membership("other@example.org")
        self.client.logout()

        response = self.confirm(
            urlsafe_base64_encode(force_bytes(other.user.pk)), token, ISSUED_AT
        )

        self.assertEqual(response.status_code, 400)


class ResendResponseAfterCommitTests(InvitationTestCase):
    def test_acceptance_right_after_the_mutation_does_not_turn_success_into_error(self):
        """F3: the response comes from the mutation, not from a later pending query."""
        membership = self.make_legacy_invitation()
        real_deliver = api_views._deliver_invitation_email

        def deliver_then_invitee_accepts(user, invitation):
            real_deliver(user, invitation)
            # From here on the membership is no longer a pending invitation.
            get_user_model().objects.filter(pk=user.pk).update(password="md5$x$accepted")

        self.login(self.admin)
        with patch.object(api_views, "_deliver_invitation_email", deliver_then_invitee_accepts):
            response = self.resend(membership.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["invitation"]["id"], membership.id)
        self.assertEqual(response.json()["invitation"]["status"], "pending")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AccountInvitation.objects.get(membership=membership).generation, 1)


class HiddenInvitationTests(InvitationTestCase):
    def test_hidden_unaccepted_membership_is_absent_from_both_lists(self):
        hidden = self.make_legacy_invitation("hidden-invitee", is_hidden_from_team=True)
        visible = self.make_legacy_invitation("visible-invitee")
        self.login(self.admin)

        listed = [item["id"] for item in self.invitations().json()["invitations"]]
        members = self.client.get(self.members_url, **self._header(self.paris)).json()["members"]

        self.assertEqual(listed, [visible.id])
        self.assertNotIn(hidden.user.email, {member["email"] for member in members})

    def test_hidden_flag_is_display_only_for_resend(self):
        hidden = self.make_legacy_invitation("hidden-invitee", is_hidden_from_team=True)
        self.login(self.admin)

        response = self.resend(hidden.id)

        self.assertEqual(response.status_code, 200)
        hidden.refresh_from_db()
        self.assertTrue(hidden.is_hidden_from_team)


class LegacyClassificationTests(InvitationTestCase):
    def members(self):
        return {
            member["email"]
            for member in self.client.get(
                self.members_url, **self._header(self.paris)
            ).json()["members"]
        }

    def listed(self):
        return {item["email"] for item in self.invitations().json()["invitations"]}

    def test_proven_legacy_invitation_is_an_invitation(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)

        self.assertIn(membership.user.email, self.listed())
        self.assertNotIn(membership.user.email, self.members())

    def test_unusable_password_without_invitation_audit_stays_a_member(self):
        membership = self.make_legacy_invitation("no-audit", audit=False)
        self.login(self.admin)

        self.assertNotIn(membership.user.email, self.listed())
        self.assertIn(membership.user.email, self.members())
        self.assertEqual(self.resend(membership.id).status_code, 409)
        self.assertFalse(AccountInvitation.objects.exists())

    def test_audit_for_another_membership_or_user_does_not_count(self):
        membership = self.make_legacy_invitation("mismatch", audit=False)
        AuditLog.objects.create(
            organization=self.paris,
            action=AuditLog.Action.CREATION,
            object_type="account",
            description="Member access created",
            metadata={"user_id": membership.user_id, "membership_id": membership.id + 999},
        )
        AuditLog.objects.create(
            organization=self.paris,
            action=AuditLog.Action.CREATION,
            object_type="account",
            description="Member access created",
            metadata={"user_id": membership.user_id + 999, "membership_id": membership.id},
        )
        self.login(self.admin)

        self.assertNotIn(membership.user.email, self.listed())

    def test_audit_in_another_organization_does_not_count(self):
        membership = self.make_legacy_invitation("elsewhere", audit=False)
        AuditLog.objects.create(
            organization=self.partner,
            action=AuditLog.Action.CREATION,
            object_type="account",
            description="Member access created",
            metadata={"user_id": membership.user_id, "membership_id": membership.id},
        )
        self.login(self.admin)

        self.assertNotIn(membership.user.email, self.listed())

    def test_account_that_already_logged_in_is_not_a_legacy_invitation(self):
        membership = self.make_legacy_invitation("logged-in")
        get_user_model().objects.filter(pk=membership.user_id).update(last_login=ISSUED_AT)
        self.login(self.admin)

        self.assertNotIn(membership.user.email, self.listed())
        self.assertIn(membership.user.email, self.members())

    def test_accepted_legacy_invitation_is_a_member(self):
        membership = self.make_legacy_invitation("accepted-legacy")
        membership.user.set_password(PASSWORD)
        membership.user.save(update_fields=["password"])
        self.login(self.admin)

        self.assertNotIn(membership.user.email, self.listed())
        self.assertIn(membership.user.email, self.members())

    def test_inactive_unaccepted_invitation_appears_in_neither_list(self):
        """Current, unchanged behavior (open product question)."""
        membership = self.make_legacy_invitation("inactive-invitee", is_active=False)
        self.login(self.admin)

        self.assertNotIn(membership.user.email, self.listed())
        self.assertNotIn(membership.user.email, self.members())
        self.assertEqual(self.resend(membership.id).status_code, 409)


class ResendStateTests(InvitationTestCase):
    def test_deactivated_membership_cannot_be_resent(self):
        membership = self.make_legacy_invitation()
        OrganizationMembership.objects.filter(pk=membership.pk).update(is_active=False)
        self.login(self.admin)

        response = self.resend(membership.id)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(AccountInvitation.objects.exists())

    def test_audit_failure_rolls_back_resend(self):
        membership = self.make_legacy_invitation()
        self.login(self.admin)
        real_create = AuditLog.objects.create

        def failing_create(**kwargs):
            if kwargs.get("description") == "Member invitation resent":
                raise RuntimeError("audit down")
            return real_create(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=failing_create):
            with self.assertRaises(RuntimeError):
                self.resend(membership.id)

        self.assertFalse(AccountInvitation.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_resend_at_exact_expiry_is_refused_and_one_second_later_allowed(self):
        self.login(self.admin)
        membership = self.invited_membership("edge@example.org")
        expires_at = ISSUED_AT + timedelta(hours=24)

        self.assertEqual(self.resend(membership.id, moment=expires_at).status_code, 409)
        self.assertEqual(
            self.resend(membership.id, moment=expires_at + timedelta(seconds=1)).status_code,
            200,
        )
