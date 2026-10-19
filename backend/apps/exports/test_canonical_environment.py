"""Original environment and canonical export contracts on disposable PostgreSQL."""

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from unittest import skipUnless

from django.db import connection
from django.test import TestCase

from apps.cultures.models import Box, BoxLocation, ThermalZone
from apps.measurements.models import BiologicalMeasurement, DailyTemperature
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .services import (
    build_weekly_measurement_csv, build_weekly_measurement_preview,
    get_measurement_export_eligibility, select_measurement_export_data,
)


def at(day):
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


@skipUnless(connection.vendor == "postgresql", "Requires disposable PostgreSQL")
class CanonicalEnvironmentExportTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Synthetic preserved environment")
        species = Species.objects.create(scientific_name="Aurelia environment", genus_species_code="AEN")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="AEN-QA-1")
        self.zone1 = ThermalZone.objects.create(
            organization=self.org, name="Original first zone", target_temperature_c=Decimal("5.0"),
        )
        self.zone2 = ThermalZone.objects.create(
            organization=self.org, name="Original second zone", target_temperature_c=Decimal("10.0"),
        )
        self.box = Box.objects.create(
            organization=self.org, strain=self.strain, global_code="AEN-QA-1.001", local_code="1.01",
            box_number="001", thermal_zone=self.zone2,
        )
        self.source = Box.objects.create(
            organization=self.org, strain=self.strain, global_code="AEN-QA-1.002", box_number="002",
            status=Box.Status.INACTIVE,
        )
        BoxLocation.objects.create(
            box=self.box, thermal_zone=self.zone1, starts_at=at(date(2025, 12, 29)),
            ends_at=at(date(2026, 1, 12)),
        )
        BoxLocation.objects.create(
            box=self.box, thermal_zone=self.zone2, starts_at=at(date(2026, 1, 12)),
        )
        self.source_period = BoxLocation.objects.create(
            box=self.source, thermal_zone=self.zone2, starts_at=at(date(2024, 12, 30)),
            ends_at=None, end_date_unknown=True,
        )
        self.day = date(2026, 6, 1)

    def boxes(self):
        return Box.objects.filter(organization=self.org)

    def locations(self):
        return list(BoxLocation.objects.order_by("pk").values())

    def measurement(self, box, day, **values):
        return BiologicalMeasurement.objects.create(
            box=box, measured_on=day, **{"polyp_count": 10, "ephyrae_count": 0, "strobila_count": None, **values},
        )

    def moved(self, day=None):
        row = self.measurement(self.source, day or self.day)
        BiologicalMeasurement.objects.filter(pk=row.pk).update(box=self.box, environment_source_box=self.source)
        row.refresh_from_db()
        return row

    def csv(self, **filters):
        content, metadata = build_weekly_measurement_csv(boxes=self.boxes(), **filters)
        return list(csv.DictReader(StringIO(content))), metadata

    def temperature(self, zone, day, value):
        DailyTemperature.objects.create(
            thermal_zone=zone, date=day, average_temperature_c=Decimal(value), measurement_count=1,
        )

    def test_unknown_source_end_keeps_null_temperature_not_canonical_occupancy(self):
        moved = self.moved()
        self.measurement(self.box, self.day + timedelta(days=2), duplicate_of=moved)
        self.temperature(self.zone2, self.day, "17.50")
        before = self.locations()
        rows, metadata = self.csv(date_from=self.day, date_to=self.day)
        self.assertEqual(metadata["measurement_count"], 1)
        self.assertEqual(rows[0]["1.01_polypes"], "10")
        self.assertEqual(rows[0]["1.01_temperature"], "")
        preview = build_weekly_measurement_preview(boxes=self.boxes(), date_from=self.day, date_to=self.day)
        self.assertEqual(preview["points"][0]["measurement_count"], 1)
        self.assertIsNone(preview["points"][0]["average_temperature_c"])
        for include_other in (False, True):
            ids, count, latest = get_measurement_export_eligibility(
                boxes=self.boxes(), zone_ids=[self.zone2.pk], date_from=self.day, date_to=self.day,
                include_other_zones=include_other,
            )
            self.assertEqual((ids, count, latest), ([], 0, {}))
        self.assertEqual(self.locations(), before)

    def test_source_anchor_day_keeps_original_zone_and_real_zero_temperature(self):
        day = date(2024, 12, 30)
        self.moved(day)
        self.temperature(self.zone2, day, "0.00")
        rows, _ = self.csv(date_from=day, date_to=day, zone_ids=[self.zone2.pk])
        self.assertEqual(rows[0]["1.01_temperature"], "0")
        ids, count, _ = get_measurement_export_eligibility(
            boxes=self.boxes(), date_from=day, date_to=day, zone_ids=[self.zone2.pk],
        )
        self.assertEqual((ids, count), ([self.box.pk], 1))
        self.assertFalse(get_measurement_export_eligibility(
            boxes=self.boxes(), date_from=day, date_to=day, zone_ids=[self.zone1.pk],
        )[0])

    def test_known_source_period_uses_original_not_canonical_zone(self):
        day = date(2026, 1, 5)
        self.source_period.end_date_unknown = False
        self.source_period.ends_at = at(date(2026, 2, 1))
        self.source_period.save()
        self.moved(day)
        self.temperature(self.zone1, day, "6.75")
        self.temperature(self.zone2, day, "12.50")
        rows, _ = self.csv(date_from=day, date_to=day)
        self.assertEqual(rows[0]["1.01_temperature"], "12.5")
        for zone, expected in ((self.zone1, []), (self.zone2, [self.box.pk])):
            self.assertEqual(get_measurement_export_eligibility(
                boxes=self.boxes(), date_from=day, date_to=day, zone_ids=[zone.pk],
            )[0], expected)

    def test_no_source_period_fallback_never_borrows_canonical_current_zone(self):
        self.source_period.delete()
        self.moved()
        rows, _ = self.csv(date_from=self.day, date_to=self.day)
        self.assertEqual(rows[0]["1.01_temperature"], "")
        self.assertEqual(get_measurement_export_eligibility(
            boxes=self.boxes(), date_from=self.day, date_to=self.day, zone_ids=[self.zone2.pk],
        )[0], [])
        Box.objects.filter(pk=self.source.pk).update(thermal_zone=self.zone1)
        rows, _ = self.csv(date_from=self.day, date_to=self.day, zone_ids=[self.zone1.pk])
        self.assertEqual(rows[0]["1.01_temperature"], "5")
        self.assertEqual(get_measurement_export_eligibility(
            boxes=self.boxes(), date_from=self.day, date_to=self.day, zone_ids=[self.zone2.pk],
        )[0], [])

    def test_include_other_zones_qualifies_by_canonical_rows_original_environment(self):
        moved = self.moved()
        self.measurement(self.box, self.day + timedelta(days=2), duplicate_of=moved)
        day = date(2026, 10, 5)
        ordinary = self.measurement(self.box, day)
        self.temperature(self.zone2, day, "11.25")
        for include_other, expected in ((False, [ordinary.pk]), (True, [moved.pk, ordinary.pk])):
            boxes, rows = select_measurement_export_data(
                boxes=self.boxes(), zone_ids=[self.zone2.pk], include_other_zones=include_other,
            )
            self.assertEqual([box.pk for box in boxes], [self.box.pk])
            self.assertEqual([row.pk for row in rows], expected)
        rows, metadata = self.csv(zone_ids=[self.zone2.pk], include_other_zones=True)
        self.assertEqual(metadata["measurement_count"], 2)
        self.assertEqual(next(row for row in rows if row["Date"] == "2026_S23")["1.01_temperature"], "")
        self.assertEqual(next(row for row in rows if row["Date"] == "2026_S41")["1.01_temperature"], "11.25")

    def test_exact_112_row_shape_retains_18_duplicates_and_untouched_final_two_weeks(self):
        jka = [self.measurement(self.source, date(2024, 12, 30) + timedelta(weeks=index)) for index in range(92)]
        cho = [self.measurement(self.box, date.fromisocalendar(2026, week, 1)) for week in range(23, 43)]
        before = {row["id"]: row for row in BiologicalMeasurement.objects.values()}
        periods = self.locations()
        target_by_week = {row.week_start: row for row in jka}
        for duplicate in cho[:18]:
            BiologicalMeasurement.objects.filter(pk=duplicate.pk).update(
                duplicate_of=target_by_week[duplicate.week_start],
            )
        BiologicalMeasurement.objects.filter(pk__in=[row.pk for row in jka]).update(
            box=self.box, environment_source_box=self.source,
        )
        after = {row["id"]: row for row in BiologicalMeasurement.objects.values()}
        self.assertEqual(len(after), 112)
        self.assertEqual(BiologicalMeasurement.objects.canonical().count(), 94)
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.box).count(), 112)
        self.assertEqual(BiologicalMeasurement.objects.filter(duplicate_of__isnull=False).count(), 18)
        for row in jka:
            self.assertEqual(after[row.pk], dict(before[row.pk], box_id=self.box.pk, environment_source_box_id=self.source.pk))
        for row in cho[:18]:
            self.assertEqual(after[row.pk], dict(before[row.pk], duplicate_of_id=target_by_week[row.week_start].pk))
        for row in cho[18:]:
            self.assertEqual(after[row.pk], before[row.pk])
        self.assertEqual(self.locations(), periods)
        rows, metadata = self.csv()
        self.assertEqual(metadata["measurement_count"], 94)
        self.assertEqual(metadata["box_ids"], [self.box.pk])
        self.assertEqual(len(rows), 94)
        for row in rows:
            self.assertEqual(row["1.01_polypes"], "10")
            self.assertEqual(row["1.01_ephyrules"], "0")
            self.assertEqual(row["1.01_temperature"], "10" if row["Date"] in {"2025_S1", "2026_S41", "2026_S42"} else "")
        self.assertEqual(self.locations(), periods)
