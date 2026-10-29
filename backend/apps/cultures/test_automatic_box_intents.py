"""Contract tests for token-bound, durable ordinary box creation intents.

TransactionTestCase deliberately leaves registration outside execution's transaction.
SQLite verifies rollback and persistence, not PostgreSQL lock serialization.
"""

import hashlib
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain

from . import automatic_box_creation as service
from .models import (
    AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxLocation, ThermalZone,
)
from .test_automatic_box_creation import AutomaticBoxFixture


class AutomaticBoxIntentFixture(AutomaticBoxFixture):
    def setUp(self):
        super().setUp()
        self.payload = {**self.payload, "resume_token": str(uuid.uuid4())}

    def key(self, payload=None):
        payload = self.payload if payload is None else payload
        return {field: payload[field] for field in ("request_id", "resume_token")}

    def register(self, data=None, *, organization=None, actor=None):
        return service.register_automatic_box_intent(
            organization=self.org if organization is None else organization,
            actor=self.user if actor is None else actor,
            data=self.payload if data is None else data,
        )

    def execute(self, data=None, *, organization=None, actor=None):
        return service.execute_automatic_box_intent(
            organization=self.org if organization is None else organization,
            actor=self.user if actor is None else actor,
            data=self.key() if data is None else data,
        )

    def cancel(self, data=None, *, organization=None, actor=None):
        return service.cancel_automatic_box_intent(
            organization=self.org if organization is None else organization,
            actor=self.user if actor is None else actor,
            data=self.key() if data is None else data,
        )

    def snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (AutomaticBoxCreationRequest, Box, BoxLocation, AuditLog, BoxCodeNamespace)
        }

    def assert_no_creation(self):
        for model in (Box, BoxLocation, AuditLog, BoxCodeNamespace):
            self.assertFalse(model.objects.exists(), model.__name__)

    def assert_pending(self, receipt):
        self.assertIsInstance(receipt, AutomaticBoxCreationRequest)
        receipt.refresh_from_db()
        self.assertIsNone(receipt.box_id)
        self.assertFalse(receipt.cancelled)
        self.assertIsNone(receipt.failure)
        self.assertIsNotNone(receipt.intent)
        self.assertEqual(receipt.resume_token_hash, hashlib.sha256(
            self.payload["resume_token"].encode("utf-8"),
        ).hexdigest())

    def assert_rejected(self, receipt, field):
        self.assertIsInstance(receipt, AutomaticBoxCreationRequest)
        receipt.refresh_from_db()
        self.assertTrue(receipt.cancelled)
        self.assertIsNone(receipt.box_id)
        self.assertIsInstance(receipt.failure, dict)
        self.assertIn(field, receipt.failure)
        self.assertIsNotNone(receipt.intent)

    def other_actor(self):
        actor = get_user_model().objects.create_user(username="intent-other", email="intent-other@example.org")
        OrganizationMembership.objects.create(organization=self.org, user=actor, role="lab_technician")
        return actor

    @staticmethod
    def fail_after_save(model, error):
        save = model.save

        def failing_save(instance, *args, **kwargs):
            save(instance, *args, **kwargs)
            raise error

        return patch.object(model, "save", failing_save)


