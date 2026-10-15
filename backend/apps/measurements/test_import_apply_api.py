"""Transport acceptance and private evidence access on isolated SQLite."""

from datetime import date
import hashlib
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase
from rest_framework.test import APIClient

from . import test_import_apply as fixtures
from .models import BiologicalMeasurement, MeasurementImportReceipt
from .serializers import BiologicalMeasurementCreateSerializer
from apps.audit.models import AuditLog
from apps.accounts.models import OrganizationMembership


class MeasurementImportApplyAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.MeasurementImportApplyTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.source = ("box_reference,measured_on,polyp_count,ephyrae_count\r\n"
                       f"{self.box.global_code},2026-09-16,0,0\r\n").encode()
        self.mapping = {key: key for key in ("box_reference", "measured_on", "polyp_count", "ephyrae_count")}

    def post(self, action, *, source=None, extra=None):
        payload = {
            "file": SimpleUploadedFile("private.csv", self.source if source is None else source),
            "delimiter": ",", "mapping": json.dumps(self.mapping), "confirmed": "true",
        }
        payload.update(extra or {})
        return self.client.post(f"/api/measurement-import/{action}/", payload, format="multipart",
                                HTTP_X_ORGANIZATION_ID=str(self.org.pk))

    def identity(self):
        response = self.post("preview")
        self.assertEqual(response.status_code, 200, response.content)
        preview = response.json()
        self.assertTrue(preview["application_available"])
        self.assertFalse(MeasurementImportReceipt.objects.exists())
        return {f"expected_{key}": preview[key] for key in ("preparation_fingerprint", "plan_fingerprint")}

    def accepted(self):
        response = self.post("apply", extra=self.identity())
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_complete_workflow_private_source_receipt_and_replay(self):
        identity = self.identity()
        accepted = self.post("apply", extra=identity)
        self.assertEqual(accepted.status_code, 201, accepted.content)
        self.assertEqual(accepted["Cache-Control"], "no-store")
        result = accepted.json()
        self.assertFalse(result["replayed"])
        receipt = result["receipt"]
        self.assertEqual(receipt["created_count"], 1)
        self.assertEqual(receipt["source_sha256"], hashlib.sha256(self.source).hexdigest())
        self.assertEqual(receipt["actor_id"], self.actor.pk)
        self.assertEqual(receipt["organization_id"], self.org.pk)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}
        downloaded = self.client.get(receipt["source_url"], **headers)
        self.assertEqual(downloaded.content, self.source)
        self.assertEqual(downloaded["Cache-Control"], "no-store")
        self.assertEqual(downloaded["X-Content-Type-Options"], "nosniff")
        self.assertTrue(downloaded["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(self.client.get(receipt["receipt_url"], **headers).json(), receipt)
        self.assertNotIn("media", receipt["source_url"])
        replay = self.post("apply", extra=identity)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), {"receipt": receipt, "replayed": True})
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_matching_only_acceptance_is_durable_without_mutation(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 9, 16), polyp_count=0, ephyrae_count=0,
        )
        sequence = measurement.polyp_state_sequence
        receipt = self.accepted()["receipt"]
        self.assertEqual(receipt["created_count"], 0)
        self.assertEqual(receipt["already_matching_count"], 1)
        self.assertEqual(receipt["measurement_ids"], [measurement.pk])
        self.assertEqual(AuditLog.objects.count(), 1)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_state_sequence, sequence)
        self.assertEqual(bytes(MeasurementImportReceipt.objects.get().source_bytes), self.source)

    def test_private_resources_are_scoped_and_reauthorize(self):
        receipt = self.accepted()["receipt"]
        urls = (receipt["receipt_url"], receipt["source_url"])
        OrganizationMembership.objects.create(user=self.actor, organization=self.other_org, role="admin")
        for url in urls:
            self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other_org.pk)).status_code, 404)
            self.client.force_authenticate(self.viewer)
            self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.org.pk)).status_code, 403)
            self.client.force_authenticate(self.actor)
        OrganizationMembership.objects.filter(user=self.actor, organization=self.org).update(is_active=False)
        for url in urls:
            response = self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response["Cache-Control"], "no-store")

    def test_bad_identity_changed_source_and_mapping_are_rejected(self):
        identity = self.identity()
        cases = [({}, "invalid_preview_identity", 400),
                 (dict(identity, expected_plan_fingerprint="x" * 64), "invalid_preview_identity", 400),
                 (dict(identity, expected_preparation_fingerprint="0" * 64), "stale_preparation", 409),
                 (dict(identity, expected_plan_fingerprint="0" * 64), "stale_plan", 409)]
        for extra, code, status in cases:
            response = self.post("apply", extra=extra)
            self.assertEqual(response.status_code, status, response.content)
            self.assertEqual(response.json()["error_code"], code)
        self.assertEqual(self.post("apply", source=self.source + b"\n", extra=identity).status_code, 409)
        mapping = dict(self.mapping, polyp_count="ephyrae_count", ephyrae_count="polyp_count")
        self.assertEqual(self.post("apply", extra=dict(identity, mapping=json.dumps(mapping))).status_code, 409)
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(MeasurementImportReceipt.objects.exists())

    def test_stale_box_or_permission_cannot_write(self):
        identity = self.identity()
        self.box.global_code = "RENAMED-APPLY-BOX"
        self.box.save(update_fields=["global_code"])
        self.assertEqual(self.post("apply", extra=identity).status_code, 409)
        self.client.force_authenticate(self.viewer)
        with patch("rest_framework.parsers.MultiPartParser.parse", side_effect=AssertionError("Unauthorized parse")):
            self.assertEqual(self.post("apply", extra=identity).status_code, 403)
        self.assertFalse(MeasurementImportReceipt.objects.exists())

    def test_apply_requires_confirmation_and_enforces_upload_bounds(self):
        identity = self.identity()
        for extra, code in ((dict(identity, confirmed="false"), "mapping_not_confirmed"),
                            (dict(identity, untrusted_plan="{}"), "invalid_configuration")):
            response = self.post("apply", extra=extra)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error_code"], code)
        response = self.post("apply", source=b"x" * (5 * 1024 * 1024 + 1), extra=identity)
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/api/measurement-import/apply/", b"broken", content_type="application/json",
                                    HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MeasurementImportReceipt.objects.exists())

    def test_apply_session_csrf_is_required(self):
        identity = self.identity()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.actor)
        self.assertEqual(self.post("apply", extra=identity).status_code, 403)
        token = "a" * 32
        self.client.cookies["csrftoken"] = token
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        response = self.post("apply", extra=identity)
        self.assertEqual(response.status_code, 201, response.content)

    def test_source_database_failure_returns_retryable_error_without_writes(self):
        identity = self.identity()
        with patch.object(MeasurementImportReceipt, "save", side_effect=DatabaseError("synthetic storage failure")):
            response = self.post("apply", extra=identity)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error_code": "acceptance_failed"})
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
        self.assertFalse(MeasurementImportReceipt.objects.exists())

    def test_interactive_null_contract_is_unchanged(self):
        for field in ("strobila_count", "culture_status", "needs_attention", "notes"):
            serializer = BiologicalMeasurementCreateSerializer(data={"measured_on": "2026-09-16", field: None})
            self.assertFalse(serializer.is_valid(), field)
            self.assertIn(field, serializer.errors)
