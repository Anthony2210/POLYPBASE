import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, Species, Strain


class StrainScopingApiTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Institution A")
        self.b = Organization.objects.create(name="Institution B")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="scope_admin", email="scope_admin@example.org", password="secret")
        self.b_admin = user_model.objects.create_user(username="scope_b_admin", email="scope_b_admin@example.org", password="secret")
        self.tech = user_model.objects.create_user(username="scope_tech", email="scope_tech@example.org", password="secret")
        for user, organization, role in (
            (self.admin, self.a, OrganizationMembership.Role.ADMIN),
            (self.admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.b_admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.tech, self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
        ):
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        self.species = Species.objects.create(scientific_name="Shared species")
        self.identity = GlobalStrainIdentity.objects.create()
        self.a_strain = Strain.objects.create(species=self.species, code="A-1", organization=self.a, global_identity=self.identity)
        self.b_strain = Strain.objects.create(species=self.species, code="B-1", organization=self.b, global_identity=self.identity)
        self.legacy = Strain.objects.create(species=self.species, code="OLD-1")
        self.b_only = Strain.objects.create(species=self.species, code="OLD-2")
        self.orphan = Strain.objects.create(species=self.species, code="OLD-3")
        Box.objects.create(organization=self.a, strain=self.legacy, global_code="OLD-1.001", box_number="001", status=Box.Status.INACTIVE)
        Box.objects.create(organization=self.b, strain=self.legacy, global_code="OLD-1.002", box_number="002")
        Box.objects.create(organization=self.b, strain=self.b_only, global_code="OLD-2.001", box_number="001")

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def create(self, active_organization, code, **extra):
        return self.client.post(reverse("api_taxonomy_strains"), data=json.dumps({
            "species": self.species.pk, "code": code,
            "translations": {"fr": {"name": "Souche"}}, **extra,
        }), content_type="application/json", **self.headers(active_organization))

    def test_create_uses_active_organization_not_submitted_ownership(self):
        self.client.force_login(self.admin)
        response = self.create(self.a, "NEW-A", organization=self.b.pk, global_identity=self.identity.pk)
        self.assertEqual(response.status_code, 201)
        strain = Strain.objects.get(code="NEW-A")
        self.assertEqual(strain.organization, self.a)
        self.assertIsNone(strain.global_identity_id)
        self.assertNotIn("organization", response.json())
        self.assertNotIn("global_identity", response.json())
        self.assertEqual(self.create(self.b, "NEW-B").status_code, 201)
        self.assertEqual(Strain.objects.get(code="NEW-B").organization, self.b)
        response = self.client.post(reverse("api_taxonomy_strains"), data=json.dumps({
            "species": self.species.pk, "code": "NO-CONTEXT",
            "translations": {"fr": {"name": "Souche"}},
        }), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Strain.objects.filter(code="NO-CONTEXT").exists())
        self.client.force_login(self.tech)
        self.assertEqual(self.create(self.a, "TECH-NEW").status_code, 403)

    def test_references_are_scoped_and_species_count_does_not_leak(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("api_taxonomy_references"), **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.json()["strains"]}, {self.a_strain.pk, self.legacy.pk})
        self.assertEqual(response.json()["species"][0]["strain_count"], 2)
        response = self.client.get(reverse("api_taxonomy_references"), **self.headers(self.b))
        self.assertEqual({row["id"] for row in response.json()["strains"]}, {self.b_strain.pk, self.legacy.pk, self.b_only.pk})
        self.assertEqual(response.json()["species"][0]["strain_count"], 3)
        self.client.force_login(self.tech)
        response = self.client.get(reverse("api_taxonomy_references"), **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.json()["strains"]}, {self.a_strain.pk, self.legacy.pk})

    def test_patch_only_owned_strains_without_cross_institution_audit(self):
        self.client.force_login(self.admin)
        url = lambda pk: reverse("api_taxonomy_strains_detail", args=[pk])
        payload = json.dumps({"notes": "Updated", "organization": self.b.pk, "global_identity": self.identity.pk})
        for strain in (self.b_strain, self.legacy):
            response = self.client.patch(url(strain.pk), data=payload, content_type="application/json", **self.headers(self.a))
            self.assertEqual(response.status_code, 404)
            strain.refresh_from_db()
            self.assertEqual(strain.notes, "")
        self.assertFalse(AuditLog.objects.exists())
        response = self.client.patch(url(self.a_strain.pk), data=payload, content_type="application/json", **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.organization, self.a)
        self.assertEqual(self.a_strain.notes, "Updated")
        self.assertEqual(AuditLog.objects.count(), 1)
        response = self.client.patch(url(self.a_strain.pk), data=payload, content_type="application/json", **self.headers(self.b))
        self.assertEqual(response.status_code, 404)

    def test_global_code_uniqueness_still_rejects_other_organization(self):
        self.client.force_login(self.admin)
        response = self.create(self.b, self.a_strain.code)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Strain.objects.filter(species=self.species, code=self.a_strain.code).count(), 1)
