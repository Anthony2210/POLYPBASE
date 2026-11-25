import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import Resolver404, resolve, reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import (GlobalStrainIdentity, LocalStrainIdentity, OrganizationSpeciesCode,
                     Species, SpeciesTranslation, Strain, StrainTranslation)


class TaxonomyReferenceApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(
            name="Aquarium de Paris",
            slug="paris",
        )
        self.admin = user_model.objects.create_user(username="taxonomy_admin", email="taxonomy_admin@example.org",password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.viewer = user_model.objects.create_user(username="taxonomy_viewer", email="taxonomy_viewer@example.org",password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.viewer,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )

    def post_json(self, name, payload):
        return self.client.post(
            reverse(name),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def patch_json(self, name, args, payload):
        return self.client.patch(
            reverse(name, args=args),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_reference_list_returns_languages_and_localized_values(self):
        species = Species.objects.create(
            scientific_name="Aurelia coerulea",
            common_name="Aurélie bleue",
        )
        SpeciesTranslation.objects.create(
            species=species,
            language_code="fr",
            name="Aurélie bleue",
        )
        strain = Strain.objects.create(species=species, code="ACO-JP-1", organization=self.organization)
        StrainTranslation.objects.create(
            strain=strain,
            language_code="fr",
            name="Souche Japon 1",
        )
        self.client.login(username="taxonomy_admin", password="secret")

        response = self.client.get(reverse("api_taxonomy_references"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["code"] for item in response.json()["languages"]],
            ["fr", "en", "ja"],
        )
        self.assertEqual(response.json()["species"][0]["translations"]["fr"]["name"], "Aurélie bleue")
        self.assertEqual(response.json()["strains"][0]["translations"]["fr"]["name"], "Souche Japon 1")

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_closed_manual_strain_creation_writes_nothing(self, create_audit):
        # Rollback on audit failure is covered on the institutional endpoint.
        species = Species.objects.create(scientific_name="Aurelia aurita")
        OrganizationSpeciesCode.objects.create(organization=self.organization, species=species, code="AAA")
        self.client.login(username="taxonomy_admin", password="secret")
        counts = (Strain.objects.count(), StrainTranslation.objects.count(), AuditLog.objects.count())
        response = self.post_json(
            "api_taxonomy_strains",
            {
                "species": species.pk,
                "code": "AAU-FRA-1",
                "number": 1,
                "origin_code": "FRA",
                "translations": {"fr": {"name": "Souche française 1"}},
            },
        )
        self.assertEqual(response.status_code, 410)
        self.assertEqual((Strain.objects.count(), StrainTranslation.objects.count(), AuditLog.objects.count()), counts)
        self.assertFalse(LocalStrainIdentity.objects.exists())
        create_audit.assert_not_called()

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_strain_update_rolls_back_model_and_translations_when_audit_fails(self, create_audit):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        replacement_species = Species.objects.create(scientific_name="Aurelia coerulea")
        organization = self.organization
        identity = GlobalStrainIdentity.objects.create()
        strain = Strain.objects.create(
            species=species,
            code="AAU-OLD-1",
            organization=organization,
            global_identity=identity,
            number=1,
            origin_code="OLD",
            notes="Anciennes notes",
        )
        StrainTranslation.objects.create(
            strain=strain, language_code="fr", name="Ancienne souche", description="Ancienne description",
        )
        StrainTranslation.objects.create(
            strain=strain, language_code="en", name="Old strain", description="Old description",
        )
        previous_fields = {
            "species_id": strain.species_id,
            "code": strain.code,
            "number": strain.number,
            "origin_code": strain.origin_code,
            "notes": strain.notes,
            "organization_id": strain.organization_id,
            "global_identity_id": strain.global_identity_id,
        }
        previous_translations = list(strain.translations.order_by("language_code").values_list(
            "language_code", "name", "description",
        ))
        audit_count = AuditLog.objects.count()
        self.client.login(username="taxonomy_admin", password="secret")

        with self.assertRaises(RuntimeError):
            self.patch_json(
                "api_taxonomy_strains_detail",
                [strain.pk],
                {
                    "species": replacement_species.pk,
                    "notes": "Nouvelles notes",
                    "translations": {
                        "fr": {"name": "Nouvelle souche", "description": "Nouvelle description"},
                        "ja": {"name": "新しい株"},
                    },
                },
            )

        strain.refresh_from_db()
        self.assertEqual(
            {field: getattr(strain, field) for field in previous_fields},
            previous_fields,
        )
        self.assertEqual(
            list(strain.translations.order_by("language_code").values_list(
                "language_code", "name", "description",
            )),
            previous_translations,
        )
        self.assertEqual(AuditLog.objects.count(), audit_count)
        create_audit.assert_called_once()

    def test_viewer_cannot_read_global_references(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("api_taxonomy_references"))
        self.assertEqual(response.status_code, 403)

    def test_legacy_species_write_routes_are_unregistered(self):
        for path in ("/api/taxonomy/species/", "/api/taxonomy/species/1/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
