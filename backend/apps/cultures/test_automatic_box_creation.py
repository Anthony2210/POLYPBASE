"""Isolated API and rollback coverage for ordinary automatic creation."""

import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, Species, Strain

from .automatic_box_creation import create_automatic_box
from .models import (
    AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxLineage, BoxLocation,
    BoxMovement, SubcultureEvent, ThermalZone,
)


class AutomaticBoxFixture:
    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(name="Automatic laboratory")
        self.other_org = Organization.objects.create(name="Other automatic laboratory")
        self.user = get_user_model().objects.create_user(username="automatic-tech", email="automatic@example.org")
        self.membership = OrganizationMembership.objects.create(
            organization=self.org, user=self.user, role="lab_technician",
        )
        self.species = Species.objects.create(scientific_name="Automatic species", genus_species_code="AUT")
        self.strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-1")
        self.zone = ThermalZone.objects.create(organization=self.org, name="Automatic zone")
        self.payload = {
            "strain": self.strain.pk, "thermal_zone": self.zone.pk,
            "entered_on": "2026-10-01", "request_id": str(uuid.uuid4()),
        }

    def service(self, payload=None):
        return create_automatic_box(organization=self.org, actor=self.user, data=payload or self.payload)


@override_settings(SECURE_SSL_REDIRECT=False)
class AutomaticBoxCreationTests(AutomaticBoxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post(self, payload=None, organization=None):
        return self.client.post(
            reverse("api_box_automatic"), payload if payload is not None else self.payload,
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str((organization or self.org).pk),
        )

    def assert_empty(self):
        for model in (Box, BoxLocation, AuditLog, AutomaticBoxCreationRequest, BoxCodeNamespace):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_creation_returns_detail_and_only_ordinary_records(self):
        response = self.post({**self.payload, "volume_liters": "0", "notes": " New box "})
        self.assertEqual(response.status_code, 201, response.content)
        box = Box.objects.get()
        self.assertEqual(response.json()["id"], box.pk)
        self.assertEqual(response.json()["global_code"], "AUT-LAB-1.001")
        self.assertEqual(response.json()["strain"]["id"], self.strain.pk)
        self.assertEqual((box.box_number, box.local_code, box.status), ("001", "", "active"))
        self.assertEqual(box.entered_on, date(2026, 10, 1))
        self.assertEqual(box.volume_liters, 0)
        self.assertEqual(box.notes, "New box")
        location = BoxLocation.objects.get(box=box)
        self.assertEqual(location.thermal_zone_id, self.zone.pk)
        self.assertIsNone(location.ends_at)
        log = AuditLog.objects.get()
        self.assertEqual((log.organization_id, log.user_id, log.action), (self.org.pk, self.user.pk, "creation"))
        self.assertEqual(log.metadata["box_id"], box.pk)
        self.assertEqual(log.metadata["valeurs"]["volume_litres"], "0.00")
        self.assertEqual(AutomaticBoxCreationRequest.objects.get().box_id, box.pk)
        for model in (BoxMovement, BoxLineage, SubcultureEvent, BiologicalMeasurement, GlobalStrainIdentity):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_normalized_replay_is_durable_without_new_records(self):
        first = self.post()
        second = self.post({**self.payload, "strain": str(self.strain.pk),
                            "volume_liters": None, "notes": "  "})
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual((Box.objects.count(), BoxLocation.objects.count(), AuditLog.objects.count(),
                          AutomaticBoxCreationRequest.objects.count()), (1, 1, 1, 1))
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 1)

    def test_decimal_and_uuid_normalization(self):
        first = self.post({**self.payload, "volume_liters": "1", "notes": " note "})
        second = self.post({**self.payload, "request_id": uuid.UUID(self.payload["request_id"]).hex,
                            "volume_liters": "1.00", "notes": "note"})
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_signed_zero_is_the_same_numeric_intent(self):
        first = self.post({**self.payload, "volume_liters": "-0.00"})
        second = self.post({**self.payload, "volume_liters": "0"})
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_each_changed_normalized_intent_conflicts(self):
        self.assertEqual(self.post().status_code, 201)
        other_strain = Strain.objects.create(organization=self.org, species=self.species, code="AUT-LAB-2")
        other_zone = ThermalZone.objects.create(organization=self.org, name="Second zone")
        for change in ({"notes": "different"}, {"volume_liters": "0"}, {"entered_on": "2026-10-02"},
                       {"strain": other_strain.pk}, {"thermal_zone": other_zone.pk}):
            with self.subTest(change=change):
                response = self.post({**self.payload, **change})
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()["code"], "box_creation_request_conflict")
        self.assertEqual(Box.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_required_fields_and_unknown_fields_rejected(self):
        for field in self.payload:
            with self.subTest(missing=field):
                response = self.post({key: value for key, value in self.payload.items() if key != field})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        for field in ("global_code", "local_code", "box_number", "organization", "status", "origin"):
            response = self.post({**self.payload, field: "client-value"})
            self.assertEqual(response.status_code, 400, response.content)
            self.assertIn(field, response.json())
        for change in ({"request_id": "invalid"}, {"request_id": None}, {"entered_on": None},
                       {"volume_liters": "1.001"}, {"volume_liters": "1000"}, {"notes": None}):
            self.assertEqual(self.post({**self.payload, **change}).status_code, 400)
        self.assert_empty()

    def test_explicit_institution_and_authentication_required(self):
        response = self.client.post(reverse("api_box_automatic"), self.payload, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post(organization=self.other_org).status_code, 403)
        self.client.logout()
        self.assertIn(self.post().status_code, (401, 403))
        self.assert_empty()

    def test_admin_and_technician_allowed_viewer_and_inactive_denied(self):
        for role in ("admin", "lab_technician", "viewer"):
            self.membership.role = role
            self.membership.save()
            response = self.post({**self.payload, "request_id": str(uuid.uuid4())})
            self.assertEqual(response.status_code, 403 if role == "viewer" else 201)
        self.membership.role = "admin"
        self.membership.is_active = False
        self.membership.save()
        self.assertEqual(self.post().status_code, 403)

    def test_superuser_still_requires_active_institution(self):
        self.user.is_superuser = True
        self.user.save()
        self.membership.delete()
        self.assertEqual(self.post().status_code, 201)
        self.org.is_active = False
        self.org.save()
        self.assertEqual(self.post().status_code, 403)

    def test_retry_rechecks_membership_role_and_activity(self):
        self.assertEqual(self.post().status_code, 201)
        for changes in ({"role": "viewer"}, {"is_active": False}):
            with self.subTest(changes=changes):
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(**changes)
                self.assertEqual(self.post().status_code, 403)
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="lab_technician", is_active=True)
        self.membership.delete()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(Box.objects.count(), 1)

    def test_service_reloads_stale_actor_and_institution_before_replay(self):
        self.service()
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.service()
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.service()
        self.assertEqual(Box.objects.count(), 1)

    def test_retry_rechecks_superuser_demotion_on_stale_actor(self):
        self.user.is_superuser = True
        self.user.save()
        self.membership.role = "viewer"
        self.membership.save()
        self.service()
        get_user_model().objects.filter(pk=self.user.pk).update(is_superuser=False)
        with self.assertRaises(PermissionDenied):
            self.service()
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 1)

    def test_retry_does_not_reveal_a_reassigned_box(self):
        box, _ = self.service()
        Box.objects.filter(pk=box.pk).update(organization=self.other_org)
        with self.assertRaises(PermissionDenied):
            self.service()
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_errors_use_french_and_english_catalogs(self):
        for language, expected in (("fr", "Ce champ n'est pas accepté."), ("en", "This field is not accepted.")):
            with self.subTest(language=language):
                UserPreference.objects.update_or_create(user=self.user, defaults={"interface_language": language})
                response = self.post({**self.payload, "local_code": "not accepted"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["local_code"], expected)

    def test_strain_and_zone_eligibility_on_creation_and_replay(self):
        foreign = Strain.objects.create(organization=self.other_org, species=self.species, code="AUT-OTHER-1")
        orphan = Strain.objects.create(species=self.species, code="AUT-ORPHAN-1")
        foreign_zone = ThermalZone.objects.create(organization=self.other_org, name="Foreign zone")
        inactive_zone = ThermalZone.objects.create(organization=self.org, name="Inactive zone", is_active=False)
        for change in ({"strain": foreign.pk}, {"strain": orphan.pk}, {"strain": 999999},
                       {"thermal_zone": foreign_zone.pk}, {"thermal_zone": inactive_zone.pk}):
            self.assertEqual(self.post({**self.payload, **change}).status_code, 400)
        self.assert_empty()
        self.assertEqual(self.post().status_code, 201)
        Strain.objects.filter(pk=self.strain.pk).update(organization=self.other_org)
        self.assertEqual(self.post().status_code, 400)
        Strain.objects.filter(pk=self.strain.pk).update(organization=self.org)
        for change in ({"is_active": False}, {"organization": self.other_org}):
            ThermalZone.objects.filter(pk=self.zone.pk).update(**change)
            self.assertEqual(self.post().status_code, 400)
            ThermalZone.objects.filter(pk=self.zone.pk).update(is_active=True, organization=self.org)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_legacy_used_strain_remains_eligible(self):
        Strain.objects.filter(pk=self.strain.pk).update(organization=None)
        self.assertEqual(self.post().status_code, 400)
        Box.objects.create(organization=self.org, strain=self.strain, global_code="AUT-LAB-1.010", box_number="010")
        self.assertEqual(self.post().json()["global_code"], "AUT-LAB-1.011")
        self.assertEqual(self.post().status_code, 200)
        self.strain.refresh_from_db()
        self.assertIsNone(self.strain.organization_id)
        self.assertIsNone(self.strain.global_identity_id)

    def test_key_scope_is_actor_and_institution(self):
        self.assertEqual(self.post().status_code, 201)
        other_user = get_user_model().objects.create_user(username="automatic-admin", email="admin@example.org")
        OrganizationMembership.objects.create(user=other_user, organization=self.org, role="admin")
        self.client.force_login(other_user)
        self.assertEqual(self.post().status_code, 201)
        OrganizationMembership.objects.create(user=other_user, organization=self.other_org, role="admin")
        strain = Strain.objects.create(organization=self.other_org, species=self.species, code="AUT-OTHER-1")
        zone = ThermalZone.objects.create(organization=self.other_org, name="Other zone")
        response = self.post({**self.payload, "strain": strain.pk, "thermal_zone": zone.pk}, organization=self.other_org)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(AutomaticBoxCreationRequest.objects.count(), 3)

    def test_manual_identifiers_cannot_be_chosen_through_legacy_post(self):
        response = self.client.post(reverse("api_box_list"), {
            "strain": self.strain.pk, "thermal_zone": self.zone.pk,
            "global_code": "AUT-LAB-1.042", "box_number": "042", "local_code": "Legacy",
        }, content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 405)
        self.assert_empty()
        self.assertEqual(self.post().json()["global_code"], "AUT-LAB-1.001")

    def test_every_write_failure_rolls_back_counter_box_location_audit_and_receipt(self):
        for model in (Box, BoxLocation, AuditLog, AutomaticBoxCreationRequest):
            with self.subTest(failure=model.__name__):
                with patch.object(model.objects, "create", side_effect=RuntimeError("Injected failure")):
                    with self.assertRaises(RuntimeError):
                        self.service()
                self.assert_empty()
        box, created = self.service()
        self.assertTrue(created)
        self.assertEqual(box.global_code, "AUT-LAB-1.001")

    def test_existing_counter_is_rolled_back_and_receipt_unique(self):
        self.service()
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("Injected failure")):
            with self.assertRaises(RuntimeError):
                self.service({**self.payload, "request_id": str(uuid.uuid4())})
        self.assertEqual(BoxCodeNamespace.objects.get().high_water, 1)
        self.assertEqual(Box.objects.count(), 1)
        receipt = AutomaticBoxCreationRequest.objects.get()
        other_box = Box.objects.create(
            organization=self.org, strain=self.strain, global_code="AUT-LAB-1.002", box_number="002",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AutomaticBoxCreationRequest.objects.create(
                organization=self.org, actor=self.user, request_id=receipt.request_id,
                intent=receipt.intent, box=other_box,
            )
