"""Real v2 roles and migrated science, only in the fresh PostgreSQL QA harness.

Use unittest.TestCase deliberately: Django must not create/flush a test database
before the independent ownership topology has been provisioned. Migrate once,
then clone the populated immutable template for each test. No production boundary
is patched and no immutable source row is reset through a trigger bypass.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from threading import Barrier
from unittest import TestCase, skipUnless
from unittest.mock import patch

import psycopg
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import IrreversibleError
from psycopg import sql

from apps.measurements import box_1902_sql as contract
from apps.measurements.models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
)
from apps.taxonomy.models import OrganizationSpeciesCode, Species, Strain

from .historical_2026 import cca_eob_production as cca
from .historical_2026 import cla_box_1902 as correction
from .test_cla_box_1902 import ClaBox1902Fixture


LEASE = os.environ.get("POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER", "")
DATABASE = "test_subculture_qa"
TEMPLATE = "box1902_v2_populated_template"
MIGRATION = ("measurements", "0018_box_1902_preservation")
PREVIOUS = ("measurements", "0017_bucket_probe_last_measured_index")
SOURCE_SHA = "0776396ad12b6479e53755496b0e6c51ec3a2e1ff581c983f84f82fe943b84fa"
SCOPE = {"organization_id": 1, "box_id": 1902, "source_strain_id": 213,
         "source_species_id": 131, "target_strain_id": 285}


@skipUnless(LEASE, "Requires scripts/test_historical_temperature_production.py and its fresh PG18 lease")
class Box1902CorrectionV2PostgreSQLTests(ClaBox1902Fixture, TestCase):
    @classmethod
    def connect(cls, user="subculture_qa", database=DATABASE):
        return psycopg.connect(
            host="127.0.0.1", port=cls.port, dbname=database, user=user,
            password="", sslmode="disable", connect_timeout=5, autocommit=True,
            options="-c lock_timeout=5000 -c statement_timeout=60000",
        )

    @classmethod
    def execute(cls, statement, params=None, *, user="subculture_qa", database=DATABASE):
        with cls.connect(user, database) as conn:
            cursor = conn.execute(statement, params)
            return cursor.fetchall() if cursor.description else []

    @classmethod
    def use_django_role(cls, user):
        connection.close()
        config = {"NAME": DATABASE, "USER": user, "PASSWORD": ""}
        settings.DATABASES["default"].update(config)
        connection.settings_dict.update(config)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        config = settings.DATABASES["default"]
        lease = Path(LEASE).resolve()
        if (settings.SETTINGS_MODULE != "ht_disposable_settings"
                or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"
                or not getattr(settings, "RUNNING_TESTS", False)
                or not lease.is_dir() or lease.name != "data"
                or not lease.parent.name.startswith("polypbase-ht-production-synthetic-")
                or (config["HOST"], config["NAME"], config["USER"], config["PASSWORD"])
                != ("127.0.0.1", "subculture_qa", "subculture_qa", "")):
            raise RuntimeError("Refuse any database outside the fresh leased runner")
        cls.port = int(config["PORT"])
        with cls.connect(database="subculture_qa") as admin:
            version, directory, login = admin.execute(
                "SELECT current_setting('server_version_num')::int, "
                "current_setting('data_directory'), session_user"
            ).fetchone()
            if (not 180000 <= version < 190000 or Path(directory).resolve() != lease
                    or login != "subculture_qa"):
                raise RuntimeError("Fresh PostgreSQL 18 administrator lease mismatch")
            cls.original_settings = deepcopy(settings.DATABASES["default"])
            cls.original_connection_settings = deepcopy(connection.settings_dict)
            cls.addClassCleanup(cls.cleanup_cluster)
            # Provisioning itself must create all reserved technical identities.
            # Precreating them would now correctly fail the collision preflight.
            if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [contract.RUNTIME]).fetchone():
                admin.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(contract.RUNTIME)))
            # Refuse collisions rather than touching a database owned by another suite.
            for name in (DATABASE, TEMPLATE):
                if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", [name]).fetchone():
                    raise RuntimeError(f"Disposable test database already exists: {name}")
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER polypbase").format(sql.Identifier(DATABASE)))
            cls.created_databases = {DATABASE}
        print("Box 1902 v2: creating the real pre-0018 Django schema", flush=True)
        cls.use_django_role(contract.RUNTIME)
        executor = MigrationExecutor(connection)
        targets = [PREVIOUS if node == MIGRATION else node for node in executor.loader.graph.leaf_nodes()]
        executor.migrate(targets)
        print("Box 1902 v2: populating immutable scientific evidence", flush=True)
        cls.create_fixture()
        # Reuse the existing rich fixture, but pin the exact v2 production PKs.
        old_target, old_species = cls.target, cls.target_species
        cls.target_species = Species.objects.create(pk=164, scientific_name="Chrysaora lactea v2 temporary")
        OrganizationSpeciesCode.objects.filter(organization=cls.org, code="CLC").update(species=cls.target_species)
        old_target.delete()
        old_species.delete()
        Species.objects.filter(pk=164).update(scientific_name="Chrysaora lactea")
        cls.target_species.refresh_from_db()
        cls.target = Strain.objects.create(
            pk=285, organization=cls.org, species=cls.target_species,
            code="CLC-JKA-1", origin_code="JKA", number=1,
        )
        cls.source.refresh_from_db()
        cls.reading_ids = list(range(12173, 12214))
        for week, reading_id in zip(range(12, 53), cls.reading_ids):
            biology = BiologicalMeasurement.objects.create(
                box=cls.box, measured_on=date.fromisocalendar(2025, week, 1),
                polyp_count=0, ephyrae_count=0, user=cls.admin,
                strobila_count=None if week % 2 else 0,
                salinity_psu=None if week % 2 else "0.00",
            )
            cls.create_reading(biology, week, reading_id)
        # Scientific sentinels include a transitive same-institution source and a
        # foreign institution, neither of which belongs in the direct manifest.
        cls.witness_ids = []
        for index, box in enumerate((cls.sentinels[0], cls.foreign_box)):
            biology = BiologicalMeasurement.objects.create(
                box=box, measured_on=date.fromisocalendar(2025, 12, 1),
                polyp_count=0, ephyrae_count=0,
                environment_source_box=cls.box if index == 0 else None,
            )
            cls.create_reading(biology, 12, 12300 + index)
            cls.witness_ids.append(12300 + index)
        cls.preinstall_science = cls.science_snapshot()
        # Exercise the actual migration against the unsafe runtime-owned schema.
        try:
            MigrationExecutor(connection).migrate([MIGRATION])
        except contract.ContractError as error:
            cls.unsafe_install_error = str(error)
        else:
            raise AssertionError("0018 installed without independent ownership provisioning")
        if cls.science_snapshot() != cls.preinstall_science:
            raise AssertionError("Refused unsafe installation changed science")
        print("Box 1902 v2: independent privilege transition and installation", flush=True)
        cls.execute(contract.provision_sql())
        cls.use_django_role(contract.MIGRATOR)
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE box1902_owner")
            cursor.execute("SELECT session_user, current_user")
            if cursor.fetchone() != (contract.MIGRATOR, contract.OWNER):
                raise AssertionError("Migration did not use a genuine SET-only migrator login")
        MigrationExecutor(connection).migrate([MIGRATION])
        # QA-only identity access; production authentication is not mocked.
        cls.execute("GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO box1902_executor")
        cls.execute(
            "CREATE FUNCTION pg_catalog.box1902_qa_data_directory() RETURNS text "
            "LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog "
            "AS 'SELECT current_setting(''data_directory'')'; "
            "REVOKE ALL ON FUNCTION pg_catalog.box1902_qa_data_directory() FROM PUBLIC; "
            "GRANT EXECUTE ON FUNCTION pg_catalog.box1902_qa_data_directory() TO box1902_executor"
        )
        cls.use_django_role(contract.EXECUTOR)
        contract.attest(connection, allow_retired=False)
        if cls.science_snapshot() != cls.preinstall_science:
            raise AssertionError("0018 changed populated historical science")
        connection.close()
        cls.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
            sql.Identifier(DATABASE), sql.Identifier(TEMPLATE)), database="subculture_qa")
        cls.created_databases = {TEMPLATE}
        print("Box 1902 v2: actual schema, 41 populated readings, independent provision and migrator installation", flush=True)

    @classmethod
    def create_reading(cls, biology, week, reading_id):
        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                pk=reading_id, biological_measurement=biology, iso_year=2025,
                iso_week=week, temperature_c="0.0000" if week % 2 else "15.12345678901234567890",
            )
            HistoricalTemperatureEvidence.objects.create(
                reading=reading, source_file="Suivi_2025.xlsx", source_sha256=SOURCE_SHA,
                sheet="Semaestomeae", temperature_cell="C61", temperature_merged_range="C61:C62",
                source_value=reading.temperature_c, source_box=biology.box.local_code or biology.box.global_code,
                polyp_cell="Q61", polyp_value="0", ephyrae_cell="Q62", ephyrae_value="0",
                week_cell="Q1", week_value=str(week), source_iso_year=2025, source_iso_week=week,
            )

    @classmethod
    def cleanup_cluster(cls):
        connection.close()
        for name in getattr(cls, "created_databases", set()):
            cls.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)),
                        database="subculture_qa")
        # Retirement changes a cluster role, so restore it for other leased suites.
        cls.execute("ALTER ROLE box1902_executor LOGIN", database="subculture_qa")
        settings.DATABASES["default"].clear()
        settings.DATABASES["default"].update(cls.original_settings)
        connection.settings_dict.clear()
        connection.settings_dict.update(cls.original_connection_settings)

    def setUp(self):
        super().setUp()
        self.execute("ALTER ROLE box1902_executor LOGIN", database="subculture_qa")
        self.execute(sql.SQL("CREATE DATABASE {} WITH TEMPLATE {} OWNER box1902_owner").format(
            sql.Identifier(DATABASE), sql.Identifier(TEMPLATE)), database="subculture_qa")
        self.created_databases.add(DATABASE)
        self.addCleanup(self.drop_working_database)
        # CREATE DATABASE copies template contents, not its database ACL.
        self.execute(sql.SQL(
            "REVOKE ALL ON DATABASE {} FROM PUBLIC, polypbase, box1902_approver, "
            "box1902_executor, box1902_migrator"
        ).format(sql.Identifier(DATABASE)), database="subculture_qa")
        self.execute(sql.SQL(
            "GRANT CONNECT ON DATABASE {} TO polypbase, box1902_approver, "
            "box1902_executor, box1902_migrator"
        ).format(sql.Identifier(DATABASE)), database="subculture_qa")
        self.use_django_role(contract.EXECUTOR)

    def drop_working_database(self):
        connection.close()
        self.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(DATABASE)),
                     database="subculture_qa")
        self.created_databases.discard(DATABASE)
        self.execute("ALTER ROLE box1902_executor LOGIN", database="subculture_qa")

    @classmethod
    def science_snapshot(cls):
        return {model._meta.db_table: list(model.objects.order_by("pk").values()) for model in
                (BiologicalMeasurement, HistoricalTemperatureReading, HistoricalTemperatureEvidence)}

    def snapshot(self):
        # One trusted read connection avoids hundreds of Windows login round trips.
        with self.connect() as admin:
            rows = admin.execute(
                "SELECT schemaname, tablename FROM pg_tables "
                "WHERE schemaname IN ('public','box1902_private') ORDER BY 1,2"
            ).fetchall()
            return {f"{schema}.{table}": admin.execute(sql.SQL(
                "SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text"
            ).format(sql.Identifier(schema, table))).fetchall() for schema, table in rows}

    def manifest(self):
        return self.execute("SELECT public.box_1902_manifest_v2()", user=contract.EXECUTOR)[0][0]

    def approve(self, *, manifest=None, actor=None, plan=None, expires=None):
        manifest = self.manifest() if manifest is None else manifest
        # Even SQL-only negative tests use the dedicated workflow's full reviewed state.
        if plan is None:
            plan = correction.build_plan(**SCOPE)
            plan["manifest"] = manifest
        text = contract.canonical_plan(plan)
        self.execute(contract.approval_sql(
            text, self.admin.pk if actor is None else actor, manifest,
            (expires or datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        ), user=contract.APPROVER)
        return contract.plan_digest(text)

    def write(self, digest, *, actor=None, user=contract.EXECUTOR):
        self.execute("SELECT public.box_1902_apply_v2(%s,%s)",
                     [self.admin.pk if actor is None else actor, digest], user=user)

    def denied(self, statement, params=None, *, user=contract.RUNTIME, state="42501"):
        with self.assertRaises(psycopg.Error) as raised:
            self.execute(statement, params, user=user)
        if state is not None:
            self.assertEqual(raised.exception.sqlstate, state, str(raised.exception))

    def assert_preserved(self, before):
        after = self.snapshot()
        allowed = {"public.cultures_box", "public.cultures_boxcodenamespace", "public.audit_auditlog",
                   *(f"box1902_private.{name}" for name in contract.PRIVATE_TABLES)}
        for table in before.keys() - allowed:
            self.assertEqual(after[table], before[table], table)
        boxes = [row[0] for row in before["public.cultures_box"]]
        for box in boxes:
            if box["id"] == 1902:
                box.update(global_code="CLC-JKA-1.009", strain_id=285)
        self.assertEqual(sorted(boxes, key=lambda row: row["id"]),
                         sorted((row[0] for row in after["public.cultures_box"]), key=lambda row: row["id"]))
        statuses = dict(HistoricalTemperatureReading.objects.filter(pk__in=self.reading_ids)
                        .values_list("pk", "association_status"))
        self.assertEqual(statuses, {pk: "confirmed" for pk in self.reading_ids})
        for table in contract.PRIVATE_TABLES:
            self.assertEqual(len(after[f"box1902_private.{table}"]), 1, table)
        context = after["box1902_private.context_v2"][0][0]
        self.assertTrue(context["validated"])
        approval = after["box1902_private.approval_v2"][0][0]
        registry = after["box1902_private.correction_v2"][0][0]
        self.assertIsNotNone(approval["consumed_at"])
        self.assertEqual(approval["consumed_at"], registry["consumed_at"])
        self.assertEqual(registry["executor"], contract.EXECUTOR)
        old_namespaces = {row[0]["namespace"]: row[0]["high_water"]
                          for row in before["public.cultures_boxcodenamespace"]}
        expected_namespaces = dict(old_namespaces)
        for namespace in ("CLA-JKA-1", "CLC-JKA-1"):
            expected_namespaces[namespace] = max(9, old_namespaces.get(namespace, 0))
        self.assertEqual({row[0]["namespace"]: row[0]["high_water"]
                          for row in after["public.cultures_boxcodenamespace"]}, expected_namespaces)
        old_audits = before["public.audit_auditlog"]
        for row in old_audits:
            self.assertIn(row, after["public.audit_auditlog"])
        new_audits = [row[0] for row in after["public.audit_auditlog"] if row not in old_audits]
        self.assertEqual(len(new_audits), 2)
        self.assertEqual({(row["object_type"], row["object_id"]) for row in new_audits},
                         {("box", "CLC-JKA-1.009"), ("cla_box_1902_correction", "1902")})
        for row in new_audits:
            self.assertEqual(row["organization_id"], 1)
            self.assertEqual(row["user_id"], self.admin.pk)
            self.assertEqual(row["metadata"]["operation"], contract.OPERATION)
            self.assertEqual(row["metadata"]["plan_hash"], registry["plan_hash"])

    def test_installation_on_populated_history_rejects_runtime_owner_then_preserves_rows(self):
        self.assertTrue(self.unsafe_install_error)
        self.assertIsNone(self.source.organization_id)
        self.assertEqual(self.science_snapshot(), self.preinstall_science)
        self.assertEqual(len(self.manifest()["readings"]), 41)
        self.assertEqual(self.execute("SELECT app, name FROM django_migrations WHERE app=%s AND name=%s", MIGRATION),
                         [MIGRATION])
        self.assertEqual(contract.attest(connection, allow_retired=False),
                         {"operation": contract.OPERATION, "retired": False})

    def test_authorized_actual_command_preserves_science_dependencies_and_receipts(self):
        plan = correction.build_plan(**SCOPE)
        self.assertEqual(plan["classification"], "READY", plan.get("conflicts"))
        self.assertEqual(plan["operation"], contract.OPERATION)
        self.assertEqual(plan["manifest"], self.manifest())
        digest = self.approve(plan=plan)
        self.assertEqual(correction.fingerprint(plan), digest)
        before = self.snapshot()
        output = StringIO()
        call_command("correct_cla_box_1902", **SCOPE, apply=True, actor_id=self.admin.pk,
                     expected_plan_hash=digest, stdout=output)
        self.assertIn("Applied atomically", output.getvalue())
        self.assert_preserved(before)

    def test_sql_writer_preserves_all_science_and_full_clean_evidence(self):
        digest = self.approve()
        before = self.snapshot()
        self.write(digest)
        self.assert_preserved(before)
        for evidence in HistoricalTemperatureEvidence.objects.order_by("pk"):
            evidence.reading.full_clean()
            evidence.full_clean()
            expected = evidence.reading_id in self.reading_ids
            self.assertEqual(self.execute("SELECT public.box_1902_evidence_valid_v2(%s)",
                                          [evidence.pk], user=contract.RUNTIME), [(expected,)])

    def test_approval_rejects_omitted_reordered_substituted_and_extra_manifest(self):
        original = self.manifest()
        mutations = []
        omitted = deepcopy(original)
        omitted["evidence"].pop()
        mutations.append(omitted)
        reordered = deepcopy(original)
        reordered["readings"].reverse()
        mutations.append(reordered)
        substituted = deepcopy(original)
        substituted["biology"][0]["polyp_count"] = None
        mutations.append(substituted)
        extra = deepcopy(original)
        extra["unreviewed"] = []
        mutations.append(extra)
        for manifest in mutations:
            with self.subTest(keys=list(manifest)):
                before = self.snapshot()
                with self.assertRaises(psycopg.Error):
                    self.approve(manifest=manifest)
                self.assertEqual(self.snapshot(), before)

    def test_missing_approval_wrong_hash_and_wrong_actor_cannot_write(self):
        before = self.snapshot()
        with self.assertRaises(psycopg.Error):
            self.write("0" * 64)
        self.assertEqual(self.snapshot(), before)
        for expiry in (datetime.now(UTC) - timedelta(seconds=1),
                       datetime.now(UTC) + timedelta(hours=2)):
            with self.subTest(expiry=expiry), self.assertRaises(psycopg.Error):
                self.approve(expires=expiry)
            self.assertEqual(self.snapshot(), before)
        digest = self.approve()
        before = self.snapshot()
        for actor, supplied in ((self.admin.pk, "0" * 64), (self.admin.pk + 10000, digest)):
            with self.subTest(actor=actor), self.assertRaises(psycopg.Error):
                self.write(supplied, actor=actor)
            self.assertEqual(self.snapshot(), before)

    def test_nonadmin_inactive_and_foreign_membership_are_rejected(self):
        digest = self.approve()
        for assignments in ("role='viewer'", "is_active=false", "organization_id=2"):
            with self.subTest(assignments=assignments):
                self.execute(f"UPDATE accounts_organizationmembership SET {assignments} WHERE user_id=%s",
                             [self.admin.pk], user=contract.RUNTIME)
                before = self.snapshot()
                with self.assertRaises(psycopg.Error):
                    self.write(digest)
                self.assertEqual(self.snapshot(), before)
                self.execute("UPDATE accounts_organizationmembership SET role='admin', is_active=true, "
                             "organization_id=1 WHERE user_id=%s", [self.admin.pk], user=contract.RUNTIME)

    def test_exact_target_scope_and_target_species_are_required(self):
        for overrides in ({"organization_id": 2}, {"box_id": self.foreign_box.pk},
                          {"target_strain_id": 214}, {"source_species_id": 164}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                correction.build_plan(**(SCOPE | overrides))
        # Even a complete independently supplied plan cannot authorize wrong taxonomy.
        plan = correction.build_plan(**SCOPE)
        self.execute("UPDATE taxonomy_strain SET species_id=131 WHERE id=285", user=contract.RUNTIME)
        plan["manifest"] = self.manifest()
        digest = self.approve(plan=plan)
        before = self.snapshot()
        with self.assertRaises(psycopg.Error):
            self.write(digest)
        self.assertEqual(self.snapshot(), before)

    def test_runtime_cannot_forge_private_state_or_replace_functions(self):
        before = self.snapshot()
        for statement in (
            "SELECT * FROM box1902_private.approval_v2",
            "INSERT INTO box1902_private.approval_v2 (id) VALUES (1)",
            "UPDATE box1902_private.approval_v2 SET consumed_at=clock_timestamp()",
            "INSERT INTO box1902_private.context_v2 (approval_id) VALUES (1)",
            "DELETE FROM box1902_private.correction_v2",
            "ALTER TABLE cultures_box DISABLE TRIGGER historical_box_guard",
            ("CREATE OR REPLACE FUNCTION public.box_1902_manifest_v2() RETURNS jsonb "
             "LANGUAGE sql AS 'SELECT NULL::jsonb'"),
            "CREATE FUNCTION public.forged_capability() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        ):
            with self.subTest(statement=statement):
                self.denied(statement)
        self.assertEqual(self.snapshot(), before)

    def test_genuine_logins_cannot_escalate_roles_or_session_authorization(self):
        for user in (contract.RUNTIME, contract.APPROVER, contract.EXECUTOR):
            for target in set(contract.ROLES) - {user}:
                with self.subTest(user=user, target=target):
                    self.denied(sql.SQL("SET ROLE {}").format(sql.Identifier(target)), user=user)
            self.denied("SET SESSION AUTHORIZATION box1902_owner", user=user)
            self.denied("SET session_replication_role=replica", user=user)

    def test_only_executor_can_lock_or_apply_and_it_cannot_write_tables(self):
        digest = self.approve()
        before = self.snapshot()
        for role in (contract.RUNTIME, contract.APPROVER, contract.MIGRATOR):
            self.denied("SELECT public.box_1902_lock_v2(%s)", [self.admin.pk], user=role)
            self.denied("SELECT public.box_1902_apply_v2(%s,%s)", [self.admin.pk, digest], user=role)
        self.denied("UPDATE cultures_box SET global_code='CLC-JKA-1.009' WHERE id=1902", user=contract.EXECUTOR)
        self.denied("UPDATE measurements_historicaltemperaturereading SET association_status='pending_review'",
                    user=contract.EXECUTOR)
        self.assertEqual(self.snapshot(), before)

    def test_approval_cannot_be_replayed_or_reissued(self):
        plan = correction.build_plan(**SCOPE)
        digest = self.approve(plan=plan)
        self.write(digest)
        before = self.snapshot()
        with self.assertRaises(psycopg.Error):
            self.write(digest)
        with self.assertRaises(psycopg.Error):
            self.approve(plan=plan, manifest=plan["manifest"])
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_execution_has_exactly_one_commit(self):
        digest = self.approve()
        barrier = Barrier(2)

        def worker():
            with self.connect(contract.EXECUTOR) as conn:
                barrier.wait(timeout=10)
                try:
                    conn.execute("SELECT public.box_1902_apply_v2(%s,%s)", [self.admin.pk, digest])
                    return "committed"
                except psycopg.Error as error:
                    return error.sqlstate

        before = self.snapshot()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results.count("committed"), 1, results)
        self.assertTrue(all(result == "committed" or result in ("55P03", "42501") for result in results), results)
        self.assert_preserved(before)

    def inject_failure(self, table, event):
        # Only the lease-verified disposable cluster administrator fault-injects.
        self.execute("CREATE FUNCTION public.box1902_test_failure() RETURNS trigger LANGUAGE plpgsql AS "
                     "$$ BEGIN RAISE EXCEPTION 'synthetic rollback fault' USING ERRCODE='23514'; END $$")
        self.execute(sql.SQL("CREATE TRIGGER box1902_test_failure BEFORE {} ON {} "
                             "FOR EACH ROW EXECUTE FUNCTION public.box1902_test_failure()").format(
                                 sql.SQL(event), sql.Identifier(*table.split("."))))

    def test_sql_audit_failure_rolls_back_science_context_registry_and_consumption(self):
        digest = self.approve()
        self.inject_failure("public.audit_auditlog", "INSERT")
        before = self.snapshot()
        self.denied("SELECT public.box_1902_apply_v2(%s,%s)", [self.admin.pk, digest],
                    user=contract.EXECUTOR, state="23514")
        self.assertEqual(self.snapshot(), before)

    def test_consumption_failure_rolls_back_entire_correction(self):
        digest = self.approve()
        self.inject_failure("box1902_private.approval_v2", "UPDATE")
        before = self.snapshot()
        self.denied("SELECT public.box_1902_apply_v2(%s,%s)", [self.admin.pk, digest],
                    user=contract.EXECUTOR, state="23514")
        self.assertEqual(self.snapshot(), before)

    def test_outer_transaction_rollback_restores_live_approval_and_all_rows(self):
        digest = self.approve()
        before = self.snapshot()
        with self.connect(contract.EXECUTOR) as conn:
            conn.execute("BEGIN")
            conn.execute("SELECT public.box_1902_apply_v2(%s,%s)", [self.admin.pk, digest])
            conn.execute("ROLLBACK")
        self.assertEqual(self.snapshot(), before)
        self.write(digest)
        self.assert_preserved(before)

    def test_reverse_migration_refuses_before_and_after_correction(self):
        for corrected in (False, True):
            if corrected:
                self.write(self.approve())
            before = self.snapshot()
            self.use_django_role(contract.MIGRATOR)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET ROLE box1902_owner")
                with self.assertRaises(IrreversibleError):
                    MigrationExecutor(connection).migrate([PREVIOUS])
            finally:
                self.use_django_role(contract.EXECUTOR)
            self.assertEqual(self.snapshot(), before)
            self.assertFalse(contract.attest(connection)["retired"])

    def test_registry_is_immutable_and_retirement_preserves_evidence_validation(self):
        self.write(self.approve())
        before = self.snapshot()
        with self.connect(contract.MIGRATOR) as owner:
            owner.execute("SET ROLE box1902_owner")
            for table in ("approval_v2", "context_v2", "correction_v2"):
                for statement in (f"UPDATE box1902_private.{table} SET approval_id=approval_id"
                                  if table != "approval_v2" else "UPDATE box1902_private.approval_v2 SET actor_id=actor_id",
                                  f"DELETE FROM box1902_private.{table}",
                                  f"TRUNCATE box1902_private.{table} CASCADE"):
                    with self.subTest(table=table, statement=statement), self.assertRaises(psycopg.Error):
                        owner.execute(statement)
        self.assertEqual(self.snapshot(), before)
        self.execute(contract.retirement_sql())
        self.use_django_role(contract.RUNTIME)
        self.assertEqual(contract.attest(connection), {"operation": contract.OPERATION, "retired": True})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.execute("SELECT to_regprocedure('public.box_1902_apply_v2(bigint,text)')"), [(None,)])
        for evidence in HistoricalTemperatureEvidence.objects.filter(reading_id__in=self.reading_ids):
            self.assertEqual(self.execute("SELECT public.box_1902_evidence_valid_v2(%s)",
                                          [evidence.pk], user=contract.RUNTIME), [(True,)])
            evidence.full_clean()

    def test_ordinary_runtime_box_identity_change_still_invalidates(self):
        before = self.science_snapshot()
        self.execute("UPDATE public.cultures_box SET global_code='CLC-JKA-1.009', strain_id=285 "
                     "WHERE id=1902 AND organization_id=1", user=contract.RUNTIME)
        after = self.science_snapshot()
        expected = deepcopy(before)
        for reading in expected[HistoricalTemperatureReading._meta.db_table]:
            if reading["id"] in self.reading_ids:
                reading["association_status"] = "pending_review"
        self.assertEqual(after, expected)
        for table in contract.PRIVATE_TABLES:
            self.assertEqual(self.execute(f"SELECT count(*) FROM box1902_private.{table}"), [(0,)])

    def test_in_memory_evidence_changes_cannot_reuse_validated_primary_key(self):
        self.write(self.approve())
        evidence_id = HistoricalTemperatureEvidence.objects.filter(reading_id=12173).get().pk
        before = self.snapshot()
        for field, value in (("sheet", "Changed source notes"),
                             ("source_value", "0.00000"),
                             ("source_box", "CLA-JKA-1.10")):
            with self.subTest(field=field):
                evidence = HistoricalTemperatureEvidence.objects.get(pk=evidence_id)
                evidence.full_clean()
                setattr(evidence, field, value)
                self.assertEqual(self.execute("SELECT public.box_1902_evidence_valid_v2(%s)",
                                              [evidence.pk], user=contract.RUNTIME), [(True,)])
                with self.assertRaises(ValidationError) as raised:
                    evidence.full_clean()
                self.assertIn("source_box", raised.exception.message_dict)
        self.assertEqual(self.snapshot(), before)

    def test_changed_biology_notes_invalidate_exact_historical_record(self):
        self.write(self.approve())
        evidence = HistoricalTemperatureEvidence.objects.get(reading_id=12173)
        self.execute("UPDATE public.measurements_biologicalmeasurement SET notes=%s WHERE id=%s",
                     ["Changed historical notes", evidence.reading.biological_measurement_id],
                     user=contract.RUNTIME)
        before = self.snapshot()
        self.assertEqual(self.execute("SELECT public.box_1902_evidence_valid_v2(%s)",
                                      [evidence.pk], user=contract.RUNTIME), [(False,)])
        evidence = HistoricalTemperatureEvidence.objects.get(pk=evidence.pk)
        with self.assertRaises(ValidationError) as raised:
            evidence.full_clean()
        self.assertIn("source_box", raised.exception.message_dict)
        self.assertEqual(self.snapshot(), before)

    def test_live_biology_change_after_approval_rejects_stale_manifest_without_writes(self):
        digest = self.approve()
        approved_manifest = self.manifest()
        biology_id = next(row["biological_measurement_id"] for row in approved_manifest["readings"]
                          if row["id"] == 12173)
        self.execute("UPDATE public.measurements_biologicalmeasurement SET polyp_count=1 WHERE id=%s",
                     [biology_id], user=contract.RUNTIME)
        self.assertNotEqual(self.manifest(), approved_manifest)
        self.assertEqual(HistoricalTemperatureReading.objects.get(pk=12173).association_status, "pending_review")
        before = self.snapshot()
        self.denied("SELECT public.box_1902_apply_v2(%s,%s)", [self.admin.pk, digest],
                    user=contract.EXECUTOR, state="23514")
        self.assertEqual(self.snapshot(), before)

    def test_application_postcondition_failure_rolls_back_real_sql_writer(self):
        plan = correction.build_plan(**SCOPE)
        self.assertEqual(plan["classification"], "READY", plan.get("conflicts"))
        digest = self.approve(plan=plan)
        before = self.snapshot()
        dependencies = correction.reconciliation._dependencies
        calls = 0
        writer_observed = False

        def changed_postcondition(*args, **kwargs):
            nonlocal calls, writer_observed
            calls += 1
            result = dependencies(*args, **kwargs)
            if calls == 2:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT global_code, strain_id FROM cultures_box WHERE id=1902")
                    writer_observed = cursor.fetchone() == ("CLC-JKA-1.009", 285)
                result = deepcopy(result)
                result["synthetic_postcondition_mismatch"] = True
            return result

        with patch.object(correction.reconciliation, "_dependencies", side_effect=changed_postcondition):
            with self.assertRaisesRegex(ValueError, "Scientific or historical state changed during correction"):
                correction.apply_plan(**SCOPE, actor=self.admin, expected_plan_hash=digest)
        self.assertEqual(calls, 2)
        self.assertTrue(writer_observed, "The protected SQL writer must run before the fault")
        self.assertEqual(self.snapshot(), before)

    def test_cca_schema_attests_new_guards_then_rejects_absent_cca_helpers(self):
        self.assertEqual(contract.attest(connection), {"operation": contract.OPERATION, "retired": False})
        self.assertFalse(cca._qa())
        for signature, _body, _result, _names in cca.role_contract.helper_specs(cca.role_contract.ROLE):
            self.assertEqual(self.execute("SELECT to_regprocedure(%s)", [signature]), [(None,)])
        with self.assertRaisesRegex(ValueError, r"^CCA-EOB helper is missing or differs from the reviewed contract\.$"):
            cca._schema_state()

    def test_cca_schema_rejects_historical_guard_drift(self):
        self.assertFalse(contract.attest(connection)["retired"])
        self.execute("ALTER FUNCTION public.historical_temperature_box_guard() SET search_path=public")
        with self.assertRaisesRegex(contract.ContractError,
                                    r"^Function definition drift: public\.historical_temperature_box_guard$"):
            cca._schema_state()

    def test_guard_attestation_rejects_drift_instead_of_accepting_cca_schema_shortcut(self):
        self.assertFalse(contract.attest(connection)["retired"])
        self.execute("ALTER TABLE cultures_box DISABLE TRIGGER historical_box_guard")
        with self.assertRaises(contract.ContractError):
            contract.attest(connection)
        self.execute("ALTER TABLE cultures_box ENABLE TRIGGER historical_box_guard")
        self.assertFalse(contract.attest(connection)["retired"])
        self.execute("ALTER FUNCTION public.historical_temperature_box_guard() SET search_path=public")
        with self.assertRaises(contract.ContractError):
            contract.attest(connection)

    def test_proof_only_document_is_rejected_by_emitter_and_sql_approval_guard(self):
        manifest = self.manifest()
        text = contract.canonical_plan({"operation": contract.OPERATION, "manifest": manifest})
        expires = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        with self.assertRaisesRegex(ValueError, "Complete reviewed READY v2 workflow plan"):
            contract.approval_sql(text, self.admin.pk, manifest, expires)
        before = self.snapshot()
        self.denied(
            "INSERT INTO box1902_private.approval_v2 "
            "(id,operation,actor_id,plan_text,plan_hash,manifest,expires_at) "
            "VALUES (1,%s,%s,%s,%s,%s::jsonb,%s::timestamptz)",
            [contract.OPERATION, self.admin.pk, text, contract.plan_digest(text),
             contract.canonical_plan(manifest), expires], user=contract.APPROVER,
        )
        self.assertEqual(self.snapshot(), before)

    def test_attestation_rejects_parameter_acl_that_could_disable_all_guards(self):
        self.execute("GRANT SET ON PARAMETER session_replication_role TO polypbase")
        try:
            with self.assertRaisesRegex(contract.ContractError, "Trigger-bypass parameter privilege"):
                contract.attest(connection)
        finally:
            self.execute("REVOKE SET ON PARAMETER session_replication_role FROM polypbase")
        self.assertFalse(contract.attest(connection)["retired"])

    def test_attestation_rejects_unreviewed_runtime_definer_capability(self):
        with self.connect(contract.MIGRATOR) as owner:
            owner.execute("SET ROLE box1902_owner")
            owner.execute("CREATE FUNCTION public.unreviewed_definer() RETURNS int LANGUAGE sql "
                          "SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT 1'")
            owner.execute("GRANT EXECUTE ON FUNCTION public.unreviewed_definer() TO polypbase")
        with self.assertRaisesRegex(contract.ContractError, "unreviewed SECURITY DEFINER"):
            contract.attest(connection)
