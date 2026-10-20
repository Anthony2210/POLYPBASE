"""Actual minimal grants, only inside the fresh-cluster production test launcher.

Native Linux identity guards remain mocked by the workflow fixture; SQL role,
row/table locks, IDENTITY inserts and triggers are real. The separate snapshot
probe proves cross-login pg_dump and restoration without these workflow mocks.
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, transaction, DatabaseError
import psycopg
from psycopg import sql

from . import historical_temperature_target as target_tools
from . import historical_temperature_update_guard as update_guard
from .historical_temperature_backup_timeouts import check_backup_timeouts
from . import test_historical_temperature_production as workflow


@skipUnless(os.environ.get("POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER"),
            "Requires the fresh-cluster launcher, never an existing database.")
class RestrictedRoleTests(workflow.ProductionFixture):
    RUNTIME = "polypbase_hist_import"
    BACKUP = "ht_fixture_backup"

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("SHOW data_directory")
            actual = Path(cursor.fetchone()[0]).resolve()
        expected = Path(os.environ["POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER"]).resolve()
        if actual != expected:
            self.fail("The launcher-created cluster identity differs; refusing role setup")
        super().setUp()
        self.owner_config = dict(connection.settings_dict)
        self.database_name = connection.settings_dict["NAME"]
        self.runtime_privilege_guard.stop()
        self.roles_created = []
        self.addCleanup(self.remove_roles)
        with connection.cursor() as cursor:
            for role in (self.RUNTIME, self.BACKUP):
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                                       "NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role)))
                self.roles_created.append(role)
            grants = (Path(__file__).resolve().parents[3] / "deploy" / "scripts" /
                      "historical_temperature_import_grants.sql").read_text(encoding="utf-8")
            # Only disposable database and approved backup-owner names differ from Debian.
            grants = grants.replace("ON DATABASE polypbase TO", "ON DATABASE " +
                                    sql.Identifier(self.database_name).as_string(connection.connection) + " TO")
            grants = grants.replace("OWNER TO polypbase;", "OWNER TO " +
                                    sql.Identifier(self.BACKUP).as_string(connection.connection) + ";")
            cursor.execute(grants)
            cursor.execute("SELECT oid FROM pg_roles WHERE rolname=%s", [self.BACKUP])
            self.backup_oid = cursor.fetchone()[0]
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(self.database_name), sql.Identifier(self.BACKUP)))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(self.BACKUP)))
            cursor.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(self.BACKUP)))
            cursor.execute(sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(self.BACKUP)))
        connection.close()
        connection.settings_dict["USER"] = self.RUNTIME
        connection.ensure_connection()
        self.target.update(role=self.RUNTIME, backup_role=self.BACKUP, backup_role_oid=self.backup_oid)
        patcher = patch.object(target_tools, "verify_target", side_effect=lambda target, **kwargs:
                               update_guard.check_update_guards(connection, self.RUNTIME, self.backup_oid))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(target_tools, "backup_database", side_effect=self.backup_connection)
        patcher.start()
        self.addCleanup(patcher.stop)

    def direct_connection(self, role):
        return psycopg.connect(host=self.owner_config["HOST"], port=self.owner_config["PORT"],
                               dbname=self.database_name, user=role, password="", sslmode="disable",
                               connect_timeout=5, autocommit=True)

    @contextmanager
    def backup_connection(self, target, snapshot):
        self.assertEqual(target["backup_role"], self.BACKUP)
        self.assertTrue(snapshot)
        with self.direct_connection(self.BACKUP) as database:
            check_backup_timeouts(database)
            database.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            database.execute(sql.SQL("SET TRANSACTION SNAPSHOT {}").format(sql.Literal(snapshot)))
            self.assertEqual(database.execute("SELECT session_user, current_user").fetchone(),
                             (self.BACKUP, self.BACKUP))
            yield {**self.owner_config, "USER": self.BACKUP}
            database.execute("SELECT 1")
            database.rollback()
            database.execute("SELECT 1")

    def _fixture_teardown(self):
        # Flush and trigger removal belong to the disposable fixture owner, never runtime.
        if hasattr(self, "owner_config"):
            connection.close()
            connection.settings_dict.update(self.owner_config)
        super()._fixture_teardown()

    def remove_roles(self):
        connection.close()
        connection.settings_dict.update(self.owner_config)
        with connection.cursor() as cursor:
            for table in update_guard.TABLES:
                cursor.execute(sql.SQL("DROP TRIGGER IF EXISTS {} ON public.{}").format(
                    sql.Identifier(update_guard.TRIGGER_NAME), sql.Identifier(table)))
            cursor.execute(sql.SQL("DROP FUNCTION IF EXISTS public.{}()").format(
                sql.Identifier(update_guard.FUNCTION_NAME)))
            cursor.execute("SELECT c.relname, array_agg(a.attname::text ORDER BY a.attnum) "
                           "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                           "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
                           "WHERE n.nspname='public' AND c.relkind IN ('r','p') GROUP BY c.relname")
            tables = cursor.fetchall()
            for role in reversed(self.roles_created):
                for table, columns in tables:
                    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ({}) ON TABLE public.{} FROM {}").format(
                        sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(table), sql.Identifier(role)))
                for statement in ("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}",
                                  "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}",
                                  "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {}",
                                  "REVOKE ALL ON SCHEMA public FROM {}"):
                    cursor.execute(sql.SQL(statement).format(sql.Identifier(role)))
                cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(
                    sql.Identifier(self.database_name), sql.Identifier(role)))
                cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def test_minimal_grants_apply_and_postcheck_preserve_biology_and_audit(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user, current_user, "
                           "has_table_privilege(current_user, 'public.auth_permission', 'SELECT'), "
                           "has_table_privilege(current_user, 'public.cultures_box', 'UPDATE'), "
                           "pg_has_role(current_user, %s, 'MEMBER')", [self.owner_config["USER"]])
            self.assertEqual(cursor.fetchone(), (self.RUNTIME, self.RUNTIME, False, False, False))
        workflow.WorkflowTests.test_plan_backup_apply_postcheck_preserve_everything_else(self)

    def test_actual_restricted_role_refuses_foreign_organization(self):
        from types import SimpleNamespace
        from . import historical_temperature_production as production
        with self.assertRaises(production.ProductionImportError):
            production.build_plan(2, actor=SimpleNamespace(pk=self.admin.pk),
                                  target=self.target, raw_directory=self.raw)

    def test_actual_restricted_role_refuses_stale_plan(self):
        from . import historical_temperature_production as production
        document, evidence = self.reviewed()
        with self.direct_connection(self.owner_config["USER"]) as writer:
            writer.execute("UPDATE public.measurements_biologicalmeasurement SET notes='Application edit' "
                           "WHERE id=%s", [document["plan"]["payloads"][0]["biological_measurement_id"]])
        with self.assertRaises(production.StalePlanError):
            self.apply(document, evidence)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_actual_restricted_role_rolls_back_failures(self):
        workflow.WorkflowTests.test_atomic_rollback_on_evidence_reading_audit_and_receipt_failure(self)

    def test_every_column_update_is_denied_including_noops_zero_rows_and_ctes(self):
        columns = ("notes", "last_login", "is_hidden_from_team", "notes", "notes", "iso_year")
        for table, column in zip(update_guard.TABLES, columns):
            for condition in ("", " WHERE false"):
                statement = sql.SQL("UPDATE public.{} SET {}={}" + condition).format(
                    sql.Identifier(table), sql.Identifier(column), sql.Identifier(column))
                with self.subTest(table=table, condition=condition), self.assertRaises(DatabaseError) as denied:
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement)
                self.assertEqual(denied.exception.__cause__.sqlstate, "42501")
        statements = (
            "WITH changed AS (UPDATE public.cultures_box SET notes='Unaudited' RETURNING id) SELECT count(*) FROM changed",
            "MERGE INTO public.auth_user t USING (VALUES (-1)) s(id) ON t.id=s.id "
            "WHEN MATCHED THEN UPDATE SET last_login=now()",
            "INSERT INTO public.measurements_historicaltemperaturereading "
            "(biological_measurement_id,association_status,iso_year,iso_week,temperature_c,created_at) "
            "SELECT id,'confirmed',2026,1,0,now() FROM public.measurements_biologicalmeasurement LIMIT 1 "
            "ON CONFLICT (biological_measurement_id) DO UPDATE SET iso_year=excluded.iso_year",
        )
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(DatabaseError) as denied:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(statement)
            self.assertEqual(denied.exception.__cause__.sqlstate, "42501")
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE public.cultures_box SET notes='Foreign edit' WHERE id=%s", [self.foreign_box.pk])
        self.foreign_box.refresh_from_db()
        self.assertEqual(self.foreign_box.notes, "")

    def test_all_four_row_lock_modes_still_work_on_all_six_tables(self):
        document, evidence = self.reviewed()
        self.apply(document, evidence)
        for table in update_guard.TABLES:
            for mode in ("UPDATE", "NO KEY UPDATE", "SHARE", "KEY SHARE"):
                with self.subTest(table=table, mode=mode), transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql.SQL("SELECT id FROM public.{} ORDER BY id LIMIT 1 FOR " + mode).format(
                        sql.Identifier(table)))
                    self.assertIsNotNone(cursor.fetchone())

    def test_disabled_or_non_always_guard_refuses_before_any_import_write(self):
        for state in ("DISABLE", "ENABLE"):
            with self.direct_connection(self.owner_config["USER"]) as owner:
                owner.execute(sql.SQL("ALTER TABLE public.cultures_box " + state + " TRIGGER {}").format(
                    sql.Identifier(update_guard.TRIGGER_NAME)))
            try:
                with self.subTest(state=state), self.assertRaisesMessage(ValueError, "UPDATE guard triggers"):
                    self.plan()
                self.assertEqual(self.counts(), (0, 0, 0, 0))
            finally:
                with self.direct_connection(self.owner_config["USER"]) as owner:
                    owner.execute(sql.SQL("ALTER TABLE public.cultures_box ENABLE ALWAYS TRIGGER {}").format(
                        sql.Identifier(update_guard.TRIGGER_NAME)))

    def test_importer_cannot_disable_or_replace_update_guards(self):
        for statement in (
            "ALTER TABLE public.cultures_box DISABLE TRIGGER polypbase_hist_import_no_update",
            "ALTER FUNCTION public.polypbase_hist_import_deny_update() SECURITY DEFINER",
            "SET session_replication_role=replica",
        ):
            with self.subTest(statement=statement), self.assertRaises(DatabaseError) as denied:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(statement)
            self.assertEqual(denied.exception.__cause__.sqlstate, "42501")
        self.plan()

    def test_altered_function_body_or_owner_is_refused(self):
        for alteration in (
            "ALTER FUNCTION public.polypbase_hist_import_deny_update() SET search_path=public",
            "ALTER FUNCTION public.polypbase_hist_import_deny_update() OWNER TO polypbase_hist_import",
            "CREATE OR REPLACE FUNCTION public.polypbase_hist_import_deny_update() RETURNS trigger "
            "LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp "
            "AS 'BEGIN RETURN NULL; END;'",
        ):
            with self.direct_connection(self.owner_config["USER"]) as owner:
                owner.execute(alteration)
            try:
                with self.subTest(alteration=alteration), self.assertRaisesMessage(ValueError, "UPDATE guard function"):
                    self.plan()
            finally:
                with self.direct_connection(self.owner_config["USER"]) as owner:
                    owner.execute(sql.SQL("CREATE OR REPLACE FUNCTION public.{}() RETURNS trigger "
                                          "LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp "
                                          "AS {}").format(sql.Identifier(update_guard.FUNCTION_NAME),
                                                          sql.Literal(update_guard.FUNCTION_BODY)))
                    owner.execute(sql.SQL("ALTER FUNCTION public.{}() OWNER TO {}").format(
                        sql.Identifier(update_guard.FUNCTION_NAME), sql.Identifier(self.BACKUP)))

    def test_competing_writer_lock_causes_atomic_timeout_under_restricted_role(self):
        from . import historical_temperature_production as production
        document, evidence = self.reviewed()
        with self.direct_connection(self.owner_config["USER"]) as writer:
            writer.execute("BEGIN")
            writer.execute("SELECT id FROM public.cultures_box WHERE id=%s FOR UPDATE", [self.box.pk])
            try:
                with patch.object(production, "LOCK_TIMEOUT", "150ms"), self.assertRaises(DatabaseError):
                    self.apply(document, evidence)
            finally:
                writer.rollback()
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_owner_corrections_and_existing_pending_review_trigger_remain_functional(self):
        document, evidence = self.reviewed()
        self.apply(document, evidence)
        biology_id = document["plan"]["payloads"][0]["biological_measurement_id"]
        with self.direct_connection(self.owner_config["USER"]) as owner:
            owner.execute("UPDATE public.auth_user SET last_login=now() WHERE id=%s", [self.admin.pk])
            owner.execute("UPDATE public.measurements_biologicalmeasurement SET polyp_count=coalesce(polyp_count,0)+1 "
                          "WHERE id=%s", [biology_id])
        with connection.cursor() as cursor:
            cursor.execute("SELECT association_status FROM public.measurements_historicaltemperaturereading "
                           "WHERE biological_measurement_id=%s", [biology_id])
            self.assertEqual(cursor.fetchone(), ("pending_review",))

    def test_short_exporter_or_backup_idle_timeout_refuses_before_dump(self):
        from . import historical_temperature_production as production
        document = self.plan()
        with connection.cursor() as cursor:
            cursor.execute("SET idle_in_transaction_session_timeout='600s'")
        try:
            with patch.object(production.database_backup, "create_evidence") as dump:
                with self.assertRaisesMessage(ValueError, "Backup timeout too short"):
                    self.backup(document)
                dump.assert_not_called()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET idle_in_transaction_session_timeout=0")
        with self.direct_connection(self.owner_config["USER"]) as owner:
            owner.execute(sql.SQL("ALTER ROLE {} SET idle_in_transaction_session_timeout='600s'").format(
                sql.Identifier(self.BACKUP)))
        try:
            with patch.object(production.database_backup, "create_evidence") as dump:
                with self.assertRaisesMessage(ValueError, "Backup timeout too short"):
                    self.backup(document)
                dump.assert_not_called()
        finally:
            with self.direct_connection(self.owner_config["USER"]) as owner:
                owner.execute(sql.SQL("ALTER ROLE {} RESET idle_in_transaction_session_timeout").format(
                    sql.Identifier(self.BACKUP)))
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_finite_sufficient_timeout_backs_up_without_changing_global_settings(self):
        with self.direct_connection(self.owner_config["USER"]) as owner:
            owner.execute(sql.SQL("ALTER ROLE {} SET idle_in_transaction_session_timeout='1500s'").format(
                sql.Identifier(self.BACKUP)))
            owner.execute(sql.SQL("ALTER ROLE {} SET transaction_timeout='3600s'").format(
                sql.Identifier(self.BACKUP)))
        with connection.cursor() as cursor:
            cursor.execute("SET idle_in_transaction_session_timeout='1500s'")
            cursor.execute("SET transaction_timeout='3600s'")
        with self.direct_connection(self.owner_config["USER"]) as observer:
            before = observer.execute("SELECT setting FROM pg_settings WHERE name IN "
                                      "('idle_in_transaction_session_timeout','transaction_timeout') ORDER BY name").fetchall()
        document, evidence = self.reviewed()
        self.assertEqual(evidence["plan_hash"], workflow.production.plan_hash(document))
        with connection.cursor() as cursor:
            self.assertEqual(check_backup_timeouts(connection), {
                "idle_in_transaction_session_timeout": 1500000, "transaction_timeout": 3600000})
        with self.direct_connection(self.owner_config["USER"]) as observer:
            self.assertEqual(observer.execute("SELECT setting FROM pg_settings WHERE name IN "
                                             "('idle_in_transaction_session_timeout','transaction_timeout') ORDER BY name").fetchall(), before)

    def test_exporter_expiry_after_archive_verification_cannot_return_evidence(self):
        from . import historical_temperature_production as production
        document = self.plan()
        create = production.database_backup.create_evidence

        def expire(*args, **kwargs):
            evidence = create(*args, **kwargs)
            # Simulate an overrun beyond an admitted finite budget on this test
            # connection only. No server/global timeout or real data is changed.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL idle_in_transaction_session_timeout='100ms'")
            time.sleep(0.5)
            return evidence

        with patch.object(production.database_backup, "create_evidence", side_effect=expire):
            with self.assertRaises(DatabaseError):
                self.backup(document)
        connection.close()
        connection.ensure_connection()
        self.assertEqual(self.counts(), (0, 0, 0, 0))
