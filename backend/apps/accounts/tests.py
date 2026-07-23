from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import OrganizationMembership, UserPreference


class AccountPreferenceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", password="secret")

    def test_account_settings_defaults_to_french(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("account_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Langue de l&#x27;interface")
        preference = UserPreference.objects.get(user=self.user)
        self.assertEqual(preference.interface_language, UserPreference.InterfaceLanguage.FRENCH)

    def test_account_settings_updates_interface_language(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("account_settings"),
            data={"interface_language": UserPreference.InterfaceLanguage.ENGLISH},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Interface language")
        self.assertEqual(
            self.user.preference.interface_language,
            UserPreference.InterfaceLanguage.ENGLISH,
        )

    def test_legacy_account_preferences_api_is_removed(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post("/accounts/api/preferences/")

        self.assertEqual(response.status_code, 404)


class SessionLoginApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", password="secret")
        self.login_url = reverse("api_session_login")

    def test_session_login_sets_an_authenticated_session(self):
        client = Client(enforce_csrf_checks=True)
        csrf_response = client.get(self.login_url)
        csrf_token = csrf_response.cookies["csrftoken"].value

        response = client.post(
            self.login_url,
            data={"username": "tech", "password": "secret"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 204)
        profile_response = client.get(reverse("api_profile"))
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(profile_response.json()["username"], self.user.username)

    def test_session_login_rejects_invalid_credentials(self):
        response = self.client.post(
            self.login_url,
            data={"username": "tech", "password": "invalid"},
        )

        self.assertEqual(response.status_code, 400)

    def test_session_logout_clears_the_current_session(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username="tech", password="secret")

        profile_response = client.get(reverse("api_profile"))
        csrf_token = profile_response.cookies["csrftoken"].value
        response = client.post(
            reverse("api_session_logout"),
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(client.get(reverse("api_profile")).status_code, 403)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AccountMemberManagementTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.paris = Organization.objects.create(name="Paris")
        self.partner = Organization.objects.create(name="Partner")

        self.admin = user_model.objects.create_user(username="admin", password="secret")
        self.viewer = user_model.objects.create_user(username="viewer", password="secret")
        self.tech = user_model.objects.create_user(username="tech", password="secret")

        OrganizationMembership.objects.create(
            user=self.admin, organization=self.paris, role=OrganizationMembership.Role.ADMIN
        )
        OrganizationMembership.objects.create(
            user=self.viewer, organization=self.paris, role=OrganizationMembership.Role.VIEWER
        )
        # Membership in an organization the admin does not administer.
        OrganizationMembership.objects.create(
            user=self.tech, organization=self.partner, role=OrganizationMembership.Role.LAB_TECHNICIAN
        )

        self.list_url = reverse("api_account_members")

    def test_admin_lists_only_managed_org_members(self):
        self.client.login(username="admin", password="secret")

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        usernames = {member["username"] for member in body["members"]}
        self.assertEqual(usernames, {"admin", "viewer"})
        self.assertEqual(
            [org["name"] for org in body["manageable_organizations"]], ["Paris"]
        )

    def test_viewer_cannot_access_member_management(self):
        self.client.login(username="viewer", password="secret")

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 403)

    def test_admin_creates_new_member(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
                "username": "newtech",
                "first_name": "kylian",
                "last_name": "mbappé",
                "email": "new@example.test",
                "password": "averysafepwd",
                "organization_id": self.paris.id,
                "role": "lab_technician",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        membership = OrganizationMembership.objects.get(
            user__username="newtech", organization=self.paris
        )
        self.assertEqual(membership.role, OrganizationMembership.Role.LAB_TECHNICIAN)
        self.assertEqual(membership.user.first_name, "Kylian")
        self.assertEqual(membership.user.last_name, "MBAPPÉ")
        self.assertEqual(response.json()["full_name"], "Kylian MBAPPÉ")

        log = AuditLog.objects.get(
            action=AuditLog.Action.CREATION,
            object_type="account",
            object_id="newtech",
        )
        self.assertEqual(log.organization, self.paris)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.metadata["valeurs"]["role"], OrganizationMembership.Role.LAB_TECHNICIAN)

    def test_admin_creates_new_member_with_generated_password(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
                "username": "nopwd",
                "email": "nopwd@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Mot de passe temporaire", mail.outbox[0].body)

    def test_admin_can_link_existing_user_from_email_to_another_organization(self):
        self.client.login(username="admin", password="secret")
        existing_user = get_user_model().objects.create_user(
            username="existing",
            email="existing@example.test",
            password="secret",
        )
        OrganizationMembership.objects.create(
            user=existing_user,
            organization=self.partner,
            role=OrganizationMembership.Role.VIEWER,
        )

        response = self.client.post(
            self.list_url,
            data={
                "username": "existing@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(UserPreference.objects.filter(user=existing_user).count(), 1)
        self.assertTrue(
            OrganizationMembership.objects.filter(
                user=existing_user,
                organization=self.paris,
                role=OrganizationMembership.Role.VIEWER,
            ).exists()
        )

    def test_admin_can_link_existing_user_using_email_field(self):
        self.client.login(username="admin", password="secret")
        existing_user = get_user_model().objects.create_user(
            username="existing",
            email="existing@example.test",
            password="secret",
        )

        response = self.client.post(
            self.list_url,
            data={
                "username": "new-alias",
                "email": "existing@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            OrganizationMembership.objects.filter(
                user=existing_user,
                organization=self.paris,
                role=OrganizationMembership.Role.VIEWER,
            ).exists()
        )
        self.assertEqual(
            get_user_model().objects.filter(email="existing@example.test").count(),
            1,
        )

    def test_admin_create_requires_email_for_generated_password(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={"username": "noemail", "organization_id": self.paris.id, "role": "viewer"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_admin_cannot_create_in_unmanaged_org(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
                "username": "intruder",
                "password": "averysafepwd",
                "organization_id": self.partner.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_changes_member_role(self):
        self.client.login(username="admin", password="secret")
        membership = OrganizationMembership.objects.get(
            user=self.viewer, organization=self.paris
        )
        url = reverse("api_account_member_detail", args=[membership.id])

        response = self.client.patch(
            url, data={"role": "admin"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role, OrganizationMembership.Role.ADMIN)

        log = AuditLog.objects.get(
            action=AuditLog.Action.UPDATE,
            object_type="account",
            object_id=self.viewer.username,
        )
        self.assertEqual(log.organization, self.paris)
        self.assertEqual(
            log.metadata["modifications"]["role"]["avant"],
            OrganizationMembership.Role.VIEWER,
        )
        self.assertEqual(
            log.metadata["modifications"]["role"]["apres"],
            OrganizationMembership.Role.ADMIN,
        )

    def test_admin_can_change_own_role_when_another_admin_exists(self):
        self.client.login(username="admin", password="secret")
        other_admin = get_user_model().objects.create_user(username="admin2", password="secret")
        OrganizationMembership.objects.create(
            user=other_admin,
            organization=self.paris,
            role=OrganizationMembership.Role.ADMIN,
        )
        membership = OrganizationMembership.objects.get(
            user=self.admin, organization=self.paris
        )
        url = reverse("api_account_member_detail", args=[membership.id])

        response = self.client.patch(
            url, data={"role": "viewer"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role, OrganizationMembership.Role.VIEWER)

    def test_admin_cannot_change_own_role_if_they_are_the_last_admin(self):
        self.client.login(username="admin", password="secret")
        membership = OrganizationMembership.objects.get(
            user=self.admin, organization=self.paris
        )
        url = reverse("api_account_member_detail", args=[membership.id])

        response = self.client.patch(
            url, data={"role": "viewer"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 403)
        membership.refresh_from_db()
        self.assertEqual(membership.role, OrganizationMembership.Role.ADMIN)

    def test_admin_cannot_downgrade_another_admin_in_the_same_organization(self):
        self.client.login(username="admin", password="secret")
        other_admin = get_user_model().objects.create_user(username="admin2", password="secret")
        OrganizationMembership.objects.create(
            user=other_admin,
            organization=self.paris,
            role=OrganizationMembership.Role.ADMIN,
        )
        membership = OrganizationMembership.objects.get(
            user=other_admin, organization=self.paris
        )
        url = reverse("api_account_member_detail", args=[membership.id])

        response = self.client.patch(
            url, data={"role": "viewer"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 403)
        membership.refresh_from_db()
        self.assertEqual(membership.role, OrganizationMembership.Role.ADMIN)


class PasswordResetTests(TestCase):
    """The "forgot password" flow reachable from the login page."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="biologiste",
            email="bio@example.org",
            password="ancien-mot-de-passe",
        )

    def request_reset(self, email):
        return self.client.post(
            reverse("api_password_reset_request"),
            data={"email": email},
            content_type="application/json",
        )

    def confirm_reset(self, uid, token, password):
        return self.client.post(
            reverse("api_password_reset_confirm"),
            data={"uid": uid, "token": token, "password": password},
            content_type="application/json",
        )

    def make_link_parts(self, user=None):
        target = user or self.user
        return (
            urlsafe_base64_encode(force_bytes(target.pk)),
            default_token_generator.make_token(target),
        )

    def test_known_address_receives_a_reset_link(self):
        response = self.request_reset("bio@example.org")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/reset-password/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["bio@example.org"])

    def test_address_is_matched_regardless_of_case(self):
        response = self.request_reset("BIO@Example.ORG")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_address_answers_the_same_and_sends_nothing(self):
        # A different status or message would let anyone probe which addresses
        # have an account, so the response must be indistinguishable.
        known = self.request_reset("bio@example.org")
        mail.outbox.clear()
        unknown = self.request_reset("personne@example.org")

        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown.content, known.content)
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_account_receives_nothing(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.request_reset("bio@example.org")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_link_sets_the_new_password(self):
        uid, token = self.make_link_parts()

        response = self.confirm_reset(uid, token, "un-mot-de-passe-solide-42")

        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("un-mot-de-passe-solide-42"))
        self.assertTrue(
            AuditLog.objects.filter(
                user=self.user,
                object_type="account",
                object_id="biologiste",
            ).exists()
        )

    def test_link_cannot_be_used_twice(self):
        uid, token = self.make_link_parts()
        self.confirm_reset(uid, token, "un-mot-de-passe-solide-42")

        # Changing the password changes the hash the token derives from.
        response = self.confirm_reset(uid, token, "encore-un-autre-mdp-77")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("un-mot-de-passe-solide-42"))

    def test_tampered_token_is_refused(self):
        uid, _token = self.make_link_parts()

        response = self.confirm_reset(uid, "pas-un-vrai-token", "un-mot-de-passe-solide-42")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ancien-mot-de-passe"))

    def test_expired_link_is_refused(self):
        uid, token = self.make_link_parts()

        # The link is checked two hours later, past the one-hour lifetime.
        # (A timeout of 0 would not do: a token minted in the same second is
        # still "0 seconds old", which is not *greater* than the limit.)
        later = datetime.now() + timedelta(hours=2)
        with override_settings(PASSWORD_RESET_TIMEOUT=3600), patch.object(
            type(default_token_generator), "_now", return_value=later
        ):
            response = self.confirm_reset(uid, token, "un-mot-de-passe-solide-42")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ancien-mot-de-passe"))

    def test_weak_password_is_refused_and_reports_why(self):
        uid, token = self.make_link_parts()

        response = self.confirm_reset(uid, token, "1234")

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ancien-mot-de-passe"))

    def test_new_password_allows_login(self):
        uid, token = self.make_link_parts()
        self.confirm_reset(uid, token, "un-mot-de-passe-solide-42")

        logged_in = self.client.login(
            username="biologiste", password="un-mot-de-passe-solide-42"
        )

        self.assertTrue(logged_in)
