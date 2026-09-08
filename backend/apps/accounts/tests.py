from datetime import datetime, timedelta
from importlib import import_module
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import AuthenticationThrottle, OrganizationMembership, UserPreference
from .api_views import normalize_email, validate_email_identity


class AccountPreferenceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", email="tech@example.org",password="secret")

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

    def test_legacy_auth_routes_are_not_exposed(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 404)
        self.assertEqual(self.client.get("/accounts/password_reset/").status_code, 404)
        self.assertEqual(self.client.get("/admin/login/").status_code, 404)


class SessionLoginApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", email="tech@example.org",password="secret")
        self.user.email = "tech@example.org"
        self.user.save(update_fields=["email"])
        self.login_url = reverse("api_session_login")

    def test_session_login_sets_an_authenticated_session(self):
        UserPreference.objects.create(
            user=self.user,
            interface_language=UserPreference.InterfaceLanguage.ENGLISH,
        )
        client = Client(enforce_csrf_checks=True)
        csrf_response = client.get(self.login_url)
        csrf_token = csrf_response.cookies["csrftoken"].value

        response = client.post(
            self.login_url,
            data={"email": self.user.email, "password": "secret"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["interface_language"], UserPreference.InterfaceLanguage.ENGLISH)
        self.assertEqual(client.session["interface_language"], UserPreference.InterfaceLanguage.ENGLISH)
        profile_response = client.get(reverse("api_profile"))
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(profile_response.json()["email"], self.user.email)

    def test_session_login_rejects_username_as_a_credential(self):
        response = self.client.post(
            self.login_url,
            data={"username": "tech", "password": "secret"},
        )

        self.assertEqual(response.status_code, 400)

    def test_session_login_matches_email_without_case_sensitivity(self):
        response = self.client.post(
            self.login_url,
            data={"email": "TECH@EXAMPLE.ORG", "password": "secret"},
        )

        self.assertEqual(response.status_code, 200)

    def test_session_login_rejects_invalid_credentials(self):
        response = self.client.post(
            self.login_url,
            data={"email": self.user.email, "password": "invalid"},
        )

        self.assertEqual(response.status_code, 400)

    @override_settings(
        AUTH_LOGIN_IP_MAX_FAILURES=2,
        AUTH_LOGIN_IP_WINDOW_SECONDS=900,
        AUTH_LOGIN_IP_BLOCK_SECONDS=900,
    )
    def test_session_login_is_rate_limited_after_repeated_failures(self):
        for _attempt in range(2):
            response = self.client.post(
                self.login_url,
                data={"email": self.user.email, "password": "invalid"},
            )
            self.assertEqual(response.status_code, 400)

        response = self.client.post(
            self.login_url,
            data={"email": self.user.email, "password": "invalid"},
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_successful_login_clears_previous_failures(self):
        self.client.post(
            self.login_url,
            data={"email": self.user.email, "password": "invalid"},
        )
        self.assertTrue(
            AuthenticationThrottle.objects.filter(
                scope="login_account",
                key_hash__isnull=False,
            ).exists()
        )

        response = self.client.post(
            self.login_url,
            data={"email": self.user.email, "password": "secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            AuthenticationThrottle.objects.filter(scope="login_account").exists()
        )
        self.assertTrue(
            AuthenticationThrottle.objects.filter(scope="login_ip").exists()
        )

    def test_email_normalization_strips_and_lowercases_ascii_identity(self):
        self.assertEqual(
            normalize_email("  USER@Example.COM  "),
            "user@example.com",
        )
        self.assertEqual(normalize_email("\tUSER@Example.COM\n"), "user@example.com")
        self.assertEqual(
            validate_email_identity(" User@München.DE "),
            "user@xn--mnchen-3ya.de",
        )
        with self.assertRaises(DRFValidationError):
            validate_email_identity("Straße@example.com")
        with self.assertRaises(DRFValidationError):
            validate_email_identity("K@example.com")
        for invalid_email in (
            "",
            "not-an-email",
            ".leading@example.org",
            "double..dot@example.org",
            "trailing.@example.org",
            "user@example",
            "user@@example.org",
            ("a" * 250) + "@x.org",
        ):
            with self.subTest(invalid_email=invalid_email):
                with self.assertRaises(DRFValidationError):
                    validate_email_identity(invalid_email)

    def test_database_identity_index_rejects_trimmed_case_duplicate(self):
        user_model = get_user_model()
        with self.assertRaises(IntegrityError):
            user_model.objects.create_user(username="duplicate", email=self.user.email,
                password="secret",
            )

    def test_database_identity_guard_rejects_noncanonical_email_storage(self):
        user_model = get_user_model()
        with self.assertRaises(IntegrityError):
            user_model(
                username="noncanonical",
                email="other@example.org ",
            ).save(force_insert=True)

    def test_database_identity_guard_rejects_blank_email_storage(self):
        user_model = get_user_model()
        with self.assertRaises(IntegrityError):
            user_model(username="blank", email="").save(force_insert=True)

    def test_database_identity_guard_rejects_invalid_email_storage(self):
        user_model = get_user_model()
        invalid_emails = (
            "not-an-email",
            ".leading@example.org",
            "double..dot@example.org",
            "trailing.@example.org",
            "user@example",
            "USER@example.org",
            f"user@{'a' * 64}.org",
        )
        for index, email in enumerate(invalid_emails):
            with self.subTest(email=email):
                with transaction.atomic():
                    with self.assertRaises(IntegrityError):
                        user_model(
                            username=f"invalid-{index}",
                            email=email,
                        ).save(force_insert=True)

    def test_identity_migration_waits_for_final_django_user_schema(self):
        from django.db.migrations.loader import MigrationLoader

        graph = MigrationLoader(connection).graph
        parents = set(graph.node_map[("accounts", "0005_email_identity")].parents)
        self.assertIn(("auth", "0012_alter_user_first_name_max_length"), parents)

    def test_identity_migration_rejects_invalid_legacy_email(self):
        migration = import_module("apps.accounts.migrations.0005_email_identity")

        class FakeUser:
            def __init__(self, pk, email):
                self.pk = pk
                self.email = email

            def save(self, **kwargs):
                self.saved_email = self.email

        class FakeManager:
            def __init__(self, users):
                self.users = users

            def order_by(self, _field):
                return self.users

        class FakeUserModel:
            def __init__(self, users):
                self.objects = FakeManager(users)

        class FakeApps:
            def __init__(self, users):
                self.users = users

            def get_model(self, _app, _model):
                return FakeUserModel(self.users)

        with self.assertRaises(RuntimeError):
            migration.normalize_existing_emails(
                FakeApps([FakeUser(1, "double..dot@example.org")]),
                None,
            )

    def test_identity_migration_rejects_duplicate_effective_email(self):
        migration = import_module("apps.accounts.migrations.0005_email_identity")

        class FakeUser:
            def __init__(self, pk, email):
                self.pk = pk
                self.email = email

            def save(self, **kwargs):
                self.saved_email = self.email

        class FakeManager:
            def __init__(self, users):
                self.users = users

            def order_by(self, _field):
                return self.users

        class FakeUserModel:
            def __init__(self, users):
                self.objects = FakeManager(users)

        class FakeApps:
            def __init__(self, users):
                self.users = users

            def get_model(self, _app, _model):
                return FakeUserModel(self.users)

        with self.assertRaises(RuntimeError):
            migration.normalize_existing_emails(
                FakeApps(
                    [
                        FakeUser(1, "first@example.org"),
                        FakeUser(2, "FIRST@example.org"),
                    ]
                ),
                None,
            )

    def test_login_response_includes_browser_security_headers(self):
        response = self.client.get(self.login_url)

        self.assertIn("Content-Security-Policy", response)
        self.assertEqual(
            response["Permissions-Policy"],
            "camera=(self), microphone=(), geolocation=()",
        )

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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_DELIVERY_ENABLED=True,
    DEFAULT_FROM_EMAIL="Polypbase <noreply@polypbase.org>",
)
class AccountMemberManagementTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.paris = Organization.objects.create(name="Paris")
        self.partner = Organization.objects.create(name="Partner")

        self.admin = user_model.objects.create_user(username="admin", email="admin@example.org",password="secret")
        self.viewer = user_model.objects.create_user(username="viewer", email="viewer@example.org",password="secret")
        self.tech = user_model.objects.create_user(username="tech", email="tech@example.org",password="secret")

        for user, email in (
            (self.admin, "admin@example.org"),
            (self.viewer, "viewer@example.org"),
            (self.tech, "tech@example.org"),
        ):
            user.email = email
            user.save(update_fields=["email"])

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
        emails = {member["email"] for member in body["members"]}
        self.assertEqual(emails, {self.admin.email, self.viewer.email})
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
                "first_name": "kylian",
                "last_name": "mbappé",
                "email": "new@münchen.de",
                "organization_id": self.paris.id,
                "role": "lab_technician",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        membership = OrganizationMembership.objects.get(
            user__email="new@xn--mnchen-3ya.de", organization=self.paris
        )
        self.assertEqual(membership.role, OrganizationMembership.Role.LAB_TECHNICIAN)
        self.assertEqual(membership.user.first_name, "Kylian")
        self.assertEqual(membership.user.last_name, "MBAPPÉ")
        self.assertFalse(membership.user.has_usable_password())
        self.assertTrue(membership.user.username.startswith("internal_"))
        self.assertEqual(response.json()["full_name"], "Kylian MBAPPÉ")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].from_email,
            "Polypbase <noreply@polypbase.org>",
        )
        self.assertIn("/reset-password/", mail.outbox[0].body)
        self.assertNotIn("Mot de passe temporaire", mail.outbox[0].body)
        self.assertIn("Hello,", mail.outbox[0].body)
        self.assertNotIn(membership.user.username, mail.outbox[0].body)
        self.assertNotIn("Paris", mail.outbox[0].body)
        self.assertNotIn("admin@example.org", mail.outbox[0].body)

        log = AuditLog.objects.get(
            action=AuditLog.Action.CREATION,
            object_type="account",
            object_id=membership.user.username,
        )
        self.assertEqual(log.organization, self.paris)
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.metadata["valeurs"]["role"], OrganizationMembership.Role.LAB_TECHNICIAN)

    def test_admin_cannot_assign_a_password_to_a_new_member(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
                "email": "nopwd@example.test",
                "password": "mot-de-passe-impose",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(email="nopwd@example.test").exists())
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_member_creation_rolls_back_when_email_delivery_is_disabled(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
                "email": "no-smtp@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(get_user_model().objects.filter(email="no-smtp@example.test").exists())

    @patch("apps.accounts.api_views.send_mail", side_effect=OSError("SMTP unavailable"))
    def test_member_creation_rolls_back_when_email_sending_fails(self, _send_mail):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
                "email": "smtp-failure@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(
            get_user_model().objects.filter(email="smtp-failure@example.test").exists()
        )

    def test_admin_cannot_reuse_an_existing_email_from_another_organization(self):
        self.client.login(username="admin", password="secret")
        existing_user = get_user_model().objects.create_user(username="existing", email="existing@example.test",
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
                "email": "existing@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["email"],
            "Cette adresse email est déjà utilisée.",
        )
        self.assertFalse(
            OrganizationMembership.objects.filter(user=existing_user, organization=self.paris).exists()
        )

    def test_admin_cannot_reuse_an_existing_email_with_a_new_username(self):
        self.client.login(username="admin", password="secret")
        existing_user = get_user_model().objects.create_user(username="existing", email="existing@example.test",
            password="secret",
        )

        response = self.client.post(
            self.list_url,
            data={
                "email": "existing@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["email"],
            "Cette adresse email est déjà utilisée.",
        )
        self.assertFalse(
            OrganizationMembership.objects.filter(user=existing_user, organization=self.paris).exists()
        )
        self.assertEqual(
            get_user_model().objects.filter(email="existing@example.test").count(),
            1,
        )

    def test_admin_rejects_case_equivalent_email(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
                "email": self.viewer.email.upper(),
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json())

    def test_admin_generates_distinct_internal_usernames(self):
        self.client.force_login(self.admin)
        payload = {
            "first_name": "A",
            "last_name": "User",
            "organization_id": self.paris.id,
            "role": "viewer",
        }

        first = self.client.post(
            self.list_url,
            data={**payload, "email": "first@example.org"},
            content_type="application/json",
        )
        second = self.client.post(
            self.list_url,
            data={**payload, "email": "second@example.org"},
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        usernames = set(
            get_user_model().objects.filter(
                email__in=["first@example.org", "second@example.org"]
            ).values_list("username", flat=True)
        )
        self.assertEqual(len(usernames), 2)
        self.assertTrue(all(username.startswith("internal_") for username in usernames))

    def test_admin_create_requires_email_for_invitation(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={"organization_id": self.paris.id, "role": "viewer"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_admin_rejects_malformed_email(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            self.list_url,
            data={
                "first_name": "Invalid",
                "last_name": "Email",
                "email": "not-an-email",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json())
        self.assertFalse(get_user_model().objects.filter(email="not-an-email").exists())

    def test_admin_rejects_non_ascii_email_identity(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            self.list_url,
            data={
                "first_name": "Unicode",
                "last_name": "Email",
                "email": "Straße@example.test",
                "organization_id": self.paris.id,
                "role": "viewer",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json())

    def test_admin_cannot_create_in_unmanaged_org(self):
        self.client.login(username="admin", password="secret")

        response = self.client.post(
            self.list_url,
            data={
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
        other_admin = get_user_model().objects.create_user(username="admin2", email="admin2@example.org",password="secret")
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

    def test_admin_can_downgrade_another_admin_when_one_admin_remains(self):
        self.client.login(username="admin", password="secret")
        other_admin = get_user_model().objects.create_user(username="admin2", email="admin2@example.org",password="secret")
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

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role, OrganizationMembership.Role.VIEWER)

    def test_admin_cannot_deactivate_their_own_access(self):
        self.client.login(username="admin", password="secret")
        membership = OrganizationMembership.objects.get(
            user=self.admin, organization=self.paris
        )
        url = reverse("api_account_member_detail", args=[membership.id])

        response = self.client.patch(
            url, data={"is_active": False}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 403)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_DELIVERY_ENABLED=True,
    DEFAULT_FROM_EMAIL="Polypbase <noreply@polypbase.org>",
)
class PasswordResetTests(TestCase):
    """The "forgot password" flow reachable from the login page."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="biologiste", email="bio@example.org",
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
        self.assertEqual(
            mail.outbox[0].from_email,
            "Polypbase <noreply@polypbase.org>",
        )
        self.assertIn("/reset-password/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["bio@example.org"])

    @patch("apps.accounts.api_views.send_mail", side_effect=OSError("SMTP unavailable"))
    def test_smtp_failure_keeps_password_reset_response_indistinguishable(self, _send_mail):
        with self.assertLogs("apps.accounts.api_views", level="ERROR"):
            response = self.request_reset("bio@example.org")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(len(mail.outbox), 0)

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

    @override_settings(
        AUTH_RESET_IP_MAX_REQUESTS=2,
        AUTH_RESET_IP_WINDOW_SECONDS=900,
        AUTH_RESET_IP_BLOCK_SECONDS=900,
    )
    def test_password_reset_is_rate_limited_by_ip(self):
        self.assertEqual(self.request_reset("personne@example.org").status_code, 204)
        self.assertEqual(self.request_reset("personne@example.org").status_code, 204)

        response = self.request_reset("personne@example.org")

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    @override_settings(AUTH_RESET_ACCOUNT_MAX_REQUESTS=3)
    def test_password_reset_sends_at_most_three_links_per_account(self):
        responses = [self.request_reset("bio@example.org") for _attempt in range(4)]

        self.assertTrue(all(response.status_code == 204 for response in responses))
        self.assertEqual(len(mail.outbox), 3)

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

    @patch(
        "apps.accounts.api_views.AuditLog.objects.create",
        side_effect=RuntimeError("Audit log unavailable"),
    )
    def test_audit_failure_rolls_back_password_reset(self, _create_audit_log):
        uid, token = self.make_link_parts()

        with self.assertRaisesMessage(RuntimeError, "Audit log unavailable"):
            self.confirm_reset(uid, token, "un-mot-de-passe-solide-42")

        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("un-mot-de-passe-solide-42"))
        self.assertTrue(self.user.check_password("ancien-mot-de-passe"))
        self.assertTrue(default_token_generator.check_token(self.user, token))
        self.assertFalse(AuditLog.objects.filter(user=self.user).exists())

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
