from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
                "first_name": "New",
                "last_name": "Tech",
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

    def test_admin_create_requires_password_for_new_user(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={"username": "nopwd", "organization_id": self.paris.id, "role": "viewer"},
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
