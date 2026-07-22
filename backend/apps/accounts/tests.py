from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

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

    def test_admin_cannot_change_own_role(self):
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
