from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain, StrainTranslation

from .models import Box


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxStrainNameTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lab = Organization.objects.create(name="Label test lab")
        cls.other_lab = Organization.objects.create(name="Other label test lab")
        cls.user = get_user_model().objects.create_user(
            username="label_viewer", email="label_viewer@example.org",
        )
        for lab in (cls.lab, cls.other_lab):
            OrganizationMembership.objects.create(
                user=cls.user, organization=lab, role=OrganizationMembership.Role.VIEWER,
            )
        cls.species = Species.objects.create(scientific_name="Test species", genus_species_code="TST")

    def box(self, code, *, owner, lab=None, strain=None, number="001"):
        if strain is None:
            strain = Strain.objects.create(species=self.species, organization=owner, code=code)
        return Box.objects.create(
            organization=self.lab if lab is None else lab,
            strain=strain, global_code=f"{code}.{number}", box_number=number,
        )

    @staticmethod
    def record_name(box, language, name):
        StrainTranslation.objects.create(
            strain=box.strain, language_code=language, name=name, description="Do not disclose this description",
        )

    def get(self, route, *args, lab=None, client=None, **query):
        return (client or self.client).get(
            reverse(route, args=args), query, HTTP_X_ORGANIZATION_ID=str((lab or self.lab).pk),
        )

    def list_strains(self, *, lab=None):
        response = self.get("api_box_list", lab=lab)
        self.assertEqual(response.status_code, 200)
        return {row["id"]: row["strain"] for row in response.json()["results"]}

    def detail_strain(self, box, *, lab=None):
        response = self.get("api_box_detail", box.pk, lab=lab)
        self.assertEqual(response.status_code, 200)
        return response.json()["strain"]

    def test_list_and_detail_resolve_recorded_name_or_null_in_interface_language(self):
        self.client.force_login(self.user)
        named = self.box("TST-LOC-1", owner=self.lab)
        fallback = self.box("TST-LOC-2", owner=self.lab)
        unnamed = self.box("TST-LOC-3", owner=self.lab)
        blank = self.box("TST-LOC-4", owner=self.lab)
        missing_preferred = self.box("TST-LOC-5", owner=self.lab)
        names = {"fr": "Nom fictif", "en": "Sample name", "ja": "テスト名"}
        for language, name in names.items():
            self.record_name(named, language, name)
        self.record_name(fallback, "ja", names["ja"])
        self.record_name(fallback, "fr", names["fr"])
        self.record_name(fallback, "en", "")
        self.record_name(blank, "fr", "")
        self.record_name(missing_preferred, "ja", names["ja"])

        for language, name in names.items():
            with self.subTest(language=language):
                UserPreference.objects.update_or_create(user=self.user, defaults={"interface_language": language})
                listed = self.list_strains()
                for box, expected in (
                    (named, name), (fallback, names["ja" if language == "ja" else "fr"]),
                    (unnamed, None), (blank, None), (missing_preferred, names["ja"]),
                ):
                    strain = self.detail_strain(box)
                    self.assertEqual(strain, listed[box.pk])
                    self.assertEqual(strain["name"], expected)
                    self.assertEqual(set(strain), {"id", "code", "name", "number", "origin_code", "species"})

    def test_only_local_and_box_linked_legacy_names_are_visible(self):
        self.client.force_login(self.user)
        foreign = self.box("TST-EXT-1", owner=self.other_lab, lab=self.other_lab)
        anomalous = self.box("TST-EXT-1", owner=None, strain=foreign.strain, number="002")
        legacy = self.box("TST-LEG-1", owner=None)
        shared_legacy = self.box("TST-LEG-1", owner=None, strain=legacy.strain, lab=self.other_lab, number="002")
        foreign_legacy = self.box("TST-LEG-2", owner=None, lab=self.other_lab)
        self.record_name(foreign, "en", "Other institution name")
        self.record_name(legacy, "en", "Shared legacy name")
        self.record_name(foreign_legacy, "en", "Other legacy name")

        listed = self.list_strains()
        self.assertEqual({pk: strain["name"] for pk, strain in listed.items()}, {
            anomalous.pk: None, legacy.pk: "Shared legacy name",
        })
        self.assertEqual(self.detail_strain(anomalous), listed[anomalous.pk])
        self.assertEqual(self.detail_strain(legacy), listed[legacy.pk])
        for box in (foreign, shared_legacy, foreign_legacy):
            self.assertEqual(self.get("api_box_detail", box.pk).status_code, 404)
        self.assertEqual(self.detail_strain(foreign, lab=self.other_lab)["name"], "Other institution name")
        self.assertEqual(self.detail_strain(shared_legacy, lab=self.other_lab)["name"], "Shared legacy name")
        self.assertEqual(self.detail_strain(foreign_legacy, lab=self.other_lab)["name"], "Other legacy name")

    def test_detail_by_code_respects_active_institution(self):
        self.client.force_login(self.user)
        box = self.box("TST-LOC-1", owner=self.lab)
        self.record_name(box, "en", "Sample name")
        response = self.get("api_box_detail_by_code", box.global_code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["strain"], self.detail_strain(box))
        self.assertEqual(response.json()["strain"]["name"], "Sample name")
        denied = self.get("api_box_detail_by_code", box.global_code, lab=self.other_lab)
        self.assertEqual(denied.status_code, 404)

    def test_no_active_institution_does_not_fall_back_to_inactive_institutions(self):
        Organization.objects.update(is_active=False)
        self.user.is_superuser = True
        client = APIClient()
        client.force_authenticate(self.user)
        box = self.box("TST-LOC-1", owner=self.lab)
        self.record_name(box, "en", "Inactive institution name")
        response = client.get(reverse("api_box_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])
        response = client.get(reverse("api_box_detail", args=[box.pk]))
        self.assertEqual(response.status_code, 404)

    def test_detail_adds_one_query_for_an_eligible_strain(self):
        client = APIClient()
        client.force_authenticate(self.user)
        foreign_link = self.box("TST-EXT-1", owner=self.other_lab)
        owned = self.box("TST-LOC-1", owner=self.lab)
        self.record_name(foreign_link, "en", "Other institution name")
        self.record_name(owned, "en", "Sample name")
        with CaptureQueriesContext(connection) as baseline:
            foreign_response = self.get("api_box_detail", foreign_link.pk, client=client)
        with self.assertNumQueries(len(baseline) + 1):
            owned_response = self.get("api_box_detail", owned.pk, client=client)
        self.assertEqual(foreign_response.status_code, 200)
        self.assertEqual(owned_response.status_code, 200)
        self.assertIsNone(foreign_response.json()["strain"]["name"])
        self.assertEqual(owned_response.json()["strain"]["name"], "Sample name")

    def test_name_lookup_is_bounded_for_one_or_many_distinct_strains(self):
        client = APIClient()
        client.force_authenticate(self.user)
        boxes = [self.box(f"TST-LOC-{index}", owner=self.lab) for index in range(1, 13)]
        for box in boxes:
            self.record_name(box, "en", box.strain.code + " name")
        for size in (1, len(boxes)):
            with self.subTest(size=size):
                with self.assertNumQueries(8):
                    response = self.get("api_box_list", client=client, limit=size)
                self.assertEqual(response.status_code, 200)
                rows = response.json()["results"]
                self.assertEqual(len(rows), size)
                for row in rows:
                    self.assertEqual(row["strain"]["name"], row["strain"]["code"] + " name")
