"""Canonical PCA navigation and scientific API contracts on disposable PostgreSQL."""


from datetime import date
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Sum
from django.test import TransactionTestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.audit.services import resolve_legacy_audit_measurements
from apps.exports.services import build_weekly_measurement_csv, build_weekly_measurement_preview
from apps.measurements.models import BiologicalMeasurement, DailyTemperature

from . import qr
from .models import Box, BoxLocation, BoxMovement
from .services import reactivate_box
from .test_pca_identity_unification import PcaFixtureMixin


@skipUnless(connection.vendor == "postgresql", "Run only on independent PostgreSQL QA.")
class PcaIdentityApiTests(PcaFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.stale_donor = Box.objects.get(pk=2141)
        self.stale_canonical = Box.objects.get(pk=2135)
        self.apply()
        self.client.force_login(self.admin)
        self.headers = {"HTTP_X_ORGANIZATION_ID": "1"}

    def test_both_printed_qrs_resolve_to_exactly_the_same_canonical_detail(self):
        details = []
        for pk in (2135, 2141):
            handoff = self.client.get(f"/bac/{pk}/")
            self.assertEqual(handoff.status_code, 302)
            self.assertIn(f"scan_box={pk}", handoff.url)
            response = self.client.post(f"/api/boxes/{pk}/scan/", **self.headers)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json(), {"id": 2135, "global_code": "PCA-JKA-1.005"})
            detail = self.client.get(f"/api/boxes/{pk}/", **self.headers)
            self.assertEqual(detail.status_code, 200)
            details.append(detail.json())
        self.assertEqual(details[0], details[1])
        self.assertEqual(details[0]["id"], 2135)
        self.assertTrue(details[0]["can_create_measurement"])
        self.assertTrue(details[0]["scan_url"].endswith("/bac/2135/"))
        scans = AuditLog.objects.filter(action="scan").order_by("pk")
        self.assertEqual([row.metadata["requested_box_id"] for row in scans], [2135, 2141])
        self.assertEqual({row.metadata["box_id"] for row in scans}, {2135})

    def test_measurements_expose_recorded_source_periods_not_current_canonical_location(self):
        history = self.client.get("/api/boxes/2135/measurements/?limit=200", **self.headers).json()["results"]
        moved = [row for row in history if row["environment_source_box"] == 2141]
        own = [row for row in history if row["environment_source_box"] is None]
        self.assertEqual(len(moved), 92)
        self.assertEqual(len(own), 2)
        self.assertTrue(all(row["environment_locations"] is None for row in own))
        for row in moved:
            self.assertEqual(len(row["environment_locations"]), 1)
            self.assertTrue(row["environment_locations"][0]["end_date_unknown"])
            self.assertEqual(row["environment_locations"][0]["id"], 5945)

    def test_zone_temperatures_follow_recorded_periods_of_both_identities_without_new_points(self):
        days = {
            "source_start": (date(2024, 12, 30), self.zone2),
            "source_unproven_2025": (date(2025, 6, 1), self.zone2),
            "canonical_zone1": (date(2025, 12, 31), self.zone1),
            "w2_zone2_unproven": (date(2026, 1, 7), self.zone2),
            "canonical_zone2": (date(2026, 1, 20), self.zone2),
        }
        for day, zone in days.values():
            DailyTemperature.objects.create(thermal_zone=zone, date=day, average_temperature_c="7.50")
        before = DailyTemperature.objects.count()
        for pk in (2135, 2141):
            points = []
            for start, end in (("2024-12-01", "2025-11-30"), ("2025-12-01", "2026-01-31")):
                response = self.client.get(
                    f"/api/boxes/{pk}/temperatures/?start={start}&end={end}&limit=100", **self.headers,
                )
                self.assertEqual(response.status_code, 200)
                points += response.json()["results"]
            by_day = {row["date"]: row for row in points}
            # An unknown end proves only the recorded start day; no curve is invented around it.
            self.assertEqual(set(by_day), {"2024-12-30", "2025-12-31", "2026-01-20"})
            self.assertEqual(by_day["2024-12-30"]["supported_by"],
                             [{"location_id": 5945, "box_id": 2141, "box_code": "PCA-JKA-1.005"}])
            self.assertEqual(by_day["2025-12-31"]["supported_by"],
                             [{"location_id": 5979, "box_id": 2135, "box_code": "PCA-CHO-1.005"}])
            self.assertEqual(by_day["2026-01-20"]["supported_by"][0]["location_id"], 5980)
            self.assertEqual(by_day["2025-12-31"]["average_temperature_c"], 7.5)
        self.assertEqual(DailyTemperature.objects.count(), before)

    def test_contradicting_original_histories_are_both_kept_and_flagged_not_resolved(self):
        detail = self.client.get("/api/boxes/2135/", **self.headers).json()
        self.assertEqual({row["id"] for row in detail["locations"]}, {5979, 5980})
        self.assertEqual([row["id"] for row in detail["identity_history"][0]["locations"]], [5945])
        conflicts = detail["location_conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["basis"], "end_unknown")
        self.assertEqual({row["location_id"] for row in conflicts[0]["locations"]}, {5945, 5979})
        self.assertEqual({row["box_code"] for row in conflicts[0]["locations"]}, {"PCA-JKA-1.005", "PCA-CHO-1.005"})
        # Early 2026 JKA readings keep their own unknown-end context beside the canonical zone-1 period.
        history = self.client.get("/api/boxes/2135/measurements/?limit=200", **self.headers).json()["results"]
        week_one = next(row for row in history if row["measured_on"] == "2025-12-31"
                        and row["environment_source_box"] == 2141)
        self.assertEqual([row["id"] for row in week_one["environment_locations"]], [5945])
        self.assertEqual(BoxLocation.objects.count(), 3)
        self.assertEqual(BoxMovement.objects.count(), 0)

    def test_duplicates_keep_original_zero_and_canonical_null_without_double_counting(self):
        detail = self.client.get("/api/boxes/2135/", **self.headers).json()
        duplicates = detail["duplicate_measurements"]
        self.assertEqual(len(duplicates), 18)
        self.assertTrue(all(row["strobila_count"] == 0 for row in duplicates))
        canonical = {row["id"]: row for row in detail["biological_measurements"]}
        for row in duplicates:
            winner = canonical[row["duplicate_of"]]
            self.assertIsNone(winner["strobila_count"])
            self.assertEqual((winner["polyp_count"], winner["ephyrae_count"]),
                             (row["polyp_count"], row["ephyrae_count"]))
            self.assertEqual(winner["measured_on"], row["measured_on"])
        self.assertTrue(all(row["user_identity"] is not None for row in duplicates))
        stored = BiologicalMeasurement.objects.filter(box_id=2135).count()
        self.assertEqual(stored, 112)
        self.assertEqual(BiologicalMeasurement.objects.canonical().filter(box_id=2135).count(), 94)
        self.assertEqual({r["id"] for r in detail["biological_measurements"]} & {r["id"] for r in duplicates}, set())

    def test_canonical_history_and_duplicate_evidence_are_separate_and_read_only(self):
        detail = self.client.get("/api/boxes/2135/", **self.headers).json()
        self.assertEqual(len(detail["biological_measurements"]), 94)
        self.assertEqual(len(detail["biological_timeline"]), 94)
        self.assertEqual(len(detail["duplicate_measurements"]), 18)
        self.assertTrue(all(row["duplicate_of"] and not row["can_edit"] for row in detail["duplicate_measurements"]))
        self.assertEqual(len(detail["identity_history"]), 1)
        original = detail["identity_history"][0]
        self.assertEqual((original["id"], original["global_code"]), (2141, "PCA-JKA-1.005"))
        self.assertTrue(original["locations"][0]["end_date_unknown"])
        history = self.client.get("/api/boxes/2135/measurements/?limit=200", **self.headers).json()
        self.assertEqual(len(history["results"]), 94)
        self.assertEqual(BoxLocation.objects.count(), 3)
        self.assertEqual(BoxMovement.objects.count(), 0)
        self.assertEqual(DailyTemperature.objects.count(), 0)
        duplicate = detail["duplicate_measurements"][0]
        before = BiologicalMeasurement.objects.get(pk=duplicate["id"]).polyp_count
        denied = self.client.patch(
            f"/api/boxes/2135/measurements/{duplicate['id']}/", {"polyp_count": 999},
            content_type="application/json", **self.headers,
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=duplicate["id"]).polyp_count, before)

    def test_only_one_operational_box_in_list_inventory_dashboard_overview_and_export(self):
        response = self.client.get("/api/boxes/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["results"]], [2135])
        inventory = self.client.get(reverse("api_admin_box_inventory"), **self.headers)
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual([row["id"] for row in inventory.json()["results"]], [2135])
        dashboard = self.client.get(reverse("api_dashboard"), **self.headers).json()
        self.assertEqual(dashboard["stats"]["boxes_total"], 1)
        self.assertEqual(dashboard["stats"]["active_boxes"], 1)
        scientific = BiologicalMeasurement.objects.canonical().aggregate(total=Sum("polyp_count"))["total"]
        self.assertEqual(dashboard["stats"]["measured_polyps"], scientific)
        overview = self.client.get(reverse("api_overview_active_boxes"), {"history": "full"}, **self.headers).json()
        self.assertEqual(len(overview["results"]), 1)
        self.assertEqual(len(overview["results"][0]["measurements"]), 94)
        boxes = Box.objects.filter(pk=2135)
        preview = build_weekly_measurement_preview(boxes=boxes)
        self.assertEqual(preview["metadata"]["measurement_count"], 94)
        _, metadata = build_weekly_measurement_csv(boxes=boxes)
        self.assertEqual(metadata["measurement_count"], 94)
        self.assertEqual(metadata["box_count"], 1)

    def test_null_strobila_and_real_zero_with_user_authorship_remain_distinct(self):
        detail = self.client.get("/api/boxes/2135/", **self.headers).json()
        readings = {row["id"]: row for row in detail["biological_measurements"]}
        self.assertIsNone(readings[100052]["strobila_count"])
        for pk in (104426, 104540):
            self.assertEqual(readings[pk]["strobila_count"], 0)
            self.assertIsNotNone(readings[pk]["user_identity"])
            self.assertEqual(readings[pk]["temporal_precision"], "legacy_unverified")
            self.assertIsNone(readings[pk]["environment_source_box"])
        self.assertEqual(sum(row["environment_source_box"] == 2141 for row in readings.values()), 92)

    def test_foreign_organization_and_anonymous_qr_resolution_is_denied_without_scan(self):
        OrganizationMembership.objects.create(organization=self.other_org, user=self.admin, role="admin")
        before = AuditLog.objects.filter(action="scan").count()
        for pk in (2135, 2141):
            for suffix in ("", "scan/"):
                request = self.client.post if suffix else self.client.get
                response = request(f"/api/boxes/{pk}/{suffix}", HTTP_X_ORGANIZATION_ID="2")
                self.assertEqual(response.status_code, 404)
            svg = self.client.get(reverse("qr_boite", args=[pk]), HTTP_X_ORGANIZATION_ID="2")
            self.assertEqual(svg.status_code, 404)
        self.assertEqual(AuditLog.objects.filter(action="scan").count(), before)
        self.client.logout()
        for pk in (2135, 2141):
            self.assertEqual(self.client.get(f"/bac/{pk}/").status_code, 302)
            self.assertIn(self.client.post(f"/api/boxes/{pk}/scan/", **self.headers).status_code, (401, 403))

    def test_viewer_permissions_apply_after_both_identity_resolutions(self):
        OrganizationMembership.objects.filter(user=self.admin, organization=self.org).update(role="viewer")
        for pk in (2135, 2141):
            detail = self.client.get(f"/api/boxes/{pk}/", **self.headers)
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["id"], 2135)
            self.assertFalse(detail.json()["can_create_measurement"])
        payload = {"measured_on": "2026-10-21", "polyp_count": 0, "ephyrae_count": 0}
        self.assertEqual(self.client.post("/api/boxes/2135/measurements/", payload, **self.headers).status_code, 403)
        self.assertEqual(self.client.post("/api/boxes/2141/measurements/", payload, **self.headers).status_code, 404)

    def test_no_write_endpoint_accepts_the_retired_pk_and_nothing_changes(self):
        def state():
            return (
                [(b.pk, b.global_code, b.status, b.thermal_zone_id, b.merged_into_id, b.polyp_state_revision)
                 for b in Box.objects.order_by("pk")],
                BiologicalMeasurement.objects.count(), BoxMovement.objects.count(), BoxLocation.objects.count(),
                AuditLog.objects.exclude(action="scan").count(),
            )
        before = state()
        payload = {"thermal_zone": self.zone2.pk, "measured_on": "2026-10-21", "polyp_count": 0,
                   "ephyrae_count": 0, "reason": "x", "child_boxes": []}
        for name, method in (("archive", "post"), ("deactivate", "post"), ("activate", "post"),
                             ("qualify", "post"), ("move", "post"), ("measurements", "post"),
                             ("subcultures", "post")):
            response = getattr(self.client, method)(
                f"/api/boxes/2141/{name}/", payload, content_type="application/json", **self.headers,
            )
            self.assertIn(response.status_code, (400, 403, 404, 405, 409), (name, response.status_code))
        for pk in (104426, 100003):
            for method in ("patch", "delete"):
                response = getattr(self.client, method)(
                    f"/api/boxes/2141/measurements/{pk}/", {"polyp_count": 9},
                    content_type="application/json", **self.headers,
                )
                self.assertIn(response.status_code, (400, 403, 404, 405), (method, pk, response.status_code))
        self.assertEqual(state(), before)
        # Reads through the retired PK expose only the canonical culture of the same institution.
        history = self.client.get("/api/boxes/2141/measurements/?limit=200", **self.headers)
        if history.status_code == 200:
            self.assertEqual(len(history.json()["results"]), 94)
        self.assertEqual(self.client.get("/api/exports/boxes/2141/trend/?date_from=2026-01-01&date_to=2026-02-01",
                                         **self.headers).status_code, 404)

    def test_retired_identity_cannot_be_reactivated_written_or_resurrected(self):
        self.assertEqual(self.client.post("/api/boxes/2141/activate/", {"thermal_zone": 2}, **self.headers).status_code, 404)
        with self.assertRaises(ValidationError):
            reactivate_box(box=self.stale_donor, thermal_zone=self.zone2, user=self.admin)
        with self.assertRaises(ValidationError):
            self.stale_donor.save()
        with self.assertRaises(ValidationError):
            self.stale_canonical.save()
        with self.assertRaises(ValidationError):
            Box.objects.create(organization=self.org, strain=self.cho_strain,
                               global_code="PCA-CHO-1.005", box_number="005")
        self.assertEqual(Box.objects.get(pk=2141).merged_into_id, 2135)
        self.assertEqual(Box.objects.get(pk=2135).global_code, "PCA-JKA-1.005")

    def test_qr_image_helpers_target_canonical_pk_and_no_resolution_chain_is_followed(self):
        self.donor.refresh_from_db()
        self.canonical.refresh_from_db()
        self.assertEqual(qr.box_scan_url(self.donor), qr.box_scan_url(self.canonical))
        for pk in (2135, 2141):
            svg = self.client.get(reverse("qr_boite", args=[pk]), **self.headers)
            self.assertEqual(svg.status_code, 200)
            self.assertIn("svg", svg["Content-Type"])
        OrganizationMembership.objects.create(organization=self.other_org, user=self.admin, role="admin")
        Box.objects.filter(pk=2141).update(organization=self.other_org)
        self.assertEqual(self.client.get("/api/boxes/2141/", HTTP_X_ORGANIZATION_ID="2").status_code, 404)

    def test_old_code_and_date_audits_resolve_to_the_correct_preserved_record(self):
        evidence = BiologicalMeasurement.objects.filter(duplicate_of__isnull=False).order_by("pk").first()
        winner = evidence.duplicate_of
        logs = []
        for code in ("PCA-CHO-1.005", "PCA-JKA-1.005"):
            logs.append(AuditLog.objects.create(
                organization=self.org, user=self.admin, action="entry", object_type="box", object_id=code,
                description=f"Biological measurement for {evidence.measured_on.isoformat()}",
                metadata={"valeurs": {"date": evidence.measured_on.isoformat()}},
            ))
        resolved = resolve_legacy_audit_measurements(logs, organization_id=1)
        self.assertEqual(resolved[("PCA-CHO-1.005", evidence.measured_on)].pk, evidence.pk)
        self.assertEqual(resolved[("PCA-JKA-1.005", winner.measured_on)].pk, winner.pk)
