"""HTTP contract for scoped, request-local history previews on isolated rows."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import HttpResponseForbidden
from django.middleware.csrf import get_token
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path, resolve, reverse
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import BiologicalMeasurement, DailyTemperature
from config.middleware import BrowserSecurityHeadersMiddleware

from . import history_preview as preview_engine
from . import history_preview_service as service
from . import test_history_preview_service as fixtures
from .effective_history import (
    AdjustEventKind, AdjustEventTemporal, AdjustEventZones, EventKind, EventTarget, InsertEvent,
    ModifyEvent, TemporalPosition,
)
from .historical_movements import accept_historical_movements, plan_historical_movements
from .history_preview_api import BoxHistoryPreviewAPIView
from .history_preview_serializers import HistoryPreviewResponseSerializer
from .history_preview_validation import MAX_JUSTIFICATIONS, MAX_OPERATIONS, OperationJustification
from .models import BoxLocation, BoxMovement, HistoricalBoxMovement


class HistoryPreviewAPITests(TestCase):
    # Borrow builders only: inheriting the service TestCase would rediscover its tests.
    setUp = fixtures.HistoryPreviewServiceTests.setUp
    make_box = fixtures.HistoryPreviewServiceTests.make_box
    probe = fixtures.HistoryPreviewServiceTests.probe
    raw = fixtures.HistoryPreviewServiceTests.raw
    bucket = fixtures.HistoryPreviewServiceTests.bucket
    observe = fixtures.HistoryPreviewServiceTests.observe
    snapshot = fixtures.HistoryPreviewServiceTests.snapshot

    def url(self, box=None):
        return reverse("api_box_history_preview", kwargs={"box_id": (box or self.box).pk})

    def window(self, **overrides):
        values = {"start": self.start.isoformat(), "end": self.end.isoformat()}
        values.update(overrides)
        return values

    def operation(self, type="adjust_event_zones", **overrides):
        values = {"type": type, "target": {"key": self.target.key}}
        if type == "adjust_event_zones":
            values["destination_zone_id"] = self.c.pk
        values.update(overrides)
        return values

    def temporal(self, instant=None):
        return {"precision": "exact", "instant": (instant or self.later).isoformat()}

    def insertion(self, **overrides):
        values = {"type": "insert_event", "draft_id": "restart", "kind": "reactivation",
                  "temporal": self.temporal(), "destination_zone_id": self.c.pk}
        values.update(overrides)
        return values

    def api_client(self, actor=None):
        client = APIClient()
        client.force_authenticate(user=actor or self.admin)
        return client

    def request(self, method="get", payload=None, *, client=None, organization=None, box=None, headers=None):
        headers = {"HTTP_X_ORGANIZATION_ID": str((organization or self.org).pk)} if headers is None else headers
        # force_authenticate avoids login/session/last_login writes inside assertions.
        try:
            with CaptureQueriesContext(connection) as queries:
                response = getattr(client or self.api_client(), method)(
                    self.url(box), data=self.window() if payload is None else payload,
                    **({"format": "json"} if method != "get" else {}), **headers,
                )
        finally:
            self.assertTrue(all(row["sql"].lstrip().upper().startswith("SELECT") for row in queries),
                            [row["sql"] for row in queries if not row["sql"].lstrip().upper().startswith("SELECT")])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertTrue({"cookie", "x-organization-id"}.issubset(
            {item.strip().lower() for item in response["Vary"].split(",")},
        ))
        return response

    def success(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        return response.json()

    def rejected(self, response, code="invalid_preview_input", status=400):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["code"], code)
        self.assertNotIn("before", response.json())
        self.assertNotIn("after", response.json())
        return response.json()

    def expected(self, **overrides):
        values = dict(actor=self.admin, organization_id=self.org.pk, box_id=self.box.pk,
                      start=self.start, end=self.end)
        values.update(overrides)
        return HistoryPreviewResponseSerializer(service.preview_box_history(**values)).data

    def segment_keys(self, state):
        return {item["evidence"]["key"] for item in state["thermal"]["segments"]}

    def expected_keys(self, boundary, destination):
        return {f"temperature_measurement:{row.pk}" for zone, row in self.readings
                if zone == (self.a.pk if row.measured_at < boundary else destination)}

    @contextmanager
    def no_sources(self):
        with patch.object(service, "load_box_source_history") as history, \
                patch.object(service, "load_box_thermal_sources") as thermal:
            yield
            history.assert_not_called()
            thermal.assert_not_called()

    def test_named_route_get_and_empty_post_return_same_actual_phase3_result(self):
        self.assertEqual(self.url(), f"/api/boxes/{self.box.pk}/history/preview/")
        self.assertEqual(resolve(self.url()).url_name, "api_box_history_preview")
        expected = self.expected()
        initial = self.success(self.request())
        self.assertEqual(initial, expected)
        self.assertEqual(initial["before"], initial["after"])
        self.assertEqual((initial["organization_id"], initial["box_id"]), (self.org.pk, self.box.pk))
        for name in ("event_changes", "interval_changes", "thermal_changes", "stay_changes", "gap_changes",
                     "side_changes", "affected_event_keys", "justifications"):
            self.assertEqual(initial[name], [])
        self.assertEqual(self.success(self.request("post")), initial)
        self.assertEqual(self.success(self.request("post", self.window(operations=[], justifications=[]))), initial)
        self.assertEqual(self.success(self.request()), initial)
        self.assertFalse(initial["after"]["safety"]["publication_evaluated"])
        self.assertEqual(initial["after"]["thermal"]["continuity"], "not_inferred")

    def test_each_typed_operation_matches_real_phase3_result(self):
        cases = (
            (self.operation(), AdjustEventZones(self.target, destination_zone_id=self.c.pk)),
            (self.operation("adjust_event_temporal", temporal=self.temporal()),
             AdjustEventTemporal(self.target, TemporalPosition.exact(self.later))),
            (self.operation("adjust_event_kind", kind="reactivation",
                            target={"key": f"movement:{self.opening.pk}"}),
             AdjustEventKind(EventTarget(f"movement:{self.opening.pk}"), EventKind.REACTIVATION)),
            (self.operation("modify_event", kind="deactivation", destination_zone_id=None),
             ModifyEvent(self.target, kind=EventKind.DEACTIVATION, destination_zone_id=None)),
        )
        for wire, typed in cases:
            with self.subTest(type=wire["type"]):
                result = self.success(self.request("post", self.window(operations=[wire])))
                self.assertEqual(result, self.expected(operations=(typed,)))
                self.assertTrue(result["event_changes"])
                self.assertIn(wire["target"]["key"], result["affected_event_keys"])
                self.assertFalse(result["after"]["safety"]["publication_evaluated"])

    def test_valid_multi_edit_and_justified_insertion_preserve_inactive_gap(self):
        operations = [self.operation("modify_event", kind="deactivation", destination_zone_id=None), self.insertion()]
        reason = "Synthetic evidence for reactivation in the selected zone"
        result = self.success(self.request("post", self.window(
            operations=operations, justifications=[{"operation_index": 1, "reason": reason}],
        )))
        self.assertEqual(result, self.expected(operations=(
            ModifyEvent(self.target, kind=EventKind.DEACTIVATION, destination_zone_id=None),
            InsertEvent("restart", EventKind.REACTIVATION, TemporalPosition.exact(self.later),
                        destination_zone_id=self.c.pk),
        ), justifications=(OperationJustification(1, reason),)))
        self.assertEqual([(item["state"], item["zone_id"]) for item in result["after"]["history"]["intervals"]],
                         [("located", self.a.pk), ("inactive", None), ("located", self.c.pk)])
        gap, = [item for item in result["after"]["thermal"]["gaps"] if item["reason"] == "inactive"]
        self.assertEqual(datetime.fromisoformat(gap["end"]), self.later)
        self.assertTrue(result["gap_changes"])
        self.assertIn("draft:restart", result["affected_event_keys"])
        self.assertEqual(result["justifications"], [{"operation_index": 1, "reason": reason}])

    def test_invalid_batch_is_atomic_and_does_not_contaminate_later_requests(self):
        baseline = self.success(self.request())
        before = self.snapshot()
        invalid = self.operation(destination_zone_id=self.foreign.pk)
        result = self.rejected(self.request("post", self.window(operations=[self.operation(), invalid])),
                               "draft_operations_rejected")
        self.assertEqual(result["rejected_operations"], [{"index": 1, "code": "zone_outside_institution"}])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.success(self.request()), baseline)
        good = self.success(self.request("post", self.window(operations=[self.operation()])))
        self.assertTrue(good["event_changes"])
        self.assertEqual(self.success(self.request()), baseline)

    def test_authentication_is_required_before_parsing_or_loading(self):
        with self.no_sources():
            for method in ("get", "post"):
                response = self.request(method, client=APIClient())
                self.assertEqual(response.status_code, 403)

    def test_unauthorized_malformed_oversized_draft_is_denied_before_parsing(self):
        before = self.snapshot()
        with self.no_sources(), override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=16):
            for client, headers in (
                (APIClient(), {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}),
                (self.api_client(), {}),
                (self.api_client(), {"HTTP_X_ORGANIZATION_ID": str(self.other.pk)}),
            ):
                with self.subTest(headers=headers), CaptureQueriesContext(connection) as queries:
                    response = client.post(self.url(), "private-sentinel" * 100,
                                           content_type="application/json", **headers)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn("private-sentinel", response.content.decode())
                self.assertTrue(all(row["sql"].lstrip().upper().startswith("SELECT") for row in queries))
        self.assertEqual(self.snapshot(), before)

    def test_explicit_active_organization_header_is_required(self):
        for value in (None, "", "not-an-institution", "0", "-1", "999999", str(self.other.pk)):
            with self.subTest(header=value), self.no_sources():
                headers = {} if value is None else {"HTTP_X_ORGANIZATION_ID": value}
                for method in ("get", "post"):
                    self.assertEqual(self.request(method, headers=headers).status_code, 403)

    def test_regular_and_superuser_require_active_admin_membership(self):
        for superuser in (False, True):
            get_user_model().objects.filter(pk=self.admin.pk).update(is_superuser=superuser)
            self.admin.is_superuser = superuser
            for role, active in (("viewer", True), ("lab_technician", True), ("admin", False), ("admin", True)):
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(role=role, is_active=active)
                with self.subTest(superuser=superuser, role=role, active=active):
                    for method in ("get", "post"):
                        if role == "admin" and active:
                            self.success(self.request(method))
                        else:
                            with self.no_sources():
                                self.assertEqual(self.request(method).status_code, 403)
            OrganizationMembership.objects.filter(pk=self.membership.pk).delete()
            with self.no_sources():
                self.assertEqual(self.request().status_code, 403)
                self.assertEqual(self.request("post").status_code, 403)
            self.membership = OrganizationMembership.objects.create(user=self.admin, organization=self.org, role="admin")

    def test_inactive_actor_is_denied_even_with_stale_authenticated_instance(self):
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertTrue(self.admin.is_active)
        with self.no_sources():
            for method in ("get", "post"):
                self.assertEqual(self.request(method).status_code, 403)

    def test_inactive_institution_is_denied(self):
        type(self.org).objects.filter(pk=self.org.pk).update(is_active=False)
        with self.no_sources():
            for method in ("get", "post"):
                self.assertEqual(self.request(method).status_code, 403)

    def test_foreign_merged_and_missing_boxes_are_indistinguishable(self):
        OrganizationMembership.objects.create(user=self.admin, organization=self.other, role="admin")
        merged = self.make_box("002", merged_into=self.box)
        missing = type(self.box)(pk=999999)
        for superuser in (False, True):
            get_user_model().objects.filter(pk=self.admin.pk).update(is_superuser=superuser)
            self.admin.is_superuser = superuser
            for box in (self.foreign_box, merged, missing):
                with self.subTest(superuser=superuser, box=box.pk), self.no_sources():
                    for method in ("get", "post"):
                        result = self.rejected(self.request(method, box=box), "box_not_found", 404)
                        self.assertEqual(result, {"code": "box_not_found"})

    def test_zone_and_event_targets_cannot_cross_box_or_institution(self):
        other_box = self.make_box("003")
        movements = [BoxMovement.objects.create(box=box, to_thermal_zone=zone, moved_at=self.start,
                                               kind=BoxMovement.Kind.INITIAL_LOCATION)
                     for box, zone in ((other_box, self.a), (self.foreign_box, self.foreign))]
        cases = [(self.operation(target={"key": f"movement:{row.pk}"}), "target_not_found") for row in movements]
        cases += [(self.operation(target={"key": "movement:999999"}), "target_not_found"),
                  (self.operation(destination_zone_id=self.foreign.pk), "zone_outside_institution"),
                  (self.operation(destination_zone_id=999999), "zone_outside_institution")]
        for operation, code in cases:
            with self.subTest(operation=operation):
                data = self.rejected(self.request("post", self.window(operations=[operation])),
                                     "draft_operations_rejected")
                self.assertEqual(data["rejected_operations"], [{"index": 0, "code": code}])

    def test_destination_date_and_kind_reassign_actual_thermal_evidence(self):
        all_keys = {f"temperature_measurement:{row.pk}" for _, row in self.readings}
        for operation, boundary, destination in (
            (self.operation(), self.moved_at, self.c.pk),
            (self.operation("adjust_event_temporal", temporal=self.temporal()), self.later, self.b.pk),
            (self.operation("modify_event", kind="deactivation", destination_zone_id=None), self.moved_at, None),
        ):
            with self.subTest(type=operation["type"]):
                result = self.success(self.request("post", self.window(operations=[operation])))
                self.assertEqual(self.segment_keys(result["before"]), self.expected_keys(self.moved_at, self.b.pk))
                before_evidence = {item["evidence"]["key"]: item["evidence"] for item in
                                   result["before"]["thermal"]["segments"] + result["before"]["thermal"]["unassigned"]}
                after_evidence = {item["evidence"]["key"]: item["evidence"] for item in
                                  result["after"]["thermal"]["segments"] + result["after"]["thermal"]["unassigned"]}
                self.assertEqual(after_evidence, before_evidence)
                before_events = {item["identity"]["key"]: item for item in result["before"]["history"]["events"]}
                for event in result["after"]["history"]["events"]:
                    self.assertEqual(event["identity"], before_events[event["identity"]["key"]]["identity"])
                    self.assertEqual(event["original"], before_events[event["identity"]["key"]]["original"])
                expected = self.expected_keys(boundary, destination)
                self.assertEqual(self.segment_keys(result["after"]), expected)
                self.assertEqual({item["evidence"]["key"] for item in result["after"]["thermal"]["unassigned"]},
                                 all_keys - expected)
                for name in ("event_changes", "interval_changes", "thermal_changes", "stay_changes"):
                    self.assertTrue(result[name])
                for segment in result["after"]["thermal"]["segments"]:
                    for attribution in segment["attributions"]:
                        self.assertFalse(attribution["established"])
                        self.assertIn("draft_assertion", attribution["provisional_reasons"])

    def test_all_thermal_source_kinds_preserve_zero_negative_null_and_missing(self):
        self.observe(7, "0")
        negative = self.raw(self.probe("negative", zone=self.b), at=self.later, value="-2.75")
        bucket = self.bucket(self.probe("condensed", zone=self.b), at=self.moved_at)
        daily = DailyTemperature.objects.create(thermal_zone=self.b, date=self.moved_at.date(),
                                                average_temperature_c=Decimal("0"))
        result = self.success(self.request())
        thermal = result["before"]["thermal"]
        evidence = {item["evidence"]["key"]: item["evidence"] for item in thermal["segments"] + thermal["unassigned"]}
        self.assertEqual({item["source_kind"] for item in evidence.values()}, {
            "raw_probe_measurement", "condensed_bucket", "mixed_daily_aggregate", "historical_setpoint",
        })
        self.assertEqual(Decimal(evidence[f"temperature_measurement:{negative.pk}"]["temperature_c"]), Decimal("-2.75"))
        condensed = evidence[f"probe_temperature_bucket:{bucket.pk}"]
        self.assertIsNone(condensed["last_temperature_c"])
        self.assertEqual(condensed["measurement_count"], 3)
        self.assertEqual(Decimal(condensed["minimum_c"]), Decimal("-1.23"))
        self.assertEqual(Decimal(evidence[f"daily_temperature:{daily.pk}"]["temperature_c"]), 0)
        self.assertTrue(any(item["temperature_c"] is not None and Decimal(item["temperature_c"]) == 0
                            for item in evidence.values()))
        empty_zone = type(self.a).objects.create(organization=self.org, name="No readings")
        changed = self.success(self.request("post", self.window(operations=[
            self.operation(destination_zone_id=empty_zone.pk),
        ])))
        self.assertTrue(changed["after"]["thermal"]["gaps"])
        self.assertFalse(any(item["evidence"]["zone_id"] == empty_zone.pk
                             for item in changed["after"]["thermal"]["segments"]))

    def test_unverified_historical_event_stays_unplaced_with_original_provenance(self):
        origin = BoxLocation.objects.create(box=self.box, thermal_zone=self.a,
                                            starts_at=self.start, ends_at=self.moved_at)
        destination = BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=self.moved_at)
        receipt, = HistoricalBoxMovement.objects.bulk_create([HistoricalBoxMovement(
            box=self.box, iso_year=2026, iso_week=7, from_thermal_zone=self.a, to_thermal_zone=self.b,
            origin_location=origin, destination_location=destination,
            candidate_key="a" * 64, plan_fingerprint="b" * 64,
            notes="private-receipt-notes", evidence={"private-source-snapshot": "private-payload"},
        )])
        before = self.snapshot()
        response = self.request()
        result = self.success(response)
        self.assertEqual(result, self.expected())
        event, = [item for item in result["before"]["history"]["unplaced_events"]
                  if item["identity"]["key"] == f"historical:{receipt.pk}"]
        self.assertFalse(event["established"])
        self.assertEqual(event["temporal"]["precision"], "unknown")
        self.assertIsNone(event["temporal"]["instant"])
        self.assertEqual(event["original"]["details"]["stored_iso_week"], 7)
        self.assertIn("historical_box_movement", {item["source"] for item in event["original"]["sources"]})
        self.assertTrue(result["before"]["safety"]["has_temporal_uncertainty"])
        self.assertIn("insufficient_temporal_precision",
                      {item["code"] for item in result["before"]["history"]["conflicts"]})
        for secret in ("private-receipt-notes", "private-source-snapshot", "private-payload"):
            self.assertNotIn(secret, response.content.decode())
        self.assertEqual(self.snapshot(), before)

    def test_accepted_historical_week_is_not_serialized_as_an_exact_movement(self):
        box = self.make_box("004", thermal_zone=self.b)
        start = datetime(2025, 12, 15, tzinfo=self.start.tzinfo)
        boundary = datetime(2025, 12, 29, tzinfo=self.start.tzinfo)
        origin = BoxLocation.objects.create(box=box, thermal_zone=self.a, starts_at=start, ends_at=boundary)
        BoxLocation.objects.create(box=box, thermal_zone=self.b, starts_at=boundary)
        for instant in (boundary - timedelta(days=7), boundary):
            BiologicalMeasurement.objects.create(box=box, measured_on=instant.date(), polyp_count=0, ephyrae_count=0)
        plan = plan_historical_movements(organization_id=self.org.pk, box_ids=[box.pk])
        accept_historical_movements(
            organization_id=self.org.pk, box_ids=[box.pk], candidate_ids=[plan["candidates"][0]["id"]],
            expected_fingerprint=plan["fingerprint"], actor=self.admin, notes="Synthetic reviewed week",
        )
        receipt = HistoricalBoxMovement.objects.get(box=box)
        before = self.snapshot()
        result = self.success(self.request(box=box, payload={"start": start.isoformat(), "end": boundary.isoformat()}))
        events = {item["identity"]["key"]: item for item in result["before"]["history"]["events"]}
        self.assertEqual(set(events), {f"location:{origin.pk}:start", f"historical:{receipt.pk}"})
        event = events[f"historical:{receipt.pk}"]
        self.assertTrue(event["established"])
        self.assertEqual(event["kind"], "reconstructed_movement")
        self.assertEqual(event["temporal"]["precision"], "iso_week")
        self.assertEqual((event["temporal"]["iso_year"], event["temporal"]["iso_week"]), (2025, 52))
        self.assertIsNone(event["temporal"]["instant"])
        self.assertEqual(events[f"location:{origin.pk}:start"]["temporal"]["precision"], "recorded_boundary")
        self.assertTrue(result["before"]["safety"]["has_temporal_uncertainty"])
        self.assertEqual(self.snapshot(), before)

    def test_week_and_observation_interval_edits_keep_uncertainty_not_fake_instants(self):
        week = TemporalPosition.week(2026, 7, self.start.tzinfo)
        for temporal in (
            {"precision": "iso_week", "iso_year": 2026, "iso_week": 7,
             "not_before": week.not_before.isoformat(), "before": week.before.isoformat()},
            {"precision": "observation_interval", "not_before": self.moved_at.isoformat(),
             "before": self.later.isoformat()},
            {"precision": "unknown"},
        ):
            with self.subTest(precision=temporal["precision"]):
                result = self.success(self.request("post", self.window(operations=[
                    self.operation("adjust_event_temporal", temporal=temporal),
                ])))
                history = result["after"]["history"]
                event, = [item for item in history["events"] + history["unplaced_events"]
                          if item["identity"]["key"] == self.target.key]
                self.assertEqual(event["temporal"]["precision"], temporal["precision"])
                self.assertIsNone(event["temporal"]["instant"])
                self.assertTrue(result["after"]["safety"]["has_temporal_uncertainty"])
                self.assertEqual(event["original"]["temporal"]["precision"], "exact")
                self.assertFalse(result["after"]["safety"]["publication_evaluated"])

    def test_omitted_zone_preserves_value_but_explicit_null_clears_it(self):
        preserved = self.success(self.request("post", self.window(operations=[
            self.operation("modify_event", temporal=self.temporal()),
        ])))
        changed, = [item for item in preserved["after"]["history"]["events"]
                    if item["identity"]["key"] == self.target.key]
        self.assertEqual(changed["destination_zone_id"], self.b.pk)
        self.assertEqual(changed["origin_zone_id"], self.a.pk)
        data = self.rejected(self.request("post", self.window(operations=[
            self.operation("modify_event", destination_zone_id=None),
        ])), "draft_operations_rejected")
        self.assertEqual(data["rejected_operations"], [{"index": 0, "code": "invalid_shape"}])

    def test_draft_provisional_occupancy_and_conflicts_remain_visible(self):
        result = self.success(self.request("post", self.window(operations=[
            self.operation(origin_zone_id=self.b.pk),
        ])))
        self.assertTrue(result["after"]["safety"]["has_conflicts"])
        self.assertTrue(result["after"]["safety"]["has_provisional_occupancy"])
        self.assertTrue(result["after"]["history"]["conflicts"])
        self.assertTrue(result["conflict_changes"])
        self.assertFalse(result["after"]["safety"]["publication_evaluated"])

    def test_invalid_dates_and_windows_are_rejected_before_source_reads(self):
        for start, end in (
            ("2026-02-09", self.end.isoformat()),
            ("2026-02-09T00:00:00", self.end.isoformat()),
            ("2026-02-30T00:00:00Z", self.end.isoformat()),
            ("2026-02-09T00:00:00+25:00", self.end.isoformat()),
            ("2026-02-09T00:00:00+01:60", self.end.isoformat()),
            ("9999-12-31T22:00:00Z", "9999-12-31T23:30:00Z"),
            ("0001-01-01T00:00:00Z", "0001-01-02T00:00:00Z"),
            ("0001-01-01T00:00:00+01:00", self.end.isoformat()),
            ("sensitive-invalid-date", self.end.isoformat()),
            (self.start.isoformat(), self.start.isoformat()),
            (self.end.isoformat(), self.start.isoformat()),
            (self.start.isoformat(), (self.start + timedelta(days=366, microseconds=1)).isoformat()),
        ):
            with self.subTest(start=start, end=end), self.no_sources():
                for method in ("get", "post"):
                    self.rejected(self.request(method, self.window(start=start, end=end)))
        for payload in ({}, {"start": self.start.isoformat()}, self.window(start=None), self.window(end=123)):
            with self.subTest(payload=payload), self.no_sources():
                self.rejected(self.request("post", payload))

    def test_explicit_dst_offsets_are_instants_not_naive_wall_times(self):
        for start, end, seconds in (
            ("2026-10-25T02:30:00+02:00", "2026-10-25T02:30:00+01:00", 3600),
            ("2026-03-29T01:30:00+01:00", "2026-03-29T03:30:00+02:00", 3600),
            ("2026-03-29T02:30:00+01:00", "2026-03-29T04:30:00+02:00", 3600),
        ):
            with self.subTest(start=start):

                for method in ("get", "post"):
                    data = self.success(self.request(method, self.window(start=start, end=end)))
                    thermal = data["after"]["thermal"]
                    self.assertEqual((datetime.fromisoformat(thermal["end"]) -
                                      datetime.fromisoformat(thermal["start"])).total_seconds(), seconds)
        with self.no_sources():
            self.rejected(self.request("post", self.window(operations=[
                self.operation("adjust_event_temporal", temporal={"precision": "exact", "instant": "2026-10-25T02:30:00"}),
            ])))
        self.success(self.request(payload=self.window(end=(self.start + timedelta(days=366)).isoformat())))

    def test_scope_anchors_and_unknown_fields_are_never_client_controlled(self):
        for key in ("actor", "organization_id", "box_id", "anchors", "source", "thermal_source", "mode", "session", "sensitive-field"):
            for method in ("get", "post"):
                with self.subTest(key=key, method=method), self.no_sources():
                    response = self.request(method, self.window(**{key: "private-sentinel"}))
                    self.rejected(response)
                    self.assertNotIn("private-sentinel", response.content.decode())
                    self.assertNotIn(key, response.json().get("errors", {}))
        for operation in (
            self.operation(extra="private-sentinel"),
            self.operation(target={"key": self.target.key, "organization_id": self.other.pk}),
            self.operation("adjust_event_temporal", temporal={**self.temporal(), "extra": "private-sentinel"}),
        ):
            with self.subTest(operation=operation), self.no_sources():
                self.rejected(self.request("post", self.window(operations=[operation])))

    def test_query_parameters_are_unambiguous_and_post_does_not_accept_query_scope(self):
        with self.no_sources():
            self.rejected(self.request(payload={**self.window(), "start": [self.start.isoformat(), self.start.isoformat()]}))
            client = self.api_client()
            response = client.post(self.url() + "?start=private-sentinel", self.window(), format="json",
                                   HTTP_X_ORGANIZATION_ID=str(self.org.pk))
            self.rejected(response)

    def test_strict_operation_shapes_types_and_enums(self):
        invalid = (
            None, 1, [], {}, {"type": "AdjustEventZones"}, {"type": "private-sentinel"},
            self.operation(destination_zone_id=True), self.operation(destination_zone_id="1"),
            self.operation(destination_zone_id=0), self.operation(target="private-sentinel"),
            self.operation(target={"key": 1}),
            self.operation(target={"key": self.target.key, "expected_evidence_fingerprint": "private-sentinel"}),
            self.operation("adjust_event_kind", kind="private-sentinel"),
            self.operation("adjust_event_temporal", temporal={"precision": "private-sentinel"}),
            self.operation("adjust_event_temporal", temporal={"precision": "iso_week", "iso_year": True, "iso_week": 7}),
            self.insertion(draft_id=1),
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.no_sources():
                response = self.request("post", self.window(operations=[operation]))
                self.rejected(response)
                self.assertNotIn("private-sentinel", response.content.decode())
        for payload in ([], None, "private-sentinel", 42):
            with self.subTest(payload=payload), self.no_sources():
                # None is a real JSON null here, not the request helper's default.
                response = self.api_client().post(self.url(), data=json.dumps(payload),
                                                  content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk))
                self.rejected(response)

    def test_justifications_are_validated_as_indexed_typed_bounded_objects(self):
        for reasons in (
            [], [{"operation_index": 0, "reason": ""}], [{"operation_index": True, "reason": "reason"}],
            [{"operation_index": 1, "reason": "reason"}],
            [{"operation_index": 0, "reason": "reason"}] * 2,
            [{"operation_index": 0, "reason": "x" * 4001}],
            [{"operation_index": 0, "reason": "reason", "scope": self.other.pk}],
        ):
            with self.subTest(reasons=reasons), self.no_sources():
                response = self.request("post", self.window(operations=[self.insertion()], justifications=reasons))
                self.assertEqual(response.status_code, 400, response.content)
        with self.no_sources():
            self.rejected(self.request("post", self.window(justifications=[{"operation_index": 0, "reason": "reason"}])),
                          "invalid_justification")

    def test_operation_and_justification_limits_precede_source_loading(self):
        with self.no_sources(), override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=1024 * 1024):
            self.rejected(self.request("post", self.window(operations=[self.operation()] * (MAX_OPERATIONS + 1))))
            self.rejected(self.request("post", self.window(justifications=[
                {"operation_index": 0, "reason": "reason"},
            ] * (MAX_JUSTIFICATIONS + 1))))
        with self.no_sources(), override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=128):
            self.rejected(self.request("post", self.window(operations=[self.operation()] * MAX_OPERATIONS)),
                          "request_too_large", 413)

    def test_errors_do_not_echo_sensitive_target_reason_enum_or_body(self):
        sentinel = "private-sentinel-password-source-snapshot"
        for payload in (
            self.window(operations=[self.operation(target={"key": sentinel})],
                        justifications=[{"operation_index": 0, "reason": sentinel}]),
            self.window(operations=[self.operation("adjust_event_kind", kind=sentinel)]),
            self.window(**{sentinel: sentinel}),
            self.window(operations=[self.operation(target={"key": self.target.key,
                                                          "expected_evidence_fingerprint": "0" * 64})]),
        ):
            with self.subTest(payload=payload):
                response = self.request("post", payload)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(sentinel, response.content.decode())
                self.assertNotIn("details", response.json())
                for rejection in response.json().get("rejected_operations", []):
                    self.assertEqual(set(rejection), {"index", "code"})

    def test_success_and_error_paths_leave_every_model_row_unchanged(self):
        self.observe(7, "0")
        self.bucket(self.probe("snapshot-bucket", zone=self.b))
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            self.success(self.request())
            self.success(self.request("post", self.window(operations=[self.operation()])))
            self.rejected(self.request("post", self.window(operations=[self.operation(destination_zone_id=self.foreign.pk)])),
                          "draft_operations_rejected")
            self.rejected(self.request("post", self.window(start="private-sentinel")))
            self.rejected(self.request(box=self.foreign_box), "box_not_found", 404)
            self.assertEqual(self.request(headers={}).status_code, 403)
            self.assertEqual(self.request(client=APIClient()).status_code, 403)
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(row["sql"].lstrip().upper().startswith("SELECT") for row in queries))
        self.assertEqual(self.snapshot(), before)

    def test_sources_are_isolated_and_same_client_can_switch_organizations(self):
        OrganizationMembership.objects.create(user=self.admin, organization=self.other, role="admin")
        foreign_probe = self.probe("foreign", zone=self.foreign, organization=self.other)
        malformed_local = self.probe("local-foreign-zone", zone=self.foreign)
        malformed_foreign = self.probe("foreign-local-zone", zone=self.a, organization=self.other)
        forbidden = [self.raw(probe) for probe in (foreign_probe, malformed_local, malformed_foreign)]
        client = self.api_client()
        initial = self.success(self.request(client=client))
        all_evidence = initial["before"]["thermal"]["segments"] + initial["before"]["thermal"]["unassigned"]
        self.assertTrue(all(item["evidence"]["organization_id"] == self.org.pk for item in all_evidence))
        self.assertTrue({f"temperature_measurement:{row.pk}" for row in forbidden}.isdisjoint(
            {item["evidence"]["key"] for item in all_evidence},
        ))
        switched = self.success(self.request(client=client, organization=self.other, box=self.foreign_box))
        self.assertEqual((switched["organization_id"], switched["box_id"]), (self.other.pk, self.foreign_box.pk))
        self.rejected(self.request(client=client, organization=self.other), "box_not_found", 404)
        self.rejected(self.request(client=client, box=self.foreign_box), "box_not_found", 404)
        self.assertEqual(self.success(self.request(client=client)), initial)

    def test_json_parser_disallowed_methods_head_and_options(self):
        client = self.api_client()
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}
        with self.no_sources():
            for body in ('{"start": "private-sentinel",', "not-json-private-sentinel"):
                response = client.post(self.url(), body, content_type="application/json", **headers)
                self.assertEqual(self.rejected(response, "invalid_json"), {"code": "invalid_json"})
                self.assertNotIn("private-sentinel", response.content.decode())
            response = client.post(self.url(), self.window(), format="multipart", **headers)
            self.assertEqual(response.status_code, 415)
            for method in ("put", "patch", "delete", "trace"):
                response = getattr(client, method)(self.url(), **headers)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(set(response["Allow"].split(", ")), {"GET", "POST", "HEAD", "OPTIONS"})
            self.assertEqual(client.options(self.url(), **headers).status_code, 200)
        response = client.head(self.url(), self.window(), **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_resource_budget_errors_are_explicit_and_never_truncate(self):
        cases = (
            (service, "MAX_HISTORY_ROWS", "history_row_limit_exceeded"),
            (service, "MAX_REFERENCE_ROWS", "reference_row_limit_exceeded"),
            (service, "MAX_THERMAL_EVIDENCE", "thermal_evidence_limit_exceeded"),
            (preview_engine, "MAX_EVENT_COMPARISONS", "history_work_limit_exceeded"),
            (preview_engine, "MAX_ATTRIBUTION_WORK", "thermal_attribution_work_limit_exceeded"),
        )
        before = self.snapshot()
        for module, name, code in cases:
            for method in ("get", "post"):
                with self.subTest(limit=name, method=method), patch.object(module, name, 0):
                    self.assertEqual(self.rejected(self.request(method), code),
                                     {"code": code, "rejected_operations": []})
        probe = self.probe("overlapping-buckets", zone=self.b)
        self.bucket(probe, at=self.moved_at)
        self.bucket(probe, at=self.moved_at + timedelta(minutes=1))
        with patch.object(preview_engine, "MAX_STORAGE_OVERLAP_REFERENCES", 0):
            self.rejected(self.request(), "thermal_overlap_limit_exceeded")
        # Only the deliberately added fixture rows can have changed.
        after = self.snapshot()
        for label in before:
            if label not in {"measurements.Probe", "measurements.ProbeTemperatureBucket"}:
                self.assertEqual(after[label], before[label])

    def test_successful_provenance_withholds_untrusted_stored_text(self):
        sentinel = "private-source-password-sentinel"
        reading, source = self.observe(7, "0", source_value=sentinel)
        row = self.raw(self.probe("untrusted-label", zone=self.b), at=self.later,
                       value="-1", source=sentinel)
        response = self.request()
        data = self.success(response)
        self.assertNotIn(sentinel, response.content.decode())
        finding, = [item for item in data["before"]["thermal"]["findings"]
                    if item["code"] == "historical_setpoint_unestablished"]
        self.assertEqual(finding["withheld_detail_keys"], ["source_values"])
        self.assertEqual(finding["details"]["reading_id"], reading.pk)
        self.assertIn(source.pk, finding["details"]["evidence_ids"])
        evidence = [item["evidence"] for item in data["before"]["thermal"]["segments"]
                    + data["before"]["thermal"]["unassigned"]]
        proof, = [item for item in evidence if item["key"] == f"temperature_measurement:{row.pk}"]
        self.assertEqual(Decimal(proof["temperature_c"]), -1)
        reference, = proof["sources"]
        self.assertIsNone(reference["basis"])
        self.assertTrue(reference["basis_withheld"])
        self.assertEqual(reference["source_id"], row.pk)

    def test_pre_view_csrf_rejections_are_private_without_invoking_preview(self):
        preview_view = BoxHistoryPreviewAPIView.as_view()

        def protected_preview(request, box_id):
            # A non-exempt callback exercises native Django CSRF rejection
            # before APIView.dispatch, unlike DRF's internal session CSRF path.
            return preview_view(request, box_id=box_id)

        urlconf = type("PreviewCSRFURLConf", (), {"urlpatterns": [
            path("api/boxes/<int:box_id>/history/preview/", protected_preview),
        ]})
        url = self.url()
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.admin)
        csrf_request = RequestFactory().get("/")
        valid_token = get_token(csrf_request)
        client.cookies[settings.CSRF_COOKIE_NAME] = csrf_request.META["CSRF_COOKIE"]
        # Generate a correctly shaped token that belongs to another cookie.
        invalid_token = get_token(RequestFactory().get("/"))
        before = self.snapshot()
        session_before = dict(client.session)
        with override_settings(ROOT_URLCONF=urlconf), CaptureQueriesContext(connection) as queries:
            for token in (None, invalid_token):
                with self.subTest(token="missing" if token is None else "invalid"), \
                        patch.object(BoxHistoryPreviewAPIView, "dispatch") as dispatch, \
                        patch("apps.cultures.history_preview_api.preview_box_history") as preview:
                    headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}
                    if token is not None:
                        headers["HTTP_X_CSRFTOKEN"] = token
                    response = client.post(url, self.window(), format="json", **headers)
                    self.assertEqual(response.status_code, 403)
                    dispatch.assert_not_called()
                    preview.assert_not_called()
                    self.assertEqual(response["Cache-Control"], "private, no-store")
                    self.assertIn("cookie", response.get("Vary", "").lower())
                    self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                    self.assertEqual(response["X-Frame-Options"], "DENY")
                    for protected in (b'"before"', b'"after"', b'"thermal"', b'"event_changes"'):
                        self.assertNotIn(protected, response.content)
                    self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
            response = client.post(url, self.window(), format="json",
                                   HTTP_X_ORGANIZATION_ID=str(self.org.pk), HTTP_X_CSRFTOKEN=valid_token)
            self.success(response)
            self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertTrue(all(row["sql"].lstrip().upper().startswith("SELECT") for row in queries))
        self.assertEqual(dict(client.session), session_before)
        self.assertEqual(self.snapshot(), before)

    def test_cache_header_middleware_matches_only_exact_preview_route_and_preserves_headers(self):
        for route, expected in (
            (self.url(), "private, no-store"),
            (f"/api/boxes/{self.box.pk}/history/", "public, max-age=60"),
            (self.url() + "extra/", "public, max-age=60"),
            (self.url().rstrip("/"), "public, max-age=60"),
            ("/api/boxes/not-an-id/history/preview/", "public, max-age=60"),
            ("/unrelated/", "public, max-age=60"),
        ):
            with self.subTest(route=route):
                original = HttpResponseForbidden("csrf denied", headers={
                    "Cache-Control": "public, max-age=60", "Vary": "Origin, Cookie",
                    "Content-Security-Policy": "default-src 'none'", "X-Test-Header": "retained",
                })
                with self.assertNumQueries(0):
                    response = BrowserSecurityHeadersMiddleware(lambda request: original)(RequestFactory().post(route))
                self.assertIs(response, original)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.content, b"csrf denied")
                self.assertEqual(response["Cache-Control"], expected)
                self.assertEqual(response["Vary"], "Origin, Cookie")
                self.assertEqual(response["Content-Security-Policy"], "default-src 'none'")
                self.assertEqual(response["X-Test-Header"], "retained")

    def test_session_authentication_keeps_usual_csrf_enforcement(self):
        client = APIClient(enforce_csrf_checks=True)
        # Session creation happens before the read-only measurement boundary.
        client.force_login(self.admin)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}
        request = RequestFactory().get("/")
        token = get_token(request)
        client.cookies[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(self.url(), self.window(), **headers)
            self.success(response)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            for csrf_headers in ({}, {"HTTP_X_CSRFTOKEN": get_token(RequestFactory().get("/"))}):
                with patch("apps.cultures.history_preview_api.preview_box_history") as preview:
                    response = client.post(self.url(), self.window(), format="json", **csrf_headers, **headers)
                    self.assertEqual(response.status_code, 403)
                    self.assertIn("CSRF", response.json()["detail"])
                    self.assertEqual(response["Cache-Control"], "private, no-store")
                    self.assertEqual(set(response.json()), {"detail"})
                    preview.assert_not_called()
            response = client.post(self.url(), self.window(), format="json", HTTP_X_CSRFTOKEN=token, **headers)
            self.success(response)
            self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertTrue(all(row["sql"].lstrip().upper().startswith("SELECT") for row in queries),
                        [row["sql"] for row in queries if not row["sql"].lstrip().upper().startswith("SELECT")])
        self.assertEqual(self.snapshot(), before)