class AutomaticBoxIntentServiceTests(AutomaticBoxIntentFixture, TransactionTestCase):
    def test_registration_binds_pending_receipt_without_allocating(self):
        receipt = self.register()
        self.assert_pending(receipt)
        self.assertEqual((receipt.organization_id, receipt.actor_id, receipt.request_id),
                         (self.org.pk, self.user.pk, uuid.UUID(self.payload["request_id"])))
        self.assertEqual(receipt.intent, {
            "strain": self.strain.pk, "thermal_zone": self.zone.pk,
            "entered_on": "2026-10-01", "volume_liters": None, "notes": "",
        })
        self.assert_no_creation()
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_missing_initial_registration_is_safe_to_retry_with_same_id(self):
        before = self.snapshot()
        for operation in (self.execute, self.cancel):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(service.AutomaticBoxCreationConflict):
                    operation()
                self.assertEqual(self.snapshot(), before)
        receipt = self.register()
        self.assert_pending(receipt)
        result = self.execute()
        self.assertEqual(result.pk, receipt.pk)
        self.assertEqual(result.box.global_code, "AUT-LAB-1.001")

    def test_lost_registration_response_replays_unchanged_receipt(self):
        receipt = self.register()
        before = self.snapshot()
        replay = self.register({**self.payload, "strain": str(self.strain.pk),
                                "thermal_zone": str(self.zone.pk), "volume_liters": None, "notes": "  ",
                                "request_id": uuid.UUID(self.payload["request_id"]).hex})
        self.assertEqual(replay.pk, receipt.pk)
        self.assertEqual(self.snapshot(), before)

    def test_decimal_notes_and_signed_zero_normalize_but_zero_is_not_absent(self):
        self.payload.update(volume_liters="-0.00", notes=" note ")
        receipt = self.register()
        self.assertEqual(receipt.intent["volume_liters"], "0.00")
        self.assertEqual(receipt.intent["notes"], "note")
        before = self.snapshot()
        self.assertEqual(self.register({**self.payload, "volume_liters": "0", "notes": "note"}).pk, receipt.pk)
        self.assertEqual(self.snapshot(), before)
        for changes in ({"volume_liters": None}, {"volume_liters": "1.00"}, {"notes": "different"}):
            with self.subTest(changes=changes):
                with self.assertRaises(service.AutomaticBoxCreationConflict):
                    self.register({**self.payload, **changes})
                self.assertEqual(self.snapshot(), before)
        result = self.execute()
        self.assertEqual(result.box.volume_liters, Decimal("0.00"))

    def test_nonzero_decimal_representation_normalizes(self):
        self.payload["volume_liters"] = "1"
        receipt = self.register()
        before = self.snapshot()
        self.assertEqual(self.register({**self.payload, "volume_liters": "1.00"}).pk, receipt.pk)
        self.assertEqual(receipt.intent["volume_liters"], "1.00")
        self.assertEqual(self.snapshot(), before)

    def test_each_changed_intent_or_token_conflicts_without_mutation(self):
        self.register()
        strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-2")
        zone = ThermalZone.objects.create(organization=self.org, name="Intent second zone")
        before = self.snapshot()
        for changes in ({"strain": strain.pk}, {"thermal_zone": zone.pk}, {"entered_on": "2026-10-02"},
                        {"notes": "changed"}, {"volume_liters": "0"}, {"resume_token": str(uuid.uuid4())}):
            with self.subTest(changes=changes):
                with self.assertRaises(service.AutomaticBoxCreationConflict):
                    self.register({**self.payload, **changes})
                self.assertEqual(self.snapshot(), before)

    def test_token_is_bound_globally_to_key_actor_and_organization(self):
        self.register()
        other_actor = self.other_actor()
        OrganizationMembership.objects.create(organization=self.other_org, user=self.user, role="admin")
        strain = Strain.objects.create(organization=self.other_org, species=self.species, code="AUT-OTH-1")
        zone = ThermalZone.objects.create(organization=self.other_org, name="Other intent zone")
        foreign_payload = {**self.payload, "strain": strain.pk, "thermal_zone": zone.pk}
        before = self.snapshot()
        attempts = (
            ({**self.payload, "request_id": str(uuid.uuid4())}, self.org, self.user),
            (self.payload, self.org, other_actor),
            (foreign_payload, self.other_org, self.user),
        )
        for payload, organization, actor in attempts:
            with self.subTest(organization=organization.pk, actor=actor.pk, request_id=payload["request_id"]):
                with self.assertRaises(service.AutomaticBoxCreationConflict):
                    self.register(payload, organization=organization, actor=actor)
                self.assertEqual(self.snapshot(), before)
        for operation in (self.execute, self.cancel):
            for organization, actor in ((self.org, other_actor), (self.other_org, self.user)):
                with self.subTest(operation=operation.__name__, organization=organization.pk, actor=actor.pk):
                    with self.assertRaises(service.AutomaticBoxCreationConflict):
                        operation(organization=organization, actor=actor)
                    self.assertEqual(self.snapshot(), before)

    def test_resume_token_hash_has_database_global_uniqueness(self):
        receipt = self.register()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AutomaticBoxCreationRequest.objects.create(
                organization=self.other_org, actor=self.user, request_id=uuid.uuid4(),
                intent=receipt.intent, resume_token_hash=receipt.resume_token_hash,
                cancelled=False, failure=None,
            )
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)
        self.assert_no_creation()

    def test_wrong_token_cannot_execute_or_cancel_existing_receipt(self):
        self.register()
        before = self.snapshot()
        for operation in (self.execute, self.cancel):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(service.AutomaticBoxCreationConflict):
                    operation({**self.key(), "resume_token": str(uuid.uuid4())})
                self.assertEqual(self.snapshot(), before)

    def test_independent_actors_can_use_same_id_for_different_strains_with_distinct_tokens(self):
        other_actor = self.other_actor()
        strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-2")
        other = {**self.payload, "strain": strain.pk, "resume_token": str(uuid.uuid4())}
        first = self.register()
        second = self.register(other, actor=other_actor)
        self.assertNotEqual(first.pk, second.pk)
        second = self.execute(self.key(other), actor=other_actor)
        first = self.execute()
        self.assertEqual(first.box.strain_id, self.strain.pk)
        self.assertEqual(second.box.strain_id, strain.pk)
        self.assertNotEqual(first.box_id, second.box_id)
        self.assertEqual((Box.objects.count(), BoxLocation.objects.count(), AuditLog.objects.count()), (2, 2, 2))

    def test_recovering_a_cannot_cancel_or_mutate_independent_b_in_same_scope(self):
        a = self.register()
        strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-2")
        other = {**self.payload, "request_id": str(uuid.uuid4()), "resume_token": str(uuid.uuid4()), "strain": strain.pk}
        b = self.register(other)
        before_b = AutomaticBoxCreationRequest.objects.filter(pk=b.pk).values().get()
        # Mixing A's proof with B's UUID never grants authority over B.
        with self.assertRaises(service.AutomaticBoxCreationConflict):
            self.cancel({"request_id": other["request_id"], "resume_token": self.payload["resume_token"]})
        with self.assertRaises(service.AutomaticBoxCreationConflict):
            service.resolve_automatic_box_request(organization=self.org, actor=self.user,
                                                 data={"request_id": other["request_id"]})
        self.cancel()
        self.assertEqual(AutomaticBoxCreationRequest.objects.filter(pk=b.pk).values().get(), before_b)
        result = self.execute(self.key(other))
        self.assertEqual(result.box.strain_id, strain.pk)
        self.assertFalse(result.cancelled)
        self.assertEqual(AutomaticBoxCreationRequest.objects.get(pk=a.pk).box_id, None)
        self.assertEqual((Box.objects.count(), BoxLocation.objects.count(), AuditLog.objects.count()), (1, 1, 1))

    def test_execute_atomically_creates_box_location_audit_and_receipt_then_replays(self):
        pending = self.register()
        result = self.execute()
        self.assertIsInstance(result, AutomaticBoxCreationRequest)
        self.assertEqual(result.pk, pending.pk)
        result.refresh_from_db()
        self.assertFalse(result.cancelled)
        self.assertIsNone(result.failure)
        self.assertEqual(result.intent, pending.intent)
        self.assertEqual(result.resume_token_hash, pending.resume_token_hash)
        box = Box.objects.get(pk=result.box_id)
        self.assertEqual((box.organization_id, box.strain_id, box.thermal_zone_id),
                         (self.org.pk, self.strain.pk, self.zone.pk))
        self.assertEqual((box.global_code, box.box_number, box.local_code, box.status),
                         ("AUT-LAB-1.001", "001", "", "active"))
        self.assertEqual(box.entered_on, date(2026, 10, 1))
        location = BoxLocation.objects.get(box=box)
        self.assertEqual(location.thermal_zone_id, self.zone.pk)
        self.assertIsNone(location.ends_at)
        audit = AuditLog.objects.get()
        self.assertEqual((audit.organization_id, audit.user_id, audit.action),
                         (self.org.pk, self.user.pk, "creation"))
        self.assertEqual(audit.metadata["box_id"], box.pk)
        self.assertEqual(audit.metadata["request_id"], self.payload["request_id"])
        before = self.snapshot()
        self.assertEqual(self.execute().box_id, box.pk)
        self.assertEqual(self.register().box_id, box.pk)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 1)

    def test_distinct_intents_share_allocator_without_duplicate_numbers(self):
        self.register()
        first = self.execute()
        other = {**self.payload, "request_id": str(uuid.uuid4()), "resume_token": str(uuid.uuid4())}
        self.register(other)
        second = self.execute(self.key(other))
        for _ in range(2):
            self.execute()
            self.execute(self.key(other))
        self.assertEqual({first.box.global_code, second.box.global_code}, {"AUT-LAB-1.001", "AUT-LAB-1.002"})
        self.assertEqual(set(Box.objects.values_list("box_number", flat=True)), {"001", "002"})
        self.assertEqual((Box.objects.count(), BoxLocation.objects.count(), AuditLog.objects.count(),
                          AutomaticBoxCreationRequest.objects.count()), (2, 2, 2, 2))
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 2)

    def test_cancellation_is_terminal_retains_intent_and_replays_without_writes(self):
        pending = self.register()
        receipt = self.cancel()
        self.assertEqual(receipt.pk, pending.pk)
        receipt.refresh_from_db()
        self.assertTrue(receipt.cancelled)
        self.assertIsNone(receipt.box_id)
        self.assertIsNone(receipt.failure)
        self.assertEqual(receipt.intent, pending.intent)
        self.assertEqual(receipt.resume_token_hash, pending.resume_token_hash)
        before = self.snapshot()
        self.assertEqual(self.cancel().pk, receipt.pk)
        self.assertEqual(self.register().pk, receipt.pk)
        self.assertEqual(self.execute().pk, receipt.pk)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_creation()

    def test_cancelling_committed_receipt_is_read_only(self):
        self.register()
        committed = self.execute()
        before = self.snapshot()
        self.assertEqual(self.cancel().box_id, committed.box_id)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_create_and_id_only_resolution_cannot_mutate_protected_rows(self):
        self.register()
        legacy_payload = {key: value for key, value in self.payload.items() if key != "resume_token"}
        for cancelled in (False, True):
            if cancelled:
                self.cancel()
            before = self.snapshot()
            for operation, data in ((service.create_automatic_box, legacy_payload),
                                    (service.resolve_automatic_box_request, {"request_id": self.payload["request_id"]})):
                with self.subTest(cancelled=cancelled, operation=operation.__name__):
                    with self.assertRaises(service.AutomaticBoxCreationConflict):
                        operation(organization=self.org, actor=self.user, data=data)
                    self.assertEqual(self.snapshot(), before)

    def test_legacy_resolution_can_read_committed_protected_box(self):
        self.register()
        committed = self.execute()
        before = self.snapshot()
        box = service.resolve_automatic_box_request(
            organization=self.org, actor=self.user, data={"request_id": self.payload["request_id"]},
        )
        self.assertEqual(box.pk, committed.box_id)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_original_parameters_are_durably_rejected_and_cannot_be_corrected_in_place(self):
        self.payload["entered_on"] = "not-a-date"
        receipt = self.register()
        self.assert_rejected(receipt, "entered_on")
        self.assertEqual(receipt.intent["invalid_parameters"]["entered_on"], "not-a-date")
        before = self.snapshot()
        self.assertEqual(self.register().pk, receipt.pk)
        self.assertEqual(self.execute().pk, receipt.pk)
        self.assertEqual(self.cancel().pk, receipt.pk)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(service.AutomaticBoxCreationConflict):
            self.register({**self.payload, "entered_on": "2026-10-01"})
        self.assertEqual(self.snapshot(), before)
        self.assert_no_creation()

    def test_malformed_or_missing_key_and_token_cannot_register_receipts(self):
        for field in ("request_id", "resume_token"):
            for value in (None, "not-a-uuid", ""):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        self.register({**self.payload, field: value})
            with self.assertRaises(ValidationError):
                self.register({key: value for key, value in self.payload.items() if key != field})
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())
        self.assert_no_creation()

    def test_execution_revalidates_zone_strain_and_identity_and_persists_rejection(self):
        changes = (
            (ThermalZone, self.zone.pk, {"is_active": False}, {"is_active": True}, "thermal_zone"),
            (ThermalZone, self.zone.pk, {"organization_id": self.other_org.pk}, {"organization_id": self.org.pk}, "thermal_zone"),
            (Strain, self.strain.pk, {"organization_id": self.other_org.pk}, {"organization_id": self.org.pk}, "strain"),
            (Strain, self.strain.pk, {"code": "invalid"}, {"code": "AUT-LAB-1"}, "strain"),
        )
        for model, pk, invalid, restored, field in changes:
            with self.subTest(model=model.__name__, invalid=invalid):
                self.payload.update(request_id=str(uuid.uuid4()), resume_token=str(uuid.uuid4()))
                pending = self.register()
                model.objects.filter(pk=pk).update(**invalid)
                rejected = self.execute()
                self.assertEqual(rejected.pk, pending.pk)
                self.assert_rejected(rejected, field)
                model.objects.filter(pk=pk).update(**restored)
                before = self.snapshot()
                self.assertEqual(self.execute().pk, rejected.pk)
                self.assertEqual(self.snapshot(), before)
                self.assert_no_creation()

    def test_all_operations_reload_current_authority_without_mutating_pending(self):
        self.register()
        changes = (
            (OrganizationMembership, self.membership.pk, {"role": "viewer"}, {"role": "lab_technician"}),
            (OrganizationMembership, self.membership.pk, {"is_active": False}, {"is_active": True}),
            (get_user_model(), self.user.pk, {"is_active": False}, {"is_active": True}),
            (Organization, self.org.pk, {"is_active": False}, {"is_active": True}),
        )
        for model, pk, invalid, restored in changes:
            model.objects.filter(pk=pk).update(**invalid)
            before = self.snapshot()
            for operation in (self.register, self.execute, self.cancel):
                with self.subTest(model=model.__name__, invalid=invalid, operation=operation.__name__):
                    with self.assertRaises(PermissionDenied):
                        operation()
                    self.assertEqual(self.snapshot(), before)
            model.objects.filter(pk=pk).update(**restored)
        self.membership.delete()
        before = self.snapshot()
        for operation in (self.register, self.execute, self.cancel):
            with self.assertRaises(PermissionDenied):
                operation()
            self.assertEqual(self.snapshot(), before)

    def test_superuser_demotion_is_reloaded_before_execute_or_cancel(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=True)
        OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="viewer")
        self.user.refresh_from_db()
        self.register()
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=False)
        before = self.snapshot()
        for operation in (self.execute, self.cancel):
            with self.assertRaises(PermissionDenied):
                operation()
            self.assertEqual(self.snapshot(), before)

    def test_registration_failure_after_insert_rolls_back_and_same_id_can_retry(self):
        self.assertFalse(connection.in_atomic_block)
        with self.fail_after_save(AutomaticBoxCreationRequest, RuntimeError("Registration failed")):
            with self.assertRaisesRegex(RuntimeError, "Registration failed"):
                self.register()
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())
        self.assert_no_creation()
        self.assert_pending(self.register())

    def test_runtime_failure_after_each_execution_write_preserves_committed_registration(self):
        pending = self.register()
        self.assertFalse(connection.in_atomic_block)
        before = self.snapshot()
        for model in (Box, BoxLocation, AuditLog, AutomaticBoxCreationRequest):
            with self.subTest(model=model.__name__):
                with self.fail_after_save(model, RuntimeError("Execution failed")):
                    with self.assertRaisesRegex(RuntimeError, "Execution failed"):
                        self.execute()
                self.assertEqual(self.snapshot(), before)
                self.assert_pending(pending)
                self.assert_no_creation()
        self.assertEqual(self.execute().box.global_code, "AUT-LAB-1.001")

    def test_business_validation_after_partial_writes_uses_savepoint_for_durable_rejection(self):
        pending = self.register()
        with self.fail_after_save(AuditLog, ValidationError({"thermal_zone": ["Zone changed"]})):
            receipt = self.execute()
        self.assertEqual(receipt.pk, pending.pk)
        self.assert_rejected(receipt, "thermal_zone")
        self.assert_no_creation()
        before = self.snapshot()
        self.assertEqual(self.execute().pk, receipt.pk)
        self.assertEqual(self.snapshot(), before)

    def test_cancellation_failure_after_update_rolls_back_to_original_pending_registration(self):
        pending = self.register()
        before = self.snapshot()
        with self.fail_after_save(AutomaticBoxCreationRequest, RuntimeError("Cancellation failed")):
            with self.assertRaisesRegex(RuntimeError, "Cancellation failed"):
                self.cancel()
        self.assertEqual(self.snapshot(), before)
        self.assert_pending(pending)
        self.assertTrue(self.cancel().cancelled)
        self.assert_no_creation()


