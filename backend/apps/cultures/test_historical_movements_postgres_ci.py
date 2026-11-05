"""PostgreSQL-only CI evidence; never bypass the historical acceptance guard."""

import importlib.util
import io
import json
from pathlib import Path
from time import perf_counter
from unittest import skipUnless

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.models import Q
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.box_lookup import box_audits, numeric_box_audit_values
from apps.audit.models import AuditLog

from . import test_historical_movements as fixture_module
from .historical_movements import accept_historical_movements, plan_historical_movements
from .models import Box, HistoricalBoxMovement


# Load the shared catalog assertions without adding scripts/ to sys.path, running
# its CLI, changing settings, or importing a second TestCase into test discovery.
_SPEC = importlib.util.spec_from_file_location(
    "historical_movements_ci_validation",
    Path(__file__).resolve().parents[3] / "scripts" / "validate_historical_movements_ci.py",
)
_validation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_validation)


@skipUnless(connection.vendor == "postgresql", "Requires the dedicated disposable PostgreSQL CI database")
class HistoricalMovementsPostgresCITests(TestCase):
    @classmethod
    def setUpClass(cls):
        # vendor is metadata: the decorator does not open a SQLite/default DB.
        # Refuse any other PostgreSQL environment before fixtures can write.
        _validation.check_environment()
        _validation.check_database_config(connection, test_database=True)
        _validation.server_identity(connection, test_database=True)
        super().setUpClass()

    def setUp(self):
        fixture_module.HistoricalMovementsTests.setUp(self)

    def plan(self):
        return plan_historical_movements(organization_id=self.org.pk, box_ids=[self.box.pk])

    def snapshot(self):
        return fixture_module.HistoricalMovementsTests.snapshot(self) + [
            list(HistoricalBoxMovement.objects.order_by("pk").values())
        ]

    def test_adjacent_week_planner_is_positive_deterministic_and_read_only(self):
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual(plan, self.plan())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(plan["candidates"]), 1)
        candidate = plan["candidates"][0]
        self.assertEqual(candidate["classification"], "eligible")
        self.assertEqual(candidate["reconciliation_status"], "review_required")
        self.assertEqual(candidate["blockers"], [])
        self.assertEqual((candidate["iso_year"], candidate["iso_week"]), (2025, 52))
        self.assertEqual(candidate["evidence"]["measurement_ids"], [self.before.pk, self.after.pk])
        self.assertFalse(candidate["evidence"]["physical_occurrence_proven"])
        self.assertEqual(self.before.polyp_count, 0)
        self.assertEqual(self.after.ephyrae_count, 0)
        self.assertIsNone(self.before.strobila_count)
        _validation.write_evidence("postgres-planner", {"candidate": candidate, "read_only": True})

    def test_planner_rejects_cross_organization_selection(self):
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            plan_historical_movements(organization_id=self.other.pk, box_ids=[self.box.pk])
        self.assertEqual(before, self.snapshot())

    def test_history_api_is_read_only_and_enforces_permissions_and_scope(self):
        url = reverse("api_box_history", args=[self.box.pk])
        self.assertIn(self.client.get(url).status_code, (401, 403))
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 403)
        OrganizationMembership.objects.create(user=self.viewer, organization=self.other, role="viewer")
        self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 404)
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 200, response.content)
        audit_queries = [query["sql"] for query in queries if 'FROM "audit_auditlog"' in query["sql"]]
        self.assertEqual(len(audit_queries), 1, "History must share one loaded audit list with its planner")
        self.assertIn('JOIN "auth_user"', audit_queries[0])
        self.assertTrue(response.json()["events"])
        expected = {"id", "kind", "temporal_precision", "occurred_at", "iso_year", "iso_week",
                    "from_thermal_zone", "to_thermal_zone", "actor", "provenance", "evidence",
                    "reconciliation_status", "recorded_movement_id", "can_correct", "notes"}
        for event in response.json()["events"]:
            self.assertEqual(set(event), expected)
            self.assertFalse(event["can_correct"])
            self.assertIsNone(event["occurred_at"])
            self.assertEqual(event["temporal_precision"], "unresolved")
        self.assertNotIn(self.foreign.name, response.content.decode())
        self.assertEqual(self.client.post(url, HTTP_X_ORGANIZATION_ID=str(self.org.pk)).status_code, 405)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(HistoricalBoxMovement.objects.exists())

    def test_postgres_service_guard_refuses_before_any_query(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(settings.SETTINGS_MODULE, _validation.SETTINGS_MODULE)
        with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
            accept_historical_movements(
                organization_id=self.org.pk, box_ids=[self.box.pk], candidate_ids=["synthetic"],
                expected_fingerprint="synthetic", actor=self.admin, notes="Must be refused",
            )
        self.assertFalse(HistoricalBoxMovement.objects.exists())

    def test_postgres_command_guard_refuses_preview_and_accept_before_any_query(self):
        for accept in (False, True):
            with self.subTest(accept=accept), self.assertNumQueries(0), self.assertRaises(CommandError):
                call_command(
                    "reconcile_historical_movements", organization_id=self.org.pk,
                    box_id=[self.box.pk], actor_id=self.admin.pk, accept=accept,
                    candidate_id=["synthetic"], expected_fingerprint="synthetic",
                    notes="Must be refused", stdout=io.StringIO(), skip_checks=True,
                )
        self.assertFalse(HistoricalBoxMovement.objects.exists())

    def test_catalog_schema_constraints_foreign_keys_and_typed_indexes(self):
        findings = _validation.schema_findings(connection)
        self.assertEqual(findings["historical_reconstruction_rows"], 0)
        self.assertEqual({index["name"] for index in findings["audit_indexes"]}, set(_validation.INDEX_NAMES))
        _validation.write_evidence("postgres-test-schema", findings)

    def make_audit(self, metadata, *, code="", organization=None, object_type="box"):
        return AuditLog.objects.create(
            organization=organization or self.org, action="update", object_type=object_type,
            object_id=code, metadata=metadata, user=self.admin, description="Synthetic CI evidence",
        )

    def explain(self, queryset):
        sql, params = queryset.query.sql_with_params()
        with connection.cursor() as cursor:
            # Deliberately no enable_seqscan/indexscan overrides or planner hints.
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
            result = cursor.fetchone()[0]
        return json.loads(result) if isinstance(result, str) else result

    @staticmethod
    def plan_nodes(plan):
        yield plan
        for child in plan.get("Plans", []):
            yield from HistoricalMovementsPostgresCITests.plan_nodes(child)

    def test_selective_lookup_equivalence_query_count_and_unforced_plans(self):
        second = Box.objects.create(
            organization=self.org, global_code="HIS-LAB-1.002", box_number="002",
            strain=self.box.strain, thermal_zone=self.b,
        )
        box_ids = [self.box.pk, second.pk]
        current, previous = self.box.global_code, "HIS-PREVIOUS-1.001"
        numeric = self.make_audit({"box_id": self.box.pk}, code="renamed-unrelated-code")
        both = self.make_audit({"box_id": self.box.pk}, code=current)
        numeric_float = self.make_audit({"box_id": float(self.box.pk)})
        second_numeric = self.make_audit({"box_id": second.pk})
        legacy_current = self.make_audit({}, code=current)
        legacy_previous = self.make_audit({"box_id": str(self.box.pk)}, code=previous)
        # Typed JSON distinguishes strings/booleans/null/containers from numeric
        # identity, without ever casting malformed values to a SQL integer.
        for value in (str(self.box.pk), True, None, "malformed", [self.box.pk], {"id": self.box.pk}):
            self.make_audit({"box_id": value})
        self.make_audit({"box_id": self.box.pk}, code=current, organization=self.other)
        self.make_audit({}, code=previous, organization=self.other)
        self.make_audit({"box_id": self.box.pk}, code=current, object_type="strain")
        numeric_ids = {numeric.pk, both.pk, numeric_float.pk, second_numeric.pk}
        current_ids = {both.pk, legacy_current.pk}
        previous_ids = {legacy_previous.pk}
        scoped = AuditLog.objects.filter(organization_id=self.org.pk, object_type="box")
        cases = {
            "numeric": (box_audits(organization_id=self.org.pk, box_ids=box_ids),
                        scoped.filter(metadata__box_id__in=box_ids), numeric_ids),
            "legacy_current": (box_audits(organization_id=self.org.pk, box_ids=[], codes=[current]),
                               scoped.filter(object_id=current), current_ids),
            "legacy_previous": (box_audits(organization_id=self.org.pk, box_ids=[], codes=[previous]),
                                scoped.filter(object_id=previous), previous_ids),
            "combined": (box_audits(organization_id=self.org.pk, box_ids=box_ids, codes=[current, previous]),
                         scoped.filter(Q(metadata__box_id__in=box_ids) | Q(object_id__in=[current, previous])),
                         numeric_ids | current_ids | previous_ids),
        }
        observations = []
        inserted = 0
        for volume in (1000, 10000, 50000):
            while inserted < volume:
                stop = min(inserted + 2000, volume)
                AuditLog.objects.bulk_create([
                    AuditLog(
                        organization_id=self.org.pk if number % 2 else self.other.pk,
                        action="update", object_type="box", object_id=f"CI-NOISE-{number}",
                        metadata={"box_id": 1000000000 + number}, description="Synthetic unrelated CI audit",
                    ) for number in range(inserted, stop)
                ], batch_size=2000)
                inserted = stop
            with connection.cursor() as cursor:
                cursor.execute('ANALYZE "audit_auditlog"')
            indexes = _validation.audit_indexes(connection)
            total_audits = AuditLog.objects.count()
            for name, (optimized, old, expected_ids) in cases.items():
                # Use fresh clones: cached querysets would conceal N+1 or cost.
                optimized = optimized.select_related("user").order_by("pk")
                old = old.select_related("user").order_by("pk")
                start = perf_counter()
                with self.assertNumQueries(1):
                    rows = list(optimized.all())
                optimized_ms = (perf_counter() - start) * 1000
                start = perf_counter()
                with self.assertNumQueries(1):
                    old_rows = list(old.all())
                old_ms = (perf_counter() - start) * 1000
                self.assertEqual([row.pk for row in rows], [row.pk for row in old_rows])
                self.assertEqual({row.pk for row in rows}, expected_ids)
                self.assertEqual(len(rows), len(expected_ids), "Combined branches must not duplicate evidence")
                with self.assertNumQueries(0):
                    numeric_values = numeric_box_audit_values(rows)
                    for row in rows:
                        self.assertEqual(row.user.pk, self.admin.pk)
                expected_numeric = expected_ids & numeric_ids if name in ("numeric", "combined") else set()
                self.assertEqual({row["id"] for row in numeric_values}, expected_numeric)
                self.assertEqual(numeric_values, list(AuditLog.objects.filter(pk__in=expected_numeric).order_by("pk").values()))
                self.assertTrue(all(row.organization_id == self.org.pk for row in rows))
                plans = {"optimized": self.explain(optimized), "previous": self.explain(old)}
                summaries = {}
                for label, plan in plans.items():
                    root = plan[0]
                    nodes = list(self.plan_nodes(root["Plan"]))
                    summaries[label] = {
                        "indexes_used": sorted({node["Index Name"] for node in nodes if "Index Name" in node}),
                        "sequential_scans": [node.get("Relation Name") for node in nodes if node["Node Type"] == "Seq Scan"],
                        "execution_ms": root["Execution Time"], "planning_ms": root["Planning Time"],
                        "actual_rows": root["Plan"]["Actual Rows"],
                    }
                observation = {
                    "unrelated_audits": volume, "total_audits": total_audits,
                    "same_organization_noise": volume // 2,
                    "other_organization_noise": volume // 2, "case": name,
                    "result_ids": sorted(expected_ids), "numeric_subset_ids": sorted(expected_numeric),
                    "orm_query_count_each": 1, "numeric_subset_extra_queries": 0,
                    "orm_ms": {"optimized": optimized_ms, "previous": old_ms},
                    "summary": summaries, "plans": plans, "indexes": indexes,
                }
                observations.append(observation)
                _validation.write_evidence("postgres-lookup", {
                    "limitations": [
                        "Synthetic CI data, not a production benchmark or latency threshold.",
                        "Single warm execution per query; shared runner, cache and instrumentation affect timings.",
                        "Previous and optimized query formulations use the same migrated indexes; indexes are never dropped.",
                        "Planner is unforced; a sequential scan at any volume is reported, not failed.",
                        "TestCase transaction and synthetic distribution differ from production traffic.",
                    ],
                    "observations": observations,
                })
                print(f"HM PG lookup noise={volume} case={name} rows={len(rows)} "
                      f"optimized_ms={optimized_ms:.3f} previous_ms={old_ms:.3f} "
                      f"plans={json.dumps(summaries, sort_keys=True)}")
        self.assertEqual(len(observations), 12)
        self.assertFalse(HistoricalBoxMovement.objects.exists())
