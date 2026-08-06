import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import Species, SpeciesTranslation, Strain, StrainTranslation


class TaxonomyReferenceApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(
            name="Aquarium de Paris",
            slug="paris",
        )
        self.admin = user_model.objects.create_user(
            username="taxonomy_admin",
            password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.viewer = user_model.objects.create_user(
            username="taxonomy_viewer",
            password="secret",
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

    def test_admin_creates_localized_species_and_strain(self):
        self.client.login(username="taxonomy_admin", password="secret")

        species_response = self.post_json(
            "api_taxonomy_species",
            {
                "scientific_name": "Aurelia aurita",
                "genus_species_code": "aau",
                "is_described": True,
                "notes": "WoRMS reference checked.",
                "translations": {
                    "fr": {
                        "name": "Aurélie",
                        "description": "Méduse lune.",
                    },
                    "en": {
                        "name": "Moon jellyfish",
                        "description": "Moon jelly species.",
                    },
                    "ja": {
                        "name": "ミズクラゲ",
                        "description": "ミズクラゲ属の一種。",
                    },
                },
            },
        )

        self.assertEqual(species_response.status_code, 201)
        species = Species.objects.get(scientific_name="Aurelia aurita")
        self.assertEqual(species.genus_species_code, "AAU")
        self.assertEqual(species.common_name, "Aurélie")
        self.assertEqual(species.translations.count(), 3)

        strain_response = self.post_json(
            "api_taxonomy_strains",
            {
                "species": species.id,
                "code": "aau-fra-1",
                "number": 1,
                "origin_code": "fra",
                "notes": "Reference culture.",
                "translations": {
                    "fr": {
                        "name": "Souche française 1",
                        "description": "Souche de référence.",
                    },
                    "en": {
                        "name": "French strain 1",
                        "description": "Reference strain.",
                    },
                },
            },
        )

        self.assertEqual(strain_response.status_code, 201)
        strain = Strain.objects.get(code="AAU-FRA-1")
        self.assertEqual(strain.origin_code, "FRA")
        self.assertEqual(strain.translations.count(), 2)
        self.assertEqual(
            AuditLog.objects.filter(object_type__in=["species", "strain"]).count(),
            2,
        )

    def test_default_language_name_is_required(self):
        self.client.login(username="taxonomy_admin", password="secret")

        response = self.post_json(
            "api_taxonomy_species",
            {
                "scientific_name": "Chrysaora colorata",
                "translations": {
                    "en": {"name": "Purple-striped jelly"},
                },
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Species.objects.filter(scientific_name="Chrysaora colorata").exists())

    def test_admin_updates_localized_reference(self):
        species = Species.objects.create(
            scientific_name="Aurelia aurita",
            common_name="Ancien nom",
        )
        SpeciesTranslation.objects.create(
            species=species,
            language_code="fr",
            name="Ancien nom",
        )
        self.client.login(username="taxonomy_admin", password="secret")

        response = self.patch_json(
            "api_taxonomy_species_detail",
            [species.id],
            {
                "translations": {
                    "fr": {
                        "name": "Aurélie",
                        "description": "Méduse lune.",
                    },
                    "ja": {
                        "name": "ミズクラゲ",
                        "description": "",
                    },
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        species.refresh_from_db()
        self.assertEqual(species.common_name, "Aurélie")
        self.assertEqual(species.translations.count(), 2)
        self.assertEqual(response.json()["translations"]["ja"]["name"], "ミズクラゲ")

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
        strain = Strain.objects.create(species=species, code="ACO-JP-1")
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

    def test_viewer_cannot_manage_global_references(self):
        self.client.login(username="taxonomy_viewer", password="secret")

        list_response = self.client.get(reverse("api_taxonomy_references"))
        create_response = self.post_json(
            "api_taxonomy_species",
            {
                "scientific_name": "Cassiopea andromeda",
                "translations": {"fr": {"name": "Cassiopée"}},
            },
        )

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
