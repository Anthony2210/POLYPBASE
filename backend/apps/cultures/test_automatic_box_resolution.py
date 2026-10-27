"""Resolution safely distinguishes committed creation from permanent cancellation."""

import uuid
from itertools import product
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Strain

from .automatic_box_creation import create_automatic_box, resolve_automatic_box_request
from .models import AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxLocation, ThermalZone
from .test_automatic_box_creation import AutomaticBoxFixture


@override_settings(SECURE_SSL_REDIRECT=False)
class AutomaticBoxResolutionTests(AutomaticBoxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def resolve(self, data=None, organization=None):
        return self.client.post(
            reverse("api_box_automatic_resolve"),
            {"request_id": self.payload["request_id"]} if data is None else data,
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str((organization or self.org).pk),
        )

    def create(self, payload=None):
        return self.client.post(
            reverse("api_box_automatic"), self.payload if payload is None else payload,
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )

    def resolve_service(self):
        return resolve_automatic_box_request(
            organization=self.org, actor=self.user, data={"request_id": self.payload["request_id"]},
        )

    def test_absent_key_is_durably_cancelled_and_resolution_is_idempotent(self):
        for _ in range(2):
            response = self.resolve()
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json(), {"status": "cancelled"})
        receipt = AutomaticBoxCreationRequest.objects.get()
        self.assertTrue(receipt.cancelled)
        self.assertIsNone(receipt.box_id)
        self.assertIsNone(receipt.intent)
        self.assertEqual((receipt.organization_id, receipt.actor_id), (self.org.pk, self.user.pk))
        for model in (Box, BoxLocation, BoxCodeNamespace, AuditLog):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_cancelled_key_never_creates_regardless_of_intent(self):
        self.resolve()
        for data in (self.payload, {**self.payload, "notes": "edited"},
                     {**self.payload, "thermal_zone": 999999, "strain": 999999},
                     {"request_id": self.payload["request_id"]},
                     {**self.payload, "local_code": "forbidden", "entered_on": "invalid"}):
            with self.subTest(data=data):
                response = self.create(data)
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()["code"], "box_creation_request_cancelled")
        self.assertFalse(Box.objects.exists())
        self.assertFalse(BoxCodeNamespace.objects.exists())
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)
        self.assertEqual(self.create({**self.payload, "request_id": str(uuid.uuid4())}).status_code, 201)

    def test_created_key_returns_existing_detail_without_additional_writes(self):
        created = self.create()
        for _ in range(2):
            response = self.resolve()
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json(), {"status": "created", "box": created.json()})
        receipt = AutomaticBoxCreationRequest.objects.get()
        self.assertFalse(receipt.cancelled)
        self.assertEqual(receipt.box_id, created.json()["id"])
        self.assertIsNotNone(receipt.intent)
        self.assertEqual((Box.objects.count(), BoxLocation.objects.count(), AuditLog.objects.count()), (1, 1, 1))
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 1)

    def test_resolution_of_committed_box_ignores_now_invalid_strain_and_zone(self):
        created = self.create()
        Strain.objects.filter(pk=self.strain.pk).update(organization=self.other_org)
        ThermalZone.objects.filter(pk=self.zone.pk).update(is_active=False)
        self.assertEqual(self.create().status_code, 400)
        resolved = self.resolve()
        self.assertEqual(resolved.status_code, 200, resolved.content)
        self.assertEqual(resolved.json()["status"], "created")
        self.assertEqual(resolved.json()["box"]["id"], created.json()["id"])
        self.assertFalse(AutomaticBoxCreationRequest.objects.get().cancelled)

    def test_failed_initial_validation_can_be_resolved_then_replaced_safely(self):
        ThermalZone.objects.filter(pk=self.zone.pk).update(is_active=False)
        self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.resolve().json(), {"status": "cancelled"})
        ThermalZone.objects.filter(pk=self.zone.pk).update(is_active=True)
        self.assertEqual(self.create().status_code, 409)
        self.assertEqual(self.create({**self.payload, "request_id": str(uuid.uuid4())}).status_code, 201)
        self.assertEqual(Box.objects.count(), 1)

    def test_resolution_requires_request_id_only(self):
        for data in ({}, {"request_id": None}, {"request_id": "invalid"}, [],
                     {**self.payload}, {"request_id": self.payload["request_id"], "box": 1}):
            with self.subTest(data=data):
                self.assertEqual(self.resolve(data).status_code, 400)
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())
        response = self.resolve({"request_id": uuid.UUID(self.payload["request_id"]).hex})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(AutomaticBoxCreationRequest.objects.get().request_id), self.payload["request_id"])

    def test_resolution_requires_explicit_institution_and_authentication(self):
        response = self.client.post(
            reverse("api_box_automatic_resolve"), {"request_id": self.payload["request_id"]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.resolve(organization=self.other_org).status_code, 403)
        self.client.logout()
        self.assertIn(self.resolve().status_code, (401, 403))
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())

    def test_viewer_inactive_and_missing_membership_cannot_resolve_or_cancel(self):
        for committed in (False, True):
            if committed:
                self.service()
            for changes in ({"role": "viewer"}, {"is_active": False}):
                with self.subTest(committed=committed, changes=changes):
                    OrganizationMembership.objects.filter(pk=self.membership.pk).update(**changes)
                    self.assertEqual(self.resolve().status_code, 403)
                    OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="lab_technician", is_active=True)
        self.membership.delete()
        self.assertEqual(self.resolve().status_code, 403)
        self.assertFalse(AutomaticBoxCreationRequest.objects.get().cancelled)

    def test_resolution_reloads_actor_and_institution_even_for_existing_tombstone(self):
        self.resolve_service()
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.resolve_service()
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.resolve_service()

    def test_resolution_reloads_superuser_demotion(self):
        self.user.is_superuser = True
        self.user.save()
        self.membership.role = "viewer"
        self.membership.save()
        self.service()
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=False)
        with self.assertRaises(PermissionDenied):
            self.resolve_service()

    def test_authorized_admin_and_superuser_can_resolve(self):
        for superuser in (False, True):
            self.user.is_superuser = superuser
            self.user.save()
            self.membership.role = "viewer" if superuser else "admin"
            self.membership.save()
            self.assertEqual(self.resolve().status_code, 200)

    def test_resolution_does_not_reveal_or_cancel_a_reassigned_box(self):
        box, _ = self.service()
        Box.objects.filter(pk=box.pk).update(organization=self.other_org)
        self.assertEqual(self.resolve().status_code, 403)
        self.assertFalse(AutomaticBoxCreationRequest.objects.get().cancelled)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_scope_does_not_resolve_or_cancel_another_actors_creation(self):
        box, _ = self.service()
        other_user = get_user_model().objects.create_user(username="resolution-other", email="other@example.org")
        OrganizationMembership.objects.create(organization=self.org, user=other_user, role="admin")
        self.client.force_login(other_user)
        self.assertEqual(self.resolve().json(), {"status": "cancelled"})
        self.assertEqual(AutomaticBoxCreationRequest.objects.get(actor=self.user).box_id, box.pk)
        self.client.force_login(self.user)
        self.assertEqual(self.resolve().json()["status"], "created")

    def test_tombstone_scope_does_not_cancel_creation_in_another_institution(self):
        self.resolve()
        OrganizationMembership.objects.create(organization=self.other_org, user=self.user, role="admin")
        strain = Strain.objects.create(organization=self.other_org, species=self.species, code="AUT-OTHER-1")
        zone = ThermalZone.objects.create(organization=self.other_org, name="Other zone")
        box, created = create_automatic_box(
            organization=self.other_org, actor=self.user,
            data={**self.payload, "strain": strain.pk, "thermal_zone": zone.pk},
        )
        self.assertTrue(created)
        response = self.resolve(organization=self.other_org)
        self.assertEqual(response.json()["status"], "created")
        self.assertEqual(response.json()["box"]["id"], box.pk)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 2)

    def test_receipt_insert_failure_rolls_back_cancellation(self):
        original = AutomaticBoxCreationRequest.save

        def fail_after_insert(receipt, *args, **kwargs):
            original(receipt, *args, **kwargs)
            raise RuntimeError("Injected failure after tombstone insert")

        with patch.object(AutomaticBoxCreationRequest, "save", fail_after_insert):
            with self.assertRaises(RuntimeError):
                self.resolve_service()
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())
        box, created = self.service()
        self.assertTrue(created)
        self.assertEqual(box.global_code, "AUT-LAB-1.001")

    def test_database_allows_exactly_coherent_created_and_cancelled_states(self):
        box, _ = self.service()
        receipt = AutomaticBoxCreationRequest.objects.get()
        for cancelled, has_box, has_intent in product((False, True), repeat=3):
            coherent = (cancelled and not has_box and not has_intent) or (not cancelled and has_box and has_intent)
            values = {"cancelled": cancelled, "box_id": box.pk if has_box else None,
                      "intent": receipt.intent if has_intent else None}
            with self.subTest(values=values):
                if coherent:
                    with transaction.atomic():
                        AutomaticBoxCreationRequest.objects.filter(pk=receipt.pk).update(**values)
                        transaction.set_rollback(True)
                else:
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        AutomaticBoxCreationRequest.objects.filter(pk=receipt.pk).update(**values)

    def test_protected_references_remain_protected(self):
        box, _ = self.service()
        for obj in (box, self.user, self.org):
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ProtectedError):
                    obj.delete()

    def test_cancelled_error_has_french_and_english_translations(self):
        self.resolve()
        for language, expected in (
            ("en", "This box creation request was cancelled. Use a new request ID to create a box."),
            ("fr", "Cette demande de création de boîte a été annulée. Utilisez un nouvel identifiant de requête pour créer une boîte."),
        ):
            with self.subTest(language=language):
                UserPreference.objects.update_or_create(user=self.user, defaults={"interface_language": language})
                response = self.create()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["code"], "box_creation_request_cancelled")
                self.assertEqual(response.json()["detail"], expected)
