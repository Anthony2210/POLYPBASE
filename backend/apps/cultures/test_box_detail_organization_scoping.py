"""Box detail access is limited to the explicitly active organization."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxDetailOrganizationScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization_a = Organization.objects.create(name="Box detail laboratory A")
        cls.organization_b = Organization.objects.create(name="Box detail laboratory B")
        cls.user = get_user_model().objects.create_user(
            username="box_detail_viewer",
            email="box_detail_viewer@example.org",
        )
        for organization in (cls.organization_a, cls.organization_b):
            OrganizationMembership.objects.create(
                user=cls.user,
                organization=organization,
                role=OrganizationMembership.Role.VIEWER,
                is_active=True,
            )
        species = Species.objects.create(
            scientific_name="Aurelia detail", genus_species_code="ADE"
        )
        strain_a = Strain.objects.create(
            species=species, organization=cls.organization_a, code="ADE-A-1"
        )
        strain_b = Strain.objects.create(
            species=species, organization=cls.organization_b, code="ADE-B-1"
        )
        cls.box_a = Box.objects.create(
            organization=cls.organization_a,
            strain=strain_a,
            global_code="ADE-A-1.001",
            box_number="001",
            notes="Laboratory A box notes",
        )
        cls.box_b = Box.objects.create(
            organization=cls.organization_b,
            strain=strain_b,
            global_code="ADE-B-1.001",
            box_number="001",
            notes="Laboratory B private box notes",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_active_organization_box_detail_succeeds(self):
        response = self.client.get(
            reverse("api_box_detail", args=[self.box_a.pk]),
            HTTP_X_ORGANIZATION_ID=str(self.organization_a.pk),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], self.box_a.pk)
        self.assertEqual(payload["organization"]["id"], self.organization_a.pk)
        self.assertEqual(payload["global_code"], self.box_a.global_code)
        self.assertEqual(payload["notes"], self.box_a.notes)

    def test_other_membership_does_not_expose_inactive_organization_box(self):
        detail_url = reverse("api_box_detail", args=[self.box_b.pk])
        response = self.client.get(
            detail_url,
            HTTP_X_ORGANIZATION_ID=str(self.organization_a.pk),
        )
        missing_response = self.client.get(
            reverse("api_box_detail", args=[max(self.box_a.pk, self.box_b.pk) + 1]),
            HTTP_X_ORGANIZATION_ID=str(self.organization_a.pk),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(set(response.json()), {"detail"})
        self.assertEqual(response.json(), missing_response.json())
        self.assertNotContains(response, self.box_b.global_code, status_code=404)
        self.assertNotContains(response, self.box_b.notes, status_code=404)
        self.assertNotContains(response, self.organization_b.name, status_code=404)

        # The same user can read the existing box only after selecting its owner.
        selected_response = self.client.get(
            detail_url,
            HTTP_X_ORGANIZATION_ID=str(self.organization_b.pk),
        )
        self.assertEqual(selected_response.status_code, 200)
        self.assertEqual(selected_response.json()["id"], self.box_b.pk)
        self.assertEqual(
            selected_response.json()["organization"]["id"], self.organization_b.pk
        )
        self.assertEqual(selected_response.json()["notes"], self.box_b.notes)
