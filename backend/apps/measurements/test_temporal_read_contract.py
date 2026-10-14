"""Temporal read contracts without enabling week-only database persistence."""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import DateField, Exists, Value
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.api_views import (
    box_inventory_queryset_for_user,
    box_list_queryset_for_user,
    box_queryset_for_user,
)
from apps.cultures.models import Box
from apps.cultures.serializers import (
    BiologicalMeasurementSerializer,
    BoxDetailSerializer,
    BoxInventorySerializer,
    BoxListSerializer,
    HISTORICAL_BOX_IMPORT_DATE,
)
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import BiologicalMeasurement
from .serializers import BiologicalMeasurementCreateSerializer


READ_ORDER = ("-week_start", "-created_at", "-pk")


class TemporalReadContractTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Temporal read laboratory")
        self.foreign_org = Organization.objects.create(name="Foreign temporal laboratory")
        self.user = get_user_model().objects.create_user(
            username="temporal-reader", first_name="Alice", last_name="Observer",
            email="temporal@example.org",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.ADMIN,
        )
        species = Species.objects.create(scientific_name="Aurelia temporal", genus_species_code="ATR")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="ATR-READ-1")
        self.foreign_strain = Strain.objects.create(
            organization=self.foreign_org, species=species, code="ATR-FOREIGN-1",
        )
        self.next_number = 0
        self.box = self.make_box()
        self.client.force_login(self.user)

    def make_box(self, *, foreign=False, **kwargs):
        self.next_number += 1
        strain = self.foreign_strain if foreign else self.strain
        return Box.objects.create(
            organization=self.foreign_org if foreign else self.org,
            strain=strain, global_code=f"{strain.code}.{self.next_number:03d}",
            box_number=f"{self.next_number:03d}", **kwargs,
        )

    def measurement(self, box=None, *, day=date(2026, 9, 16), **kwargs):
        values = {"polyp_count": 12, "ephyrae_count": 3, "strobila_count": 0, "user": self.user}
        values.update(kwargs)
        return BiologicalMeasurement.objects.create(box=box or self.box, measured_on=day, **values)

    def get(self, name, args=None, params=None):
        return self.client.get(
            reverse(name, args=args), params or {}, HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )

    def snapshot(self):
        return (
            list(BiologicalMeasurement.objects.order_by("pk").values()),
            list(Box.objects.order_by("pk").values()),
            list(AuditLog.objects.order_by("pk").values()),
        )

    def assert_temporal(self, data, row):
        self.assertEqual(data["week_start"], row.week_start.isoformat())
        self.assertEqual(data["measured_on"], row.measured_on.isoformat() if row.measured_on else None)
        self.assertEqual(data["temporal_precision"], row.temporal_precision)

    def test_date_fields_keep_iso_representation_and_date_writability(self):
        fields = BiologicalMeasurementSerializer().fields
        self.assertIsInstance(fields["week_start"], serializers.DateField)
        self.assertTrue(fields["week_start"].read_only)
        self.assertIsInstance(fields["measured_on"], serializers.DateField)
        self.assertFalse(fields["measured_on"].allow_null)
        self.assertFalse(fields["measured_on"].read_only)

    def test_exact_and_legacy_dates_expose_week_start_without_inference(self):
        for day, precision in (
            (date(2021, 1, 3), BiologicalMeasurement.TemporalPrecision.EXACT_DATE),
            (date(2021, 1, 6), BiologicalMeasurement.TemporalPrecision.LEGACY_UNVERIFIED),
        ):
            with self.subTest(precision=precision):
                row = self.measurement(day=day, temporal_precision=precision)
                self.assert_temporal(BiologicalMeasurementSerializer(row).data, row)
        response = self.get("api_box_measurements", [self.box.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(item["measured_on"], item["week_start"], item["temporal_precision"])
             for item in response.data["results"]],
            [("2021-01-06", "2021-01-04", "legacy_unverified"),
             ("2021-01-03", "2020-12-28", "exact_date")],
        )

    def test_nonpersisted_iso_week_fixture_serializes_null_without_a_synthetic_day(self):
        before = self.snapshot()
        row = BiologicalMeasurement(
            box=self.box, measured_on=None, week_start=date(2020, 12, 28),
            temporal_precision="iso_week", created_at=timezone.now(),
            polyp_count=0, ephyrae_count=0, strobila_count=None,
        )
        self.assertIsNone(row.pk)
        with self.assertNumQueries(0):
            data = BiologicalMeasurementSerializer(row).data
        self.assert_temporal(data, row)
        self.assertEqual(data["polyp_count"], 0)
        self.assertIsNone(data["strobila_count"])
        self.assertEqual(self.snapshot(), before)

    def test_shared_serializers_reject_explicit_null_on_create_and_partial_update(self):
        row = self.measurement()
        before = self.snapshot()
        for serializer_class in (BiologicalMeasurementCreateSerializer, BiologicalMeasurementSerializer):
            for instance, partial in ((None, False), (row, True)):
                with self.subTest(serializer=serializer_class.__name__, partial=partial):
                    serializer = serializer_class(instance, data={"measured_on": None}, partial=partial)
                    self.assertFalse(serializer.is_valid())
                    self.assertIn("measured_on", serializer.errors)
                    self.assertEqual(serializer.errors["measured_on"][0].code, "null")
        self.assertEqual(self.snapshot(), before)

    def test_http_post_and_patch_reject_null_without_mutation(self):
        row = self.measurement()
        before = self.snapshot()
        for method, url in (
            (self.client.post, reverse("api_box_measurements", args=[self.box.pk])),
            (self.client.patch, reverse("api_box_measurement_detail", args=[self.box.pk, row.pk])),
        ):
            with self.subTest(method=method.__name__):
                response = method(
                    url, {"measured_on": None}, content_type="application/json",
                    HTTP_X_ORGANIZATION_ID=str(self.org.pk),
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("measured_on", response.data)
                self.assertEqual(self.snapshot(), before)

    def test_explicit_date_patch_still_changes_date_not_precision_or_provenance(self):
        row = self.measurement()
        serializer = BiologicalMeasurementSerializer(row, data={"measured_on": "2026-09-24"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["measured_on"], date(2026, 9, 24))
        response = self.client.patch(
            reverse("api_box_measurement_detail", args=[self.box.pk, row.pk]),
            {"measured_on": "2026-09-24", "week_start": "2000-01-03"},
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.measured_on, date(2026, 9, 24))
        self.assertEqual(row.week_start, date(2026, 9, 21))
        self.assertEqual(row.temporal_precision, "legacy_unverified")
        self.assertEqual(row.observer_status, "legacy_unestablished")
        self.assertEqual(row.origin, "legacy_unclassified")
        self.assert_temporal(response.data, row)

    def year_boundary_rows(self):
        rows = [self.measurement(day=day) for day in (
            date(2020, 12, 23), date(2021, 1, 3), date(2021, 1, 6),
        )]
        now = timezone.now()
        for index, row in enumerate(rows):
            BiologicalMeasurement.objects.filter(pk=row.pk).update(created_at=now - timedelta(days=index))
        return rows

    def test_latest_and_history_follow_weeks_across_iso_year_not_insertion_clock(self):
        rows = self.year_boundary_rows()
        latest = rows[-1]
        detail = self.get("api_box_detail", [self.box.pk])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["latest_measurement"]["id"], latest.pk)
        self.assertEqual([item["id"] for item in detail.data["biological_measurements"]],
                         [row.pk for row in reversed(rows)])
        for name in ("api_box_list", "api_admin_box_inventory"):
            with self.subTest(endpoint=name):
                response = self.get(name)
                self.assertEqual(response.status_code, 200)
                item = next(item for item in response.data["results"] if item["id"] == self.box.pk)
                self.assertEqual(item["latest_measurement"]["id"], latest.pk)
                self.assert_temporal(item["latest_measurement"], latest)
        response = self.get("api_box_measurements", [self.box.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data["results"]],
                         [row.pk for row in reversed(rows)])

    def test_latest_recorded_salinity_follows_weeks_without_replacing_missing_values(self):
        self.measurement(day=date(2020, 12, 23), salinity_psu="31.00")
        self.measurement(day=date(2021, 1, 3), salinity_psu="0.00")
        latest = self.measurement(day=date(2021, 1, 6), salinity_psu=None)
        response = self.get("api_box_list")
        self.assertEqual(response.status_code, 200)
        item = response.data["results"][0]
        self.assertEqual(item["latest_measurement"]["id"], latest.pk)
        self.assertIsNone(item["latest_measurement"]["salinity_psu"])
        self.assertEqual(item["latest_salinity_psu"], "0.00")
        self.assertEqual(BoxListSerializer().get_latest_salinity_psu(self.box), "0.00")
        detail = box_queryset_for_user(self.user, organization_ids=[self.org.pk]).get(pk=self.box.pk)
        self.assertEqual(BoxDetailSerializer().get_latest_salinity_psu(detail), "0.00")

    def test_read_prefetches_have_deterministic_week_created_at_pk_order(self):
        # Valid persisted rows cannot tie on week_start: verify the defensive
        # ordering directly rather than bypassing the weekly uniqueness constraint.
        for factory in (box_queryset_for_user, box_list_queryset_for_user, box_inventory_queryset_for_user):
            with self.subTest(queryset=factory.__name__):
                queryset = factory(self.user, organization_ids=[self.org.pk])
                prefetch = next(
                    item for item in queryset._prefetch_related_lookups
                    if getattr(item, "prefetch_through", None) == "biological_measurements"
                    and getattr(item, "to_attr", None) in (None, "list_latest_measurements")
                )
                self.assertEqual(tuple(prefetch.queryset.query.order_by), READ_ORDER)

    def test_unprefetched_serializer_fallbacks_use_the_same_total_order(self):
        self.year_boundary_rows()
        manager_class = type(self.box.biological_measurements)
        for serializer_class in (BoxListSerializer, BoxDetailSerializer, BoxInventorySerializer):
            with self.subTest(serializer=serializer_class.__name__):
                original = manager_class.order_by
                calls = []

                def record_order(manager, *fields):
                    calls.append(fields)
                    return original(manager, *fields)

                with patch.object(manager_class, "order_by", record_order):
                    result = serializer_class().get_latest_measurement(self.box)
                self.assertEqual(result["measured_on"], "2021-01-06")
                self.assertEqual(calls, [READ_ORDER])

    def test_measurement_get_query_uses_week_and_pk_tiebreaker(self):
        self.year_boundary_rows()
        with CaptureQueriesContext(connection) as queries:
            response = self.get("api_box_measurements", [self.box.pk])
        self.assertEqual(response.status_code, 200)
        ordered_reads = [query["sql"] for query in queries if
                         'FROM "measurements_biologicalmeasurement"' in query["sql"]
                         and 'ORDER BY "measurements_biologicalmeasurement"."week_start" DESC' in query["sql"]]
        self.assertTrue(ordered_reads, "Measurement GET must order by the ISO week key.")
        for sql in ordered_reads:
            self.assertIn('"measurements_biologicalmeasurement"."created_at" DESC', sql)
            self.assertIn('"measurements_biologicalmeasurement"."id" DESC', sql)

    def test_inventory_exists_annotation_distinguishes_absence_zero_and_normal(self):
        zero = self.make_box(status=Box.Status.PENDING_REVIEW)
        normal = self.make_box(status=Box.Status.PENDING_REVIEW)
        self.measurement(zero, polyp_count=0, ephyrae_count=0)
        self.measurement(normal)
        queryset = box_inventory_queryset_for_user(self.user, organization_ids=[self.org.pk])
        self.assertIsInstance(queryset.query.annotations.get("has_biological_measurement_annotation"), Exists)
        self.assertEqual(dict(queryset.values_list("pk", "has_biological_measurement_annotation")),
                         {self.box.pk: False, zero.pk: True, normal.pk: True})
        response = self.get("api_admin_box_inventory")
        self.assertEqual(response.status_code, 200)
        results = {item["id"]: item for item in response.data["results"]}
        self.assertIsNone(results[self.box.pk]["latest_measurement"])
        self.assertEqual(results[zero.pk]["latest_measurement"]["polyp_count"], 0)
        self.assertEqual(results[normal.pk]["latest_measurement"]["polyp_count"], 12)
        missing = self.get("api_admin_box_inventory", params={"measurement_filter": "none"})
        self.assertEqual(missing.status_code, 200)
        self.assertEqual([item["id"] for item in missing.data["results"]], [self.box.pk])

    def test_inventory_absence_filter_uses_existence_even_when_latest_date_is_null(self):
        self.measurement()
        empty = self.make_box(status=Box.Status.PENDING_REVIEW)
        original = box_inventory_queryset_for_user

        def without_date(*args, **kwargs):
            # Simulate a nullable read projection, never an unsupported DB row.
            return original(*args, **kwargs).annotate(
                latest_measurement_on_annotation=Value(None, output_field=DateField()),
            )

        before = self.snapshot()
        with patch("apps.cultures.api_views.box_inventory_queryset_for_user", side_effect=without_date):
            response = self.get("api_admin_box_inventory", params={"measurement_filter": "none"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data["results"]], [empty.pk])
        self.assertEqual(self.snapshot(), before)

    def test_historical_first_date_and_calendar_age_semantics_are_unchanged(self):
        imported = self.make_box(status=Box.Status.PENDING_REVIEW)
        regular = self.make_box(status=Box.Status.PENDING_REVIEW)
        empty_imported = self.make_box(status=Box.Status.PENDING_REVIEW)
        boundary = self.make_box(status=Box.Status.PENDING_REVIEW)
        future = self.make_box(status=Box.Status.PENDING_REVIEW)
        Box.objects.filter(pk__in=[imported.pk, empty_imported.pk]).update(created_on=HISTORICAL_BOX_IMPORT_DATE)
        Box.objects.filter(pk=regular.pk).update(created_on=date(2026, 7, 4))
        first = self.measurement(imported, day=date(2021, 5, 12), polyp_count=0)
        self.measurement(imported, day=date(2026, 2, 27), polyp_count=0)
        self.measurement(regular, day=date(2025, 12, 31))
        self.measurement(boundary, day=date(2026, 2, 28))
        self.measurement(future, day=date(2026, 6, 15))
        response = self.get("api_admin_box_inventory")
        self.assertEqual(response.status_code, 200)
        results = {item["id"]: item for item in response.data["results"]}
        self.assertEqual(results[imported.pk]["inventory_created_on"], first.measured_on.isoformat())
        self.assertEqual(results[empty_imported.pk]["inventory_created_on"], "2026-07-03")
        self.assertEqual(results[regular.pk]["inventory_created_on"], "2026-07-04")
        response = self.get("api_admin_box_inventory", params={
            "measurement_filter": "older_than", "age_months": 3, "reference_date": "2026-05-31",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["id"] for item in response.data["results"]}, {imported.pk, regular.pk})

    def test_provenance_and_legacy_writer_identity_remain_compatible_in_all_reads(self):
        row = self.measurement(
            observer_status="known", original_observer=self.user,
            origin="file_import", import_format="csv", imported_by=self.user,
        )
        identity = {"first_name": "Alice", "last_name": "Observer", "email": "temporal@example.org"}
        payloads = [BiologicalMeasurementSerializer(row).data]
        detail = self.get("api_box_detail", [self.box.pk])
        self.assertEqual(detail.status_code, 200)
        payloads.extend([detail.data["latest_measurement"], detail.data["biological_measurements"][0]])
        for name in ("api_box_list", "api_admin_box_inventory"):
            response = self.get(name)
            self.assertEqual(response.status_code, 200)
            payloads.append(response.data["results"][0]["latest_measurement"])
        response = self.get("api_box_measurements", [self.box.pk])
        self.assertEqual(response.status_code, 200)
        payloads.append(response.data["results"][0])
        for data in payloads:
            self.assert_temporal(data, row)
            self.assertEqual(data["user"], self.user.username)
            self.assertEqual(data["user_identity"], identity)
            self.assertEqual(data["observer_status"], "known")
            self.assertEqual(data["origin"], "file_import")
            self.assertEqual(data["import_format"], "csv")
            for prefix in ("original_observer", "imported_by"):
                self.assertEqual(data[f"{prefix}_identity"], identity)
                self.assertEqual(data[f"{prefix}_display"], "Alice OBSERVER")
                self.assertNotIn(prefix, data)
                self.assertNotIn(f"{prefix}_id", data)

    def test_foreign_institution_ids_are_blocked_and_lists_are_scoped(self):
        foreign = self.make_box(foreign=True)
        foreign_row = self.measurement(foreign)
        own_row = self.measurement()
        before = self.snapshot()
        for name in ("api_box_detail", "api_box_measurements"):
            response = self.get(name, [foreign.pk])
            self.assertEqual(response.status_code, 404)
        for box, row in ((foreign, foreign_row), (self.box, foreign_row), (foreign, own_row)):
            response = self.client.patch(
                reverse("api_box_measurement_detail", args=[box.pk, row.pk]),
                {"measured_on": "2026-09-24"}, content_type="application/json",
                HTTP_X_ORGANIZATION_ID=str(self.org.pk),
            )
            self.assertEqual(response.status_code, 404)
        response = self.client.post(
            reverse("api_box_measurements", args=[foreign.pk]),
            {"measured_on": "2026-09-24"}, content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )
        self.assertEqual(response.status_code, 404)
        # Also cover a multi-institution administrator with an explicit active scope.
        OrganizationMembership.objects.create(
            user=self.user, organization=self.foreign_org, role=OrganizationMembership.Role.ADMIN,
        )
        for name in ("api_box_list", "api_admin_box_inventory"):
            response = self.get(name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual({item["id"] for item in response.data["results"]}, {self.box.pk})
        self.assertEqual(self.snapshot(), before)

    def query_count(self, name, args=None):
        with CaptureQueriesContext(connection) as queries:
            response = self.get(name, args, {"limit": 100})
        self.assertEqual(response.status_code, 200)
        return len(queries), len(response.data["results"])

    def test_list_and_inventory_query_growth_is_bounded_with_provenance_accounts(self):
        values = {"observer_status": "known", "original_observer": self.user,
                  "origin": "file_import", "import_format": "csv", "imported_by": self.user}
        self.measurement(**values)
        before = {name: self.query_count(name) for name in ("api_box_list", "api_admin_box_inventory")}
        for _ in range(12):
            box = self.make_box()
            self.measurement(box, **values)
        for name, (small_count, small_size) in before.items():
            with self.subTest(endpoint=name):
                large_count, large_size = self.query_count(name)
                self.assertEqual(small_size, 1)
                self.assertEqual(large_size, 13)
                self.assertLessEqual(large_count, small_count + 1)

    def test_measurement_get_query_growth_is_bounded_with_history_and_provenance(self):
        values = {"observer_status": "known", "original_observer": self.user,
                  "origin": "file_import", "import_format": "csv", "imported_by": self.user}
        self.measurement(**values)
        small_count, small_size = self.query_count("api_box_measurements", [self.box.pk])
        for index in range(1, 13):
            self.measurement(day=date(2026, 9, 16) - timedelta(weeks=index), **values)
        large_count, large_size = self.query_count("api_box_measurements", [self.box.pk])
        self.assertEqual(small_size, 1)
        self.assertEqual(large_size, 13)
        self.assertLessEqual(large_count, small_count + 1)

    def test_reads_issue_no_writes_and_preserve_audit_revision_and_source_sequences(self):
        # Session language middleware writes preferences independently of the
        # business read contract; authenticate at the DRF boundary instead.
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.year_boundary_rows()
        response = self.client.post(
            reverse("api_box_measurements", args=[self.box.pk]),
            {"measured_on": "2021-01-13", "polyp_count": 0}, format="json",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(AuditLog.objects.exists())
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            for name, args in (
                ("api_box_list", None), ("api_admin_box_inventory", None),
                ("api_box_detail", [self.box.pk]), ("api_box_measurements", [self.box.pk]),
            ):
                self.assertEqual(self.get(name, args).status_code, 200)
        writes = [query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "CREATE", "DROP"),
        )]
        self.assertEqual(writes, [])
        self.assertEqual(self.snapshot(), before)
