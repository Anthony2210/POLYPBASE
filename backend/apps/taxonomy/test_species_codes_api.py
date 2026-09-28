import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain


class SpeciesCodesApiTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Institution A")
        self.b = Organization.objects.create(name="Institution B")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="aaa_admin", email="aaa_admin@example.org", password="secret")
        self.tech = user_model.objects.create_user(username="aaa_tech", email="aaa_tech@example.org", password="secret")
        self.viewer = user_model.objects.create_user(username="aaa_viewer", email="aaa_viewer@example.org", password="secret")
        for user, organization, role in (
            (self.admin, self.a, OrganizationMembership.Role.ADMIN),
            (self.admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.tech, self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
            (self.viewer, self.a, OrganizationMembership.Role.VIEWER),
        ):
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        self.species = Species.objects.create(scientific_name="Shared species", genus_species_code="OLD")
        self.other_species = Species.objects.create(scientific_name="Other species")
        self.a_code = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="AAA")
        self.b_code = OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="BBB")
        self.list_url = reverse("api_taxonomy_species_codes")

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def detail(self, assignment):
        return reverse("api_taxonomy_species_codes_detail", args=[assignment.pk])

    def post(self, organization, data):
        return self.client.post(self.list_url, data=json.dumps(data), content_type="application/json", **self.headers(organization))

    def patch_code(self, organization, assignment, data):
        return self.client.patch(self.detail(assignment), data=json.dumps(data), content_type="application/json", **self.headers(organization))

    def test_read_is_scoped_and_requires_laboratory_role(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.list_url, **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{
            "id": self.a_code.pk, "species": self.species.pk,
            "species_scientific_name": "Shared species", "code": "AAA",
        }])
        self.assertNotIn("BBB", json.dumps(response.json()))
        self.assertEqual(self.client.get(self.detail(self.b_code), **self.headers(self.a)).status_code, 404)

        self.assertEqual(self.client.get(self.detail(self.b_code), **self.headers(self.b)).json()["code"], "BBB")
        self.assertEqual(self.client.get(self.list_url, **self.headers(self.b)).json()[0]["code"], "BBB")
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(self.list_url, **self.headers(self.a)).json()[0]["code"], "AAA")
        self.assertEqual(self.client.get(self.detail(self.a_code), **self.headers(self.a)).status_code, 200)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.list_url, **self.headers(self.a)).status_code, 403)
        self.assertEqual(self.client.get(self.detail(self.a_code), **self.headers(self.a)).status_code, 403)
        self.assertEqual(self.client.get(reverse("api_taxonomy_references"), **self.headers(self.a)).status_code, 403)

    def test_shared_species_reference_does_not_expose_assignments(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("api_taxonomy_references"), **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("BBB", json.dumps(response.json()))
        self.assertNotIn("local_code_assignments", response.json()["species"][0])

    def test_create_binds_active_organization_and_rejects_spoofing(self):
        self.client.force_login(self.admin)
        response = self.post(self.a, {"species": self.other_species.pk, "code": "abc"})
        self.assertEqual(response.status_code, 201)
        assignment = OrganizationSpeciesCode.objects.get(pk=response.json()["id"])
        self.assertEqual(assignment.organization_id, self.a.pk)
        self.assertEqual(assignment.code, "abc")
        self.assertNotIn("organization", response.json())
        self.assertFalse(LocalStrainIdentity.objects.exists())
        self.assertEqual(AuditLog.objects.get(object_id=str(assignment.pk)).organization_id, self.a.pk)
        self.assertEqual(self.post(self.a, {
            "species": self.other_species.pk, "code": "DEF", "organization": self.b.pk,
        }).status_code, 400)
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.b, species=self.other_species).exists())
        self.assertEqual(self.post(self.a, {"species": 999999, "code": "XYZ"}).status_code, 400)
        self.assertEqual(self.post(self.a, {"species": self.species.pk, "code": "AAAA"}).status_code, 400)

    def test_duplicate_conflicts_are_local_and_distinct(self):
        self.client.force_login(self.admin)
        response = self.post(self.a, {"species": self.species.pk, "code": "CCC"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("species", response.json())
        response = self.post(self.a, {"species": self.other_species.pk, "code": "AAA"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.json())
        self.assertEqual(self.post(self.b, {"species": self.other_species.pk, "code": "AAA"}).status_code, 201)
        self.assertEqual(self.post(self.b, {"species": self.other_species.pk, "code": "AAA"}).status_code, 400)
        self.assertEqual(self.b_code.code, "BBB")
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_technician_cannot_mutate_and_foreign_context_is_denied(self):
        self.client.force_login(self.tech)
        self.assertEqual(self.post(self.a, {"species": self.other_species.pk, "code": "CCC"}).status_code, 403)
        self.assertEqual(self.patch_code(self.a, self.a_code, {"code": "CCC"}).status_code, 403)
        self.assertEqual(self.client.get(self.list_url, **self.headers(self.b)).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.list_url, HTTP_X_ORGANIZATION_ID="999999").status_code, 403)
        self.assertEqual(self.post(self.a, {"species": self.other_species.pk, "code": "CCC", "global_identity": 1}).status_code, 400)
        self.assertFalse(OrganizationSpeciesCode.objects.filter(code="CCC").exists())

    def test_update_changes_only_aaa_and_preserves_issued_identifiers(self):
        strain = Strain.objects.create(organization=self.a, species=self.species, code="OLD-1")
        identity = LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=self.a_code)
        box = Box.objects.create(organization=self.a, strain=strain, global_code="OLD-1.001", box_number="001")
        self.client.force_login(self.admin)
        response = self.patch_code(self.a, self.a_code, {"code": "CCC"})
        self.assertEqual(response.status_code, 200)
        self.a_code.refresh_from_db()
        self.species.refresh_from_db()
        strain.refresh_from_db()
        box.refresh_from_db()
        identity.refresh_from_db()
        self.assertEqual(identity.species_code_assignment_id, self.a_code.pk)
        self.assertEqual(self.a_code.code, "CCC")
        self.assertEqual(self.species.genus_species_code, "OLD")
        self.assertEqual(strain.code, "OLD-1")
        self.assertEqual(box.global_code, "OLD-1.001")
        audit = AuditLog.objects.get()
        self.assertEqual((audit.organization_id, audit.user_id, audit.action, audit.object_type),
                         (self.a.pk, self.admin.pk, AuditLog.Action.UPDATE, "organization_species_code"))
        self.assertEqual(self.patch_code(self.a, self.a_code, {"species": self.other_species.pk, "code": "DDD"}).status_code, 400)
        self.assertEqual(self.patch_code(self.a, self.a_code, {"organization": self.b.pk, "code": "DDD"}).status_code, 400)
        self.assertEqual(self.patch_code(self.a, self.a_code, {"code": ""}).status_code, 400)
        self.assertEqual(self.patch_code(self.a, self.a_code, {}).status_code, 400)
        self.a_code.refresh_from_db()
        self.assertEqual((self.a_code.species_id, self.a_code.code), (self.species.pk, "CCC"))

    def test_foreign_pk_is_not_found_and_duplicate_update_is_rejected(self):
        other = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.other_species, code="CCC")
        self.client.force_login(self.admin)
        self.assertEqual(self.patch_code(self.a, self.b_code, {"code": "DDD"}).status_code, 404)
        response = self.patch_code(self.a, other, {"code": "AAA"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.json())
        other.refresh_from_db()
        self.assertEqual(other.code, "CCC")
        self.assertFalse(AuditLog.objects.exists())
        self.assertEqual(self.client.delete(self.detail(self.a_code), **self.headers(self.a)).status_code, 405)
        self.assertEqual(self.client.delete(self.list_url, **self.headers(self.a)).status_code, 405)

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_audit_failure_rolls_back_create_and_update(self, create_audit):
        self.client.force_login(self.admin)
        with self.assertRaises(RuntimeError):
            self.post(self.a, {"species": self.other_species.pk, "code": "CCC"})
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.a, species=self.other_species).exists())
        with self.assertRaises(RuntimeError):
            self.patch_code(self.a, self.a_code, {"code": "DDD"})
        self.a_code.refresh_from_db()
        self.assertEqual(self.a_code.code, "AAA")

    @patch("apps.taxonomy.api_views.SpeciesCodeWriteSerializer.save")
    def test_known_constraint_race_is_reported_without_audit(self, save):
        self.client.force_login(self.admin)
        for detail, field in (
            ("unique_species_code_per_organization_species", "species"),
            ("unique_species_code_per_organization_code", "code"),
        ):
            cause = Exception()
            cause.diag = type("Diag", (), {"constraint_name": detail})()
            save.side_effect = IntegrityError("duplicate")
            save.side_effect.__cause__ = cause
            response = self.post(self.a, {"species": self.other_species.pk, "code": "CCC"})
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.json())
        save.side_effect = IntegrityError("unrelated constraint")
        with self.assertRaises(IntegrityError):
            self.post(self.a, {"species": self.other_species.pk, "code": "CCC"})
        self.assertFalse(AuditLog.objects.exists())
