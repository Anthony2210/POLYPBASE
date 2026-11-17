"""Thermal source loading against an isolated test database, never live data."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.audit.models import AuditLog
from apps.measurements.historical_temperature_source import SOURCES
from apps.measurements.models import (
    BiologicalMeasurement, DailyTemperature, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
    Probe, ProbeTemperatureBucket, TemperatureMeasurement,
)
from apps.measurements.probe_linking import link_probe
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .effective_history import (
    AdjustEventTemporal, AdjustEventZones, ConflictCode, EventKind, EventOrigin, EventTarget,
    ModifyEvent, OccupancyState, TemporalPosition, TemporalPrecision, ThermalSourceKind, project_history,
)
from .effective_history_sources import HistorySourceNotFound, load_box_source_history
from .effective_temperature import AttributionExtent, project_temperature
from .effective_temperature_sources import load_box_thermal_sources, preview_box_temperature
from .location_history_diagnostics import _read_only_snapshot
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone

SOURCE_FILE = "Suivi_2026_actualisé_v2.xlsx"
SOURCE_SHA = SOURCES[SOURCE_FILE][1]
ADAPTER = "apps.cultures.effective_temperature_sources"


def paris(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=MOVEMENT_WEEK_TIME_ZONE)


class ThermalSourceTests(TestCase):
    def setUp(self):
        self.org, _ = Organization.objects.update_or_create(pk=1, defaults={"name": "Aquarium de Paris"})
        self.other = Organization.objects.create(name="Other institution")
        species = Species.objects.create(scientific_name="Thermal synthetic", genus_species_code="THS")
        self.strain = Strain.objects.create(
            species=species, code="THS-LAB-1", organization=self.org, number=1, origin_code="LAB",
        )
        self.a = ThermalZone.objects.create(organization=self.org, name="A", target_temperature_c=Decimal("15"))
        self.b = ThermalZone.objects.create(organization=self.org, name="B", target_temperature_c=Decimal("0"))
        self.foreign = ThermalZone.objects.create(organization=self.other, name="Foreign", target_temperature_c=Decimal("10"))
        self.box = self.make_box("001")
        self.start, self.end = paris(2026, 2, 9), paris(2026, 2, 16)

    def make_box(self, number, **kwargs):
        return Box.objects.create(
            organization=self.org, global_code=f"THS-LAB-1.{number}", box_number=number,
            strain=self.strain, status=Box.Status.INACTIVE, **kwargs,
        )

    def probe(self, code="probe", *, zone=None, organization=None, **kwargs):
        return Probe.objects.create(
            organization=organization or self.org, thermal_zone=zone or self.a, code=code, **kwargs,
        )

    def raw(self, probe, *, at=None, value="0", source="iminilide"):
        return TemperatureMeasurement.objects.create(
            probe=probe, measured_at=at or self.start + timedelta(hours=1),
            temperature_c=Decimal(value), source=source,
        )

    def bucket(self, probe, *, at=None, last_value=None):
        lower = at or self.start
        return ProbeTemperatureBucket.objects.create(
            probe=probe, period_start=lower, period_minutes=15,
            last_measured_at=lower + timedelta(minutes=13, seconds=17),
            last_temperature_c=last_value, min_temperature_c=Decimal("-1.23"),
            average_temperature_c=Decimal("10.01"), max_temperature_c=Decimal("17.89"),
            temperature_sum_c=Decimal("30.02"), measurement_count=3,
        )

    def observe(self, week=7, temperature="0", *, box=None, evidence=True, year=2026, **source_overrides):
        box = box or self.box
        monday = date.fromisocalendar(year, week, 1)
        biology = BiologicalMeasurement.objects.create(box=box, measured_on=monday, polyp_count=0, ephyrae_count=0)
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=biology, iso_year=year, iso_week=week, temperature_c=temperature,
        )
        source = None
        if evidence:
            values = dict(
                reading=reading, source_file=SOURCE_FILE, source_sha256=SOURCE_SHA, sheet="Aurelia",
                temperature_cell=f"C{week + 2}", source_value=temperature, source_box=box.global_code,
                polyp_cell=f"F{week + 2}", polyp_value="0", ephyrae_cell=f"F{week + 3}", ephyrae_value="0",
                week_cell="F2", week_value=str(week), source_iso_year=year, source_iso_week=week,
            )
            values.update(source_overrides)
            source = HistoricalTemperatureEvidence(**values)
            if source_overrides:
                # Reproduce contradictory legacy evidence rejected by current writes.
                models.Model.save(source, force_insert=True)
            else:
                source.save()
        return reading, source

    def load(self, **kwargs):
        values = dict(organization_id=self.org.pk, box_id=self.box.pk, start=self.start, end=self.end)
        values.update(kwargs)
        return load_box_thermal_sources(**values)

    def test_request_is_finite_aware_nonempty_and_normalized_to_utc(self):
        for start, end in (
            (None, self.end), (self.start, None), (datetime(2026, 2, 9), self.end),
            (self.start, datetime(2026, 2, 16)), (self.start, self.start), (self.end, self.start),
        ):
            with self.subTest(start=start, end=end), self.assertNumQueries(0), self.assertRaises(ValueError):
                self.load(start=start, end=end)
        history = self.load()
        self.assertEqual((history.start, history.end), (
            self.start.astimezone(timezone.utc), self.end.astimezone(timezone.utc),
        ))
        self.assertIs(history.start.tzinfo, timezone.utc)
        self.assertIs(history.end.tzinfo, timezone.utc)

    def test_only_exact_canonical_box_in_requested_institution_is_accepted(self):
        merged = self.make_box("002", merged_into=self.box)
        for values in (
            {"box_id": merged.pk}, {"organization_id": self.other.pk}, {"box_id": 999999},
        ):
            with self.subTest(values=values), self.assertRaises(HistorySourceNotFound):
                self.load(**values)

        self.assertEqual(self.load().box_id, self.box.pk)

    def test_all_institutional_zones_and_inactive_probes_are_authorized(self):
        inactive = self.probe("inactive", zone=self.b, is_active=False)
        self.raw(inactive)
        # The Box has neither a current zone nor any location period.
        history = self.load()
        self.assertEqual(history.zone_ids, frozenset((self.a.pk, self.b.pk)))
        self.assertEqual(history.probe_ids, frozenset((inactive.pk,)))
        self.assertEqual([(item.zone_id, item.probe_id) for item in history.evidence], [(self.b.pk, inactive.pk)])

    def test_foreign_and_malformed_cross_institution_probes_are_excluded(self):
        local = self.probe("local")
        bad_local = self.probe("local-foreign-zone", zone=self.foreign)
        bad_foreign = self.probe("foreign-local-zone", organization=self.other)
        foreign = self.probe("foreign", organization=self.other, zone=self.foreign)
        good_raw, good_bucket = self.raw(local), self.bucket(local)
        for probe in (bad_local, bad_foreign, foreign):
            self.raw(probe)
            self.bucket(probe)
        DailyTemperature.objects.create(thermal_zone=self.foreign, date=self.start.date(), average_temperature_c=99)
        history = self.load()
        self.assertEqual(history.probe_ids, frozenset((local.pk,)))
        self.assertEqual({item.key for item in history.evidence}, {
            f"temperature_measurement:{good_raw.pk}", f"probe_temperature_bucket:{good_bucket.pk}",
        })
        self.assertTrue(all(item.zone_id == self.a.pk for item in history.evidence))

    def test_raw_and_bucket_share_one_union_all_without_grouping_or_locks(self):
        probe = self.probe()
        raw, bucket = self.raw(probe, value="-0.12", source="original-cloud"), self.bucket(probe)
        with CaptureQueriesContext(connection) as queries:
            history = self.load()
        tier_queries = [query["sql"] for query in queries if "measurements_temperaturemeasurement" in query["sql"]]
        self.assertEqual(len(tier_queries), 1)
        sql = tier_queries[0].upper()
        self.assertIn("UNION ALL", sql)
        self.assertIn("MEASUREMENTS_PROBETEMPERATUREBUCKET", sql)
        for forbidden in ("GROUP BY", "SUM(", "AVG(", "ROUND(", "FOR UPDATE"):
            self.assertNotIn(forbidden, sql)
        by_key = {item.key: item for item in history.evidence}
        item = by_key[f"temperature_measurement:{raw.pk}"]
        self.assertEqual(item.source_kind, ThermalSourceKind.RAW_PROBE_MEASUREMENT)
        self.assertEqual(item.temperature_c, Decimal("-0.12"))
        self.assertEqual(item.temporal.precision, TemporalPrecision.EXACT)
        self.assertEqual(item.temporal.instant, raw.measured_at)
        self.assertEqual((item.last_measured_at, item.last_temperature_c), (raw.measured_at, Decimal("-0.12")))
        self.assertEqual((item.sources[0].source_id, item.sources[0].basis), (raw.pk, "original-cloud"))
        self.assertIn(f"probe_temperature_bucket:{bucket.pk}", by_key)

    def test_partial_bucket_retains_original_pk_whole_exact_statistics_and_unknown_last_value(self):
        probe = self.probe()
        bucket = self.bucket(probe)
        history = self.load(start=self.start + timedelta(minutes=5), end=self.start + timedelta(minutes=6))
        (item,) = history.evidence
        self.assertEqual(item.source_kind, ThermalSourceKind.CONDENSED_BUCKET)
        self.assertEqual(item.sources[0].source_id, bucket.pk)
        self.assertEqual((item.temporal.not_before, item.temporal.before), (
            bucket.period_start, bucket.period_start + timedelta(minutes=15),
        ))
        self.assertEqual((item.temperature_c, item.minimum_c, item.maximum_c, item.temperature_sum_c, item.measurement_count), (
            Decimal("10.01"), Decimal("-1.23"), Decimal("17.89"), Decimal("30.02"), 3,
        ))
        self.assertEqual(item.last_measured_at, bucket.last_measured_at)
        self.assertIsNone(item.last_temperature_c)
        self.assertNotEqual(item.temperature_sum_c, item.temperature_c * item.measurement_count)

    def test_exact_half_open_probe_overlap_and_known_zero_last_value(self):
        probe = self.probe()
        self.raw(probe, at=self.start - timedelta(microseconds=1))
        included = self.raw(probe, at=self.start)
        self.raw(probe, at=self.end)
        self.bucket(probe, at=self.start - timedelta(minutes=15))
        included_bucket = self.bucket(probe, at=self.end - timedelta(minutes=5), last_value=Decimal("0"))
        self.bucket(probe, at=self.end)
        history = self.load()
        self.assertEqual({item.key for item in history.evidence}, {
            f"temperature_measurement:{included.pk}", f"probe_temperature_bucket:{included_bucket.pk}",
        })
        bucket = next(item for item in history.evidence if item.probe_id and item.temporal.instant is None)
        self.assertEqual(bucket.last_temperature_c, Decimal("0"))

    def test_link_probe_replacement_keeps_per_id_evidence_and_original_zones(self):
        old, _ = link_probe(organization=self.org, zone=self.a, serial="synthetic", channel=1, code="old")
        old_raw, old_bucket = self.raw(old), self.bucket(old)
        new, created = link_probe(
            organization=self.org, zone=self.b, serial="synthetic", channel=1, code="new", replace=True,
        )
        self.assertTrue(created)
        new_raw = self.raw(new, value="12")
        history = self.load()
        self.assertEqual(history.probe_ids, frozenset((old.pk, new.pk)))
        self.assertEqual({(item.key, item.probe_id, item.zone_id) for item in history.evidence}, {
            (f"temperature_measurement:{old_raw.pk}", old.pk, self.a.pk),
            (f"probe_temperature_bucket:{old_bucket.pk}", old.pk, self.a.pk),
            (f"temperature_measurement:{new_raw.pk}", new.pk, self.b.pk),
        })
        old.refresh_from_db()
        self.assertFalse(old.is_active)

    def test_daily_manual_or_mixed_and_probe_derived_stay_separate_from_probe_evidence(self):
        probe = self.probe()
        self.raw(probe)
        self.bucket(probe)
        mixed = DailyTemperature.objects.create(
            thermal_zone=self.a, date=self.start.date(), average_temperature_c=Decimal("0"),
            measurement_count=5, computed_from_probes=False,
        )
        derived = DailyTemperature.objects.create(
            thermal_zone=self.b, date=self.start.date(), average_temperature_c=Decimal("12.34"),
            min_temperature_c=Decimal("10.00"), max_temperature_c=Decimal("14.00"),
            measurement_count=90, computed_from_probes=True,
        )
        history = self.load()
        self.assertEqual(len(history.evidence), 4)
        days = {item.sources[0].source_id: item for item in history.evidence
                if item.source_kind == ThermalSourceKind.MIXED_DAILY_AGGREGATE}
        self.assertEqual(set(days), {mixed.pk, derived.pk})
        self.assertEqual((days[mixed.pk].temperature_c, days[mixed.pk].daily_computed_from_probes), (Decimal("0"), False))
        self.assertIsNone(days[mixed.pk].minimum_c)
        self.assertIsNone(days[mixed.pk].maximum_c)
        self.assertEqual(days[mixed.pk].sources[0].basis, "manual_or_mixed")
        self.assertEqual(days[derived.pk].sources[0].basis, "probe_derived")
        self.assertTrue(days[derived.pk].daily_computed_from_probes)
        self.assertEqual(days[derived.pk].measurement_count, 90)
        self.assertTrue(all(item.temperature_sum_c is None for item in days.values()))

    def test_daily_paris_dst_support_is_whole_23_or_25_hour_day(self):
        for month, day, hours in ((3, 29, 23), (10, 25, 25)):
            with self.subTest(month=month):
                lower = paris(2026, month, day)
                upper = paris(2026, month, day + 1)
                DailyTemperature.objects.create(thermal_zone=self.a, date=lower.date(), average_temperature_c=1)
                DailyTemperature.objects.create(thermal_zone=self.a, date=upper.date(), average_temperature_c=2)
                (item,) = self.load(start=lower + timedelta(hours=12), end=upper).evidence
                self.assertEqual((item.temporal.not_before, item.temporal.before), (
                    lower.astimezone(timezone.utc), upper.astimezone(timezone.utc),
                ))
                self.assertEqual(item.temporal.before - item.temporal.not_before, timedelta(hours=hours))
                self.assertIsNone(item.temporal.instant)

    def test_one_historical_zero_without_transition_is_week_precision_with_both_source_pks(self):
        reading, source = self.observe()
        (item,) = self.load(start=self.start + timedelta(days=3), end=self.end).evidence
        self.assertEqual(item.source_kind, ThermalSourceKind.HISTORICAL_SETPOINT)
        self.assertEqual((item.temperature_c, item.zone_id, item.box_id), (Decimal("0"), self.b.pk, self.box.pk))
        self.assertTrue(item.established)
        self.assertEqual(item.temporal.precision, TemporalPrecision.ISO_WEEK)
        self.assertIsNone(item.temporal.instant)
        self.assertEqual((item.temporal.not_before, item.temporal.before), (self.start, self.end))
        self.assertEqual({(ref.source, ref.source_id) for ref in item.sources}, {
            ("historical_temperature_reading", reading.pk), ("historical_temperature_evidence", source.pk),
        })
        self.assertIn("unique_current_target_mapping", item.association_basis)

    def test_missing_or_ambiguous_mapping_retains_source_never_substitutes_current_target(self):
        self.observe(temperature="10.12345678901234567890123456789")
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.a)
        (item,) = self.load().evidence
        self.assertEqual(item.temperature_c, Decimal("10.12345678901234567890123456789"))
        self.assertIsNone(item.zone_id)
        self.assertFalse(item.established)
        self.assertEqual(item.uncertainty_reasons, ("location_mapping_unavailable",))
        self.assertEqual(self.load().findings[0].code, "historical_setpoint_unestablished")
        self.a.target_temperature_c = item.temperature_c
        # Mapping uses stored zone precision, not a rounded reading.
        self.a.save()
        self.assertIsNone(self.load().evidence[0].zone_id)

    def test_duplicate_target_mapping_is_explicitly_unknown(self):
        self.observe()
        ThermalZone.objects.create(organization=self.org, name="Another zero", target_temperature_c=0)
        (item,) = self.load().evidence
        self.assertEqual(item.temperature_c, Decimal("0"))
        self.assertIsNone(item.zone_id)
        self.assertFalse(item.established)
        self.assertEqual(item.uncertainty_reasons, ("location_mapping_ambiguous",))

    def test_absent_historical_source_never_becomes_a_current_zone_target(self):
        Box.objects.filter(pk=self.box.pk).update(thermal_zone=self.a)
        self.assertEqual(self.load().evidence, ())
        self.observe(evidence=False)
        (item,) = self.load().evidence
        self.assertEqual(item.temperature_c, Decimal("0"))
        self.assertIsNone(item.zone_id)
        self.assertFalse(item.established)
        self.assertIn("source_box_identity_unavailable", item.uncertainty_reasons)

    def test_conflicting_or_invalid_evidence_remains_unestablished_with_original_references(self):
        reading, source = self.observe(source_value="15", source_file="unreviewed.xlsx")
        history = self.load()
        (item,) = history.evidence
        self.assertEqual(item.temperature_c, Decimal("0"))
        self.assertFalse(item.established)
        self.assertIsNone(item.zone_id)
        self.assertEqual(set(item.uncertainty_reasons), {"observation_source_value_conflict", "source_not_validated"})
        self.assertIn(source.pk, [ref.source_id for ref in item.sources if ref.source == "historical_temperature_evidence"])
        self.assertEqual(history.findings[0].details["reading_id"], reading.pk)
        self.assertEqual(history.findings[0].details["source_values"], ("15",))

    def test_identity_mismatch_is_not_an_alias_and_only_persisted_approved_proof_can_exempt_it(self):
        _, source = self.observe(source_box="THS-LAB-1.099")
        (unknown,) = self.load().evidence
        self.assertFalse(unknown.established)
        self.assertIn("source_box_identity_mismatch", unknown.uncertainty_reasons)
        with patch(f"{ADAPTER}._approved_starting_evidence_ids", return_value={source.pk}) as reviewed:
            (approved,) = self.load().evidence
        reviewed.assert_called_once()
        self.assertEqual(reviewed.call_args.args, ({source.pk},))
        self.assertTrue(approved.established)
        self.assertEqual(approved.zone_id, self.b.pk)
        self.assertEqual(approved.sources[1].basis, "persisted_reviewed_proof")

    def test_reviewed_identity_does_not_override_source_value_conflicts(self):
        _, source = self.observe(source_box="THS-LAB-1.099", source_value="15")
        with patch(f"{ADAPTER}._approved_starting_evidence_ids", return_value={source.pk}):
            (item,) = self.load().evidence
        self.assertFalse(item.established)
        self.assertIsNone(item.zone_id)
        self.assertEqual(item.uncertainty_reasons, ("observation_source_value_conflict",))

    def test_invalid_or_pending_readings_are_retained_as_unknown_not_current_targets(self):
        reading, _ = self.observe()
        table = HistoricalTemperatureReading._meta.db_table
        for field, value, reason in (
            ("association_status", "pending_review", "observation_association_not_confirmed"),
            ("temperature_c", "invalid", "observation_temperature_invalid"),
        ):
            with self.subTest(field=field):
                # Simulate persisted legacy/invalidation states, bypassing write guards.
                with connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {table} SET {field}=%s WHERE id=%s", [value, reading.pk])
                (item,) = self.load().evidence
                self.assertFalse(item.established)
                self.assertIsNone(item.zone_id)
                self.assertIn(reason, item.uncertainty_reasons)
                if field == "temperature_c":
                    self.assertIsNone(item.temperature_c)

    def test_conflicting_same_week_observations_are_both_preserved(self):
        reading, _ = self.observe()
        duplicate = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.start.date(), duplicate_of_id=reading.biological_measurement_id,
            polyp_count=0, ephyrae_count=0,
        )
        other = HistoricalTemperatureReading(
            biological_measurement=duplicate, iso_year=2026, iso_week=7, temperature_c="15",
        )
        models.Model.save(other, force_insert=True)
        history = self.load()
        self.assertEqual({item.temperature_c for item in history.evidence}, {Decimal("0"), Decimal("15")})
        self.assertEqual(len(history.findings), 2)
        for item in history.evidence:
            self.assertFalse(item.established)
            self.assertIsNone(item.zone_id)
            self.assertIn("observations_conflict_in_week", item.uncertainty_reasons)

    def test_exact_biological_date_does_not_promote_a_weekly_temperature_to_an_instant(self):
        reading, _ = self.observe()
        BiologicalMeasurement.objects.filter(pk=reading.biological_measurement_id).update(
            measured_on=self.start.date() + timedelta(days=3),
            temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE,
        )
        (item,) = self.load().evidence
        self.assertTrue(item.established)
        self.assertEqual(item.temporal.precision, TemporalPrecision.ISO_WEEK)
        self.assertIsNone(item.temporal.instant)
        self.assertEqual((item.temporal.not_before, item.temporal.before), (self.start, self.end))

    def test_historical_scope_is_org_one_and_strictly_before_cutoff(self):
        reading, _ = self.observe(week=41)
        with patch(f"{ADAPTER}._approved_starting_evidence_ids") as reviewed:
            history = self.load(start=paris(2026, 10, 5), end=paris(2026, 10, 12))
        reviewed.assert_not_called()
        self.assertEqual(history.evidence, ())
        self.assertEqual(history.findings[0].code, "historical_setpoint_outside_approved_scope")
        self.assertEqual(history.findings[0].details["reading_id"], reading.pk)
        self.observe()
        Box.objects.filter(pk=self.box.pk).update(organization=self.other)
        with patch(f"{ADAPTER}._approved_starting_evidence_ids") as reviewed:
            history = self.load(organization_id=self.other.pk)
        reviewed.assert_not_called()
        self.assertEqual(history.evidence, ())
        self.assertEqual(history.findings[0].code, "historical_setpoint_outside_approved_scope")

    def test_historical_readings_of_other_boxes_and_merged_boxes_are_not_followed(self):
        other = self.make_box("002")
        self.observe(box=other)
        self.assertEqual(self.load().evidence, ())
        Box.objects.filter(pk=other.pk).update(merged_into=self.box)
        self.assertEqual(self.load().evidence, ())

    def test_foreign_environment_source_blocks_same_box_observation(self):
        reading, _ = self.observe()
        other = self.make_box("002")
        BiologicalMeasurement.objects.filter(pk=reading.biological_measurement_id).update(environment_source_box=other)
        (item,) = self.load().evidence
        self.assertFalse(item.established)
        self.assertIsNone(item.zone_id)
        self.assertIn("observation_environment_from_other_box", item.uncertainty_reasons)

    def test_historical_week_overlap_is_not_legacy_monday_instant(self):
        self.observe()
        self.assertEqual(len(self.load(start=self.end - timedelta(seconds=1)).evidence), 1)
        self.assertEqual(self.load(start=self.end, end=self.end + timedelta(days=1)).evidence, ())
        self.assertEqual(self.load(start=self.start - timedelta(days=1), end=self.start).evidence, ())

    def test_deterministic_read_only_snapshot_and_bounded_queries(self):
        self.observe()
        probe = self.probe()
        self.raw(probe)
        self.bucket(probe)
        watched = (
            Box, BoxLocation, BoxMovement, HistoricalBoxMovement, AuditLog, ThermalZone, Probe,
            BiologicalMeasurement, HistoricalTemperatureReading, HistoricalTemperatureEvidence,
            TemperatureMeasurement, ProbeTemperatureBucket, DailyTemperature,
        )
        before = [list(model.objects.order_by("pk").values()) for model in watched]
        with CaptureQueriesContext(connection) as first_queries:
            first = self.load()
        self.assertEqual(first, self.load())
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in watched])
        self.assertTrue(all(query["sql"].lstrip().upper().startswith("SELECT") for query in first_queries))
        self.assertEqual(len(first_queries), 7)
        for number in range(15):
            self.raw(self.probe(f"probe-{number}", zone=self.b), value="-2.12")
        with CaptureQueriesContext(connection) as expanded_queries:
            expanded = self.load()
        self.assertEqual(len(expanded_queries), len(first_queries))
        self.assertEqual(len(expanded.evidence), len(first.evidence) + 15)

    def test_uses_existing_snapshot_wrapper(self):
        with patch(f"{ADAPTER}._read_only_snapshot") as snapshot:
            self.load()
        snapshot.assert_called_once_with()
        snapshot.return_value.__enter__.assert_called_once_with()
        snapshot.return_value.__exit__.assert_called_once()

    def _exact_movement_fixture(self):
        opening = BoxMovement.objects.create(
            box=self.box, to_thermal_zone=self.a, moved_at=self.start,
            kind=BoxMovement.Kind.INITIAL_LOCATION,
        )
        movement = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
            moved_at=self.start + timedelta(days=2, hours=12), kind=BoxMovement.Kind.ORDINARY,
        )
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.ACTIVE, thermal_zone=self.b)
        return opening, movement

    def test_pipeline_validated_historical_changes_keep_weeks_and_uncertain_transition_bounds(self):
        zone10 = ThermalZone.objects.create(organization=self.org, name="Ten", target_temperature_c=Decimal("10"))
        for number, value, destination in (("002", "0", self.b), ("003", "10", zone10)):
            with self.subTest(value=value):
                box = self.make_box(number)
                first, first_source = self.observe(7, "15", box=box)
                last, last_source = self.observe(8, value, box=box)
                Box.objects.filter(pk=box.pk).update(status=Box.Status.ACTIVE, thermal_zone=destination)
                source = load_box_source_history(organization_id=self.org.pk, box_id=box.pk)
                history = project_history(source)
                data = self.load(box_id=box.pk, end=paris(2026, 2, 23))
                preview = project_temperature(history, data)
                self.assertNotIn(ConflictCode.CURRENT_OCCUPANCY_UNRECONCILED,
                                 [conflict.code for conflict in history.conflicts])
                (transition,) = [event for event in history.events if event.origin == EventOrigin.HISTORICAL_TRANSITION]
                self.assertEqual(transition.kind, EventKind.RECONSTRUCTED_MOVEMENT)
                self.assertEqual(transition.temporal.precision, TemporalPrecision.OBSERVATION_INTERVAL)
                self.assertIsNone(transition.temporal.instant)
                self.assertEqual((transition.temporal.not_before, transition.temporal.before),
                                 (self.start, paris(2026, 2, 23)))
                self.assertEqual(transition.temporal.ordering_anchor, self.end)
                self.assertTrue(all(interval.boundary_uncertain for interval in history.intervals))
                self.assertEqual(preview.unassigned, ())
                segments = {segment.evidence.key: segment for segment in preview.segments}
                self.assertEqual(set(segments), {f"historical_temperature_reading:{first.pk}",
                                                 f"historical_temperature_reading:{last.pk}"})
                for reading, proof, zone, temperature in (
                    (first, first_source, self.a, Decimal("15")), (last, last_source, destination, Decimal(value)),
                ):
                    segment = segments[f"historical_temperature_reading:{reading.pk}"]
                    item = segment.evidence
                    self.assertTrue(item.established)
                    self.assertEqual((item.temperature_c, item.zone_id), (temperature, zone.pk))
                    self.assertEqual(item.temporal.precision, TemporalPrecision.ISO_WEEK)
                    self.assertEqual((item.temporal.iso_year, item.temporal.iso_week), (2026, reading.iso_week))
                    self.assertIsNone(item.temporal.instant)
                    self.assertEqual({(ref.source, ref.source_id) for ref in item.sources}, {
                        ("historical_temperature_reading", reading.pk), ("historical_temperature_evidence", proof.pk),
                    })
                    (attribution,) = segment.attributions
                    self.assertEqual(attribution.zone_id, zone.pk)
                    self.assertEqual(attribution.extent, AttributionExtent.POSSIBLE)
                    self.assertFalse(attribution.established)
                    self.assertIn("uncertain_occupancy_boundary", attribution.uncertainty_reasons)
                self.assertEqual(preview.continuity, "not_inferred")

    def test_pipeline_historical_source_is_not_reused_as_a_draft_zone_target(self):
        self.observe(7, "15")
        last, _ = self.observe(8, "0")
        zone10 = ThermalZone.objects.create(organization=self.org, name="Unobserved ten", target_temperature_c=Decimal("10"))
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.ACTIVE, thermal_zone=self.b)
        source = load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)
        (transition,) = [event for event in source.events if event.origin == EventOrigin.HISTORICAL_TRANSITION]
        data = self.load(end=paris(2026, 2, 23))
        baseline = project_temperature(project_history(source), data)
        draft = project_history(source, operations=[AdjustEventZones(
            EventTarget(transition.identity.key, transition.identity.evidence_fingerprint), destination_zone_id=zone10.pk,
        )])
        self.assertEqual(draft.rejected_operations, ())
        preview = project_temperature(draft, data)
        self.assertIn(last.pk, [segment.evidence.sources[0].source_id for segment in baseline.segments])
        (unassigned,) = [segment for segment in preview.unassigned
                         if segment.evidence.key == f"historical_temperature_reading:{last.pk}"]
        self.assertEqual((unassigned.evidence.temperature_c, unassigned.evidence.zone_id), (Decimal("0"), self.b.pk))
        self.assertIn("no_matching_effective_occupancy", unassigned.uncertainty_reasons)
        (stay,) = [stay for stay in preview.stays if stay.occupancy.zone_id == zone10.pk]
        self.assertEqual(stay.source_keys, ())
        self.assertIn("documented_historical_setpoint_unavailable", stay.missing_reasons)
        self.assertIn("temperature_data_unavailable", stay.missing_reasons)
        self.assertIn("draft_assertion", stay.occupancy.provisional_reasons)
        self.assertFalse(any(item.zone_id == zone10.pk or item.temperature_c == Decimal("10") for item in data.evidence))
        self.assertEqual(project_temperature(project_history(source), data), baseline)

    def test_pipeline_exact_movement_drafts_reuse_one_snapshot_and_reselect_raw_timestamps(self):
        _, movement = self._exact_movement_fixture()
        third = ThermalZone.objects.create(organization=self.org, name="Third", target_temperature_c=Decimal("10"))
        later = movement.moved_at + timedelta(days=2)
        readings = []
        for zone in (self.a, self.b, third):
            probe = self.probe(f"zone-{zone.pk}", zone=zone)
            for instant in (movement.moved_at - timedelta(microseconds=1), movement.moved_at,
                            movement.moved_at + timedelta(days=1), later):
                readings.append((zone.pk, self.raw(probe, at=instant, value=str(zone.pk))))
        source = load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)
        data = self.load()
        original_items = {item.key: item for item in data.evidence}
        target = EventTarget(f"movement:{movement.pk}")
        baseline = project_temperature(project_history(source), data)
        baseline_keys = {f"temperature_measurement:{row.pk}" for zone, row in readings
                         if zone == (self.a.pk if row.measured_at < movement.moved_at else self.b.pk)}
        self.assertEqual({segment.evidence.key for segment in baseline.segments}, baseline_keys)
        self.assertTrue(all(attribution.established for segment in baseline.segments for attribution in segment.attributions))
        for name, operation, boundary, destination in (
            ("destination", AdjustEventZones(target, destination_zone_id=third.pk), movement.moved_at, third.pk),
            ("time", AdjustEventTemporal(target, TemporalPosition.exact(later)), later, self.b.pk),
            ("type", ModifyEvent(target, kind=EventKind.DEACTIVATION, destination_zone_id=None), movement.moved_at, None),
        ):
            with self.subTest(draft=name), self.assertNumQueries(0):
                history = project_history(source, operations=[operation])
                self.assertEqual(history.rejected_operations, ())
                preview = project_temperature(history, data)
                expected = {f"temperature_measurement:{row.pk}" for zone, row in readings
                            if zone == (self.a.pk if row.measured_at < boundary else destination)}
                self.assertEqual({segment.evidence.key for segment in preview.segments}, expected)
                self.assertEqual({segment.evidence.key for segment in preview.unassigned}, set(original_items) - expected)
                for segment in preview.segments:
                    self.assertIs(segment.evidence, original_items[segment.evidence.key])
                    self.assertEqual(segment.evidence.temporal.precision, TemporalPrecision.EXACT)
                    for attribution in segment.attributions:
                        self.assertFalse(attribution.established)
                        self.assertIn("draft_assertion", attribution.provisional_reasons)
                if name == "type":
                    (gap,) = [gap for gap in preview.gaps if gap.reason == "inactive"]
                    self.assertEqual((gap.start, gap.end), (movement.moved_at.astimezone(timezone.utc), data.end))
        self.assertEqual(project_temperature(project_history(source), data), baseline)

    def test_pipeline_probe_replacement_selects_by_effective_stay_not_inferred_installation(self):
        opening, movement = self._exact_movement_fixture()
        before_link = load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)
        old, _ = link_probe(organization=self.org, zone=self.a, serial="pipeline-replacement", channel=1, code="old")
        old_before = self.raw(old, at=movement.moved_at - timedelta(microseconds=1), value="15")
        old_after = self.raw(old, at=movement.moved_at, value="14")
        new, _ = link_probe(
            organization=self.org, zone=self.b, serial="pipeline-replacement", channel=1, code="new", replace=True,
        )
        new_before = self.raw(new, at=movement.moved_at - timedelta(microseconds=1), value="1")
        new_after = self.raw(new, at=movement.moved_at, value="0")
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        source = load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)
        self.assertEqual(source, before_link)
        history = project_history(source)
        self.assertEqual({event.key for event in history.events}, {f"movement:{opening.pk}", f"movement:{movement.pk}"})
        self.assertEqual([(interval.zone_id, interval.start.instant) for interval in history.intervals],
                         [(self.a.pk, self.start), (self.b.pk, movement.moved_at)])
        data = self.load()
        self.assertEqual(data.probe_ids, frozenset((old.pk, new.pk)))
        self.assertEqual(len(data.evidence), 4)
        preview = project_temperature(history, data)
        self.assertEqual({(segment.evidence.sources[0].source_id, segment.evidence.probe_id,
                           segment.evidence.temporal.instant) for segment in preview.segments}, {
            (old_before.pk, old.pk, old_before.measured_at), (new_after.pk, new.pk, new_after.measured_at),
        })
        self.assertEqual({segment.evidence.sources[0].source_id for segment in preview.unassigned}, {old_after.pk, new_before.pk})
        self.assertTrue(all(attribution.established for segment in preview.segments for attribution in segment.attributions))
        self.assertEqual(preview.continuity, "not_inferred")

    def test_pipeline_reads_and_successive_drafts_are_select_only_and_preserve_all_source_rows(self):
        self.observe(7, "15")
        self.observe(8, "0")
        third = ThermalZone.objects.create(organization=self.org, name="Later zone", target_temperature_c=Decimal("10"))
        movement = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.b, to_thermal_zone=third,
            moved_at=paris(2026, 3, 5, 12), kind=BoxMovement.Kind.ORDINARY,
        )
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.ACTIVE, thermal_zone=third)
        self.raw(self.probe("old-zone", zone=self.b), at=paris(2026, 3, 4, 12), value="0")
        probe = self.probe("later-zone", zone=third)
        self.raw(probe, at=paris(2026, 3, 6, 12), value="10")
        self.bucket(probe, at=paris(2026, 3, 7))
        DailyTemperature.objects.create(thermal_zone=third, date=date(2026, 3, 6), average_temperature_c=Decimal("10"))
        watched = (
            Box, BoxLocation, BoxMovement, HistoricalBoxMovement, AuditLog, ThermalZone, Probe,
            BiologicalMeasurement, HistoricalTemperatureReading, HistoricalTemperatureEvidence,
            TemperatureMeasurement, ProbeTemperatureBucket, DailyTemperature,
        )
        before = [list(model.objects.order_by("pk").values()) for model in watched]
        with CaptureQueriesContext(connection) as queries:
            source = load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)
            data = self.load(end=paris(2026, 3, 9))
            history = project_history(source)
            baseline = project_temperature(history, data)
            target = EventTarget(f"movement:{movement.pk}")
            for operation in (
                AdjustEventZones(target, destination_zone_id=self.a.pk),
                AdjustEventTemporal(target, TemporalPosition.exact(paris(2026, 3, 6, 13))),
                ModifyEvent(target, kind=EventKind.DEACTIVATION, destination_zone_id=None),
            ):
                draft = project_history(source, operations=[operation])
                self.assertEqual(draft.rejected_operations, ())
                preview = project_temperature(draft, data)
                self.assertTrue(any("draft_assertion" in stay.occupancy.provisional_reasons for stay in preview.stays))
            self.assertEqual(project_temperature(project_history(source), data), baseline)
            self.assertEqual(load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk), source)
            self.assertEqual(self.load(end=paris(2026, 3, 9)), data)
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries.captured_queries))
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in watched])
        self.assertEqual({item.source_kind for item in data.evidence}, set(ThermalSourceKind))
        self.assertTrue(any(ref.source == "historical_temperature_evidence" for item in data.evidence for ref in item.sources))
        self.assertTrue(any(stay.occupancy.state == OccupancyState.LOCATED for stay in baseline.stays))

    def test_standalone_preview_encloses_real_loaders_in_one_outer_snapshot_without_writes(self):
        _, movement = self._exact_movement_fixture()
        third = ThermalZone.objects.create(organization=self.org, name="Draft zone", target_temperature_c=Decimal("10"))
        before_move = self.raw(self.probe("before", zone=self.a), at=movement.moved_at - timedelta(seconds=1), value="15")
        original = self.raw(self.probe("original", zone=self.b), at=movement.moved_at, value="0")
        proposed = self.raw(self.probe("proposed", zone=third), at=movement.moved_at, value="10")
        scope = dict(organization_id=self.org.pk, box_id=self.box.pk, start=self.start, end=self.end)
        source = load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)
        data = self.load()
        baseline = project_temperature(project_history(source), data)
        self.assertEqual(preview_box_temperature(**scope), baseline)
        operations = (AdjustEventZones(EventTarget(f"movement:{movement.pk}"), destination_zone_id=third.pk),)
        expected = project_temperature(project_history(source, operations=operations), data)
        watched = (
            Box, BoxLocation, BoxMovement, HistoricalBoxMovement, AuditLog, ThermalZone, Probe,
            BiologicalMeasurement, HistoricalTemperatureReading, HistoricalTemperatureEvidence,
            TemperatureMeasurement, ProbeTemperatureBucket, DailyTemperature,
        )
        before = [list(model.objects.order_by("pk").values()) for model in watched]
        active, depths = [], []

        @contextmanager
        def recording_snapshot():
            with _read_only_snapshot() as database_read_only:
                active.append(len(active) + 1)
                depths.append(len(active))
                try:
                    yield database_read_only
                finally:
                    active.pop()

        def check_snapshot(execute, sql, params, many, context):
            # Both real loader contexts must nest inside the orchestration context.
            self.assertGreaterEqual(len(active), 2)
            return execute(sql, params, many, context)

        with patch(f"{ADAPTER}._read_only_snapshot", side_effect=recording_snapshot) as snapshot, \
                patch("apps.cultures.effective_history_sources._read_only_snapshot", side_effect=recording_snapshot), \
                CaptureQueriesContext(connection) as queries, connection.execute_wrapper(check_snapshot):
            preview = preview_box_temperature(**scope, operations=operations)
        snapshot.assert_called_with()
        self.assertEqual(depths[0], 1)
        self.assertEqual(depths.count(1), 1)
        self.assertIn(2, depths)
        self.assertEqual(active, [])
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries.captured_queries))
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in watched])
        self.assertEqual(preview, expected)
        self.assertEqual({segment.evidence.sources[0].source_id for segment in preview.segments}, {before_move.pk, proposed.pk})
        self.assertEqual({segment.evidence.sources[0].source_id for segment in preview.unassigned}, {original.pk})
        selected = next(segment for segment in preview.segments if segment.evidence.sources[0].source_id == proposed.pk)
        self.assertIn("draft_assertion", selected.attributions[0].provisional_reasons)
        self.assertFalse(selected.attributions[0].established)
        self.assertEqual(preview_box_temperature(**scope), baseline)
