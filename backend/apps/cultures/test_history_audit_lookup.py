"""Synthetic identity equivalence and reproducible SQLite lookup plans."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Q
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import OrganizationMembership
from apps.audit.box_lookup import box_audits, numeric_box_audit_values
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .box_history import box_history
from .historical_movements import _plan, plan_historical_movements
from .models import Box


class HistoryAuditLookupTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Synthetic lookup")
        self.other = Organization.objects.create(name="Synthetic other")
        self.user = get_user_model().objects.create_user(username="lookup", email="lookup@example.org")
        OrganizationMembership.objects.create(user=self.user, organization=self.org, role="viewer")
        species = Species.objects.create(scientific_name="Synthetic lookup", genus_species_code="LKP")
        strain = Strain.objects.create(species=species, code="LKP-LAB-1", organization=self.org, number=1, origin_code="LAB")
        self.box = Box.objects.create(organization=self.org, strain=strain, box_number="001",
                                      global_code="CURRENT", previous_global_code="PREVIOUS")

    def audit(self, metadata, code="CURRENT", **kwargs):
        return AuditLog.objects.create(organization=kwargs.pop("organization", self.org),
                                       object_type=kwargs.pop("object_type", "box"), object_id=code,
                                       action="update", user=self.user, metadata=metadata, **kwargs)

    def lookup(self):
        return box_audits(organization_id=self.org.pk, box_ids=[self.box.pk], codes=["CURRENT", "PREVIOUS"])

    def old_numeric(self):
        return AuditLog.objects.filter(organization=self.org, object_type="box", metadata__box_id__in=[self.box.pk])

    def old_projection(self):
        return AuditLog.objects.filter(organization=self.org, object_type="box").filter(
            Q(metadata__box_id=self.box.pk) | Q(object_id__in=["CURRENT", "PREVIOUS"]))

    def test_typed_identity_scope_legacy_barriers_and_deduplication(self):
        pk = self.box.pk
        for metadata in ({"box_id": pk}, {"box_id": float(pk)}, {"box_id": str(pk)},
                         {"box_id": True}, {"box_id": False}, {"box_id": None},
                         {"box_id": pk + 100}, {}, [], "scalar"):
            for code in ("CURRENT", "PREVIOUS", "RENAMED"):
                self.audit(metadata, code)
        self.audit({"box_id": pk}, organization=self.other)
        self.audit({}, organization=self.other)
        self.audit({"box_id": pk}, object_type="other")
        old = list(self.old_projection().order_by("created_at", "pk"))
        new = list(self.lookup().order_by("created_at", "pk"))
        self.assertEqual([a.pk for a in old], [a.pk for a in new])
        self.assertEqual(len(new), len({a.pk for a in new}))
        self.assertEqual(numeric_box_audit_values(new), list(self.old_numeric().order_by("pk").values()))
        numeric = list(box_audits(organization_id=self.org.pk, box_ids=[pk]).order_by("pk"))
        self.assertTrue(all(a.is_numeric_box_audit for a in numeric))
        self.assertEqual(numeric_box_audit_values(numeric), list(self.old_numeric().order_by("pk").values()))
        with patch("apps.cultures.historical_movements.box_audits", return_value=self.old_numeric()):
            old_plan = plan_historical_movements(organization_id=self.org.pk, box_ids=[pk])
        self.assertEqual(plan_historical_movements(organization_id=self.org.pk, box_ids=[pk]), old_plan)
        self.assertEqual(_plan(organization_id=self.org.pk, box_ids=[pk],
                               audit_rows=numeric_box_audit_values(new)), old_plan)
        # Code-only rows stay available to the inactive-interval ambiguity checks.
        self.assertTrue(any(a.metadata == {} and a.object_id == "PREVIOUS" for a in new))

    def test_numeric_values_obey_database_membership_despite_decoded_float_rounding(self):
        requested_id = 1000000000000000100
        decoded_id = float("1.0000000000000001e+18")
        self.assertEqual(int(decoded_id), 1000000000000000128)
        self.assertNotEqual(decoded_id, requested_id)
        included = AuditLog(pk=2, metadata={"box_id": decoded_id})
        included.is_numeric_box_audit = True
        excluded = AuditLog(pk=1, metadata={"box_id": decoded_id})
        excluded.is_numeric_box_audit = False
        # Same decoded JSON, opposite database judgments: never recompute them.
        with self.assertNumQueries(0):
            rows = numeric_box_audit_values([included, excluded])
        self.assertEqual(rows, [{field.attname: getattr(included, field.attname)
                                 for field in AuditLog._meta.concrete_fields}])
        self.assertNotIn("is_numeric_box_audit", rows[0])
        self.assertIs(rows[0]["metadata"], included.metadata)

    def test_batch_numeric_lookup_is_one_query_and_matches_existing_values(self):
        ids = [self.box.pk, self.box.pk + 1]
        for pk in ids:
            self.audit({"box_id": pk}, "RENAMED")
            self.audit({"box_id": str(pk)}, "RENAMED")
            self.audit({"box_id": pk}, organization=self.other)
        with self.assertNumQueries(1):
            audits = list(box_audits(organization_id=self.org.pk, box_ids=ids).order_by("pk"))
        with self.assertNumQueries(0):
            rows = numeric_box_audit_values(audits)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows, list(AuditLog.objects.filter(
            organization=self.org, object_type="box", metadata__box_id__in=ids).order_by("pk").values()))

    def test_postgresql_compilation_keeps_typed_json_without_connecting(self):
        from django.db.backends.postgresql.base import DatabaseWrapper

        pg = DatabaseWrapper({"ENGINE": "django.db.backends.postgresql", "NAME": "synthetic_compile_only"})
        with patch.object(pg, "get_new_connection", side_effect=AssertionError("No PostgreSQL connection allowed")):
            sql, params = self.lookup().query.get_compiler(connection=pg).as_sql()
            numeric_index = next(index for index in AuditLog._meta.indexes if index.name == "audit_box_numeric_idx")
            ddl = str(numeric_index.create_sql(AuditLog, pg.schema_editor()))
        self.assertIn('(U0."metadata" -> %s) IN (%s)', sql)
        self.assertIn('CASE WHEN ("audit_auditlog"."metadata" -> %s) IN (%s)', sql)
        self.assertIn('AS "is_numeric_box_audit"', sql)
        self.assertNotIn("->>", sql)
        self.assertNotIn("CAST", sql)
        self.assertIn("box_id", params)
        self.assertIn('(("metadata" -> \'box_id\'))', ddl)
        self.assertIsNone(pg.connection)

    def test_projection_reuses_one_loaded_audit_list_without_user_n_plus_one(self):
        for _ in range(20):
            self.audit({"box_id": self.box.pk, "transition": "active->inactive"})
        self.audit({}, "PREVIOUS")
        self.audit({"box_id": self.box.pk + 100})
        expected = plan_historical_movements(organization_id=self.org.pk, box_ids=[self.box.pk])
        with CaptureQueriesContext(connection) as queries, patch("apps.cultures.box_history._plan", wraps=_plan) as planner:
            history = box_history(box=self.box, user=self.user)
        audit_queries = [q["sql"] for q in queries if 'FROM "audit_auditlog"' in q["sql"]]
        self.assertEqual(len(audit_queries), 1)
        self.assertIn("UNION", audit_queries[0])
        self.assertIn('JOIN "auth_user"', audit_queries[0])
        rows = planner.call_args.kwargs["audit_rows"]
        self.assertEqual(rows, list(self.old_numeric().order_by("pk").values()))
        self.assertEqual(_plan(organization_id=self.org.pk, box_ids=[self.box.pk], audit_rows=rows), expected)
        self.assertEqual(history["candidates"], expected["candidates"])
        self.assertEqual(len([e for e in history["events"] if e["provenance"] == "lifecycle_audit"]), 20)
        with self.assertRaises(TypeError):
            plan_historical_movements(organization_id=self.org.pk, box_ids=[self.box.pk], audit_rows=[])

    def test_synthetic_before_after_sqlite_full_identity_index_plans(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite expression-index plan regression")
        AuditLog.objects.bulk_create([
            AuditLog(organization=self.org, object_type="box", object_id=f"unrelated-{i}",
                     action="update", metadata={"box_id": i + 100}) for i in range(4000)
        ])
        self.audit({"box_id": self.box.pk}, "RENAMED")
        self.audit({})
        self.audit({}, "PREVIOUS")
        self.audit({"box_id": self.box.pk + 1})
        self.assertEqual(AuditLog.objects.count(), 4004)
        indexes = [index for index in AuditLog._meta.indexes if index.name in ("audit_box_numeric_idx", "audit_box_code_idx")]
        editor = connection.schema_editor()
        with connection.cursor() as cursor:
            for index in indexes:
                cursor.execute(f"DROP INDEX {connection.ops.quote_name(index.name)}")
        try:
            before_numeric = self.old_numeric().order_by("pk").explain()
            before_projection = self.old_projection().order_by("created_at", "pk").explain()
            old_rows = list(self.old_projection().order_by("created_at", "pk"))
            self.assertEqual(len(old_rows), 4)
            self.assertEqual(self.old_numeric().count(), 1)
        finally:
            with connection.cursor() as cursor:
                for index in indexes:
                    cursor.execute(str(index.create_sql(AuditLog, editor)))
        numeric = box_audits(organization_id=self.org.pk, box_ids=[self.box.pk]).order_by("pk")
        projection = self.lookup().select_related("user").order_by("created_at", "pk")
        after_numeric, after_projection = numeric.explain(), projection.explain()
        self.assertRegex(after_numeric, r"audit_box_numeric_idx \(organization_id=\? AND object_type=\? AND <expr>=\?\)")
        self.assertRegex(after_projection, r"audit_box_numeric_idx \(organization_id=\? AND object_type=\? AND <expr>=\?\)")
        self.assertRegex(after_projection, r"audit_box_code_idx \(organization_id=\? AND object_type=\? AND object_id=\?\)")
        self.assertNotIn("SCAN audit_auditlog", after_projection)
        self.assertIn("SEARCH audit_auditlog USING INTEGER PRIMARY KEY (rowid=?)", after_projection)
        new_rows = list(projection)
        self.assertEqual([a.pk for a in old_rows], [a.pk for a in new_rows])
        retained = [a for a in new_rows if not isinstance(a.metadata, dict) or a.metadata.get("box_id", self.box.pk) == self.box.pk]
        self.assertEqual((numeric.count(), len(new_rows), len(retained)), (1, 4, 3))
        print("\nFIX2 synthetic: total=4004; before/after numeric=1, projection=4, retained=3")
        print("BEFORE numeric:\n", before_numeric, "\nBEFORE projection:\n", before_projection)
        print("AFTER numeric SQL:", numeric.query.sql_with_params())
        print("AFTER projection SQL:", projection.query.sql_with_params())
        print("AFTER numeric:\n", after_numeric, "\nAFTER projection:\n", after_projection)
