"""Opt-in synthetic PostgreSQL benchmark; never uses the default database.

POLYPBASE_TEST_POSTGRES=1 POLYPBASE_BOX_BENCHMARK=before uv run python
manage.py test apps.cultures.test_box_list_benchmark --settings=config.test_settings

Reports are written in the checkout's .qa-box-list directory. The test runner
creates/destroys test_subculture_qa in the disposable loopback QA container.
"""

import hashlib
import json
import os
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, SubcultureAllocation, SubcultureEvent, ThermalZone
from .test_box_list_query import legacy_box_list_queryset_for_user, legacy_current_state_prefetches


@skipUnless(os.getenv("POLYPBASE_BOX_BENCHMARK"), "Opt-in performance benchmark")
class BoxListBenchmark(TestCase):
    @classmethod
    def setUpTestData(cls):
        assert connection.vendor == "postgresql"
        assert connection.settings_dict["HOST"] == "127.0.0.1"
        assert connection.settings_dict["PORT"] == "55439"
        assert connection.settings_dict["NAME"] == "test_subculture_qa"
        cls.now = datetime(2026, 10, 5, 12, tzinfo=datetime_timezone.utc)
        cls.org = Organization.objects.create(name="Synthetic performance laboratory")
        cls.user = get_user_model().objects.create_user(
            username="synthetic-performance", email="synthetic-performance@example.org",
        )
        OrganizationMembership.objects.create(user=cls.user, organization=cls.org, role="admin")
        species = Species.objects.create(scientific_name="Synthetic benchmark", genus_species_code="BQA")
        strain = Strain.objects.create(species=species, organization=cls.org, code="BQA-QA-1")
        zone = ThermalZone.objects.create(organization=cls.org, name="Synthetic zone")
        cls.boxes = Box.objects.bulk_create([
            Box(organization=cls.org, strain=strain, thermal_zone=zone if i % 5 else None,
                global_code=f"BQA-QA-1.{i + 1:04d}", box_number=str(i + 1),
                polyp_state_revision=200, status="inactive" if i % 13 == 12 else "active")
            for i in range(650)
        ])
        BoxLocation.objects.bulk_create([
            BoxLocation(box=box, thermal_zone=zone, starts_at=cls.now - timedelta(days=730))
            for box in cls.boxes if box.thermal_zone_id
        ])
        measurements = []
        for i, box in enumerate(cls.boxes):
            count = 100 if i < 50 else (4 if i < 250 else 3)
            for j in range(count):
                day = cls.now.date() - timedelta(weeks=count - j - 1)
                measurements.append(BiologicalMeasurement(
                    box=box, measured_on=day, week_start=BiologicalMeasurement.week_start_for(day),
                    polyp_count=0 if i % 7 == 0 else 100 + j, ephyrae_count=0,
                    strobila_count=None if i % 3 == 0 else 0,
                    salinity_psu="0.00" if j == 0 else None,
                    polyp_state_sequence=200 if j == count - 1 and i % 2 else j + 1,
                    user=cls.user if i % 2 else None,
                    notes="Synthetic performance shape only. " * 5,
                ))
        BiologicalMeasurement.objects.bulk_create(measurements)
        BiologicalMeasurement.objects.update(created_at=cls.now - timedelta(hours=1))
        events = []
        for box in cls.boxes[:100]:
            for j in range(4):
                complete = j < 3
                events.append(SubcultureEvent(
                    parent_box=box, event_date=cls.now.date() - timedelta(days=14 - j),
                    occurred_at=cls.now - timedelta(days=14 - j), parent_state_sequence=101 + j,
                    parent_polyp_count_before=100, allocated_polyp_count=10 if complete else None,
                    parent_polyp_count_after=90 if complete else None, parent_state_snapshot={},
                ))
        SubcultureEvent.objects.bulk_create(events)
        SubcultureAllocation.objects.bulk_create([
            SubcultureAllocation(event=events[i * 4 + 2], child_box=cls.boxes[550 + i], position=0,
                allocated_polyps=None if i % 3 == 0 else (0 if i % 3 == 1 else 10),
                child_global_code=cls.boxes[550 + i].global_code)
            for i in range(100)
        ])
        assert BiologicalMeasurement.objects.count() == 7000
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE")

    def test_benchmark(self):
        label = os.environ["POLYPBASE_BOX_BENCHMARK"]
        self.assertIn(label, ("before", "after"))
        client = APIClient()
        client.force_authenticate(self.user)
        report = {"postgres_version": connection.pg_version,
                  "dataset": {"boxes": 650, "measurements": 7000, "events": 400,
                              "allocations": 100, "long_history_weeks": 100}, "endpoints": {}}
        paths = [f"/api/boxes/?limit=100&offset={offset}" for offset in range(0, 650, 100)]
        paths += ["/api/boxes/?limit=100&q=BQA-QA-1.0001", "/api/dashboard/",
                  "/api/overview/active-boxes/?months=3"]
        with ExitStack() as stack:
            stack.enter_context(patch("django.utils.timezone.now", return_value=self.now))
            if label == "before":
                # Replay the frozen original queries without reverting code.
                stack.enter_context(patch(
                    "apps.cultures.api_views.box_list_queryset_for_user", legacy_box_list_queryset_for_user,
                ))
                stack.enter_context(patch(
                    "apps.cultures.api_views.current_state_prefetches", legacy_current_state_prefetches,
                ))
            for path in paths:
                samples = []
                for repetition in range(3):
                    timings = []

                    def timed_execute(execute, sql, params, many, context):
                        started = perf_counter()
                        try:
                            return execute(sql, params, many, context)
                        finally:
                            timings.append((perf_counter() - started) * 1000)

                    started = perf_counter()
                    with CaptureQueriesContext(connection) as captured, connection.execute_wrapper(timed_execute):
                        response = client.get(path, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
                        self.assertEqual(response.status_code, 200)
                        payload = response.content
                    elapsed = (perf_counter() - started) * 1000
                    samples.append(elapsed)
                    if repetition == 0:
                        sql_queries = [
                            {"ms": timing, "sql": query["sql"]}
                            for timing, query in zip(timings, captured.captured_queries, strict=True)
                        ]
                        queries = sorted(sql_queries, key=lambda query: query["ms"], reverse=True)
                        for query in queries[:3]:
                            if query["sql"].lstrip().upper().startswith("SELECT"):
                                with connection.cursor() as cursor:
                                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query["sql"])
                                    query["plan"] = cursor.fetchone()[0]
                        entry = {"queries": len(queries), "sql_queries": sql_queries,
                                 "sql_total_ms": sum(timings), "slowest": queries[:3],
                                 "payload_bytes": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest(),
                                 "snapshot": response.json(), "returned_boxes": len(response.json().get("results", []))}
                entry.update(samples_ms=samples, median_ms=median(samples))
                report["endpoints"][path] = entry
                print(f"{path}: median={median(samples):.1f}ms queries={entry['queries']} "
                      f"bytes={entry['payload_bytes']} slowest={entry['slowest'][0]['ms']:.1f}ms", flush=True)
        directory = Path(__file__).resolve().parents[3] / ".qa-box-list"
        directory.mkdir(exist_ok=True)
        (directory / f"{label}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if label == "after":
            before = json.loads((directory / "before.json").read_text(encoding="utf-8"))
            for path, entry in report["endpoints"].items():
                self.assertEqual(entry["snapshot"], before["endpoints"][path]["snapshot"], path)
            print("All endpoint snapshots are exactly equivalent.", flush=True)
