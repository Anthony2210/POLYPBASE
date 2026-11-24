import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import OrganizationSpeciesCode, Species


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
        self.a_code = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="AAA")
        self.b_code = OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="BBB")
        self.list_url = reverse("api_taxonomy_species_codes")

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def detail(self, assignment):
        return reverse("api_taxonomy_species_codes_detail", args=[assignment.pk])

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

    def test_foreign_context_is_denied(self):
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(self.list_url, **self.headers(self.b)).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.list_url, HTTP_X_ORGANIZATION_ID="999999").status_code, 403)

    def test_species_code_routes_are_read_only(self):
        self.client.force_login(self.admin)
        for url in (self.list_url, self.detail(self.a_code)):
            for method in (self.client.post, self.client.patch, self.client.put, self.client.delete):
                with self.subTest(url=url, method=method.__name__):
                    response = method(url, data='{"code": "CCC"}', content_type="application/json", **self.headers(self.a))
                    self.assertEqual(response.status_code, 405)
        self.a_code.refresh_from_db()
        self.assertEqual(self.a_code.code, "AAA")
        self.assertEqual(OrganizationSpeciesCode.objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())
