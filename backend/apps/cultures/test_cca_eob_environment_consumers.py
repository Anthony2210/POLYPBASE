"""Synthetic consumers of biological reassignment, never environmental repair."""

import csv
from datetime import date, datetime, time, timezone
from decimal import Decimal
from io import StringIO

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.exports.services import (
    build_weekly_measurement_csv,
    build_weekly_measurement_preview,
    get_measurement_export_eligibility,
    select_measurement_export_data,
)
from apps.measurements.models import (
    BiologicalMeasurement, DailyTemperature, Probe, TemperatureMeasurement,
)

from .models import Box, BoxLocation, BoxMovement, ThermalZone
from .temperature_history import box_temperature_queryset
from .test_cca_eob_correction import CcaEobFixtureMixin


def at(month, day):
    return datetime(2026, month, day, 12, tzinfo=timezone.utc)


@override_settings(SECURE_SSL_REDIRECT=False)
class CcaEobEnvironmentConsumerTests(CcaEobFixtureMixin, TestCase):
    def boxes_queryset(self):
        return Box.objects.filter(organization=self.org).order_by("pk")

    def add_distinct_histories(self):
        self.historical_zones = {}
        self.current_zones = {}
        for number, box in self.boxes.items():
            historical = ThermalZone.objects.create(
                organization=self.org, name=f"Historical zone {number}",
                target_temperature_c=Decimal(number + 10),
                salinity_psu=Decimal("0.00"), notes=f"Historical settings {number}",
            )
            current = ThermalZone.objects.create(
                organization=self.org, name=f"Current zone {number}",
                target_temperature_c=Decimal(number + 20), capacity=number,
            )
            self.historical_zones[number] = historical
            self.current_zones[number] = current
            first = BoxLocation.objects.create(
                box=box, thermal_zone=self.zone, starts_at=at(1, 1),
                ends_at=at(7, 1), notes="First recorded stay",
            )
            historical_period = BoxLocation.objects.create(
                box=box, thermal_zone=historical, starts_at=at(7, 1),
                ends_at=at(9, 1), notes="Summer stay",
            )
            current_period = BoxLocation.objects.create(
                box=box, thermal_zone=current, starts_at=at(9, 1), notes="Current stay",
            )
            for origin, destination in ((first, historical_period), (historical_period, current_period)):
                BoxMovement.objects.create(
                    box=box, from_thermal_zone=origin.thermal_zone,
                    to_thermal_zone=destination.thermal_zone, moved_at=destination.starts_at,
                    origin_location=origin, destination_location=destination,
                    kind=BoxMovement.Kind.ORDINARY, user=self.writer,
                    notes="Recorded independently of biological attribution",
                )
            Box.objects.filter(pk=box.pk).update(thermal_zone=current)
        self.add_temperatures()

    def add_temperatures(self):
        days = sorted({measurement.measured_on for measurement in self.measurements})
        for zone in ThermalZone.objects.filter(organization=self.org).order_by("pk"):
            probe = Probe.objects.create(
                organization=self.org, thermal_zone=zone, code=f"QA-{zone.pk}",
                probe_type=Probe.ProbeType.OTHER, location="Synthetic laboratory",
                notes="Preserve probe metadata",
            )
            value = (
                Decimal("0.00") if zone == self.historical_zones.get(4)
                else Decimal("15.25") if zone == self.historical_zones.get(5)
                else Decimal("16.50") if zone == self.historical_zones.get(6)
                else Decimal("27.75")
            )
            for day in days:
                TemperatureMeasurement.objects.create(
                    probe=probe, measured_at=datetime.combine(day, time(12), tzinfo=timezone.utc),
                    temperature_c=value, source="Synthetic probe", user=self.writer,
                    raw_data={"temperature": str(value), "quality": "synthetic"},
                )
                DailyTemperature.objects.create(
                    thermal_zone=zone, date=day, average_temperature_c=value,
                    min_temperature_c=value, max_temperature_c=value, measurement_count=1,
                )

    def environmental_snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (
                BoxLocation, BoxMovement, ThermalZone, Probe,
                TemperatureMeasurement, DailyTemperature,
            )
        }

    def apply_preserving_product_fields(self):
        environment = self.environmental_snapshot()
        boxes = list(self.boxes_queryset().values())
        for box in boxes:
            box.pop("polyp_state_revision")
        measurements = {
            row["id"]: {key: value for key, value in row.items() if key != "box_id"}
            for row in BiologicalMeasurement.objects.order_by("pk").values()
        }
        temperatures = self.temperature_histories()
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.apply(plan=plan)
        self.assertEqual(self.environmental_snapshot(), environment)
        after_boxes = list(self.boxes_queryset().values())
        for box in after_boxes:
            box.pop("polyp_state_revision")
        self.assertEqual(after_boxes, boxes)
        self.assertEqual({
            row["id"]: {key: value for key, value in row.items() if key != "box_id"}
            for row in BiologicalMeasurement.objects.order_by("pk").values()
        }, measurements)
        self.assertEqual(self.temperature_histories(), temperatures)

    def temperature_histories(self):
        return {
            box.pk: list(box_temperature_queryset(box, date(2026, 8, 1), date(2026, 8, 31)).values())
            for box in self.boxes_queryset()
        }

    def export(self, day, **filters):
        options = dict(boxes=self.boxes_queryset(), date_from=day, date_to=day, **filters)
        content, metadata = build_weekly_measurement_csv(**options)
        rows = list(csv.DictReader(StringIO(content)))
        self.assertEqual(len(rows), 1)
        preview = build_weekly_measurement_preview(**options)
        self.assertEqual(preview["metadata"], metadata)
        self.assertEqual(len(preview["points"]), 1)
        return rows[0], preview["points"][0], metadata

    def assert_zone_selection(self, measurement, zone, expected_box):
        options = dict(
            boxes=self.boxes_queryset(), date_from=measurement.measured_on,
            date_to=measurement.measured_on, zone_ids=[zone.pk],
        )
        boxes, measurements = select_measurement_export_data(**options)
        ids, count, latest = get_measurement_export_eligibility(**options)
        expected_ids = [] if expected_box is None else [expected_box.pk]
        self.assertEqual([box.pk for box in boxes], expected_ids)
        self.assertEqual(ids, expected_ids)
        self.assertEqual(count, len(expected_ids))
        self.assertEqual([row.pk for row in measurements], [] if expected_box is None else [measurement.pk])
        self.assertEqual(latest, {} if expected_box is None else {expected_box.pk: measurement.measured_on})

    def test_csv_preview_and_zone_selection_follow_target_history_preserving_zero(self):
        self.add_distinct_histories()
        moving = ((self.measurements[0], 5, 4), (self.measurements[1], 6, 5), (self.measurements[2], 6, 4))
        for measurement, source, target in moving:
            self.assert_zone_selection(measurement, self.historical_zones[source], self.boxes[source])
            self.assert_zone_selection(measurement, self.historical_zones[target], None)
        row, point, metadata = self.export(self.measurements[0].measured_on)
        self.assertEqual(row["2.05_temperature"], "15.25")
        self.assertEqual(point["average_temperature_c"], 15.25)
        self.assertEqual(metadata["box_ids"], [self.boxes[5].pk])
        zero_row, zero_point, _ = self.export(self.measurements[2].measured_on)
        self.assertEqual(zero_row["2.06_polypes"], "0")
        self.assertEqual(zero_row["2.06_ephyrules"], "0")
        self.assertEqual(zero_point["average_temperature_c"], 16.5)

        self.apply_preserving_product_fields()

        for measurement, source, target in moving:
            self.assert_zone_selection(measurement, self.historical_zones[source], None)
            self.assert_zone_selection(measurement, self.historical_zones[target], self.boxes[target])
            self.assert_zone_selection(measurement, self.current_zones[target], None)
        for measurement, _, target in moving:
            row, point, metadata = self.export(measurement.measured_on)
            code = f"2.{target:02}"
            self.assertEqual(row[f"{code}_polypes"], str(measurement.polyp_count))
            self.assertEqual(row[f"{code}_ephyrules"], str(measurement.ephyrae_count))
            expected_temperature = "0" if target == 4 else "15.25"
            self.assertEqual(row[f"{code}_temperature"], expected_temperature)
            self.assertEqual(point["average_temperature_c"], float(expected_temperature))
            self.assertEqual(point["measurement_count"], 1)
            self.assertEqual(point["polyp_count"], measurement.polyp_count)
            self.assertEqual(point["ephyrae_count"], measurement.ephyrae_count)
            self.assertEqual(metadata["box_ids"], [self.boxes[target].pk])

    def test_chart_biological_ids_move_but_location_and_movement_arrays_do_not(self):
        self.add_distinct_histories()
        self.client.force_login(self.admin)

        def trends():
            result = {}
            for number, box in self.boxes.items():
                response = self.client.get(
                    reverse("api_export_box_trend", args=[box.pk]),
                    {"date_from": "2026-01-01", "date_to": "2026-09-30"},
                    HTTP_X_ORGANIZATION_ID=str(self.org.pk),
                )
                self.assertEqual(response.status_code, 200)
                result[number] = response.json()
            return result

        before = trends()
        expected_before = {4: [], 5: [0, 3], 6: [1, 2]}
        for number, indices in expected_before.items():
            self.assertEqual(
                [row["id"] for row in before[number]["biological_measurements"]],
                [self.measurements[index].pk for index in indices],
            )
            self.assertEqual(len(before[number]["locations"]), 3)
            self.assertEqual(len(before[number]["movements"]), 2)

        self.apply_preserving_product_fields()

        after = trends()
        for number, indices in {4: [0, 2], 5: [1, 3], 6: []}.items():
            self.assertEqual(
                [row["id"] for row in after[number]["biological_measurements"]],
                [self.measurements[index].pk for index in indices],
            )
            for field in ("locations", "movements"):
                self.assertEqual(after[number][field], before[number][field])
        zero = next(row for row in after[4]["biological_measurements"] if row["id"] == self.measurements[2].pk)
        self.assertEqual((zero["polyp_count"], zero["ephyrae_count"], zero["strobila_count"]), (0, 0, 0))
        self.assertEqual(Decimal(zero["salinity_psu"]), Decimal("0.00"))

    def test_temperature_queryset_remains_box_scoped_and_preserves_exact_aggregates(self):
        self.add_distinct_histories()
        before = self.temperature_histories()
        for number, box in self.boxes.items():
            rows = before[box.pk]
            self.assertEqual(len(rows), 4)
            self.assertEqual({row["thermal_zone_id"] for row in rows}, {self.historical_zones[number].pk})
            expected = {4: Decimal("0.00"), 5: Decimal("15.25"), 6: Decimal("16.50")}[number]
            for row in rows:
                for field in ("average_temperature_c", "min_temperature_c", "max_temperature_c"):
                    self.assertEqual(row[field], expected)
                self.assertEqual(row["measurement_count"], 1)
        self.apply_preserving_product_fields()
        self.assertEqual(self.temperature_histories(), before)

    def test_different_current_zone_fallback_uses_target_setpoint_without_occupancy(self):
        self.historical_zones = {}
        target_zone = ThermalZone.objects.create(
            organization=self.org, name="Target fallback", target_temperature_c=Decimal("0.0"),
        )
        ThermalZone.objects.filter(pk=self.zone.pk).update(target_temperature_c=Decimal("18.5"))
        Box.objects.filter(pk=self.boxes[4].pk).update(thermal_zone=target_zone)
        measurement = self.measurements[0]
        self.assert_zone_selection(measurement, self.zone, self.boxes[5])
        self.assertEqual(self.export(measurement.measured_on)[0]["2.05_temperature"], "18.5")

        self.apply_preserving_product_fields()

        self.assert_zone_selection(measurement, self.zone, None)
        self.assert_zone_selection(measurement, target_zone, self.boxes[4])
        row, point, _ = self.export(measurement.measured_on)
        self.assertEqual(row["2.04_temperature"], "0")
        self.assertEqual(point["average_temperature_c"], 0.0)
        self.assertTrue(all(not rows for rows in self.temperature_histories().values()))
        self.assertFalse(BoxLocation.objects.exists())
        self.assertFalse(BoxMovement.objects.exists())
        self.assertFalse(DailyTemperature.objects.exists())
        self.assertFalse(TemperatureMeasurement.objects.exists())

    def test_target_history_gap_keeps_biology_but_does_not_invent_environment(self):
        self.add_distinct_histories()
        # A gap is missing evidence, not an invitation to extend the old stay.
        BoxMovement.objects.filter(box=self.boxes[4]).delete()
        BoxLocation.objects.filter(box=self.boxes[4], starts_at=at(7, 1)).update(ends_at=at(8, 1))
        measurement = self.measurements[2]
        self.assertEqual(self.export(measurement.measured_on)[0]["2.06_temperature"], "16.5")

        self.apply_preserving_product_fields()

        row, point, metadata = self.export(measurement.measured_on)
        self.assertEqual(row["2.04_polypes"], "0")
        self.assertEqual(row["2.04_ephyrules"], "0")
        self.assertEqual(row["2.04_temperature"], "")
        self.assertIsNone(point["average_temperature_c"])
        self.assertEqual(point["measurement_count"], 1)
        self.assertEqual(metadata["measurement_count"], 1)
        self.assertEqual(metadata["box_ids"], [self.boxes[4].pk])
        for zone in (self.historical_zones[4], self.current_zones[4], self.zone):
            self.assert_zone_selection(measurement, zone, None)
        self.assertEqual(self.temperature_histories()[self.boxes[4].pk], [])
        self.assertTrue(DailyTemperature.objects.filter(
            thermal_zone=self.historical_zones[4], date=measurement.measured_on,
            average_temperature_c=Decimal("0.00"),
        ).exists())
