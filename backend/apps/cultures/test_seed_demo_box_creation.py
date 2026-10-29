"""A freshly seeded demo can exercise ordinary automatic creation."""

import uuid
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import UserPreference
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, Strain, StrainTranslation

from .box_identity import strain_box_identity
from .management.commands.seed_demo_data import DEMO_CREATION_STRAIN_CODE
from .models import Box, BoxLocation, ThermalZone


@override_settings(SECURE_SSL_REDIRECT=False)
class DemoSeedBoxCreationTests(TestCase):
    def setUp(self):
        call_command("seed_demo_data", stdout=StringIO())
        self.paris = Organization.objects.get(slug="aquarium-de-paris")

    def test_seed_has_exactly_one_synthetic_usable_strain_and_keeps_legacy_codes(self):
        identities = {
            strain.code: strain_box_identity(strain, strain.organization)
            for strain in Strain.objects.select_related("organization")
        }
        usable = [code for code, identity in identities.items() if identity["status"] == "usable"]
        self.assertEqual(usable, [DEMO_CREATION_STRAIN_CODE])
        for legacy in ("1-ATL", "3-MED", "2-PAC", "1-LAB"):
            self.assertEqual(identities[legacy], {"status": "unusable", "reason": "code_format"})
        strain = Strain.objects.get(code=DEMO_CREATION_STRAIN_CODE)
        self.assertEqual((strain.organization, strain.number, strain.origin_code), (self.paris, 1, "DMO"))
        self.assertIsNone(strain.origin_id, "No acquisition event is fabricated")
        self.assertFalse(LocalStrainIdentity.objects.exists(), "No provenance assignment is fabricated")
        self.assertIsNone(identities[DEMO_CREATION_STRAIN_CODE]["recorded_provenance_name"])
        self.assertIn("Synthetic", strain.notes)
        # The fixture is marked fictitious through the existing localized strain name.
        self.assertEqual(
            dict(StrainTranslation.objects.filter(strain=strain).values_list("language_code", "name")),
            {"fr": "Souche de démonstration fictive", "en": "Fictitious demonstration strain"},
        )
        self.assertFalse(StrainTranslation.objects.exclude(strain=strain).exists())
        self.assertFalse(Box.objects.filter(strain=strain).exists())

    def test_demo_technician_can_create_and_replay_a_box_from_the_synthetic_strain(self):
        user = get_user_model().objects.get(username="demo_lab")
        self.client.force_login(user)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.paris.pk)}
        options = self.client.get(reverse("api_box_automatic_strains"), **headers).json()
        demo = next(item for item in options if item["code"] == DEMO_CREATION_STRAIN_CODE)
        self.assertEqual(demo["identity"]["status"], "usable")
        zone = ThermalZone.objects.filter(organization=self.paris, is_active=True).order_by("pk").first()
        legacy_codes = sorted(Box.objects.values_list("global_code", flat=True))
        payload = {"strain": demo["id"], "thermal_zone": zone.pk, "entered_on": "2026-10-28", "request_id": str(uuid.uuid4())}
        response = self.client.post(reverse("api_box_automatic"), payload, content_type="application/json", **headers)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["global_code"], f"{DEMO_CREATION_STRAIN_CODE}.001")
        box = Box.objects.get(pk=response.json()["id"])
        self.assertTrue(BoxLocation.objects.filter(box=box, thermal_zone=zone, ends_at__isnull=True).exists())
        self.assertTrue(AuditLog.objects.filter(object_id=box.global_code, action=AuditLog.Action.CREATION).exists())
        replay = self.client.post(reverse("api_box_automatic"), payload, content_type="application/json", **headers)
        self.assertEqual((replay.status_code, replay.json()["id"]), (200, box.pk))
        legacy = next(item for item in options if item["code"] == "1-LAB")
        refused = self.client.post(reverse("api_box_automatic"), {**payload, "strain": legacy["id"], "request_id": str(uuid.uuid4())},
                                   content_type="application/json", **headers)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(sorted(Box.objects.exclude(pk=box.pk).values_list("global_code", flat=True)), legacy_codes)
        call_command("seed_demo_data", stdout=StringIO())
        self.assertEqual(Strain.objects.filter(code=DEMO_CREATION_STRAIN_CODE).count(), 1)
        self.assertEqual(StrainTranslation.objects.count(), 2)
        self.assertTrue(Box.objects.filter(pk=box.pk, global_code=f"{DEMO_CREATION_STRAIN_CODE}.001").exists())

    def test_options_name_the_demo_strain_as_fictitious_in_each_language(self):
        user = get_user_model().objects.get(username="demo_lab")
        self.client.force_login(user)
        for language, expected in (("fr", "Souche de démonstration fictive"), ("en", "Fictitious demonstration strain")):
            with self.subTest(language=language):
                UserPreference.objects.update_or_create(user=user, defaults={"interface_language": language})
                options = self.client.get(
                    reverse("api_box_automatic_strains"), HTTP_X_ORGANIZATION_ID=str(self.paris.pk),
                ).json()
                demo = next(item for item in options if item["code"] == DEMO_CREATION_STRAIN_CODE)
                self.assertEqual(demo["name"], expected)
                self.assertIsNone(demo["identity"]["recorded_provenance_name"])
