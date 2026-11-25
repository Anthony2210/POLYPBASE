from datetime import date, datetime, timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.measurements.models import (
    BiologicalMeasurement, DailyTemperature, Observation, Probe, SalinityMeasurement, TemperatureMeasurement,
)
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, IdentificationTag, ThermalZone


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class DemoSeedVolumeTests(TestCase):
    def seed(self, *args):
        output = StringIO()
        call_command("seed_demo_data", *args, stdout=output)
        return output.getvalue()

    def counts(self):
        return tuple(model.objects.count() for model in (
            Box, BoxLocation, IdentificationTag, BiologicalMeasurement, Observation,
            ThermalZone, Probe, DailyTemperature, SalinityMeasurement, TemperatureMeasurement,
        ))

    def test_zero_only_seeds_the_existing_small_demo(self):
        self.assertNotIn("Volume", self.seed("--volume", "0"))
        self.assertEqual(self.counts(), (6, 6, 6, 24, 2, 5, 5, 25, 25, 15))

    def test_invalid_volume_fails_before_writes(self):
        for value in ("-1", "501", "1.5"):
            with self.subTest(value=value), self.assertRaisesMessage(CommandError, "--volume"):
                self.seed("--volume", value)
        self.assertFalse(Organization.objects.exists())
        self.assertFalse(get_user_model().objects.exists())

    @override_settings(DEBUG=False)
    def test_volume_does_not_bypass_the_non_debug_guard(self):
        with self.assertRaisesMessage(CommandError, "requires DEBUG or --allow-non-debug"):
            self.seed("--volume", "1")
        self.assertFalse(Organization.objects.exists())

    def test_collision_rolls_back_the_seed_without_claiming_a_foreign_box(self):
        foreign = Organization.objects.create(name="Other", slug="other")
        species = Species.objects.create(scientific_name="Unrelated species")
        strain = Strain.objects.create(organization=foreign, species=species, code="OTHER")
        box = Box.objects.create(organization=foreign, strain=strain, global_code="DEMO-VOL-0002", box_number="2")
        with self.assertRaisesMessage(CommandError, "DEMO-VOL-0002 conflicts"):
            self.seed("--volume", "2")
        self.assertEqual(list(Box.objects.values_list("pk", "organization_id")), [(box.pk, foreign.pk)])
        self.assertFalse(Organization.objects.filter(slug="aquarium-de-paris").exists())
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BiologicalMeasurement.objects.exists())

    def test_replay_grow_and_smaller_request_preserve_identity_and_counts(self):
        self.seed("--volume", "1")
        box = Box.objects.get(global_code="DEMO-VOL-0001")
        self.assertEqual(self.counts(), (7, 7, 7, 56, 10, 7, 9, 137, 137, 21519))
        first = box.biological_measurements.get(measured_on=date(2026, 4, 20))
        self.assertEqual((first.ephyrae_count, first.polyp_state_sequence), (0, 1))
        self.assertEqual(box.polyp_state_revision, 32)
        self.assertEqual(box.locations.get().thermal_zone_id, box.thermal_zone_id)
        self.assertEqual(box.strain.organization_id, box.organization_id)

        output = self.seed("--volume", "2")
        self.assertIn("2 boxes, 64 biological measurements, 16 box observations", output)
        self.assertIn(f"organization_id={box.organization_id}", output)
        self.assertIn(f"box_id={box.pk} code={box.global_code} zone_id={box.thermal_zone_id}", output)
        self.assertEqual(output, self.seed("--volume", "2"))
        self.seed("--volume", "1")
        self.seed()
        self.assertEqual(self.counts(), (8, 8, 8, 88, 18, 7, 9, 137, 137, 21519))
        box.refresh_from_db()
        self.assertEqual(box.polyp_state_revision, 32)
        self.assertEqual(box.biological_measurements.get(measured_on=date(2026, 4, 20)).pk, first.pk)

    def test_normal_box_and_thermal_endpoints_expose_volume_histories(self):
        self.seed("--volume", "2")
        box = Box.objects.get(global_code="DEMO-VOL-0001")
        self.client.force_login(get_user_model().objects.get(username="demo_lab"))
        headers = {"HTTP_X_ORGANIZATION_ID": str(box.organization_id)}

        def get_json(route, pk, query=None):
            response = self.client.get(reverse(route, args=[pk]), query or {}, **headers)
            self.assertEqual(response.status_code, 200, response.content)
            return response.json()

        detail = get_json("api_box_detail", box.pk)
        self.assertEqual(len(detail["biological_measurements"]), 32)
        with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 25, tzinfo=timezone.utc)):
            daily = get_json("api_box_temperatures", box.pk, {"start": "2026-09-30", "end": "2026-11-24"})
            self.assertEqual(daily["count"], 56)
            monitoring = get_json("api_thermal_zone_thermal_monitoring", box.thermal_zone_id)
            self.assertEqual(monitoring["summary"]["measurement_count"], 32)
        series = get_json("api_thermal_zone_probe_readings", box.thermal_zone_id, {
            "start": "2026-09-30T00:00:00Z", "end": "2026-11-25T00:00:00Z", "group_sources": "1",
        })
        self.assertEqual(len(series["probes"]), 2)
        self.assertTrue(all(probe["points"] for probe in series["probes"]))
        self.assertTrue(series["probe_average"])
        for probe in box.thermal_zone.probes.all():
            self.assertEqual(probe.temperature_measurements.count(), 5376)