@override_settings(SECURE_SSL_REDIRECT=False)
class AutomaticBoxIntentAPITests(AutomaticBoxIntentFixture, TestCase):
    routes = (
        ("api_box_automatic_intent", "/api/boxes/automatic/intents/"),
        ("api_box_automatic_intent_execute", "/api/boxes/automatic/intents/execute/"),
        ("api_box_automatic_intent_cancel", "/api/boxes/automatic/intents/cancel/"),
    )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post(self, route, data=None, *, client=None, explicit_organization=True):
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)} if explicit_organization else {}
        return (self.client if client is None else client).post(
            reverse(route), self.payload if data is None else data,
            content_type="application/json", **headers,
        )

    def assert_envelope(self, response, status):
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], status)

        def check(value):
            if isinstance(value, dict):
                self.assertTrue({"resume_token", "resume_token_hash", "intent"}.isdisjoint(value), value.keys())
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(body)
        self.assertNotIn(self.payload["resume_token"], response.content.decode())
        self.assertNotIn(hashlib.sha256(self.payload["resume_token"].encode()).hexdigest(), response.content.decode())
        return body

    def inspect(self, request_id=None):
        return self.client.get(
            reverse("api_box_automatic_resolve"),
            {"request_id": self.payload["request_id"] if request_id is None else request_id},
            HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )

    def test_routes_match_contract(self):
        for name, path in self.routes:
            with self.subTest(name=name):
                self.assertEqual(reverse(name), path)

    def test_lost_register_and_execute_responses_replay_same_created_box(self):
        first = self.assert_envelope(self.post("api_box_automatic_intent"), "pending")
        before = self.snapshot()
        replay = self.assert_envelope(self.post("api_box_automatic_intent"), "pending")
        self.assertEqual(replay, first)
        self.assertEqual(self.snapshot(), before)
        created = self.assert_envelope(self.post("api_box_automatic_intent_execute", self.key()), "created")
        box = Box.objects.get()
        self.assertEqual(created["box"]["id"], box.pk)
        self.assertEqual(created["box"]["global_code"], "AUT-LAB-1.001")
        self.assertEqual(created["box"]["strain"]["id"], self.strain.pk)
        before = self.snapshot()
        for route, data in (("api_box_automatic_intent_execute", self.key()),
                            ("api_box_automatic_intent", self.payload),
                            ("api_box_automatic_intent_cancel", self.key())):
            self.assertEqual(self.assert_envelope(self.post(route, data), "created"), created)
            self.assertEqual(self.snapshot(), before)

    def test_inspection_is_read_only_for_unknown_pending_cancelled_and_created(self):
        self.assertEqual(self.inspect().json(), {"status": "unknown"})
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())
        self.register()
        before = self.snapshot()
        self.assert_envelope(self.inspect(), "pending")
        self.assertEqual(self.snapshot(), before)
        self.cancel()
        before = self.snapshot()
        self.assert_envelope(self.inspect(), "cancelled")
        self.assertEqual(self.snapshot(), before)
        self.payload.update(request_id=str(uuid.uuid4()), resume_token=str(uuid.uuid4()))
        self.register()
        committed = self.execute()
        before = self.snapshot()
        self.assertEqual(self.assert_envelope(self.inspect(), "created")["box"]["id"], committed.box_id)
        self.assertEqual(self.snapshot(), before)

    def test_explicit_cancellation_and_retry_have_cancelled_envelopes(self):
        self.register()
        self.assert_envelope(self.post("api_box_automatic_intent_cancel", self.key()), "cancelled")
        before = self.snapshot()
        for route, data in (("api_box_automatic_intent_cancel", self.key()),
                            ("api_box_automatic_intent_execute", self.key()),
                            ("api_box_automatic_intent", self.payload)):
            self.assert_envelope(self.post(route, data), "cancelled")
            self.assertEqual(self.snapshot(), before)
        self.assert_no_creation()

    def test_invalid_params_return_durable_rejected_envelope_with_field_errors(self):
        self.payload["volume_liters"] = "not-a-number"
        rejected = self.assert_envelope(self.post("api_box_automatic_intent"), "rejected")
        self.assertIn("volume_liters", rejected["errors"])
        receipt = AutomaticBoxCreationRequest.objects.get()
        self.assert_rejected(receipt, "volume_liters")
        self.assertEqual(receipt.intent["invalid_parameters"]["volume_liters"], "not-a-number")
        before = self.snapshot()
        self.assertEqual(self.assert_envelope(self.post("api_box_automatic_intent"), "rejected"), rejected)
        self.assertEqual(self.assert_envelope(self.post("api_box_automatic_intent_execute", self.key()), "rejected"), rejected)
        self.assert_envelope(self.inspect(), "cancelled")
        self.assertEqual(self.snapshot(), before)
        self.assert_no_creation()

    def test_unknown_key_and_wrong_token_are_conflicts_without_tombstones(self):
        before = self.snapshot()
        for route in ("api_box_automatic_intent_execute", "api_box_automatic_intent_cancel"):
            response = self.post(route, self.key())
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(self.snapshot(), before)
        self.register()
        before = self.snapshot()
        for route in ("api_box_automatic_intent", "api_box_automatic_intent_execute", "api_box_automatic_intent_cancel"):
            data = self.payload if route == "api_box_automatic_intent" else self.key()
            response = self.post(route, {**data, "resume_token": str(uuid.uuid4())})
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.json()["code"], "box_creation_request_conflict")
            self.assertEqual(self.snapshot(), before)

    def test_all_posts_require_authenticated_explicit_organization_and_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        anonymous = Client()
        before = self.snapshot()
        for name, _ in self.routes:
            data = self.payload if name == "api_box_automatic_intent" else self.key()
            with self.subTest(route=name):
                self.assertEqual(self.post(name, data, explicit_organization=False).status_code, 403)
                self.assertIn(self.post(name, data, client=anonymous).status_code, (401, 403))
                self.assertEqual(self.post(name, data, client=csrf_client).status_code, 403)
                self.assertEqual(self.snapshot(), before)

    def test_permission_revocation_blocks_execute_and_cancel_without_terminal_transition(self):
        self.register()
        OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="viewer")
        before = self.snapshot()
        for name, _ in self.routes:
            data = self.payload if name == "api_box_automatic_intent" else self.key()
            self.assertEqual(self.post(name, data).status_code, 403)
            self.assertEqual(self.snapshot(), before)

    def test_api_does_not_disclose_or_mutate_other_actors_pending_receipt(self):
        self.register()
        self.client.force_login(self.other_actor())
        before = self.snapshot()
        self.assertEqual(self.inspect().json(), {"status": "unknown"})
        for name, _ in self.routes:
            data = self.payload if name == "api_box_automatic_intent" else self.key()
            response = self.post(name, data)
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(self.snapshot(), before)

    def test_legacy_id_only_cancellation_cannot_cancel_registered_pending(self):
        self.register()
        before = self.snapshot()
        response = self.post("api_box_automatic_resolve", {"request_id": self.payload["request_id"]})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(self.snapshot(), before)
        self.assert_envelope(self.inspect(), "pending")
