from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.db.models import signals
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.historical_temperature_source import SOURCES
from apps.measurements.models import BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import historical_location_transitions as projection
from .historical_location_transitions import HISTORICAL_CUTOFF, project_historical_transitions
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone

SOURCE_FILE = "Suivi_2026_actualisé_v2.xlsx"
SOURCE_SHA = SOURCES[SOURCE_FILE][1]


def paris(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=MOVEMENT_WEEK_TIME_ZONE)


class HistoricalLocationTransitionTests(TestCase):
    def setUp(self):
        # The confirmed rule names this institution by its primary key.
        self.org, _created = Organization.objects.update_or_create(pk=1, defaults={"name": "Aquarium de Paris"})
        self.other = Organization.objects.create(name="Other institution")
        self.user = get_user_model().objects.create_user(username="transition_user", email="transition@example.org")
        OrganizationMembership.objects.create(user=self.user, organization=self.org, role="viewer")
        self.outsider = get_user_model().objects.create_user(username="transition_other", email="transition-other@example.org")
        OrganizationMembership.objects.create(user=self.outsider, organization=self.other, role="viewer")
        self.species = Species.objects.create(scientific_name="Transition synthetic", genus_species_code="TRS")
        self.strain = Strain.objects.create(species=self.species, code="TRS-LAB-1", organization=self.org, number=1, origin_code="LAB")
        self.zone15 = ThermalZone.objects.create(organization=self.org, name="Zone 15.0°C", target_temperature_c=Decimal("15.0"))
        self.zone10 = ThermalZone.objects.create(organization=self.org, name="Zone 10.0°C", target_temperature_c=Decimal("10.0"))
        self.zone20 = ThermalZone.objects.create(organization=self.org, name="Zone 20.0°C", target_temperature_c=Decimal("20.0"))
        self.box = self.make_box("TRS-LAB-1.001", "001")

    def make_box(self, code, number, organization=None, strain=None):
        return Box.objects.create(organization=organization or self.org, global_code=code, box_number=number,
                                  strain=strain or self.strain, status=Box.Status.INACTIVE)

    def observe(self, week, temperature, *, year=2026, box=None, evidence=True, source_box=None, source_file=SOURCE_FILE, sha=SOURCE_SHA, source_value=None):
        box = box or self.box
        monday = date.fromisocalendar(year, week, 1)
        biology = BiologicalMeasurement.objects.create(box=box, measured_on=monday, polyp_count=0, ephyrae_count=0)
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=biology, iso_year=year, iso_week=week, temperature_c=temperature)
        if evidence:
            row = HistoricalTemperatureEvidence(
                reading=reading, source_file=source_file, source_sha256=sha, sheet="Aurelia", temperature_cell=f"C{week + 2}",
                source_value=source_value or temperature, source_box=source_box or box.global_code, polyp_cell=f"F{week + 2}",
                polyp_value="0", ephyrae_cell=f"F{week + 3}", ephyrae_value="0", week_cell="F2", week_value=str(week),
                source_iso_year=year, source_iso_week=week)
            # Contradictory fixtures are the states the model guard refuses to write.
            if source_box or source_value or source_file != SOURCE_FILE:
                models.Model.save(row, force_insert=True)
            else:
                row.save()
        return reading

    def transitions(self, **kwargs):
        return project_historical_transitions(organization_id=self.org.pk, **kwargs)

    def only(self):
        rows = self.transitions()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def login(self, user=None, organization=None):
        self.client.force_login(user or self.user)
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.org).pk)}

    def zone_list(self, zone, **query):
        return self.client.get(reverse("api_thermal_zone_history", args=[zone.pk]), query, **self.login()).json()

    def zone_summary(self, zone):
        return self.client.get(reverse("api_thermal_zone_history_summary", args=[zone.pk]), **self.login()).json()

    def test_01_change_before_cutoff_is_established_without_timestamp(self):
        origin, destination = self.observe(7, "15"), self.observe(8, "10")
        row = self.only()
        self.assertEqual(row["status"], "established")
        self.assertEqual(row["from_thermal_zone"]["id"], self.zone15.pk)
        self.assertEqual(row["to_thermal_zone"]["id"], self.zone10.pk)
        self.assertEqual((row["origin_observation"]["iso_week"], row["destination_observation"]["iso_week"]), (7, 8))
        self.assertEqual(row["origin_observation"]["reading_id"], origin.pk)
        self.assertEqual(row["destination_observation"]["reading_id"], destination.pk)
        # Test 13: an observation pair bounds the change, it never dates it.
        self.assertFalse(row["physical_occurrence_known"])
        self.assertNotIn("occurred_at", row)
        self.assertTrue(row["adds_location_history"])

    def test_02_same_temperature_with_different_lexeme_is_not_a_movement(self):
        self.observe(7, "15")
        self.observe(8, "15.0")
        self.assertEqual(self.transitions(), [])

    def test_03_multiple_real_changes_on_one_box(self):
        for week, temperature in ((7, "15"), (8, "10"), (20, "10"), (21, "15")):
            self.observe(week, temperature)
        rows = self.transitions()
        self.assertEqual([(row["from_environment_c"], row["to_environment_c"]) for row in rows], [("15", "10"), ("10", "15")])
        self.assertEqual(len({row["id"] for row in rows}), 2)

    def test_04_05_06_one_transition_is_one_exit_and_one_entry_with_shared_identity(self):
        self.observe(7, "15")
        self.observe(8, "10")
        exits = self.zone_list(self.zone15, direction="departure")
        entries = self.zone_list(self.zone10, direction="arrival")
        self.assertEqual((exits["count"], entries["count"]), (1, 1))
        self.assertEqual(self.zone_list(self.zone15, direction="arrival")["count"], 0)
        self.assertEqual(self.zone_list(self.zone10, direction="departure")["count"], 0)
        self.assertEqual(self.zone_list(self.zone20)["count"], 0)
        exit_row, entry_row = exits["results"][0], entries["results"][0]
        self.assertEqual(exit_row["transition_id"], entry_row["transition_id"])
        self.assertEqual(exit_row["transition_id"], self.only()["id"])
        self.assertEqual((exit_row["box_id"], exit_row["box_code"]), (self.box.pk, self.box.global_code))
        self.assertEqual(exit_row["related_zone_id"], self.zone10.pk)
        self.assertEqual(entry_row["related_zone_id"], self.zone15.pk)
        self.assertIsNone(exit_row["occurred_at"])
        self.assertIsNone(exit_row["location_id"])
        self.assertEqual(exit_row["temporal_precision"], "observation_interval")
        self.assertEqual(exit_row["historical_interval"], {"from_iso_year": 2026, "from_iso_week": 7, "to_iso_year": 2026, "to_iso_week": 8})
        self.assertNotIn("_sort_week", exit_row)
        summary = self.zone_summary(self.zone15)
        self.assertEqual(summary["historical_counts"], {"entry_count": 0, "exit_count": 1})
        self.assertEqual([row["transition_id"] for row in summary["recent_departures"]], [exit_row["transition_id"]])
        self.assertEqual(self.zone_summary(self.zone10)["historical_counts"], {"entry_count": 1, "exit_count": 0})

    def test_21_historical_rows_stay_out_of_weekly_buckets(self):
        self.observe(39, "15")
        self.observe(40, "10")
        summary = self.zone_summary(self.zone15)
        self.assertEqual(sum(week["entry_count"] + week["exit_count"] for week in summary["weeks"]), 0)
        self.assertEqual(summary["historical_counts"]["exit_count"], 1)

    def test_pagination_counts_each_side_once_and_merges_dated_rows(self):
        self.observe(7, "15")
        self.observe(8, "10")
        BoxLocation.objects.create(box=self.make_box("TRS-LAB-1.002", "002"), thermal_zone=self.zone15, starts_at=paris(2026, 11, 2))
        payload = self.zone_list(self.zone15, limit=1)
        self.assertEqual(payload["count"], 2)
        self.assertIsNotNone(payload["results"][0]["occurred_at"])
        second = self.zone_list(self.zone15, limit=1, offset=1)
        self.assertEqual(second["count"], 2)
        self.assertIn("transition_id", second["results"][0])

    def test_07_existing_movement_absorbs_transition(self):
        self.observe(7, "15")
        self.observe(8, "10")
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone15, to_thermal_zone=self.zone10,
                                              moved_at=paris(2026, 2, 17, 9), kind=BoxMovement.Kind.ORDINARY)
        row = self.only()
        self.assertEqual(row["status"], "already_represented")
        self.assertEqual(row["representation"]["event_id"], f"movement:{movement.pk}")
        self.assertFalse(row["adds_location_history"])
        self.assertEqual(self.zone_list(self.zone15)["count"], 0)

    def test_accepted_reconstruction_absorbs_transition(self):
        self.observe(7, "15")
        self.observe(8, "10")
        origin = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 2), ends_at=paris(2026, 2, 16))
        destination = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 16))
        receipt = HistoricalBoxMovement.objects.bulk_create([HistoricalBoxMovement(
            box=self.box, iso_year=2026, iso_week=7, from_thermal_zone=self.zone15, to_thermal_zone=self.zone10,
            origin_location=origin, destination_location=destination, candidate_key="a" * 64, plan_fingerprint="b" * 64)])[0]
        row = self.only()
        self.assertEqual(row["status"], "already_represented")
        self.assertEqual(row["representation"]["event_id"], f"historical:{receipt.pk}")
        self.assertEqual(self.zone_list(self.zone15, direction="departure")["count"], 1)

    def test_location_periods_already_listed_are_not_counted_twice(self):
        self.observe(7, "15")
        self.observe(8, "10")
        origin = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 2), ends_at=paris(2026, 2, 16))
        destination = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 16))
        row = self.only()
        self.assertEqual(row["status"], "established")
        self.assertEqual(row["representation"], {"kind": "location_periods", "origin_location_id": origin.pk, "destination_location_id": destination.pk})
        self.assertFalse(row["adds_location_history"])
        departures = self.zone_list(self.zone15, direction="departure")
        self.assertEqual(departures["count"], 1)
        self.assertEqual(departures["results"][0]["location_id"], origin.pk)
        self.assertEqual(self.zone_summary(self.zone15)["historical_counts"]["exit_count"], 0)

    def test_08_competing_movements_require_review(self):
        self.observe(7, "15")
        self.observe(10, "10")
        for moved_at in (paris(2026, 2, 17, 9), paris(2026, 2, 24, 9)):
            BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone15, to_thermal_zone=self.zone10,
                                       moved_at=moved_at, kind=BoxMovement.Kind.ORDINARY)
        row = self.only()
        self.assertEqual((row["status"], row["blockers"]), ("review", ["multiple_recorded_candidates"]))
        self.assertFalse(row["adds_location_history"])

    def test_08_contradictory_movement_requires_review(self):
        self.observe(7, "15")
        self.observe(8, "10")
        BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone20, to_thermal_zone=self.zone10,
                                   moved_at=paris(2026, 2, 17, 9), kind=BoxMovement.Kind.ORDINARY)
        self.assertEqual(self.only()["blockers"], ["conflicting_recorded_movement"])

    def test_contradictory_location_period_requires_review(self):
        self.observe(7, "15")
        self.observe(8, "10")
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone20, starts_at=paris(2026, 1, 5))
        row = self.only()
        self.assertEqual((row["status"], row["blockers"]), ("review", ["location_period_conflict"]))

    def test_09_source_box_identity_unavailable_or_different(self):
        self.observe(7, "15", evidence=False)
        self.observe(8, "10")
        row = self.only()
        self.assertEqual((row["status"], row["blockers"]), ("review", ["source_box_identity_unavailable"]))
        other = self.make_box("TRS-LAB-1.003", "003")
        self.observe(7, "15", box=other, source_box="TRS-LAB-1.099")
        self.observe(8, "10", box=other)
        row = self.transitions(box_ids=[other.pk])[0]
        self.assertEqual(row["blockers"], ["source_box_identity_mismatch"])

    def test_10_invalid_or_conflicting_observations(self):
        self.observe(7, "15", source_file="unknown.xlsx")
        self.observe(8, "10")
        self.assertEqual(self.only()["blockers"], ["source_not_validated"])
        other = self.make_box("TRS-LAB-1.004", "004")
        self.observe(7, "15", box=other, source_value="20")
        self.observe(8, "10", box=other)
        self.assertEqual(self.transitions(box_ids=[other.pk])[0]["blockers"], ["observation_source_value_conflict"])
        pending = self.make_box("TRS-LAB-1.005", "005")
        first = self.observe(7, "15", box=pending)
        self.observe(8, "10", box=pending)
        # Saves are guarded; this reproduces the state left by a real invalidation.
        table = HistoricalTemperatureReading._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {table} SET association_status='pending_review' WHERE id=%s", [first.pk])
        self.assertEqual(self.transitions(box_ids=[pending.pk])[0]["blockers"], ["observation_association_not_confirmed"])

    def test_11_unique_setpoint_resolves_the_location(self):
        self.observe(7, "15.0")
        self.observe(8, "10.00")
        row = self.only()
        self.assertEqual((row["from_thermal_zone"]["name"], row["to_thermal_zone"]["name"]), ("Zone 15.0°C", "Zone 10.0°C"))

    def test_12_unavailable_or_ambiguous_mapping_keeps_environment_only(self):
        self.observe(7, "15")
        self.observe(8, "25")
        row = self.only()
        self.assertEqual((row["status"], row["blockers"]), ("environment_only", ["location_mapping_unavailable"]))
        self.assertIsNone(row["from_thermal_zone"])
        self.assertIsNone(row["to_thermal_zone"])
        self.assertEqual((row["from_environment_c"], row["to_environment_c"]), ("15", "25"))
        self.assertFalse(row["adds_location_history"])
        self.assertEqual(self.zone_list(self.zone15)["count"], 0)
        ThermalZone.objects.create(organization=self.org, name="Second 25", target_temperature_c=Decimal("25.0"))
        ThermalZone.objects.create(organization=self.org, name="Third 25", target_temperature_c=Decimal("25.0"))
        self.assertEqual(self.only()["blockers"], ["location_mapping_ambiguous"])

    def test_hidden_evidence_adds_no_zone_rows_or_historical_counts(self):
        cases = (
            ("missing_mapping", {"temperature": "25"}),
            ("missing_source", {"evidence": False}),
            ("conflicting_source", {"source_value": "20"}),
            ("unvalidated_source", {"source_file": "unknown.xlsx"}),
            ("unconfirmed", {}),
            ("missing_observation", None),
        )
        for index, (name, options) in enumerate(cases, start=10):
            with self.subTest(name=name):
                box = self.make_box(f"TRS-LAB-1.{index:03}", f"{index:03}")
                self.observe(7, "15", box=box)
                if options is not None:
                    values = {"temperature": "10", **options}
                    reading = self.observe(8, box=box, **values)
                    if name == "unconfirmed":
                        table = HistoricalTemperatureReading._meta.db_table
                        with connection.cursor() as cursor:
                            cursor.execute(f"UPDATE {table} SET association_status='pending_review' WHERE id=%s", [reading.pk])
                before = list(HistoricalTemperatureEvidence.objects.filter(
                    reading__biological_measurement__box=box).order_by("pk").values())
                payload = self.client.get(f"/api/boxes/{box.pk}/history/", **self.login()).json()
                transitions = payload["historical_transitions"]
                self.assertTrue(all(row["status"] != "established" for row in transitions))
                if options is not None:
                    self.assertEqual(len(transitions), 1)
                    self.assertTrue(transitions[0]["blockers"])
                for zone in (self.zone15, self.zone10, self.zone20):
                    self.assertEqual(self.zone_list(zone)["count"], 0)
                    summary = self.zone_summary(zone)
                    self.assertEqual(summary["historical_counts"], {"entry_count": 0, "exit_count": 0})
                    self.assertEqual(summary["recent_arrivals"], [])
                    self.assertEqual(summary["recent_departures"], [])
                self.assertEqual(before, list(HistoricalTemperatureEvidence.objects.filter(
                    reading__biological_measurement__box=box).order_by("pk").values()))

    def test_unresolved_source_never_removes_recorded_movement(self):
        self.observe(7, "15", evidence=False)
        self.observe(8, "10")
        movement = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.zone15, to_thermal_zone=self.zone10,
            moved_at=paris(2026, 2, 17, 9), kind=BoxMovement.Kind.ORDINARY)
        payload = self.client.get(f"/api/boxes/{self.box.pk}/history/", **self.login()).json()
        self.assertEqual(payload["historical_transitions"][0]["status"], "review")
        recorded = next(row for row in payload["events"] if row["id"] == f"movement:{movement.pk}")
        self.assertEqual(recorded["temporal_precision"], "exact")
        self.assertEqual(recorded["recorded_movement_id"], movement.pk)
        self.assertEqual(recorded["reconciliation_status"], "recorded")

    def test_14_nonadjacent_weeks_use_the_observation_interval(self):
        self.observe(7, "15")
        self.observe(12, "10")
        row = self.only()
        self.assertEqual(row["interval"], {"starts_on": "2026-02-09", "ends_before": "2026-03-23", "observation_week_gap": 5})

    def test_15_iso_year_boundary(self):
        self.observe(52, "15", year=2025)
        self.observe(1, "10", year=2026)
        row = self.only()
        self.assertEqual((row["origin_observation"]["iso_year"], row["destination_observation"]["iso_year"]), (2025, 2026))
        self.assertEqual(row["interval"]["observation_week_gap"], 1)

    def test_16_pair_crossing_the_cutoff_is_not_inferred(self):
        self.assertEqual(HISTORICAL_CUTOFF, date(2026, 10, 5))
        self.observe(40, "15")
        self.observe(41, "10")
        self.assertEqual(self.transitions(), [])
        self.observe(42, "20")
        self.assertEqual(self.transitions(), [])

    def test_17_recorded_movements_on_or_after_cutoff_are_untouched(self):
        self.observe(39, "15")
        self.observe(40, "10")
        BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone10, to_thermal_zone=self.zone20,
                                   moved_at=paris(2026, 10, 5, 8), kind=BoxMovement.Kind.ORDINARY)
        row = self.only()
        self.assertEqual((row["status"], row["representation"]), ("established", None))
        self.assertEqual(BoxMovement.objects.count(), 1)
        self.assertEqual(self.client.get(f"/api/boxes/{self.box.pk}/history/", **self.login()).json()["events"][0]["temporal_precision"], "exact")

    def test_18_other_organizations_are_unaffected(self):
        strain = Strain.objects.create(species=self.species, code="TRS-OTH-1", organization=self.other, number=1, origin_code="OTH")
        box = self.make_box("TRS-OTH-1.001", "001", organization=self.other, strain=strain)
        self.observe(7, "15", box=box)
        self.observe(8, "10", box=box)
        zone = ThermalZone.objects.create(organization=self.other, name="Other 15", target_temperature_c=Decimal("15.0"))
        self.assertEqual(project_historical_transitions(organization_id=self.other.pk), [])
        self.client.force_login(self.outsider)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.other.pk)}
        history = self.client.get(f"/api/boxes/{box.pk}/history/", **headers).json()
        self.assertEqual(history["historical_transitions"], [])
        listing = self.client.get(reverse("api_thermal_zone_history", args=[zone.pk]), **headers).json()
        self.assertEqual(listing["count"], 0)

    def test_19_permissions_and_isolation(self):
        self.observe(7, "15")
        self.observe(8, "10")
        self.assertIn(self.client.get(f"/api/boxes/{self.box.pk}/history/").status_code, (401, 403))
        self.client.force_login(self.outsider)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.other.pk)}
        self.assertEqual(self.client.get(f"/api/boxes/{self.box.pk}/history/", **headers).status_code, 404)
        self.assertEqual(self.client.get(reverse("api_thermal_zone_history", args=[self.zone15.pk]), **headers).status_code, 404)
        self.assertEqual(self.client.get(reverse("api_thermal_zone_history_summary", args=[self.zone15.pk]), **headers).status_code, 404)

    def test_20_projection_writes_nothing(self):
        self.observe(7, "15")
        self.observe(8, "10")
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.zone10)
        before = (Box.objects.values("id", "thermal_zone_id", "status").get(pk=self.box.pk),
                  list(BiologicalMeasurement.objects.order_by("pk").values("id", "polyp_count", "ephyrae_count", "measured_on")),
                  BoxMovement.objects.count(), BoxLocation.objects.count(), HistoricalBoxMovement.objects.count(), AuditLog.objects.count())
        domain = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone, BiologicalMeasurement,
                  HistoricalTemperatureReading, HistoricalTemperatureEvidence, AuditLog)
        def forbid(sender, **kwargs):
            if sender in domain:
                self.fail(f"Projection wrote {sender.__name__}")
        self.client.force_login(self.user)
        signals.pre_save.connect(forbid)
        self.addCleanup(signals.pre_save.disconnect, forbid)
        self.client.get(f"/api/boxes/{self.box.pk}/history/", HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.client.get(reverse("api_thermal_zone_history", args=[self.zone15.pk]), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.client.get(reverse("api_thermal_zone_history_summary", args=[self.zone15.pk]), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        signals.pre_save.disconnect(forbid)
        after = (Box.objects.values("id", "thermal_zone_id", "status").get(pk=self.box.pk),
                 list(BiologicalMeasurement.objects.order_by("pk").values("id", "polyp_count", "ephyrae_count", "measured_on")),
                 BoxMovement.objects.count(), BoxLocation.objects.count(), HistoricalBoxMovement.objects.count(), AuditLog.objects.count())
        self.assertEqual(before, after)

    def test_22_full_provenance_is_retained(self):
        self.observe(7, "15")
        self.observe(8, "10")
        row = self.only()
        source = row["origin_observation"]["sources"][0]
        self.assertEqual((source["source_file"], source["source_sha256"], source["temperature_cell"], source["source_box"]),
                         (SOURCE_FILE, SOURCE_SHA, "C9", self.box.global_code))
        self.assertEqual(row["source_box_labels"], [self.box.global_code])
        self.assertEqual(row["policy"], "aquarium_paris_weekly_environment_change_v1")

    def test_identity_is_stable_across_calls_and_scopes(self):
        self.observe(7, "15")
        self.observe(8, "10")
        self.assertEqual(self.only()["id"], self.transitions(box_ids=[self.box.pk])[0]["id"])

    def test_query_count_does_not_grow_with_boxes(self):
        self.observe(7, "15")
        self.observe(8, "10")
        with self.assertNumQueries(7):
            self.transitions()
        for number in range(6, 10):
            box = self.make_box(f"TRS-LAB-1.{number:03d}", f"{number:03d}")
            self.observe(7, "15", box=box)
            self.observe(8, "10", box=box)
        with self.assertNumQueries(7):
            self.assertEqual(len(self.transitions()), 5)

    def reference_rows(self, zone, direction=None):
        return projection.zone_sides_from_transitions(self.transitions(), zone, direction)

    def assert_matches_reference(self, *zones):
        for zone in zones:
            for direction in (None, "arrival", "departure"):
                self.assertEqual(projection.zone_history_rows(zone=zone, direction=direction),
                                 self.reference_rows(zone, direction), (zone.name, direction))

    def spy_projection(self):
        spy = patch.object(projection, "project_historical_transitions", wraps=project_historical_transitions)
        self.addCleanup(spy.stop)
        return spy.start()

    def test_a_b_entering_and_exiting_transitions_are_retained(self):
        self.observe(7, "20")
        self.observe(8, "15")
        leaving = self.make_box("TRS-LAB-1.010", "010")
        self.observe(7, "15", box=leaving)
        self.observe(8, "20", box=leaving)
        rows = projection.zone_history_rows(zone=self.zone15)
        self.assertEqual({(row["event_type"], row["box_id"]) for row in rows}, {("arrival", self.box.pk), ("departure", leaving.pk)})

    def test_c_unrelated_boxes_are_excluded_before_reconstruction(self):
        self.observe(7, "15")
        self.observe(8, "10")
        unrelated = self.make_box("TRS-LAB-1.011", "011")
        self.observe(7, "10", box=unrelated)
        self.observe(8, "20", box=unrelated)
        # Never leaving the setpoint cannot create an entry or an exit for it.
        constant = self.make_box("TRS-LAB-1.012", "012")
        self.observe(7, "15", box=constant)
        self.observe(8, "15.0", box=constant)
        spy = self.spy_projection()
        projection.zone_history_rows(zone=self.zone15)
        spy.assert_called_once_with(organization_id=self.org.pk, box_ids=[self.box.pk])

    def test_no_reconstruction_without_candidates_or_with_ambiguous_setpoint(self):
        self.observe(7, "20")
        self.observe(8, "10")
        spy = self.spy_projection()
        self.assertEqual(projection.zone_history_rows(zone=self.zone15), [])
        unset = ThermalZone.objects.create(organization=self.org, name="No setpoint")
        self.assertEqual(projection.zone_history_rows(zone=unset), [])
        twin = ThermalZone.objects.create(organization=self.org, name="Twin 20", target_temperature_c=Decimal("20.0"))
        self.assertEqual(projection.zone_history_rows(zone=twin), [])
        spy.assert_not_called()
        self.assertEqual(self.reference_rows(twin), [])

    def test_d_equivalent_lexemes_select_the_box(self):
        for index, lexeme in enumerate(("15", "15.0", "15.00", "+15", "1.5e1")):
            box = self.make_box(f"TRS-LAB-1.{20 + index:03d}", f"{20 + index:03d}")
            self.observe(7, lexeme, box=box)
            self.observe(8, "10", box=box)
        self.assertEqual(len(projection.zone_history_rows(zone=self.zone15)), 5)
        self.assert_matches_reference(self.zone15, self.zone10)

    def test_e_every_change_of_a_selected_box_is_retained(self):
        for week, temperature in ((7, "15"), (8, "10"), (20, "10"), (21, "15"), (30, "15"), (31, "20")):
            self.observe(week, temperature)
        rows = projection.zone_history_rows(zone=self.zone15)
        self.assertEqual(sorted(row["event_type"] for row in rows), ["arrival", "departure", "departure"])
        self.assertEqual(len({row["transition_id"] for row in rows}), 3)

    def test_f_unrelated_change_on_a_selected_box_stays_out(self):
        for week, temperature in ((7, "15"), (8, "10"), (9, "20")):
            self.observe(week, temperature)
        self.assertEqual(len(self.transitions()), 2)
        rows = projection.zone_history_rows(zone=self.zone15)
        self.assertEqual([(row["event_type"], row["related_zone_id"]) for row in rows], [("departure", self.zone10.pk)])
        self.assertEqual(self.zone_summary(self.zone15)["historical_counts"], {"entry_count": 0, "exit_count": 1})

    def test_g_summary_and_paginated_list_are_consistent(self):
        for number in range(30, 36):
            box = self.make_box(f"TRS-LAB-1.{number:03d}", f"{number:03d}")
            self.observe(7, "15", box=box)
            self.observe(8, "10" if number % 2 else "20", box=box)
            self.observe(9, "15", box=box)
        arrivals = self.zone_list(self.zone15, direction="arrival", limit=4)
        departures = self.zone_list(self.zone15, direction="departure", limit=4)
        summary = self.zone_summary(self.zone15)
        self.assertEqual(summary["historical_counts"], {"entry_count": arrivals["count"], "exit_count": departures["count"]})
        self.assertEqual((arrivals["count"], departures["count"]), (6, 6))
        pages = [row for offset in (0, 4) for row in self.zone_list(self.zone15, direction="arrival", limit=4, offset=offset)["results"]]
        self.assertEqual(len({row["transition_id"] for row in pages}), 6)
        self.assertEqual([row["transition_id"] for row in summary["recent_arrivals"]], [row["transition_id"] for row in pages[:3]])

    def test_h_candidates_never_cross_institutions(self):
        strain = Strain.objects.create(species=self.species, code="TRS-OTH-2", organization=self.other, number=2, origin_code="OTH")
        foreign = self.make_box("TRS-OTH-2.001", "001", organization=self.other, strain=strain)
        self.observe(7, "15", box=foreign)
        self.observe(8, "10", box=foreign)
        spy = self.spy_projection()
        self.assertEqual(projection.zone_history_rows(zone=self.zone15), [])
        spy.assert_not_called()
        other_zone = ThermalZone.objects.create(organization=self.other, name="Other setpoint", target_temperature_c=Decimal("15.0"))
        self.assertEqual(projection.zone_history_rows(zone=other_zone), [])

    def test_merged_box_is_not_a_candidate(self):
        self.observe(7, "15")
        self.observe(8, "10")
        survivor = self.make_box("TRS-LAB-1.040", "040")
        Box.objects.filter(pk=self.box.pk).update(merged_into=survivor)
        self.assertEqual(projection.zone_history_rows(zone=self.zone15), [])

    def test_i_filtered_rows_match_the_unfiltered_reference(self):
        unmapped = self.make_box("TRS-LAB-1.050", "050")
        for week, value in ((7, "15"), (8, "35")):
            self.observe(week, value, box=unmapped)
        unknown_source = self.make_box("TRS-LAB-1.051", "051")
        self.observe(7, "15", box=unknown_source, source_file="unknown.xlsx")
        self.observe(8, "10", box=unknown_source)
        no_evidence = self.make_box("TRS-LAB-1.052", "052")
        self.observe(7, "15", box=no_evidence, evidence=False)
        self.observe(8, "20", box=no_evidence)
        crossing = self.make_box("TRS-LAB-1.053", "053")
        self.observe(40, "15", box=crossing)
        self.observe(41, "10", box=crossing)
        represented = self.make_box("TRS-LAB-1.054", "054")
        self.observe(7, "15.0", box=represented)
        self.observe(8, "10", box=represented)
        BoxMovement.objects.create(box=represented, from_thermal_zone=self.zone15, to_thermal_zone=self.zone10,
                                   moved_at=paris(2026, 2, 17, 9), kind=BoxMovement.Kind.ORDINARY)
        periods = self.make_box("TRS-LAB-1.055", "055")
        self.observe(7, "15", box=periods)
        self.observe(8, "10", box=periods)
        BoxLocation.objects.create(box=periods, thermal_zone=self.zone15, starts_at=paris(2026, 2, 2), ends_at=paris(2026, 2, 16))
        BoxLocation.objects.create(box=periods, thermal_zone=self.zone10, starts_at=paris(2026, 2, 16))
        conflicting = self.make_box("TRS-LAB-1.056", "056")
        self.observe(7, "15", box=conflicting)
        self.observe(8, "10", box=conflicting)
        BoxLocation.objects.create(box=conflicting, thermal_zone=self.zone20, starts_at=paris(2026, 1, 5))
        for week, value in ((7, "15"), (8, "10"), (20, "10.0"), (21, "1.5e1"), (30, "15"), (31, "20")):
            self.observe(week, value)
        for number, (first, second) in enumerate((("20", "10"), ("10", "20"), ("15", "20"), ("20", "15"))):
            box = self.make_box(f"TRS-LAB-1.{60 + number:03d}", f"{60 + number:03d}")
            self.observe(52, first, box=box, year=2025)
            self.observe(3, second, box=box)
        reference_sides = sum(len(self.reference_rows(zone)) for zone in (self.zone15, self.zone10, self.zone20))
        self.assertEqual(reference_sides, 2 * sum(row["adds_location_history"] for row in self.transitions()))
        self.assertGreater(reference_sides, 0)
        self.assert_matches_reference(self.zone15, self.zone10, self.zone20)

    def test_j_processing_is_bounded_by_relevant_boxes(self):
        self.observe(7, "15")
        self.observe(8, "10")
        with patch.object(projection, "_observations", wraps=projection._observations) as small:
            with CaptureQueriesContext(connection) as small_queries:
                self.assertEqual(len(projection.zone_history_rows(zone=self.zone15)), 1)
        for number in range(100, 140):
            box = self.make_box(f"TRS-LAB-1.{number:03d}", f"{number:03d}")
            self.observe(7, "10", box=box)
            self.observe(8, "20", box=box)
        with patch.object(projection, "_observations", wraps=projection._observations) as large:
            with CaptureQueriesContext(connection) as large_queries:
                self.assertEqual(len(projection.zone_history_rows(zone=self.zone15)), 1)
        self.assertEqual((small.call_count, large.call_count), (1, 1))
        self.assertEqual(len(large.call_args.args[0]), 2)
        self.assertEqual(len(large_queries), len(small_queries))
        self.assertEqual(len(large_queries), 10)

    def test_box_history_links_only_exact_period_candidates(self):
        self.observe(7, "15")
        self.observe(8, "10")
        origin = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 2), ends_at=paris(2026, 2, 16))
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 16))
        payload = self.client.get(f"/api/boxes/{self.box.pk}/history/", **self.login()).json()
        rows = [*payload["candidates"], *payload["assessments"]]
        expected = [row["id"] for row in rows if row["evidence"].get("origin_location_id") == origin.pk]
        self.assertEqual(len(expected), 1)
        self.assertEqual(payload["historical_transitions"][0]["planner_candidate_ids"], expected)

    def test_gap_in_time_is_not_an_invented_instant(self):
        self.observe(7, "15")
        self.observe(8, "10")
        self.assertNotIn("T", self.only()["interval"]["starts_on"])
        self.assertEqual(date.fromisoformat(self.only()["interval"]["ends_before"]) - date.fromisoformat(self.only()["interval"]["starts_on"]), timedelta(days=14))
