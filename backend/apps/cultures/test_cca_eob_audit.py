"""Preserve CCA-EOB receipts under the existing unknown-event audit fallback."""

from django.test import TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.audit.services import classify_audit_log, impactful_audit_logs

from .historical_2026 import cca_eob_correction as correction
from .test_cca_eob_correction import CcaEobFixtureMixin


class CcaEobAuditCompatibilityTests(CcaEobFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.reviewed_plan = self.plan()
        self.apply(plan=self.reviewed_plan)
        self.receipt = AuditLog.objects.get(object_type="historical_correction")
        self.updates = list(AuditLog.objects.filter(action=AuditLog.Action.UPDATE))
        self.client.force_login(self.admin)

    def get_actions(self, route, query=None):
        response = self.client.get(
            reverse(route), query or {}, HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_batch_receipt_uses_visible_reference_fallback_not_transfer_family(self):
        self.assertEqual(self.receipt.action, AuditLog.Action.IMPORT)
        self.assertEqual(self.receipt.object_id, correction.OPERATION)
        self.assertEqual(self.receipt.organization_id, self.org.pk)
        self.assertEqual(self.receipt.user_id, self.admin.pk)
        self.assertEqual(self.receipt.metadata, {
            "operation": correction.OPERATION,
            "plan_hash": correction.fingerprint(self.reviewed_plan),
            "product_decision": correction.PRODUCT_DECISION,
            "reviewed_plan": self.reviewed_plan,
            "revisions": self.reviewed_plan["revision_changes"],
            "moved": 3,
        })
        self.assertIn("biological identities corrected", self.receipt.description)
        self.assertIn("Environmental context now resolves through the target box", self.receipt.description)
        self.assertIn("No location or movement history is modified", self.receipt.description)
        for log in self.updates:
            self.assertEqual(log.metadata["product_decision"], correction.PRODUCT_DECISION)
            self.assertEqual(log.metadata["location_context"]["resolution"], "target_box_existing_consumers")
            self.assertTrue(log.metadata["location_context"]["context_change_accepted"])
        expected = {self.receipt.pk: "references"}
        expected.update({log.pk: "measurements" for log in self.updates})
        self.assertEqual(len(self.updates), 3)
        self.assertEqual(
            {log.pk: classify_audit_log(log) for log in [self.receipt, *self.updates]},
            expected,
        )
        self.assertEqual(
            {log.pk: log.business_family for log in impactful_audit_logs(
                organization_id=self.org.pk,
            )},
            expected,
        )
        self.apply()
        self.assertEqual(AuditLog.objects.count(), 4)

    def test_admin_actions_keep_receipt_metadata_filters_and_counts_consistent(self):
        payload = self.get_actions("api_account_audit_log", {"include_options": "1"})
        entries = {entry["id"]: entry for entry in payload["results"]}
        self.assertEqual(set(entries), {self.receipt.pk, *(log.pk for log in self.updates)})
        receipt = entries[self.receipt.pk]
        self.assertEqual(receipt["family"], "references")
        self.assertEqual(receipt["action"], "import")
        self.assertEqual(receipt["object_type"], "historical_correction")
        self.assertEqual(receipt["metadata"], self.receipt.metadata)
        self.assertEqual(receipt["business_details"], {"type": "reference"})
        self.assertIsNone(receipt["box_reference"])
        counts = {option["key"]: option["count"] for option in payload["family_options"]}
        self.assertEqual(counts["references"], 1)
        self.assertEqual(counts["measurements"], 3)
        self.assertEqual(counts["transfers"], 0)
        for family, expected_ids in (
            ("references", {self.receipt.pk}),
            ("measurements", {log.pk for log in self.updates}),
            ("transfers", set()),
        ):
            with self.subTest(family=family):
                filtered = self.get_actions("api_account_audit_log", {"family": family})
                self.assertEqual({entry["id"] for entry in filtered["results"]}, expected_ids)
        for log in self.updates:
            entry = entries[log.pk]
            self.assertEqual(entry["family"], "measurements")
            self.assertEqual(entry["business_details"]["type"], "measurement")
            self.assertEqual(entry["metadata"]["measurement_id"], log.metadata["measurement_id"])
            self.assertEqual(entry["business_details"]["values"]["ephyrules"],
                             log.metadata["valeurs"]["ephyrules"])

    def test_personal_actions_show_receipt_without_raw_plan_or_foreign_events(self):
        for organization, user in ((self.other_org, self.admin), (self.org, self.writer)):
            AuditLog.objects.create(
                organization=organization, user=user, action=AuditLog.Action.IMPORT,
                object_type="historical_correction", object_id=correction.OPERATION,
                metadata=self.receipt.metadata,
            )
        payload = self.get_actions("api_profile_actions")
        entries = {entry["id"]: entry for entry in payload["results"]}
        self.assertEqual(set(entries), {self.receipt.pk, *(log.pk for log in self.updates)})
        receipt = entries[self.receipt.pk]
        self.assertEqual(receipt["family"], "references")
        self.assertEqual(receipt["action"], "import")
        self.assertEqual(receipt["resource"]["type"], "historical_correction")
        self.assertEqual(receipt["resource"]["identifier"], correction.OPERATION)
        self.assertEqual(receipt["business_details"], {"type": "reference"})
        self.assertEqual(receipt["details"], {})
        self.assertNotIn("metadata", receipt)
        self.assertNotIn("reviewed_plan", receipt)
        self.assertIsNone(receipt["box_reference"])
        self.assertTrue(all(entries[log.pk]["family"] == "measurements" for log in self.updates))
