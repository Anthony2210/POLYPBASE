from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.db.models import signals
from django.test import SimpleTestCase, TestCase
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
from .services import qualify_pending_box

SOURCE_FILE = "Suivi_2026_actualisé_v2.xlsx"
SOURCE_SHA = SOURCES[SOURCE_FILE][1]


def paris(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=MOVEMENT_WEEK_TIME_ZONE)


class StartingObservationSelectionTests(SimpleTestCase):
    """Synthetic groups exercise ambiguity without relaxing canonical DB uniqueness."""

    def setUp(self):
        self.readings, self.evidences = [], {}

    def observe(self, reading_id, temperature, *, week=7, year=2026, day=1, exact=False):
        monday = date.fromisocalendar(year, week, 1)
        reading = {
            "id": reading_id, "biological_measurement_id": reading_id,
            "association_status": "confirmed", "iso_year": year, "iso_week": week, "temperature_c": temperature,
            "biological_measurement__box_id": 1, "biological_measurement__week_start": monday,
            "biological_measurement__measured_on": monday + timedelta(days=day - 1),
            "biological_measurement__duplicate_of_id": None, "biological_measurement__environment_source_box_id": None,
            "biological_measurement__temporal_precision": (
                BiologicalMeasurement.TemporalPrecision.EXACT_DATE if exact
                else BiologicalMeasurement.TemporalPrecision.LEGACY_UNVERIFIED),
        }
        self.readings.append(reading)
        self.evidences[reading_id] = [{
            "id": reading_id, "source_file": SOURCE_FILE, "source_sha256": SOURCE_SHA,
            "source_box": "TRS-LAB-1.001", "source_value": temperature,
            "source_iso_year": year, "source_iso_week": week,
        }]
        return reading

    def select(self, *, approved_evidence_ids=frozenset()):
        return projection._starting_observation(self.readings, self.evidences, "TRS-LAB-1.001",
                                                approved_evidence_ids=approved_evidence_ids)

    def test_consistent_week_lexemes_form_one_anchor_independent_of_pk(self):
        self.observe(90, "15", day=6)
        self.observe(2, "15.0", day=1)
        self.observe(5, "1.5e1", day=3)
        self.observe(1, "10", week=8)
        for readings in (self.readings[:], list(reversed(self.readings))):
            self.readings = readings
            selected = self.select()
            self.assertEqual((selected["iso_week"], projection._temperature(selected["temperature_c"])), (7, Decimal("15")))
            self.assertEqual(selected["biological_measurement__temporal_precision"], "legacy_unverified")

    def test_conflicting_first_week_never_chooses_pk_or_substitutes_later_week(self):
        self.observe(90, "15", day=1)
        self.observe(2, "10", day=6)
        self.observe(1, "20", week=8, exact=True)
        for readings in (self.readings[:], list(reversed(self.readings))):
            self.readings = readings
            self.assertIsNone(self.select())
        first = next(reading for reading in self.readings if reading["temperature_c"] == "15")
        second = next(reading for reading in self.readings if reading["temperature_c"] == "10")
        first["id"], second["id"] = 2, 90
        self.evidences[2], self.evidences[90] = self.evidences[90], self.evidences[2]
        self.assertIsNone(self.select())

    def test_all_exact_dates_use_chronology_not_pk(self):
        self.observe(1, "10", day=6, exact=True)
        first = self.observe(90, "15", day=2, exact=True)
        self.observe(2, "20", week=8, exact=True)
        self.assertIs(self.select(), first)
        self.readings.reverse()
        self.assertIs(self.select(), first)

    def test_conflicting_same_earliest_exact_day_is_ambiguous(self):
        self.observe(90, "15", day=2, exact=True)
        self.observe(1, "10", day=2, exact=True)
        self.observe(2, "20", day=6, exact=True)
        self.observe(3, "15", week=8, exact=True)
        self.assertIsNone(self.select())
        self.readings.reverse()
        self.assertIsNone(self.select())

    def test_consistent_earliest_exact_day_survives_later_day_conflicts(self):
        first = self.observe(90, "15", day=2, exact=True)
        self.observe(1, "1.5e1", day=2, exact=True)
        self.observe(2, "10", day=6, exact=True)
        self.observe(3, "20", day=6, exact=True)
        self.assertIs(self.select(), first)

    def test_mixed_precision_different_setpoints_cannot_establish_precedence(self):
        week_only = self.observe(90, "15", day=6)
        exact = self.observe(1, "10", day=2, exact=True)
        self.observe(2, "20", week=8, exact=True)
        self.assertIsNone(self.select())
        week_only["biological_measurement__measured_on"], exact["biological_measurement__measured_on"] = (
            exact["biological_measurement__measured_on"], week_only["biological_measurement__measured_on"])
        self.readings.reverse()
        self.assertIsNone(self.select())

    def test_mixed_precision_same_setpoint_retains_week_precision(self):
        self.observe(1, "15.0", day=2, exact=True)
        week_only = self.observe(90, "1.5e1", day=6)
        self.assertIs(self.select(), week_only)
        self.readings.reverse()
        self.assertIs(self.select(), week_only)

    def test_zero_is_an_admissible_setpoint_not_missing_data(self):
        first = self.observe(90, "0", exact=True)
        self.observe(1, "0.0", exact=True)
        self.observe(2, "15", week=8)
        self.assertIs(self.select(), first)
        self.assertEqual(projection._temperature(self.select()["temperature_c"]), Decimal("0"))

    def test_first_group_is_based_on_individual_admissibility(self):
        for target, field, value in (
            ("reading", "association_status", "pending_review"),
            ("reading", "temperature_c", "invalid"),
            ("reading", "biological_measurement__duplicate_of_id", 99),
            ("reading", "biological_measurement__environment_source_box_id", 99),
            ("reading", "biological_measurement__week_start", date(2026, 2, 2)),
            ("evidence", "source_file", "unknown.xlsx"),
            ("evidence", "source_sha256", "0" * 64),
            ("evidence", "source_box", "TRS-LAB-1.099"),
            ("evidence", "source_value", "20"),
            ("evidence", "source_iso_week", 6),
        ):
            with self.subTest(target=target, field=field):
                self.readings, self.evidences = [], {}
                invalid = self.observe(1, "15")
                row = invalid if target == "reading" else self.evidences[1][0]
                row[field] = value
                first = self.observe(90, "10", week=8)
                self.observe(2, "20", week=9)
                self.assertIs(self.select(), first)
        self.evidences[90] = []
        self.assertEqual(self.select()["iso_week"], 9)

    def test_invalid_week_only_source_does_not_poison_valid_exact_chronology(self):
        self.observe(1, "20")
        self.evidences[1][0]["source_box"] = "TRS-LAB-1.099"
        first = self.observe(90, "15", day=2, exact=True)
        self.observe(2, "10", day=6, exact=True)
        self.assertIs(self.select(), first)

    def test_invalid_exact_source_cannot_waive_valid_week_conflicts(self):
        self.observe(90, "15")
        self.observe(2, "10")
        self.observe(1, "20", exact=True)
        self.evidences[1][0]["source_file"] = "unknown.xlsx"
        self.observe(3, "20", week=8)
        self.assertIsNone(self.select())

    def test_approved_identity_participates_in_conflict_without_bypassing_other_guards(self):
        self.observe(90, "15")
        other = self.observe(1, "10")
        self.evidences[1][0]["source_box"] = "CLA-JKA-1.09"
        self.assertEqual(self.select()["temperature_c"], "15")
        self.assertIsNone(self.select(approved_evidence_ids={1}))
        self.evidences[1][0]["source_sha256"] = "0" * 64
        self.assertEqual(self.select(approved_evidence_ids={1})["temperature_c"], "15")
        self.evidences[1][0]["source_sha256"] = SOURCE_SHA
        other["biological_measurement__duplicate_of_id"] = 99
        self.assertEqual(self.select(approved_evidence_ids={1})["temperature_c"], "15")

    def test_default_transition_observations_retain_week_conflicts_and_identity_guard(self):
        self.observe(90, "15", day=2, exact=True)
        self.observe(1, "10", day=6, exact=True)
        self.assertIsNotNone(self.select())
        observations = projection._observations(self.readings, self.evidences, "TRS-LAB-1.001")
        self.assertEqual([row["reading"]["id"] for row in observations], [1, 90])
        self.assertTrue(all("observations_conflict_in_week" in row["blockers"] for row in observations))
        self.evidences[90][0]["source_box"] = "CLA-JKA-1.09"
        observations = projection._observations(self.readings, self.evidences, "TRS-LAB-1.001")
        self.assertIn("source_box_identity_mismatch", observations[1]["blockers"])

    def test_temporal_group_uses_iso_year_and_excludes_cutoff(self):
        self.observe(1, "10", week=2)
        first = self.observe(90, "15", week=1, day=3)
        self.assertEqual(first["biological_measurement__measured_on"], date(2025, 12, 31))
        self.assertIs(self.select(), first)
        self.readings, self.evidences = [], {}
        self.observe(1, "15", week=41)
        self.assertIsNone(self.select())


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

    def box_history(self):
        response = self.client.get(f"/api/boxes/{self.box.pk}/history/", **self.login())
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def observe_exact_day(self, *args, **kwargs):
        reading = self.observe(*args, **kwargs)
        BiologicalMeasurement.objects.filter(pk=reading.biological_measurement_id).update(
            temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE,
        )
        return reading

    def test_starting_api_uses_earliest_eligible_zero_week_not_pk_and_preserves_records(self):
        self.observe(9, "20")
        first = self.observe(8, "0")
        self.observe(7, "15", source_file="unknown.xlsx")
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 1, 14), polyp_count=0)
        zero = ThermalZone.objects.create(organization=self.org, name="Zero setpoint", target_temperature_c=Decimal("0"))
        ThermalZone.objects.create(organization=self.other, name="Foreign zero", target_temperature_c=Decimal("0"))
        sibling = self.make_box("TRS-LAB-1.002", "002")
        self.observe(2, "15", box=sibling)
        domain = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone, BiologicalMeasurement,
                  HistoricalTemperatureReading, HistoricalTemperatureEvidence, AuditLog)
        before = [list(model.objects.order_by("pk").values()) for model in domain]
        for _ in range(2):
            self.assertEqual(self.box_history()["starting_location"], {
                "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 8,
                "thermal_zone": {"id": zero.pk, "name": zero.name}, "represented_movement_id": None,
            })
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in domain])
        first.biological_measurement.refresh_from_db()
        self.assertEqual((first.biological_measurement.polyp_count, first.biological_measurement.ephyrae_count), (0, 0))
        competing = ThermalZone.objects.create(organization=self.org, name="Another zero", target_temperature_c=Decimal("0"))
        self.assertIsNone(self.box_history()["starting_location"])
        ThermalZone.objects.filter(pk__in=[zero.pk, competing.pk]).update(target_temperature_c=None)
        self.assertIsNone(self.box_history()["starting_location"])

    def test_starting_api_temporal_groups_preserve_precision_transitions_and_snapshots(self):
        first = self.observe(7, "15")
        self.observe_exact_day(8, "10")
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 1))
        BoxMovement.objects.create(
            box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone10,
            destination_location=period, moved_at=period.starts_at,
        )
        selector = projection._starting_observation
        domain = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone, BiologicalMeasurement,
                  HistoricalTemperatureReading, HistoricalTemperatureEvidence, AuditLog)
        # Competing canonical rows cannot be persisted in one Box/week. Inject only
        # the pure selector's input, leaving the API, transitions and DB guards real.
        for exact, competing_exact, temperature, day, expected_precision in (
            (False, False, "1.5e1", 5, "iso_week"),
            (False, False, "10", 5, None),
            (True, True, "10", 5, "exact"),
            (True, True, "15.0", 3, "exact"),
            (True, True, "10", 3, None),
            (True, False, "15.0", 5, "iso_week"),
            (True, False, "10", 5, None),
            (False, True, "10", 1, None),
        ):
            precision = (BiologicalMeasurement.TemporalPrecision.EXACT_DATE if exact
                         else BiologicalMeasurement.TemporalPrecision.LEGACY_UNVERIFIED)
            BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(
                measured_on=date(2026, 2, 11), temporal_precision=precision,
            )
            reference = self.box_history()
            before = [list(model.objects.order_by("pk").values()) for model in domain]
            for competing_id in (-first.pk, first.pk + 10000):
                with self.subTest(exact=exact, competing_exact=competing_exact, temperature=temperature,
                                  day=day, competing_id=competing_id):
                    def select_group(readings, evidences, global_code, **kwargs):
                        original = next(reading for reading in readings if reading["id"] == first.pk)
                        competing = {**original, "id": competing_id, "temperature_c": temperature,
                                     "biological_measurement__measured_on": date(2026, 2, 8 + day),
                                     "biological_measurement__temporal_precision": (
                                         BiologicalMeasurement.TemporalPrecision.EXACT_DATE if competing_exact
                                         else BiologicalMeasurement.TemporalPrecision.LEGACY_UNVERIFIED)}
                        sources = [{**evidence, "id": evidence["id"] + 10000, "source_value": temperature}
                                   for evidence in evidences[first.pk]]
                        return selector([*readings, competing], {**evidences, competing_id: sources}, global_code, **kwargs)

                    expected = None
                    if expected_precision:
                        expected = {"observed_on": "2026-02-11", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                                    "represented_movement_id": None}
                        if expected_precision == "iso_week":
                            expected.update(observed_on=None, temporal_precision="iso_week", iso_year=2026, iso_week=7)
                    with patch.object(projection, "_starting_observation", side_effect=select_group) as select:
                        history = self.box_history()
                        select.assert_called_once()
                    self.assertEqual(history["starting_location"], expected)
                    self.assertEqual(history["events"], reference["events"])
                    self.assertEqual(history["historical_transitions"], reference["historical_transitions"])
                    self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in domain])

    def test_starting_location_precedes_first_transition_after_stable_weeks(self):
        first = self.observe_exact_day(3, "15")
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(measured_on=date(2026, 1, 14))
        self.observe(4, "15.0")
        self.observe(7, "15")
        self.observe(8, "10")
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.zone10)
        history = self.box_history()
        self.assertEqual(history["starting_location"], {
            "observed_on": "2026-01-14", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
            "represented_movement_id": None,
        })
        self.assertEqual(len(history["historical_transitions"]), 1)
        self.assertEqual(history["historical_transitions"][0]["origin_observation"]["iso_week"], 7)
        self.assertFalse(any(e["kind"] == "initial_location" for e in history["events"]))

    def test_starting_location_needs_no_transition_or_second_reading(self):
        self.assertIsNone(self.box_history()["starting_location"])
        self.observe_exact_day(7, "15")
        history = self.box_history()
        self.assertEqual(history["historical_transitions"], [])
        self.assertEqual(history["starting_location"], {
            "observed_on": "2026-02-09", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
            "represented_movement_id": None,
        })

    def test_starting_location_represents_only_explicit_initial_period_movement(self):
        self.observe_exact_day(7, "15")
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 8, 12))
        movement = BoxMovement.objects.create(
            box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, from_thermal_zone=None,
            to_thermal_zone=self.zone15, destination_location=period, moved_at=period.starts_at,
        )
        models_to_check = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, BiologicalMeasurement,
                           HistoricalTemperatureReading, HistoricalTemperatureEvidence, AuditLog)
        before = [list(model.objects.order_by("pk").values()) for model in models_to_check]
        for _ in range(2):
            history = self.box_history()
            self.assertEqual(history["starting_location"], {
                "observed_on": "2026-02-09", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                "represented_movement_id": movement.pk,
            })
            self.assertTrue(any(row["recorded_movement_id"] == movement.pk for row in history["events"]))
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in models_to_check])

    def test_starting_period_fallback_can_represent_explicit_initial_movement(self):
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 2, 11), polyp_count=0,
            temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE,
        )
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 10))
        movement = BoxMovement.objects.create(
            box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone15,
            destination_location=period, moved_at=period.starts_at,
        )
        self.assertEqual(self.box_history()["starting_location"], {
            "observed_on": "2026-02-11", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
            "represented_movement_id": movement.pk,
        })

    def test_historical_starting_location_never_represents_later_initial_assignment(self):
        self.observe_exact_day(7, "15")
        for at in (paris(2026, 2, 9, 12), paris(2026, 2, 12)):
            with self.subTest(at=at):
                period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=at)
                movement = BoxMovement.objects.create(
                    box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone15,
                    destination_location=period, moved_at=at,
                )
                history = self.box_history()
                self.assertEqual(history["starting_location"], {
                    "observed_on": "2026-02-09", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                    "represented_movement_id": None,
                })
                self.assertTrue(any(row["recorded_movement_id"] == movement.pk for row in history["events"]))
                movement.delete()
                period.delete()

    def test_starting_movement_representation_requires_every_explicit_link(self):
        self.observe_exact_day(7, "15")
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 8))
        later = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 12))
        other_box = self.make_box("TRS-LAB-1.002", "002")
        original = {"box": self.box, "kind": BoxMovement.Kind.INITIAL_LOCATION, "from_thermal_zone": None,
                    "to_thermal_zone": self.zone15, "destination_location": period, "moved_at": period.starts_at}
        cases = [
            {"destination_location": None}, {"destination_location": later},
            {"moved_at": period.starts_at + timedelta(hours=1)}, {"to_thermal_zone": self.zone10},
            {"from_thermal_zone": self.zone10}, {"box": other_box},
            *({"kind": kind} for kind in BoxMovement.Kind.values if kind != BoxMovement.Kind.INITIAL_LOCATION),
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                movement = BoxMovement.objects.create(**{**original, **changes})
                self.assertIsNone(self.box_history()["starting_location"]["represented_movement_id"])
                movement.delete()

    def test_ambiguous_starting_initial_period_pairs_are_not_represented(self):
        self.observe_exact_day(7, "15")
        for _ in range(2):
            period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 8))
            BoxMovement.objects.create(
                box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone15,
                destination_location=period, moved_at=period.starts_at,
            )
        self.assertIsNone(self.box_history()["starting_location"]["represented_movement_id"])

    def test_ambiguous_or_unknown_starting_period_never_represents_movement(self):
        self.observe_exact_day(7, "15")
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 8))
        BoxMovement.objects.create(
            box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone15,
            destination_location=period, moved_at=period.starts_at,
        )
        overlap = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 8))
        self.assertIsNone(self.box_history()["starting_location"]["represented_movement_id"])
        overlap.delete()
        BoxLocation.objects.filter(pk=period.pk).update(end_date_unknown=True)
        self.assertIsNone(self.box_history()["starting_location"]["represented_movement_id"])

    def test_starting_location_skips_biology_without_temperature(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 1, 14), polyp_count=0)
        self.observe(7, "15")
        self.observe(8, "10")
        self.assertEqual(self.box_history()["starting_location"], {
            "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 7,
            "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name}, "represented_movement_id": None,
        })

    def test_invalid_temperature_proof_is_skipped_for_earliest_eligible_reading(self):
        for invalid in ({"evidence": False}, {"source_box": "TRS-LAB-1.099"},
                        {"source_file": "unknown.xlsx"}, {"source_value": "20"}):
            with self.subTest(invalid=invalid):
                self.box = self.make_box(f"TRS-LAB-1.{100 + Box.objects.count():03d}", str(100 + Box.objects.count()))
                self.observe_exact_day(7, "15", **invalid)
                self.observe_exact_day(8, "10")
                self.observe_exact_day(9, "20")
                self.assertEqual(self.box_history()["starting_location"], {
                    "observed_on": "2026-02-16", "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name},
                    "represented_movement_id": None,
                })

    def test_unconfirmed_invalid_or_inconsistent_first_reading_is_not_proof(self):
        for field, value in (("association_status", "pending_review"), ("temperature_c", "invalid"),
                             ("iso_week", 6)):
            with self.subTest(field=field):
                self.box = self.make_box(f"TRS-LAB-1.{100 + Box.objects.count():03d}", str(100 + Box.objects.count()))
                reading = self.observe_exact_day(7, "15")
                self.observe(8, "10")
                # Reproduce invalid stored evidence without invoking immutable model saves.
                table = HistoricalTemperatureReading._meta.db_table
                with connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {table} SET {field}=%s WHERE id=%s", [value, reading.pk])
                self.assertEqual(self.box_history()["starting_location"], {
                    "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 8,
                    "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name}, "represented_movement_id": None,
                })

    def test_ambiguous_first_temperature_mapping_does_not_use_later_zone(self):
        self.observe_exact_day(4, "20", evidence=False)
        self.observe_exact_day(7, "15")
        self.observe(8, "10")
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 1, 1))
        ThermalZone.objects.create(organization=self.org, name="Another 15", target_temperature_c=Decimal("15"))
        self.assertIsNone(self.box_history()["starting_location"])
        self.zone15.target_temperature_c = None
        self.zone15.save()
        ThermalZone.objects.filter(organization=self.org, name="Another 15").update(target_temperature_c=None)
        self.assertIsNone(self.box_history()["starting_location"])

    def test_first_observation_ignores_duplicate_measurement_evidence(self):
        first = self.observe_exact_day(7, "15")
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(measured_on=date(2026, 2, 11))
        duplicate = BiologicalMeasurement(
            box=self.box, measured_on=date(2026, 2, 9), week_start=date(2026, 2, 9),
            duplicate_of_id=first.biological_measurement_id, polyp_count=0,
        )
        models.Model.save(duplicate, force_insert=True)
        duplicate_reading = HistoricalTemperatureReading(
            biological_measurement=duplicate, iso_year=2026, iso_week=7, temperature_c="20",
        )
        models.Model.save(duplicate_reading, force_insert=True)
        evidence = first.evidences.get()
        evidence.pk = None
        evidence.reading = duplicate_reading
        evidence.source_value = "20"
        models.Model.save(evidence, force_insert=True)
        self.assertEqual(self.box_history()["starting_location"], {
            "observed_on": "2026-02-11", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
            "represented_movement_id": None,
        })

    def test_first_observation_with_foreign_environment_is_not_location_proof(self):
        first = self.observe_exact_day(7, "15")
        self.observe(8, "10")
        foreign_strain = Strain.objects.create(species=self.species, code="TRS-OTH-1", organization=self.other, number=1, origin_code="OTH")
        foreign_box = self.make_box("TRS-OTH-1.001", "001", organization=self.other, strain=foreign_strain)
        for source in (self.make_box("TRS-LAB-1.002", "002"), foreign_box):
            with self.subTest(source=source.pk):
                BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(environment_source_box=source)
                self.assertEqual(self.box_history()["starting_location"], {
                    "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 8,
                    "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name}, "represented_movement_id": None,
                })
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(environment_source_box=self.box)
        self.assertEqual(self.box_history()["starting_location"]["thermal_zone"]["id"], self.zone15.pk)

    def test_starting_temperature_rule_respects_institution_and_cutoff(self):
        self.observe_exact_day(41, "15")
        self.observe(42, "10")
        self.assertIsNone(self.box_history()["starting_location"])
        foreign_strain = Strain.objects.create(species=self.species, code="TRS-OTH-1", organization=self.other, number=1, origin_code="OTH")
        foreign_box = self.make_box("TRS-OTH-1.001", "001", organization=self.other, strain=foreign_strain)
        self.observe_exact_day(7, "15", box=foreign_box)
        history = self.client.get(f"/api/boxes/{foreign_box.pk}/history/", **self.login(self.outsider, self.other)).json()
        self.assertIsNone(history["starting_location"])

    def test_period_fallback_requires_one_period_covering_first_whole_day(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 2, 11), polyp_count=0,
                                             temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE)
        for start, end, unknown, expected in (
            (paris(2026, 2, 11), paris(2026, 2, 12), False, True),
            (paris(2026, 2, 10), None, False, True),
            (paris(2026, 2, 11, 12), None, False, False),
            (paris(2026, 2, 10), paris(2026, 2, 11, 12), False, False),
            (paris(2026, 2, 12), None, False, False),
            (paris(2026, 2, 10), None, True, False),
        ):
            with self.subTest(start=start, end=end, unknown=unknown):
                period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=start, ends_at=end, end_date_unknown=unknown)
                result = self.box_history()["starting_location"]
                self.assertEqual(result, {"observed_on": "2026-02-11", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                                          "represented_movement_id": None} if expected else None)
                period.delete()

    def test_period_fallback_rejects_overlaps_and_foreign_zones(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 2, 11), polyp_count=0,
                                             temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE)
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 10))
        overlap = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 11, 12))
        self.assertIsNone(self.box_history()["starting_location"])
        overlap.delete()
        foreign_zone = ThermalZone.objects.create(organization=self.other, name="Private foreign zone")
        BoxLocation.objects.filter(pk=period.pk).update(thermal_zone=foreign_zone)
        self.assertIsNone(self.box_history()["starting_location"])

    def test_current_zone_and_later_qualification_never_prove_first_location(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 2, 11), polyp_count=0,
                                             temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE)
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.zone10)
        BoxMovement.objects.create(box=self.box, to_thermal_zone=self.zone10, moved_at=paris(2026, 2, 12), kind=BoxMovement.Kind.QUALIFICATION)
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 12))
        self.assertIsNone(self.box_history()["starting_location"])

    def test_legacy_starting_location_uses_source_week_without_certifying_stored_day(self):
        first = self.observe(7, "15")
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(measured_on=date(2026, 2, 11))
        self.observe_exact_day(8, "10")
        expected = {
            "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 7,
            "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name}, "represented_movement_id": None,
        }
        self.assertEqual(self.box_history()["starting_location"], expected)
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 1))
        BoxMovement.objects.create(
            box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone15,
            destination_location=period, moved_at=period.starts_at,
        )
        self.assertEqual(self.box_history()["starting_location"], expected)
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(
            temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE,
        )
        self.assertEqual(self.box_history()["starting_location"]["observed_on"], "2026-02-11")

    def test_starting_location_setpoint_overrides_conflicting_period(self):
        self.observe_exact_day(7, "15")
        self.observe_exact_day(8, "10")
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 1))
        movement = BoxMovement.objects.create(
            box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone10,
            destination_location=period, moved_at=period.starts_at,
        )
        expected = {
            "observed_on": "2026-02-09", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
            "represented_movement_id": None,
        }
        self.assertEqual(self.box_history()["starting_location"], expected)
        BoxLocation.objects.filter(pk=period.pk).update(thermal_zone=self.zone15)
        self.assertEqual(self.box_history()["starting_location"], expected)
        BoxMovement.objects.filter(pk=movement.pk).update(to_thermal_zone=self.zone15)
        self.assertEqual(self.box_history()["starting_location"], {**expected, "represented_movement_id": movement.pk})

    def test_starting_location_preserves_first_exact_observation_and_all_records(self):
        self.observe_exact_day(3, "15")
        self.observe_exact_day(4, "15")
        self.observe_exact_day(7, "15")
        self.observe_exact_day(8, "10")
        models_to_check = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, BiologicalMeasurement,
                           HistoricalTemperatureReading, HistoricalTemperatureEvidence, AuditLog)
        before = [list(model.objects.order_by("pk").values()) for model in models_to_check]
        for _ in range(2):
            history = self.box_history()
            self.assertEqual(history["starting_location"], {
                "observed_on": "2026-01-12", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                "represented_movement_id": None,
            })
            self.assertEqual(history["historical_transitions"][0]["origin_observation"]["iso_week"], 7)
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in models_to_check])

    def test_legacy_starting_week_survives_unlocated_qualification(self):
        self.observe(7, "15")
        self.observe_exact_day(8, "10")
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.PENDING_REVIEW)
        qualify_pending_box(box=self.box, user=self.user, target_status=Box.Status.ACTIVE)
        history = self.box_history()
        self.assertEqual(history["starting_location"], {
            "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 7,
            "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name}, "represented_movement_id": None,
        })
        self.assertFalse(self.box.locations.exists())
        self.assertFalse(self.box.movements.exists())
        event = next(row for row in history["events"] if row["provenance"] == "lifecycle_audit")
        self.assertEqual(event["kind"], "qualification")
        self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertIsNone(event["to_thermal_zone"])

    def test_legacy_earliest_eligible_mapping_gap_never_uses_later_reading_or_period(self):
        self.observe(4, "20", evidence=False)
        first = self.observe(7, "15")
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(measured_on=date(2026, 2, 11))
        self.observe_exact_day(8, "10")
        period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 1, 1))
        BoxMovement.objects.create(
            box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone10,
            destination_location=period, moved_at=period.starts_at,
        )
        competing = ThermalZone.objects.create(organization=self.org, name="Another 15", target_temperature_c=Decimal("15"))
        self.assertIsNone(self.box_history()["starting_location"])
        ThermalZone.objects.filter(pk__in=[competing.pk, self.zone15.pk]).update(target_temperature_c=None)
        self.assertIsNone(self.box_history()["starting_location"])

    def test_invalid_historical_source_never_falls_back_to_period(self):
        for invalid in ({"evidence": False}, {"source_box": "TRS-LAB-1.099"},
                        {"source_file": "unknown.xlsx"}, {"sha": "0" * 64}, {"source_value": "20"}):
            with self.subTest(invalid=invalid):
                self.box = self.make_box(f"TRS-LAB-1.{100 + Box.objects.count():03d}", str(100 + Box.objects.count()))
                self.observe_exact_day(7, "15", **invalid)
                BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 1))
                self.assertIsNone(self.box_history()["starting_location"])

    def test_ineligible_historical_association_never_falls_back_to_period(self):
        other_box = self.make_box("TRS-LAB-1.002", "002")
        for field, value in (("association_status", "pending_review"), ("temperature_c", "invalid"), ("iso_week", 6)):
            with self.subTest(field=field):
                self.box = self.make_box(f"TRS-LAB-1.{100 + Box.objects.count():03d}", str(100 + Box.objects.count()))
                reading = self.observe_exact_day(7, "15")
                BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 1))
                with connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {HistoricalTemperatureReading._meta.db_table} SET {field}=%s WHERE id=%s", [value, reading.pk])
                self.assertIsNone(self.box_history()["starting_location"])
        self.box = other_box
        reading = self.observe_exact_day(7, "15")
        BiologicalMeasurement.objects.filter(pk=reading.biological_measurement_id).update(environment_source_box=self.make_box("TRS-LAB-1.003", "003"))
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 1))
        self.assertIsNone(self.box_history()["starting_location"])

    def test_inconsistent_historical_dates_never_fall_back_to_period(self):
        for week_start in (date(2026, 2, 9), date(2026, 10, 5)):
            with self.subTest(week_start=week_start):
                self.box = self.make_box(f"TRS-LAB-1.{100 + Box.objects.count():03d}", str(100 + Box.objects.count()))
                first = self.observe_exact_day(7, "15")
                BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(
                    measured_on=date(2026, 10, 7), week_start=week_start,
                )
                BoxLocation.objects.create(box=self.box, thermal_zone=self.zone20, starts_at=paris(2026, 1, 1))
                self.assertIsNone(self.box_history()["starting_location"])
                self.observe_exact_day(8, "10")
                self.assertEqual(self.box_history()["starting_location"], {
                    "observed_on": "2026-02-16", "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name},
                    "represented_movement_id": None,
                })

    def test_source_week_or_digest_mismatch_is_skipped_not_used_as_start(self):
        for field, value in (("source_iso_week", 6), ("source_iso_year", 2025), ("source_sha256", "0" * 64)):
            with self.subTest(field=field):
                self.box = self.make_box(f"TRS-LAB-1.{100 + Box.objects.count():03d}", str(100 + Box.objects.count()))
                first = self.observe_exact_day(7, "15")
                evidence = first.evidences.get()
                setattr(evidence, field, value)
                models.Model.save(evidence, update_fields=[field])
                self.observe(8, "10")
                self.assertEqual(self.box_history()["starting_location"], {
                    "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 8,
                    "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name}, "represented_movement_id": None,
                })

    def test_starting_reading_and_mapping_are_scoped_to_exact_box_and_organization(self):
        sibling = self.make_box("TRS-LAB-1.002", "002")
        self.observe_exact_day(3, "20", box=sibling)
        foreign_strain = Strain.objects.create(species=self.species, code="TRS-OTH-1", organization=self.other, number=1, origin_code="OTH")
        foreign_box = self.make_box("TRS-OTH-1.001", "001", organization=self.other, strain=foreign_strain)
        self.observe_exact_day(2, "10", box=foreign_box)
        foreign_zone = ThermalZone.objects.create(organization=self.other, name="Private 15", target_temperature_c=Decimal("15"))
        self.observe_exact_day(7, "15", source_box=foreign_box.global_code)
        self.observe_exact_day(8, "15")
        expected = {"observed_on": "2026-02-16", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                    "represented_movement_id": None}
        self.assertEqual(self.box_history()["starting_location"], expected)
        ThermalZone.objects.filter(pk=self.zone15.pk).update(target_temperature_c=None)
        self.assertIsNone(self.box_history()["starting_location"])
        self.assertNotEqual(foreign_zone.pk, expected["thermal_zone"]["id"])
        response = self.client.get(f"/api/boxes/{self.box.pk}/history/", **self.login(self.outsider, self.other))
        self.assertEqual(response.status_code, 404)

    def test_legacy_starting_week_keeps_iso_year_across_calendar_year_boundary(self):
        first = self.observe(1, "15")
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(measured_on=date(2025, 12, 31))
        self.assertEqual(self.box_history()["starting_location"], {
            "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 1,
            "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name}, "represented_movement_id": None,
        })

    def test_unverified_day_without_historical_reading_never_gets_period_fallback(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 2, 11), polyp_count=0)
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15, starts_at=paris(2026, 2, 1))
        self.assertIsNone(self.box_history()["starting_location"])

    def test_nonhistorical_exact_day_retains_period_fallback(self):
        self.observe_exact_day(41, "15")
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 10, 1))
        self.assertEqual(self.box_history()["starting_location"], {
            "observed_on": "2026-10-05", "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name},
            "represented_movement_id": None,
        })
        foreign_strain = Strain.objects.create(species=self.species, code="TRS-OTH-1", organization=self.other, number=1, origin_code="OTH")
        foreign_box = self.make_box("TRS-OTH-1.001", "001", organization=self.other, strain=foreign_strain)
        self.observe_exact_day(7, "15", box=foreign_box)
        foreign_zone = ThermalZone.objects.create(organization=self.other, name="Other zone")
        BoxLocation.objects.create(box=foreign_box, thermal_zone=foreign_zone, starts_at=paris(2026, 2, 1))
        response = self.client.get(f"/api/boxes/{foreign_box.pk}/history/", **self.login(self.outsider, self.other))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["starting_location"], {
            "observed_on": "2026-02-09", "thermal_zone": {"id": foreign_zone.pk, "name": foreign_zone.name},
            "represented_movement_id": None,
        })

    def test_starting_anchor_survives_conflicting_periods_and_unlinked_recorded_movement_without_writes(self):
        for precision in BiologicalMeasurement.TemporalPrecision.values:
            with self.subTest(precision=precision):
                self.box = self.make_box(f"TRS-LAB-1.{100 + Box.objects.count():03d}", str(100 + Box.objects.count()))
                first = self.observe(7, "15")
                BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(
                    measured_on=date(2026, 2, 11), temporal_precision=precision,
                )
                self.observe(8, "10")
                period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone20, starts_at=paris(2026, 2, 1))
                initial = BoxMovement.objects.create(
                    box=self.box, kind=BoxMovement.Kind.INITIAL_LOCATION, to_thermal_zone=self.zone20,
                    destination_location=period, moved_at=period.starts_at,
                )
                movement = BoxMovement.objects.create(
                    box=self.box, from_thermal_zone=self.zone20, to_thermal_zone=self.zone10, moved_at=paris(2026, 2, 12),
                )
                self.assertEqual(movement.kind, BoxMovement.Kind.UNCLASSIFIED)
                self.assertIsNone(movement.origin_location_id)
                self.assertIsNone(movement.destination_location_id)
                models_to_check = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone, BiologicalMeasurement,
                                   HistoricalTemperatureReading, HistoricalTemperatureEvidence, AuditLog)
                before = [list(model.objects.order_by("pk").values()) for model in models_to_check]
                expected = {"observed_on": "2026-02-11", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                            "represented_movement_id": None}
                if precision == BiologicalMeasurement.TemporalPrecision.LEGACY_UNVERIFIED:
                    expected.update(observed_on=None, temporal_precision="iso_week", iso_year=2026, iso_week=7)
                for _ in range(2):
                    history = self.box_history()
                    self.assertEqual(history["starting_location"], expected)
                    event = next(row for row in history["events"] if row["recorded_movement_id"] == movement.pk)
                    self.assertEqual(event["kind"], "unclassified")
                    self.assertEqual(event["from_thermal_zone"]["id"], self.zone20.pk)
                    self.assertEqual(event["to_thermal_zone"]["id"], self.zone10.pk)
                    self.assertFalse(event["evidence"]["has_origin_link"])
                    self.assertFalse(event["evidence"]["has_destination_link"])
                    self.assertTrue(any(row["recorded_movement_id"] == initial.pk for row in history["events"]))
                    self.assertEqual(history["historical_transitions"][0]["status"], "review")
                    self.assertIn("conflicting_recorded_movement", history["historical_transitions"][0]["blockers"])
                self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in models_to_check])

    def test_starting_anchor_survives_incomplete_or_foreign_periods(self):
        self.observe_exact_day(7, "15")
        foreign_zone = ThermalZone.objects.create(organization=self.other, name="Private foreign zone")
        for zone, start, end, unknown in (
            (self.zone10, paris(2026, 2, 9, 12), None, False),
            (self.zone10, paris(2026, 2, 1), paris(2026, 2, 9, 12), False),
            (self.zone10, paris(2026, 2, 1), None, True),
            (foreign_zone, paris(2026, 2, 1), None, False),
        ):
            with self.subTest(zone=zone.pk, start=start, end=end, unknown=unknown):
                period = BoxLocation.objects.create(box=self.box, thermal_zone=zone, starts_at=start, ends_at=end, end_date_unknown=unknown)
                self.assertEqual(self.box_history()["starting_location"], {
                    "observed_on": "2026-02-09", "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name},
                    "represented_movement_id": None,
                })
                period.delete()

    def test_merged_box_never_gets_starting_anchor(self):
        self.observe_exact_day(7, "15")
        target = self.make_box("TRS-LAB-1.002", "002")
        Box.objects.filter(pk=self.box.pk).update(merged_into=target)
        self.box.refresh_from_db()
        self.assertIsNone(projection.project_starting_location(box=self.box, periods=[], zones={}))

    def preserved_starting_evidence(self):
        Box.objects.filter(pk=self.box.pk).update(global_code="CLC-JKA-1.009")
        self.box.refresh_from_db()
        first = self.observe(7, "15", source_box="CLA-JKA-1.09")
        BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(measured_on=date(2026, 2, 11))
        self.observe(8, "10")
        return first.evidences.get()

    def test_starting_identity_correction_is_not_a_generic_sqlite_alias(self):
        self.preserved_starting_evidence()
        self.assertEqual(self.box_history()["starting_location"], {
            "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 8,
            "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name}, "represented_movement_id": None,
        })
        self.box = self.make_box("CLC-JKA-1.010", "010")
        self.observe(7, "15", source_box="CLA-JKA-1.10")
        self.assertIsNone(self.box_history()["starting_location"])

    def test_starting_identity_proof_helper_never_queries_sqlite_or_empty_ids(self):
        self.assertEqual(connection.vendor, "sqlite")
        with patch.object(connection, "cursor", side_effect=AssertionError("SQLite must not query correction proof")):
            self.assertEqual(projection._approved_starting_evidence_ids({4, 8}, using=connection.alias), set())
        with patch.object(projection, "connections", {}):
            self.assertEqual(projection._approved_starting_evidence_ids(set(), using="unused"), set())

    def test_starting_identity_proof_helper_batches_only_requested_ids_on_selected_alias(self):
        database = MagicMock(vendor="postgresql")
        cursor = database.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [(4,), (999,)]
        with patch.object(projection, "connections", {"proof_reader": database}):
            self.assertEqual(projection._approved_starting_evidence_ids({8, 4}, using="proof_reader"), {4})
            database.cursor.assert_called_once_with()
            cursor.execute.assert_called_once_with(
                "SELECT evidence_id FROM unnest(%s::bigint[]) AS scoped(evidence_id) "
                "WHERE public.box_1902_evidence_valid_v2(evidence_id)", [[4, 8]],
            )
            cursor.fetchall.return_value = []
            self.assertEqual(projection._approved_starting_evidence_ids({4, 8}, using="proof_reader"), set())

    def test_starting_approved_identity_restores_only_exact_persisted_evidence_without_writes(self):
        evidence = self.preserved_starting_evidence()
        earlier = self.observe(6, "15", source_box="CLA-JKA-1.09").evidences.get()
        domain = (Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone, BiologicalMeasurement,
                  HistoricalTemperatureReading, HistoricalTemperatureEvidence, AuditLog)
        before = [list(model.objects.order_by("pk").values()) for model in domain]
        expected = {"observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 7,
                    "thermal_zone": {"id": self.zone15.pk, "name": self.zone15.name}, "represented_movement_id": None}
        with patch.object(projection, "_approved_starting_evidence_ids", return_value={evidence.pk}) as proof:
            with patch.object(HistoricalTemperatureEvidence, "full_clean", side_effect=AssertionError("Projection must not validate model instances")):
                for _ in range(2):
                    history = self.box_history()
                    self.assertEqual(history["starting_location"], expected)
                    proof.assert_called_with({earlier.pk, evidence.pk}, using=evidence._state.db)
                    transition = history["historical_transitions"][0]
                    self.assertEqual(transition["status"], "review")
                    self.assertIn("source_box_identity_mismatch", transition["blockers"])
                    self.assertEqual(set(transition["origin_observation"]["sources"][0]), set(projection._EVIDENCE_FIELDS))
            self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in domain])
            BiologicalMeasurement.objects.filter(pk=evidence.reading.biological_measurement_id).update(
                temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE,
            )
            self.assertEqual(self.box_history()["starting_location"], {
                "observed_on": "2026-02-11", "thermal_zone": expected["thermal_zone"], "represented_movement_id": None,
            })
            proof.return_value = set()
            self.assertEqual(self.box_history()["starting_location"]["iso_week"], 8)

    def test_starting_approved_identity_does_not_authorize_another_evidence_on_same_reading(self):
        evidence = self.preserved_starting_evidence()
        extra = HistoricalTemperatureEvidence.objects.get(pk=evidence.pk)
        extra.pk = None
        extra.source_box = "CLA-JKA-1.08"
        extra.polyp_cell, extra.ephyrae_cell, extra.week_cell = "G9", "G10", "G2"
        models.Model.save(extra, force_insert=True)
        with patch.object(projection, "_approved_starting_evidence_ids", return_value={evidence.pk}) as proof:
            self.assertEqual(self.box_history()["starting_location"]["iso_week"], 8)
            proof.assert_called_once_with({evidence.pk, extra.pk}, using=evidence._state.db)

    def test_starting_approved_identity_keeps_source_value_week_and_association_guards(self):
        source = self.make_box("TRS-LAB-1.002", "002")
        for target, field, value in (
            ("evidence", "source_file", "unknown.xlsx"), ("evidence", "source_sha256", "0" * 64),
            ("evidence", "source_value", "20"), ("evidence", "source_iso_week", 6),
            ("evidence", "source_iso_year", 2025), ("reading", "association_status", "pending_review"),
            ("reading", "temperature_c", "invalid"), ("reading", "iso_week", 6),
            ("biology", "measured_on", date(2026, 2, 2)), ("biology", "week_start", date(2026, 2, 2)),
            ("biology", "environment_source_box_id", source.pk),
        ):
            with self.subTest(target=target, field=field):
                number = str(100 + Box.objects.count())
                self.box = self.make_box(f"TRS-LAB-1.{number}", number)
                first = self.observe(7, "15", source_box="CLA-JKA-1.09")
                evidence = first.evidences.get()
                if target == "biology":
                    BiologicalMeasurement.objects.filter(pk=first.biological_measurement_id).update(**{field: value})
                else:
                    row = evidence if target == "evidence" else first
                    setattr(row, field, value)
                    models.Model.save(row, update_fields=[field])
                self.observe(8, "10")
                with patch.object(projection, "_approved_starting_evidence_ids", return_value={evidence.pk}):
                    self.assertEqual(self.box_history()["starting_location"], {
                        "observed_on": None, "temporal_precision": "iso_week", "iso_year": 2026, "iso_week": 8,
                        "thermal_zone": {"id": self.zone10.pk, "name": self.zone10.name}, "represented_movement_id": None,
                    })

    def test_starting_identity_proof_is_scoped_to_canonical_box_and_organization_evidence(self):
        evidence = self.preserved_starting_evidence()
        sibling = self.make_box("CLC-JKA-1.010", "010")
        other_evidence = self.observe(4, "20", box=sibling, source_box="CLA-JKA-1.09").evidences.get()
        foreign_strain = Strain.objects.create(species=self.species, code="TRS-OTH-1", organization=self.other, number=1, origin_code="OTH")
        foreign_box = self.make_box("CLC-JKA-1.011", "011", organization=self.other, strain=foreign_strain)
        foreign_evidence = self.observe(3, "20", box=foreign_box, source_box="CLA-JKA-1.09").evidences.get()
        duplicate = BiologicalMeasurement(
            box=self.box, measured_on=date(2026, 2, 9), week_start=date(2026, 2, 9),
            duplicate_of_id=evidence.reading.biological_measurement_id, polyp_count=0,
        )
        models.Model.save(duplicate, force_insert=True)
        duplicate_reading = HistoricalTemperatureReading(
            biological_measurement=duplicate, iso_year=2026, iso_week=7, temperature_c="15",
        )
        models.Model.save(duplicate_reading, force_insert=True)
        duplicate_evidence = HistoricalTemperatureEvidence.objects.get(pk=evidence.pk)
        duplicate_evidence.pk = None
        duplicate_evidence.reading = duplicate_reading
        models.Model.save(duplicate_evidence, force_insert=True)
        with patch.object(projection, "_approved_starting_evidence_ids", return_value={
            other_evidence.pk, foreign_evidence.pk, duplicate_evidence.pk,
        }) as proof:
            self.assertEqual(self.box_history()["starting_location"]["iso_week"], 8)
            proof.assert_called_once_with({evidence.pk}, using=evidence._state.db)
            self.assertIsNone(projection.project_starting_location(box=foreign_box, periods=[], zones={}))
            proof.assert_called_once()

    def test_starting_approved_identity_never_skips_earliest_mapping_gap(self):
        evidence = self.preserved_starting_evidence()
        with patch.object(projection, "_approved_starting_evidence_ids", return_value={evidence.pk}):
            ThermalZone.objects.create(organization=self.org, name="Another 15", target_temperature_c=Decimal("15"))
            self.assertIsNone(self.box_history()["starting_location"])
            ThermalZone.objects.filter(organization=self.org, target_temperature_c=Decimal("15")).update(target_temperature_c=None)
            self.assertIsNone(self.box_history()["starting_location"])

    def test_review_transition_keeps_recorded_representation_without_upgrading(self):
        self.observe(7, "15", evidence=False)
        self.observe(8, "10")
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone15,
                                             to_thermal_zone=self.zone10, moved_at=paris(2026, 2, 10))
        row = self.only()
        self.assertEqual(row["status"], "review")
        self.assertIn("source_box_identity_unavailable", row["blockers"])
        self.assertEqual(row["representation"], {"kind": "box_movement", "event_id": f"movement:{movement.pk}", "movement_id": movement.pk})
        self.assertFalse(row["adds_location_history"])
        self.assertFalse(row["physical_occurrence_known"])
        self.assertEqual(self.box_history()["historical_transitions"][0]["representation"], row["representation"])

    def test_review_transition_retains_conflicts_without_guessing_representation(self):
        self.observe(7, "15", evidence=False)
        self.observe(8, "10")
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone20,
                                             to_thermal_zone=self.zone10, moved_at=paris(2026, 2, 10))
        row = self.only()
        self.assertEqual(row["status"], "review")
        self.assertEqual(set(row["blockers"]), {"source_box_identity_unavailable", "conflicting_recorded_movement"})
        self.assertIsNone(row["representation"])
        self.assertFalse(row["adds_location_history"])
        movement.delete()
        for day in (10, 11):
            BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone15,
                                       to_thermal_zone=self.zone10, moved_at=paris(2026, 2, day))
        row = self.only()
        self.assertEqual(set(row["blockers"]), {"source_box_identity_unavailable", "multiple_recorded_candidates"})
        self.assertIsNone(row["representation"])
        self.assertEqual(row["status"], "review")

    def test_review_transition_keeps_period_representation(self):
        self.observe(7, "15", evidence=False)
        self.observe(8, "10")
        origin = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone15,
                                            starts_at=paris(2026, 2, 1), ends_at=paris(2026, 2, 10))
        destination = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 10))
        row = self.only()
        self.assertEqual(row["representation"], {"kind": "location_periods", "origin_location_id": origin.pk, "destination_location_id": destination.pk})
        self.assertEqual(row["status"], "review")
        self.assertFalse(row["adds_location_history"])

    def assert_mapping_gap_retains_recorded_conflict_with_invalid_source(self, mapping_gap):
        self.observe(7, "15", source_file="unknown.xlsx")
        self.observe(8, "10")
        movement = BoxMovement.objects.create(box=self.box, from_thermal_zone=self.zone15,
                                             to_thermal_zone=self.zone10, moved_at=paris(2026, 2, 10))
        expected = {"source_not_validated", mapping_gap, "recorded_location_data_not_comparable"}
        for record_kind in ("movement", "period"):
            with self.subTest(record_kind=record_kind):
                row = self.only()
                self.assertEqual(set(row["blockers"]), expected)
                self.assertEqual(row["status"], "review")
                self.assertIsNone(row["representation"])
                self.assertFalse(row["adds_location_history"])
                self.assertFalse(row["physical_occurrence_known"])
                self.assertEqual(row["origin_observation"]["sources"][0]["source_file"], "unknown.xlsx")
                self.assertEqual((row["from_environment_c"], row["to_environment_c"]), ("15", "10"))
                self.assertEqual(set(self.box_history()["historical_transitions"][0]["blockers"]), expected)
                if record_kind == "movement":
                    movement.delete()
                    period = BoxLocation.objects.create(box=self.box, thermal_zone=self.zone10, starts_at=paris(2026, 2, 10))
        period.delete()
        row = self.only()
        self.assertEqual(set(row["blockers"]), {"source_not_validated", mapping_gap})
        self.assertEqual(row["status"], "review")

    def test_missing_mapping_with_invalid_source_keeps_recorded_conflict(self):
        ThermalZone.objects.filter(pk=self.zone15.pk).update(target_temperature_c=None)
        self.assert_mapping_gap_retains_recorded_conflict_with_invalid_source("location_mapping_unavailable")

    def test_ambiguous_mapping_with_invalid_source_keeps_recorded_conflict(self):
        ThermalZone.objects.create(organization=self.org, name="Another 15", target_temperature_c=Decimal("15"))
        self.assert_mapping_gap_retains_recorded_conflict_with_invalid_source("location_mapping_ambiguous")

    def test_gap_in_time_is_not_an_invented_instant(self):
        self.observe(7, "15")
        self.observe(8, "10")
        self.assertNotIn("T", self.only()["interval"]["starts_on"])
        self.assertEqual(date.fromisoformat(self.only()["interval"]["ends_before"]) - date.fromisoformat(self.only()["interval"]["starts_on"]), timedelta(days=14))
