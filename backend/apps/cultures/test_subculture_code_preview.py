"""Advisory code candidates never reserve identities or change scientific state."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .api_views import BoxSubcultureCodePreviewAPIView
from .box_codes import allocate_box_codes, preview_box_codes
from .models import Box, BoxCodeNamespace, BoxLineage, BoxLocation, BoxMovement, SubcultureAllocation, SubcultureEvent, ThermalZone
from .polyp_state import resolve_current_polyp_state


class SubcultureCodePreviewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Preview laboratory")
        self.other_org = Organization.objects.create(name="Other laboratory")
        self.user = get_user_model().objects.create_user(username="preview", email="preview@example.org")
        self.membership = OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="lab_technician",
        )
        self.zone = ThermalZone.objects.create(organization=self.org, name="Zone")
        species = Species.objects.create(scientific_name="Aurelia preview", genus_species_code="AQT")
        self.strain = Strain.objects.create(species=species, organization=self.org, code="AQT-QA-1")
        self.box = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.zone,
            global_code="AQT-QA-1.001", box_number="001",
        )
        self.url = reverse("api_box_subculture_code_preview", args=[self.box.pk])
        self.client.force_login(self.user)

    def preview(self, data=None, organization=None, box=None):
        url = reverse("api_box_subculture_code_preview", args=[box.pk]) if box else self.url
        return self.client.get(url, {"count": 2} if data is None else data,
                               HTTP_X_ORGANIZATION_ID=str((organization or self.org).pk))

    def snapshot(self):
        models = (Box, BoxCodeNamespace, SubcultureEvent, SubcultureAllocation,
                  BoxLineage, BoxLocation, BoxMovement, AuditLog, BiologicalMeasurement)
        return {model: list(model.objects.order_by("pk").values()) for model in models}

    def test_exact_shape_no_store_and_no_mutation(self):
        before = self.snapshot()
        request = APIRequestFactory().get(self.url, {"count": 2}, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        force_authenticate(request, user=self.user)
        with CaptureQueriesContext(connection) as queries:
            direct_response = BoxSubcultureCodePreviewAPIView.as_view()(request, box_id=self.box.pk)
        response = self.preview()
        self.assertEqual(direct_response.data, response.json())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"reserved": False, "children": [
            {"position": 0, "global_code": "AQT-QA-1.002", "box_number": "002"},
            {"position": 1, "global_code": "AQT-QA-1.003", "box_number": "003"},
        ]})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.snapshot(), before)
        non_select = [query["sql"] for query in queries if not query["sql"].lstrip().upper().startswith("SELECT")]
        self.assertEqual(non_select, [])
        self.assertTrue(all("FOR UPDATE" not in query["sql"].upper() for query in queries))

    def test_absent_counter_is_not_created_and_repeated_previews_do_not_consume(self):
        BoxCodeNamespace.objects.all().delete()
        before = self.snapshot()
        first = self.preview()
        self.assertEqual(first.json(), self.preview().json())
        self.assertEqual(first.json()["children"][0]["box_number"], "002")
        self.assertEqual(self.snapshot(), before)

    def test_stale_counter_reconciles_legacy_suffix_in_memory_only(self):
        Box.objects.filter(pk=self.box.pk).update(global_code="AQT-QA-1.00087-legacy")
        before = self.snapshot()
        response = self.preview()
        self.assertEqual(response.json()["children"][0]["global_code"], "AQT-QA-1.088")
        self.assertEqual(self.snapshot(), before)

    def test_retained_high_water_no_holes_and_padding_grows(self):
        foreign = Box.objects.create(organization=self.other_org, strain=self.strain,
                                     global_code="AQT-QA-1.999", box_number="999")
        foreign.delete()
        before = self.snapshot()
        self.assertEqual([row["box_number"] for row in self.preview().json()["children"]], ["1000", "1001"])
        self.assertEqual(self.snapshot(), before)

    def test_global_reconciliation_does_not_return_foreign_box_details(self):
        foreign = Box.objects.create(organization=self.other_org, strain=self.strain,
                                     global_code="unrelated", box_number="9")
        Box.objects.filter(pk=foreign.pk).update(global_code="AQT-QA-1.009-private")
        response = self.preview({"count": 1, "namespace": "client-imposed"})
        self.assertEqual(response.json(), {"reserved": False, "children": [
            {"position": 0, "global_code": "AQT-QA-1.010", "box_number": "010"},
        ]})
        self.assertNotIn("private", response.content.decode())

    def test_escaped_dotted_namespace_uses_exact_prefix(self):
        self.strain.code = "A.Q+-1"
        self.strain.save(update_fields=["code"])
        Box.objects.filter(pk=self.box.pk).update(global_code="A.Q+-1.07-tail")
        BoxCodeNamespace.objects.all().delete()
        self.assertEqual(preview_box_codes(self.strain.code, 1), [("A.Q+-1.008", "008")])
        self.assertFalse(BoxCodeNamespace.objects.exists())

    def test_code_length_validation_does_not_create_counter(self):
        before = self.snapshot()
        with self.assertRaises(ValidationError) as caught:
            preview_box_codes("X" * 97, 1)
        self.assertEqual(self.snapshot(), before)
        with patch("apps.cultures.api_views.preview_box_codes", side_effect=caught.exception) as preview:
            response = self.preview()
        preview.assert_called_once_with(self.strain.code, 2)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), caught.exception.messages)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.snapshot(), before)

    def test_count_is_required_and_bounded(self):
        before = self.snapshot()
        for data in ({}, {"count": ""}, {"count": "bad"}, {"count": "1.5"},
                     {"count": 0}, {"count": -1}, {"count": 21}):
            with self.subTest(data=data):
                response = self.preview(data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(len(self.preview({"count": 1}).json()["children"]), 1)
        self.assertEqual(len(self.preview({"count": 20}).json()["children"]), 20)
        self.assertEqual(self.snapshot(), before)

    def test_authentication_and_lab_write_permission(self):
        before = self.snapshot()
        self.client.logout()
        self.assertIn(self.preview().status_code, (401, 403))
        self.client.force_login(self.user)
        self.membership.role = "viewer"
        self.membership.save(update_fields=["role"])
        response = self.preview()
        self.assertEqual(response.status_code, 403)
        self.assertIn("no-store", response["Cache-Control"])
        self.membership.role = "admin"
        self.membership.save(update_fields=["role"])
        self.assertEqual(self.preview().status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_foreign_parent_and_active_organization_are_scoped(self):
        foreign = Box.objects.create(organization=self.other_org, strain=self.strain,
                                     global_code="AQT-QA-1.005", box_number="005")
        before = self.snapshot()
        self.assertEqual(self.preview(box=foreign).status_code, 404)
        self.assertEqual(self.preview(organization=self.other_org).status_code, 403)
        OrganizationMembership.objects.create(user=self.user, organization=self.other_org, role="admin")
        self.assertEqual(self.preview(organization=self.other_org).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_preview_matches_allocator_when_no_writer_intervenes(self):
        candidates = preview_box_codes(self.strain.code, 2)
        with transaction.atomic():
            self.assertEqual(allocate_box_codes(self.strain.code, 2), candidates)

    def test_post_is_authoritative_after_an_intervening_writer(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=timezone.localdate(),
                                             polyp_count=10, user=self.user)
        self.box.refresh_from_db()
        revision = resolve_current_polyp_state(self.box)["revision"]
        first = self.preview({"count": 1}).json()
        self.assertEqual(first, self.preview({"count": 1}).json())
        Box.objects.create(organization=self.org, strain=self.strain,
                           global_code=first["children"][0]["global_code"], box_number="002")
        self.box.refresh_from_db()
        self.assertEqual(resolve_current_polyp_state(self.box)["revision"], revision)
        payload = {"expected_current_state_revision": revision, "children": [
            {"thermal_zone_id": self.zone.pk, "allocated_polyps": 3},
        ]}
        response = self.client.post(reverse("api_box_subcultures", args=[self.box.pk]),
            data=payload, content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["children"][0]["global_code"], "AQT-QA-1.003")
        payload["children"][0]["global_code"] = first["children"][0]["global_code"]
        response = self.client.post(reverse("api_box_subcultures", args=[self.box.pk]),
            data=payload, content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 400)
