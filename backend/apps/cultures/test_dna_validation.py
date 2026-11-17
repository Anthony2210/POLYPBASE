"""DNA evidence API, authorization, retained history and required audit."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .dna_validation import can_edit_dna_validation, mutate_dna_validation
from .models import Box, BoxDnaValidation, BoxLineage


@override_settings(SECURE_SSL_REDIRECT=False)
class DnaValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="DNA laboratory A")
        cls.other_organization = Organization.objects.create(name="DNA laboratory B")
        cls.author = get_user_model().objects.create_user(
            username="dna-author", email="author@example.org", first_name="Ada", last_name="Lovelace",
        )
        cls.editor = get_user_model().objects.create_user(
            username="dna-editor", email="editor@example.org",
        )
        for user, role in ((cls.author, "lab_technician"), (cls.editor, "admin")):
            for organization in (cls.organization, cls.other_organization):
                OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        species = Species.objects.create(scientific_name="Aurelia DNA", genus_species_code="DNA")
        strain = Strain.objects.create(species=species, organization=cls.organization, code="DNA-A-1")
        cls.box = Box.objects.create(
            organization=cls.organization, strain=strain, global_code="DNA-A-1.001", box_number="001",
        )
        other_strain = Strain.objects.create(
            species=species, organization=cls.other_organization, code="DNA-B-1",
        )
        cls.other_box = Box.objects.create(
            organization=cls.other_organization, strain=other_strain,
            global_code="DNA-B-1.001", box_number="001",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.author)
        self.client.credentials(HTTP_X_ORGANIZATION_ID=str(self.organization.pk))

    def intent(self, operation="add", data=None, box=None):
        name = "api_box_dna_validation_revoke" if operation == "revoke" else "api_box_dna_validation"
        url = reverse(name, args=[(box or self.box).pk])
        method = self.client.patch if operation == "update" else self.client.post
        return method(url, data if data is not None else {"evidence_url": "https://example.org/evidence"}, format="json")

    def detail(self, box=None):
        return self.client.get(reverse("api_box_detail", args=[(box or self.box).pk]))

    def audits(self):
        return AuditLog.objects.filter(metadata__event__startswith="box_dna_validation_").order_by("pk")

    def test_add_update_revoke_preserves_attribution_and_snapshots(self):
        self.assertIsNone(self.detail().data["dna_validation"])
        self.assertTrue(self.detail().data["can_edit_dna_validation"])
        response = self.intent(data={
            "evidence_url": "  https://example.org/evidence  ",
            "created_by": self.editor.pk, "revision": 900, "box": self.other_box.pk,
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(set(response.data), {"id", "evidence_url", "created_by", "created_at", "revision"})
        self.assertEqual(response.data["created_by"], "Ada LOVELACE")
        self.assertEqual(response.data["revision"], 1)
        self.assertEqual(self.detail().data["dna_validation"], response.data)
        validation = BoxDnaValidation.objects.get()
        created_at = validation.created_at
        self.assertEqual(validation.box_id, self.box.pk)
        self.assertEqual(validation.created_by_id, self.author.pk)
        self.assertEqual(validation.updated_by_id, self.author.pk)
        self.assertEqual(validation.evidence_url, "https://example.org/evidence")
        first = self.audits().get()
        self.assertEqual(first.organization_id, self.organization.pk)
        self.assertEqual(first.user_id, self.author.pk)
        self.assertEqual(first.object_type, "box")
        self.assertEqual(first.object_id, self.box.global_code)
        self.assertEqual(first.metadata["event"], "box_dna_validation_added")
        self.assertIsNone(first.metadata["before"])
        self.assertEqual(first.metadata["after"]["created_by"], "Ada LOVELACE")

        self.client.force_authenticate(self.editor)
        response = self.intent("update", {"evidence_url": "http://example.org/corrected", "expected_revision": 1})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["revision"], 2)
        self.assertEqual(response.data["created_by"], "Ada LOVELACE")
        validation.refresh_from_db()
        self.assertEqual(validation.created_at, created_at)
        self.assertEqual(validation.updated_by_id, self.editor.pk)
        self.assertGreaterEqual(validation.updated_at, created_at)
        second = self.audits().last()
        self.assertEqual(second.metadata["event"], "box_dna_validation_updated")
        self.assertEqual(second.metadata["before"], first.metadata["after"])
        self.assertEqual(second.metadata["after"]["updated_by_id"], self.editor.pk)
        self.assertEqual(second.metadata["actor_id"], self.editor.pk)
        self.assertEqual(second.metadata["actor"], "editor@example.org")

        response = self.intent("revoke", {"expected_revision": 2})
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.detail().data["dna_validation"])
        validation.refresh_from_db()
        self.assertEqual(validation.revision, 3)
        self.assertEqual(validation.revoked_by_id, self.editor.pk)
        self.assertIsNotNone(validation.revoked_at)
        self.assertEqual(validation.created_by_id, self.author.pk)
        third = self.audits().last()
        self.assertEqual(third.metadata["event"], "box_dna_validation_revoked")
        self.assertEqual(third.metadata["before"], second.metadata["after"])
        self.assertEqual(third.metadata["after"]["revoked_by_id"], self.editor.pk)
        self.assertEqual(third.user_id, self.editor.pk)
        self.assertEqual(third.organization_id, self.organization.pk)
        self.assertEqual(self.audits().count(), 3)
        self.assertEqual(BoxDnaValidation.objects.count(), 1)

    def test_duplicate_stale_no_change_and_aba_do_not_write(self):
        self.assertEqual(self.intent().status_code, 201)
        duplicate = self.intent()
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.data["code"], "dna_validation_exists")
        no_change = self.intent("update", {"evidence_url": " https://example.org/evidence ", "expected_revision": 1})
        self.assertEqual(no_change.status_code, 400)
        self.assertEqual(no_change.data["code"], "dna_validation_no_change")
        for operation in ("update", "revoke"):
            response = self.intent(operation, {"evidence_url": "https://example.org/new", "expected_revision": 2})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["code"], "dna_validation_changed")
        self.assertEqual(self.audits().count(), 1)
        self.assertEqual(BoxDnaValidation.objects.get().revision, 1)
        self.assertEqual(self.intent("revoke", {"expected_revision": 1}).status_code, 204)
        for operation in ("update", "revoke"):
            response = self.intent(operation, {"evidence_url": "https://example.org/new", "expected_revision": 1})
            self.assertEqual(response.status_code, 409)
        replacement = self.intent()
        self.assertEqual(replacement.status_code, 201)
        self.assertEqual(replacement.data["revision"], 3)
        for operation in ("update", "revoke"):
            response = self.intent(operation, {"evidence_url": "https://example.org/new", "expected_revision": 1})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data["code"], "dna_validation_changed")
        self.assertEqual(self.audits().count(), 3)
        self.assertEqual(BoxDnaValidation.objects.count(), 2)
        self.assertEqual(BoxDnaValidation.objects.filter(revoked_at__isnull=True).count(), 1)

    def test_url_validation_and_limit_on_create_and_update(self):
        invalid = (None, "", "   ", "not a URL", "ftp://example.org/file", "file:///tmp/dna", "javascript:alert(1)", "//example.org/file", "https://", "https://example.org/" + "a" * 1981)
        for value in invalid:
            with self.subTest(value=value):
                response = self.intent(data={"evidence_url": value})
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.intent(data={}).status_code, 400)
        url = "https://example.org/" + "a" * (2000 - len("https://example.org/"))
        response = self.intent(data={"evidence_url": "  " + url + "  "})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["evidence_url"], url)
        for value in invalid:
            with self.subTest(update=value):
                self.assertEqual(self.intent("update", {"evidence_url": value, "expected_revision": 1}).status_code, 400)
        self.assertEqual(self.audits().count(), 1)

    def test_expected_revision_is_required_positive_integer(self):
        self.intent()
        for operation in ("update", "revoke"):
            for revision in (None, 0, -1, True, "bad", 1.5):
                with self.subTest(operation=operation, revision=revision):
                    response = self.intent(operation, {"evidence_url": "https://example.org/new", "expected_revision": revision})
                    self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(self.intent(operation, {"evidence_url": "https://example.org/new"}).status_code, 400)
        self.assertEqual(self.audits().count(), 1)

    def test_all_box_statuses_allow_all_intents(self):
        for status in Box.Status.values:
            with self.subTest(status=status):
                Box.objects.filter(pk=self.box.pk).update(status=status)
                response = self.intent()
                self.assertEqual(response.status_code, 201, response.data)
                revision = response.data["revision"]
                self.assertTrue(self.detail().data["can_edit_dna_validation"])
                response = self.intent("update", {"evidence_url": "https://example.org/new", "expected_revision": revision})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.intent("revoke", {"expected_revision": response.data["revision"]}).status_code, 204)

    def test_foreign_box_is_404_for_every_intent_and_status_even_with_membership(self):
        for status in Box.Status.values:
            Box.objects.filter(pk=self.other_box.pk).update(status=status)
            for operation in ("add", "update", "revoke"):
                with self.subTest(status=status, operation=operation):
                    response = self.intent(operation, {}, box=self.other_box)
                    self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(BoxDnaValidation.objects.exists())
        self.assertFalse(self.audits().exists())

    def test_viewer_is_403_including_when_superuser(self):
        membership = OrganizationMembership.objects.get(user=self.author, organization=self.organization)
        membership.role = "viewer"
        membership.save()
        for superuser in (False, True):
            self.author.is_superuser = superuser
            self.author.save()
            self.assertFalse(self.detail().data["can_edit_dna_validation"])
            for operation in ("add", "update", "revoke"):
                self.assertEqual(self.intent(operation, {}).status_code, 403)
        self.assertFalse(self.audits().exists())

    def test_inactive_or_missing_membership_has_no_breakglass(self):
        self.author.is_superuser = True
        self.author.save()
        memberships = OrganizationMembership.objects.filter(user=self.author, organization=self.organization)
        memberships.update(is_active=False)
        self.assertFalse(self.detail().data["can_edit_dna_validation"])
        for operation in ("add", "update", "revoke"):
            self.assertEqual(self.intent(operation, {}).status_code, 403)
        memberships.delete()
        self.assertFalse(self.detail().data["can_edit_dna_validation"])
        self.assertEqual(self.intent().status_code, 403)
        self.assertFalse(BoxDnaValidation.objects.exists())

    def test_inactive_institution_and_user_are_denied_even_with_stale_objects(self):
        Organization.objects.filter(pk=self.organization.pk).update(is_active=False)
        self.assertEqual(self.intent().status_code, 403)
        from rest_framework.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            mutate_dna_validation(user=self.author, organization=self.organization, box_id=self.box.pk, operation="add", data={})
        Organization.objects.filter(pk=self.organization.pk).update(is_active=True)
        get_user_model().objects.filter(pk=self.author.pk).update(is_active=False)
        self.assertFalse(can_edit_dna_validation(user=self.author, organization=self.organization))
        self.assertEqual(self.intent().status_code, 403)
        self.assertFalse(BoxDnaValidation.objects.exists())

    def test_explicit_context_and_authentication_are_required(self):
        self.client.credentials()
        self.assertEqual(self.intent().status_code, 403)
        self.client.credentials(HTTP_X_ORGANIZATION_ID="invalid")
        self.assertEqual(self.intent().status_code, 403)
        self.client.force_authenticate(user=None)
        self.assertIn(self.intent().status_code, (401, 403))
        self.assertFalse(self.audits().exists())

    def test_audit_failure_rolls_back_each_intent(self):
        for operation in ("add", "update", "revoke"):
            if operation == "update":
                self.assertEqual(self.intent().status_code, 201)
            before = list(BoxDnaValidation.objects.values())
            audits_before = list(self.audits().values())
            with patch("apps.cultures.dna_validation.AuditLog.objects.create", side_effect=RuntimeError("audit unavailable")):
                with self.assertRaisesMessage(RuntimeError, "audit unavailable"):
                    self.intent(operation, {"evidence_url": "https://example.org/new", "expected_revision": 1})
            self.assertEqual(list(BoxDnaValidation.objects.values()), before)
            self.assertEqual(list(self.audits().values()), audits_before)

    def test_database_constraints_bypass_model_validation(self):
        self.intent()
        existing = BoxDnaValidation.objects.get()
        fields = dict(box=self.box, evidence_url="https://example.org/other", created_by=self.author, updated_by=self.author)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxDnaValidation.objects.bulk_create([BoxDnaValidation(**fields)])
        for values in ({"revision": 0}, {"revoked_at": timezone.now()}, {"revoked_by": self.author}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                BoxDnaValidation.objects.filter(pk=existing.pk).update(**values)
        self.assertEqual(BoxDnaValidation.objects.get().revision, 1)

    def test_model_validates_trims_and_protects_history_from_deletion(self):
        validation = BoxDnaValidation.objects.create(
            box=self.box, evidence_url="  http://example.org/file  ", created_by=self.author, updated_by=self.author,
        )
        self.assertEqual(validation.evidence_url, "http://example.org/file")
        validation.evidence_url = "ftp://example.org/file"
        with self.assertRaises(ValidationError):
            validation.save()
        for delete in (validation.delete, BoxDnaValidation.objects.all().delete):
            with self.assertRaises(ValidationError):
                delete()
        with self.assertRaises(ProtectedError):
            self.box.delete()
        with self.assertRaises(ProtectedError):
            self.author.delete()
        self.assertEqual(self.intent("revoke", {"expected_revision": 1}).status_code, 204)
        with self.assertRaises(ValidationError):
            BoxDnaValidation.objects.all().delete()
        self.assertEqual(BoxDnaValidation.objects.count(), 1)

    def test_no_inheritance_to_related_box_and_no_delete_endpoint(self):
        self.intent()
        child = Box.objects.create(
            organization=self.organization, strain=self.box.strain, global_code="DNA-A-1.002", box_number="002",
        )
        BoxLineage.objects.create(parent_box=self.box, child_box=child)
        self.assertIsNone(self.detail(child).data["dna_validation"])
        response = self.client.delete(reverse("api_box_dna_validation", args=[self.box.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(BoxDnaValidation.objects.count(), 1)

    def test_french_and_english_messages(self):
        self.intent()
        for language, expected in (("fr", "Une validation ADN existe déjà."), ("en", "A DNA validation already exists.")):
            session = self.client.session
            session["interface_language"] = language
            session.save()
            with translation.override(language):
                response = self.intent()
                self.assertEqual(str(response.data["detail"]), expected)
