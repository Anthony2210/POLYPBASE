import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import GlobalStrainIdentity, Species, SpeciesTranslation, Strain, StrainTranslation


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

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_species_create_rolls_back_when_audit_fails(self, create_audit):
        self.client.login(username="taxonomy_admin", password="secret")
        species_count = Species.objects.count()
        translation_count = SpeciesTranslation.objects.count()
        audit_count = AuditLog.objects.count()

        with self.assertRaises(RuntimeError):
            self.post_json(
                "api_taxonomy_species",
                {
                    "scientific_name": "Chrysaora colorata",
                    "genus_species_code": "CCO",
                    "translations": {
                        "fr": {"name": "Méduse rayée"},
                        "en": {"name": "Purple-striped jelly"},
                    },
                },
            )

        self.assertEqual(Species.objects.count(), species_count)
        self.assertFalse(Species.objects.filter(scientific_name="Chrysaora colorata").exists())
        self.assertEqual(SpeciesTranslation.objects.count(), translation_count)
        self.assertFalse(SpeciesTranslation.objects.filter(species__scientific_name="Chrysaora colorata").exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)
        create_audit.assert_called_once()

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_species_update_rolls_back_model_and_translations_when_audit_fails(self, create_audit):
        species = Species.objects.create(
            scientific_name="Aurelia aurita",
            common_name="Ancien nom",
            genus_species_code="AAU",
            worms_aphia_id=123,
            is_described=True,
            notes="Anciennes notes",
        )
        SpeciesTranslation.objects.create(
            species=species, language_code="fr", name="Ancien nom", description="Ancienne description",
        )
        SpeciesTranslation.objects.create(
            species=species, language_code="en", name="Old name", description="Old description",
        )
        previous_fields = {
            "scientific_name": species.scientific_name,
            "common_name": species.common_name,
            "genus_species_code": species.genus_species_code,
            "worms_aphia_id": species.worms_aphia_id,
            "is_described": species.is_described,
            "notes": species.notes,
        }
        previous_translations = list(species.translations.order_by("language_code").values_list(
            "language_code", "name", "description",
        ))
        audit_count = AuditLog.objects.count()
        self.client.login(username="taxonomy_admin", password="secret")

        with self.assertRaises(RuntimeError):
            self.patch_json(
                "api_taxonomy_species_detail",
                [species.pk],
                {
                    "scientific_name": "Aurelia coerulea",
                    "genus_species_code": "ACO",
                    "worms_aphia_id": 456,
                    "is_described": False,
                    "notes": "Nouvelles notes",
                    "translations": {
                        "fr": {"name": "Nouveau nom", "description": "Nouvelle description"},
                        "ja": {"name": "ミズクラゲ"},
                    },
                },
            )

        species.refresh_from_db()
        self.assertEqual(
            {field: getattr(species, field) for field in previous_fields},
            previous_fields,
        )
        self.assertEqual(
            list(species.translations.order_by("language_code").values_list(
                "language_code", "name", "description",
            )),
            previous_translations,
        )
        self.assertEqual(AuditLog.objects.count(), audit_count)
        create_audit.assert_called_once()

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_strain_create_rolls_back_when_audit_fails(self, create_audit):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        self.client.login(username="taxonomy_admin", password="secret")
        strain_count = Strain.objects.count()
        translation_count = StrainTranslation.objects.count()
        audit_count = AuditLog.objects.count()

        with self.assertRaises(RuntimeError):
            self.post_json(
                "api_taxonomy_strains",
                {
                    "species": species.pk,
                    "code": "AAU-FRA-1",
                    "number": 1,
                    "origin_code": "FRA",
                    "notes": "Souche de référence.",
                    "translations": {
                        "fr": {"name": "Souche française 1"},
                        "en": {"name": "French strain 1"},
                    },
                },
            )

        self.assertEqual(Strain.objects.count(), strain_count)
        self.assertFalse(Strain.objects.filter(species=species, code="AAU-FRA-1").exists())
        self.assertEqual(StrainTranslation.objects.count(), translation_count)
        self.assertFalse(StrainTranslation.objects.filter(strain__code="AAU-FRA-1").exists())
        self.assertEqual(AuditLog.objects.count(), audit_count)
        create_audit.assert_called_once()

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_strain_update_rolls_back_model_and_translations_when_audit_fails(self, create_audit):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        replacement_species = Species.objects.create(scientific_name="Aurelia coerulea")
        organization = Organization.objects.create(name="Strain institution")
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
                    "code": "ACO-NEW-2",
                    "number": 2,
                    "origin_code": "NEW",
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
