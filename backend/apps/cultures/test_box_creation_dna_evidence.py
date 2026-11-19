"""Optional DNA evidence given in the ordinary Box creation form.

The evidence reuses the Box page DNA validation contract: same URL rule, same
Admin/technician membership authority, same row and audit event. It is written
in the creation transaction, so a Box never commits without the evidence it was
created with, and creation without evidence keeps its previous stored form.
"""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from config.disposable_postgres import DisposablePostgresFlushMixin

from . import dna_validation
from .automatic_box_creation import AutomaticBoxCreationConflict
from .models import (
    AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxDnaValidation, BoxGlobalIdentity, BoxLocation, ThermalZone,
)
from .test_automatic_box_creation import AutomaticBoxFixture
from .test_automatic_box_intents import AutomaticBoxIntentFixture

EVIDENCE_URL = "https://lab.example.org/dna/report-42"


class BoxCreationDnaEvidenceServiceTests(AutomaticBoxIntentFixture, DisposablePostgresFlushMixin, TransactionTestCase):
    def create(self, **extra):
        self.payload.update(extra)
        self.register()
        return self.execute()

    def test_creation_without_evidence_keeps_the_previous_intent_and_creates_no_evidence(self):
        receipt = self.create(initial_polyp_count=0)
        self.assertEqual(receipt.intent, {
            "strain": self.strain.pk, "thermal_zone": self.zone.pk,
            "entered_on": "2026-10-01", "volume_liters": None, "notes": "",
            "initial_polyp_count": 0,
        })
        box = Box.objects.get()
        self.assertEqual(receipt.box_id, box.pk)
        self.assertFalse(BoxDnaValidation.objects.exists())
        self.assertFalse(AuditLog.objects.filter(metadata__event__startswith="box_dna_validation").exists())
        # Zero stays a real initial count, never an absent one.
        self.assertEqual(BiologicalMeasurement.objects.get(box=box).polyp_count, 0)

    def test_explicit_null_evidence_is_the_same_intent_as_absent_evidence(self):
        self.register()
        receipt = self.register({**self.payload, "dna_evidence_url": None})
        self.assertNotIn("dna_evidence_url", receipt.intent)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_valid_evidence_is_recorded_on_the_new_box_with_its_own_audit(self):
        receipt = self.create(dna_evidence_url=f"  {EVIDENCE_URL}  ", initial_polyp_count=0)
        self.assertEqual(receipt.intent["dna_evidence_url"], EVIDENCE_URL)
        box = Box.objects.get()
        validation = BoxDnaValidation.objects.get()
        self.assertEqual((validation.box_id, validation.evidence_url, validation.revision), (box.pk, EVIDENCE_URL, 1))
        self.assertEqual((validation.created_by_id, validation.updated_by_id), (self.user.pk, self.user.pk))
        self.assertIsNone(validation.revoked_at)
        audit = AuditLog.objects.get(metadata__event="box_dna_validation_added")
        self.assertEqual((audit.organization_id, audit.user_id, audit.object_type, audit.object_id),
                         (self.org.pk, self.user.pk, "box", box.global_code))
        self.assertEqual(audit.action, AuditLog.Action.CREATION)
        self.assertEqual(audit.metadata["box_id"], box.pk)
        self.assertIsNone(audit.metadata["before"])
        self.assertEqual(audit.metadata["after"]["evidence_url"], EVIDENCE_URL)
        # The Box creation audit itself is unchanged.
        creation = AuditLog.objects.get(description__startswith="Box created automatically")
        self.assertNotIn("dna_evidence_url", creation.metadata["valeurs"])
        self.assertEqual(BiologicalMeasurement.objects.get(box=box).polyp_count, 0)

    def test_replays_never_add_a_second_evidence(self):
        self.create(dna_evidence_url=EVIDENCE_URL)
        before = list(AuditLog.objects.order_by("pk").values_list("pk", flat=True))
        self.register()
        self.execute()
        self.assertEqual(BoxDnaValidation.objects.count(), 1)
        self.assertEqual(list(AuditLog.objects.order_by("pk").values_list("pk", flat=True)), before)

    def test_changed_evidence_with_the_same_key_conflicts(self):
        self.register({**self.payload, "dna_evidence_url": EVIDENCE_URL})
        for changed in ({**self.payload, "dna_evidence_url": "https://lab.example.org/other"},
                        {**self.payload}):
            with self.subTest(changed=changed.get("dna_evidence_url")):
                with self.assertRaises(AutomaticBoxCreationConflict):
                    self.register(changed)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_invalid_evidence_is_durably_rejected_without_any_creation(self):
        for value in ("", "   ", "not a url", "ftp://lab.example.org/report", "javascript:alert(1)",
                      "https://", f"https://lab.example.org/{'a' * 2000}"):
            with self.subTest(value=value[:40]):
                self.payload.update(request_id=str(uuid.uuid4()), resume_token=str(uuid.uuid4()))
                receipt = self.register({**self.payload, "dna_evidence_url": value})
                self.assert_rejected(receipt, "dna_evidence_url")
                self.assertEqual(self.execute().box_id, None)
        self.assert_no_creation()
        self.assertFalse(BoxDnaValidation.objects.exists())

    def test_superuser_without_lab_membership_cannot_register_evidence(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=True)
        self.membership.delete()
        self.user.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            self.register({**self.payload, "dna_evidence_url": EVIDENCE_URL})
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())
        self.assert_no_creation()

    def test_reader_membership_cannot_register_evidence(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=True)
        self.membership.role = OrganizationMembership.Role.VIEWER
        self.membership.save()
        self.user.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            self.register({**self.payload, "dna_evidence_url": EVIDENCE_URL})
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())

    def test_evidence_authority_is_revalidated_at_execution_without_terminal_transition(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=True)
        self.user.refresh_from_db()
        receipt = self.register({**self.payload, "dna_evidence_url": EVIDENCE_URL})
        self.membership.is_active = False
        self.membership.save()
        with self.assertRaises(PermissionDenied):
            self.execute()
        self.assert_pending(receipt)
        self.assert_no_creation()
        self.assertFalse(BoxDnaValidation.objects.exists())

    def test_evidence_failure_rolls_back_the_whole_creation(self):
        receipt = self.register({**self.payload, "dna_evidence_url": EVIDENCE_URL})
        with patch.object(dna_validation, "_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.assert_pending(receipt)
        self.assertFalse(Box.objects.exists())
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BoxDnaValidation.objects.exists())
        self.assertFalse(BiologicalMeasurement.objects.exists())
        # The same registered intention then completes with its evidence.
        self.assertIsNotNone(self.execute().box_id)
        self.assertEqual(BoxDnaValidation.objects.get().evidence_url, EVIDENCE_URL)


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxCreationDnaEvidenceAPITests(AutomaticBoxIntentFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post(self, route, data, organization=None):
        return self.client.post(
            reverse(route), data, content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str((organization or self.org).pk),
        )

    def create(self, data):
        registered = self.post("api_box_automatic_intent", data)
        self.assertEqual(registered.status_code, 200, registered.content)
        self.assertEqual(registered.json()["status"], "pending")
        return self.post("api_box_automatic_intent_execute", self.key(data))

    def test_created_box_detail_exposes_the_evidence_to_the_box_page(self):
        response = self.create({**self.payload, "dna_evidence_url": EVIDENCE_URL})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "created")
        self.assertEqual(body["box"]["dna_validation"]["evidence_url"], EVIDENCE_URL)
        self.assertEqual(body["box"]["dna_validation"]["revision"], 1)
        self.assertTrue(body["box"]["can_edit_dna_validation"])
        detail = self.client.get(
            reverse("api_box_detail", args=[body["box"]["id"]]), HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["dna_validation"], body["box"]["dna_validation"])
        # The Box page keeps its own contract: a second addition is a conflict.
        duplicate = self.client.post(
            reverse("api_box_dna_validation", args=[body["box"]["id"]]), {"evidence_url": "https://lab.example.org/x"},
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.content)
        self.assertEqual(duplicate.json()["code"], "dna_validation_exists")

    def test_created_box_without_evidence_has_no_dna_validation(self):
        response = self.create({**self.payload, "initial_polyp_count": 0})
        body = response.json()
        self.assertEqual(body["status"], "created")
        self.assertIsNone(body["box"]["dna_validation"])
        self.assertFalse(BoxDnaValidation.objects.exists())

    def test_invalid_evidence_returns_a_rejected_envelope_with_its_field_error(self):
        response = self.post("api_box_automatic_intent", {**self.payload, "dna_evidence_url": "ftp://x.example.org"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "rejected")
        self.assertIn("dna_evidence_url", body["errors"])
        self.assertFalse(Box.objects.exists())

    def test_evidence_of_another_institution_box_stays_invisible(self):
        response = self.create({**self.payload, "dna_evidence_url": EVIDENCE_URL})
        box_id = response.json()["box"]["id"]
        self.assertEqual(Box.objects.get(pk=box_id).organization_id, self.org.pk)
        outsider = get_user_model().objects.create_user(username="dna-outsider", email="dna-outsider@example.org")
        OrganizationMembership.objects.create(organization=self.other_org, user=outsider, role="admin")
        self.client.force_login(outsider)
        detail = self.client.get(reverse("api_box_detail", args=[box_id]), HTTP_X_ORGANIZATION_ID=str(self.other_org.pk))
        self.assertEqual(detail.status_code, 404)
        self.assertNotIn(EVIDENCE_URL, detail.content.decode())


@override_settings(SECURE_SSL_REDIRECT=False)
class DirectBoxCreationDnaEvidenceTests(AutomaticBoxFixture, DisposablePostgresFlushMixin, TransactionTestCase):
    # Unlike TestCase, the request runs without a test-owned transaction.
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.payload["initial_polyp_count"] = 0

    def post(self, data, organization=None):
        return self.client.post(
            reverse("api_box_automatic"), data, content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str((organization or self.org).pk),
        )

    def snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (Box, BoxLocation, BiologicalMeasurement, AuditLog, AutomaticBoxCreationRequest,
                          BoxDnaValidation, BoxGlobalIdentity, BoxCodeNamespace)
        }

    def test_without_evidence_and_zero_count(self):
        response = self.post(self.payload)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["dna_validation"])
        self.assertFalse(BoxDnaValidation.objects.exists())
        self.assertNotIn("dna_evidence_url", AutomaticBoxCreationRequest.objects.get().intent)
        self.assertEqual(BiologicalMeasurement.objects.get().polyp_count, 0)

    def test_evidence_is_normalized_and_replayed_without_new_writes(self):
        payload = {**self.payload, "dna_evidence_url": EVIDENCE_URL}
        created = self.post({**payload, "dna_evidence_url": f"  {EVIDENCE_URL}  "})
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["dna_validation"]["evidence_url"], EVIDENCE_URL)
        receipt = AutomaticBoxCreationRequest.objects.get()
        self.assertEqual((receipt.box_id, receipt.intent["dna_evidence_url"]), (created.json()["id"], EVIDENCE_URL))
        before = self.snapshot()
        replay = self.post(payload)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), created.json())
        conflict = self.post({**payload, "dna_evidence_url": "https://lab.example.org/other"})
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_evidence_does_not_write_anything(self):
        before = self.snapshot()
        response = self.post({**self.payload, "dna_evidence_url": "ftp://lab.example.org/report"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("dna_evidence_url", response.json())
        self.assertEqual(self.snapshot(), before)

    def test_evidence_write_authority_and_institution_scope(self):
        payload = {**self.payload, "dna_evidence_url": EVIDENCE_URL}
        foreign_zone = ThermalZone.objects.create(organization=self.other_org, name="Foreign zone")
        before = self.snapshot()
        self.assertEqual(self.post(payload, organization=self.other_org).status_code, 403)
        response = self.post({**payload, "thermal_zone": foreign_zone.pk})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("thermal_zone", response.json())
        self.membership.role = OrganizationMembership.Role.VIEWER
        self.membership.save(update_fields=["role"])
        self.assertEqual(self.post(payload).status_code, 403)
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=True)
        self.membership.delete()
        self.assertEqual(self.post(payload).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.ADMIN,
        )
        created = self.post(payload)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(BoxDnaValidation.objects.get().box.organization_id, self.org.pk)

    def assert_rollback_and_retry(self, target, method):
        BoxCodeNamespace.objects.update_or_create(namespace=self.strain.code, defaults={"high_water": 7})
        before = self.snapshot()
        payload = {**self.payload, "dna_evidence_url": EVIDENCE_URL}
        original = getattr(target, method)

        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError(f"Injected failure after {method}")

        with patch.object(target, method, side_effect=fail_after_write) as failure:
            with self.assertRaisesMessage(RuntimeError, f"Injected failure after {method}"):
                self.post(payload)
            failure.assert_called_once()
        self.assertEqual(self.snapshot(), before)

        created = self.post(payload)
        self.assertEqual(created.status_code, 201, created.content)
        box = Box.objects.get()
        self.assertEqual((created.json()["id"], box.global_code), (box.pk, "AUT-LAB-1.008"))
        self.assertEqual(BoxLocation.objects.get().box_id, box.pk)
        self.assertEqual(BiologicalMeasurement.objects.get(box=box).polyp_count, 0)
        self.assertEqual(BoxDnaValidation.objects.get(box=box).evidence_url, EVIDENCE_URL)
        self.assertEqual(AutomaticBoxCreationRequest.objects.get().box_id, box.pk)
        self.assertEqual(AuditLog.objects.count(), 3)  # Box, measurement, DNA
        self.assertEqual(AuditLog.objects.get(metadata__event="box_dna_validation_added").object_id, box.global_code)

    def test_rolls_back_when_evidence_persistence_fails(self):
        self.assert_rollback_and_retry(BoxDnaValidation.objects, "create")

    def test_rolls_back_when_mandatory_dna_audit_fails(self):
        self.assert_rollback_and_retry(dna_validation, "_audit")
