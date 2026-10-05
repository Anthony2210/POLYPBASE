"""Current account identity and organization-scoped audit presentation tests."""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.audit.services import readable_account_label
from apps.organizations.models import Organization

from .identity import readable_user_identity_label, serialize_user_identity
from .models import OrganizationMembership


class UserIdentityTests(SimpleTestCase):
    def test_identity_strips_fields_without_mutating_user(self):
        user = get_user_model()(
            username="legacy-tech", first_name="  Élise ",
            last_name=" du Pont ", email=" elise@example.org ",
        )
        self.assertEqual(serialize_user_identity(user), {
            "first_name": "Élise", "last_name": "du Pont", "email": "elise@example.org",
        })
        self.assertEqual(readable_user_identity_label(user), "Élise DU PONT")
        self.assertEqual(user.last_name, " du Pont ")
        self.assertEqual(readable_account_label(get_user_model()(
            first_name="Élise", last_name="du Pont",
        )), "Élise du Pont")

    def test_partial_and_missing_names_never_fall_back_to_username(self):
        for first, last, email, label in (
            ("Ada", "", "ada@example.org", "Ada"),
            ("", "Dùrand", "ada@example.org", "DÙRAND"),
            ("", "", " ada@example.org ", "ada@example.org"),
            (" ", " ", " ", None),
        ):
            with self.subTest(first=first, last=last, email=email):
                user = get_user_model()(
                    username="legacy-tech", first_name=first, last_name=last, email=email,
                )
                self.assertEqual(readable_user_identity_label(user), label)
        self.assertIsNone(serialize_user_identity(None))
        self.assertIsNone(readable_user_identity_label(None))


class AuditIdentityApiTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Identity Lab")
        self.other = Organization.objects.create(name="Other Identity Lab")
        self.actor = self._member("admin", self.organization, role="admin")
        self.target = self._member("legacy-tech", self.organization)
        self.client.force_login(self.actor)

    def _member(self, username, organization, role="viewer"):
        user = get_user_model().objects.create_user(
            username=username, email=f"{username}@example.org",
        )
        OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        return user

    def _log(self, target=None, metadata=None, **kwargs):
        target = target or self.target
        return AuditLog.objects.create(
            organization=self.organization, user=self.actor,
            action=AuditLog.Action.UPDATE, object_type="account",
            object_id=target.username, description="Historical legacy-tech description",
            metadata=metadata if metadata is not None else {
                "user_id": target.id,
                "valeurs": {"nom": target.username, "email": "old@example.org"},
            }, **kwargs,
        )

    def _entries(self, route):
        response = self.client.get(
            reverse(route), HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(response.status_code, 200)
        return {entry["id"]: entry for entry in response.json()["results"]}

    def _assert_target(self, log, identity, label):
        admin = self._entries("api_account_audit_log")[log.id]
        personal = self._entries("api_profile_actions")[log.id]
        self.assertEqual(admin["account_identity"], identity)
        self.assertEqual(personal["resource"]["account_identity"], identity)
        self.assertEqual(personal["resource"]["label"], label)
        self.assertEqual(personal["resource"]["identifier"], label)
        return admin, personal

    def test_current_identity_overrides_snapshot_without_changing_history(self):
        log = self._log(edited_by=self.target)
        metadata = log.metadata.copy()
        self.target.first_name = "  Léa "
        self.target.last_name = " du Pré "
        self.target.email = "current@example.org"
        self.target.save()
        identity = {"first_name": "Léa", "last_name": "du Pré", "email": "current@example.org"}
        admin, personal = self._assert_target(log, identity, "Léa DU PRÉ")
        for entry in (admin, personal):
            self.assertEqual(entry["user_identity"], serialize_user_identity(self.actor))
            self.assertEqual(entry["edited_by_identity"], identity)
            self.assertEqual(entry["description"], log.description)
        self.assertEqual(admin["user"], self.actor.username)
        self.assertEqual(admin["edited_by"], self.target.username)
        self.assertEqual(admin["edited_by_display"], "Léa   du Pré")
        self.assertEqual(admin["metadata"], metadata)
        log.refresh_from_db()
        self.assertEqual(log.metadata, metadata)
        self.assertEqual(log.description, "Historical legacy-tech description")
        self.assertEqual(log.object_id, "legacy-tech")

    def test_nameless_legacy_target_uses_live_email_and_deleted_target_is_null(self):
        log = self._log(edited_by=self.target)
        self._assert_target(log, serialize_user_identity(self.target), self.target.email)
        self.target.delete()
        admin, personal = self._assert_target(log, None, None)
        self.assertIsNone(admin["edited_by_identity"])
        self.assertIsNone(personal["edited_by_identity"])
        self.assertEqual(admin["metadata"]["valeurs"]["nom"], "legacy-tech")

    def test_foreign_target_cannot_be_resolved_by_id_or_legacy_username(self):
        foreign = self._member("foreign-tech", self.other)
        for metadata in ({"user_id": foreign.id}, {"valeurs": {"nom": "foreign-tech"}}):
            log = self._log(target=foreign, metadata=metadata)
            self._assert_target(log, None, None)

    def test_missing_and_malformed_target_ids_do_not_fall_back_to_snapshot(self):
        for user_id in (999999, True, "invalid", [], None):
            with self.subTest(user_id=user_id):
                log = self._log(metadata={
                    "user_id": user_id,
                    "valeurs": {"nom": "legacy-tech", "email": "old@example.org"},
                })
                self._assert_target(log, None, None)

    def test_username_only_target_has_no_current_identity(self):
        log = self._log(metadata={"valeurs": {"nom": "legacy-tech"}})
        self._assert_target(log, None, None)

    def test_deleted_username_only_target_is_not_attributed_to_replacement(self):
        metadata = {"valeurs": {"nom": self.target.username, "email": self.target.email}}
        log = self._log(metadata=metadata)
        original_id = self.target.id
        username = self.target.username
        self.target.delete()
        self._assert_target(log, None, None)
        replacement = self._member(username, self.organization)
        self.assertNotEqual(replacement.id, original_id)
        admin, personal = self._assert_target(log, None, None)
        self.assertEqual(admin["metadata"], metadata)
        self.assertEqual(personal["description"], log.description)
        log.refresh_from_db()
        self.assertEqual(log.metadata, metadata)
        self.assertEqual(log.object_id, username)

    def test_missing_actor_serializes_null(self):
        log = self._log()
        self.actor.delete()
        self.client.force_login(self.target)
        OrganizationMembership.objects.filter(user=self.target).update(role="admin")
        admin = self._entries("api_account_audit_log")[log.id]
        self.assertIsNone(admin["user_identity"])
        self.assertIsNone(admin["edited_by_identity"])

    def test_member_payload_has_structured_names_and_compatibility_full_name(self):
        self.target.first_name = "Anne Marie"
        self.target.last_name = "du Pré"
        self.target.save()
        response = self.client.get(
            reverse("api_account_members"),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(response.status_code, 200)
        member = next(row for row in response.json()["members"] if row["user_id"] == self.target.id)
        self.assertEqual(member["first_name"], "Anne Marie")
        self.assertEqual(member["last_name"], "du Pré")
        self.assertEqual(member["full_name"], "Anne Marie du Pré")
