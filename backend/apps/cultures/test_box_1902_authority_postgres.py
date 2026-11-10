"""Real-role authority proof, not the Box 1902 correction implementation.

Run only through scripts/test_historical_temperature_production.py. SimpleTestCase
requests no Django database: the harness's fresh cluster hosts two minimal SQL
fixtures, without applying migrations or installing the blocked 0018 prototype.
"""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import os
from pathlib import Path
import re
from threading import Barrier
import time
from unittest import skipUnless

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
LEASE = os.environ.get("POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER", "")
OWNER_DB = "box1902_owner_proof"
HARD_DB = "box1902_hardened_proof"
RUNTIME = "polypbase"
OWNER = "box1902_owner"
APPROVER = "box1902_approver"
EXECUTOR = "box1902_executor"
MIGRATOR = "box1902_migrator"
OTHER = "box1902_other"
ROLES = (RUNTIME, OWNER, APPROVER, EXECUTOR, MIGRATOR, OTHER)
TABLES = (
    "public.taxonomy_species", "public.taxonomy_strain", "public.cultures_box",
    "public.measurements_biologicalmeasurement",
    "public.measurements_historicaltemperaturereading",
    "public.measurements_historicaltemperatureevidence",
    "box1902_private.approval", "box1902_private.context", "box1902_private.outcome",
)
APPROVAL_COLUMNS = (
    "id, operation, executor, organization_id, box_id, old_code, new_code, "
    "old_strain, new_strain, review_ref, expires_at"
)
ISSUE = f"""INSERT INTO box1902_private.approval ({APPROVAL_COLUMNS}) VALUES (
    1, 'box1902_authority_proof_v1', 'box1902_executor', 1, 1902,
    'CLA-JKA-1.009', 'CLC-JKA-1.009', 213, 285,
    'synthetic-authority-only-not-a-manifest', clock_timestamp() + interval '10 minutes')"""
CALL = "SELECT box1902_private.apply(1)"
EXCEPTION_CHECK = """
        IF session_user = 'box1902_executor' AND EXISTS (
            SELECT 1 FROM box1902_private.context c
            JOIN box1902_private.approval a ON a.id = c.approval_id
            WHERE c.xid = pg_current_xact_id() AND c.backend_pid = pg_backend_pid()
              AND c.executor = session_user AND a.executor = session_user
              AND a.consumed_at IS NULL AND a.expires_at > clock_timestamp()
              AND a.operation = 'box1902_authority_proof_v1'
              AND a.box_id = OLD.id AND a.organization_id = OLD.organization_id
              AND a.old_code = OLD.global_code AND a.old_strain = OLD.strain_id
              AND a.new_code = NEW.global_code AND a.new_strain = NEW.strain_id
              AND c.before_box = to_jsonb(OLD) AND c.after_box = to_jsonb(NEW)
        ) THEN
            UPDATE box1902_private.context SET validated = true
                WHERE xid = pg_current_xact_id() AND backend_pid = pg_backend_pid()
                  AND executor = session_user;
            RETURN NEW;
        END IF;
"""


def guard_sql():
    """Keep the deployed 0014 ordinary branch; qualify tables and add one private check."""
    original = import_module("apps.measurements.migrations.0014_measurement_canonical_evidence")._box
    marker = "        UPDATE measurements_historicaltemperaturereading r SET association_status"
    if original.count(marker) != 1:
        raise AssertionError("Historical Box guard changed; review the proof fixture")
    guarded = original.replace(marker, EXCEPTION_CHECK + marker)
    guarded = guarded.replace(
        "FUNCTION historical_temperature_box_guard()", "FUNCTION box1902_private.box_guard()"
    ).replace(
        "LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp",
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp",
    )
    for table in ("cultures_box", "measurements_biologicalmeasurement", "measurements_historicaltemperaturereading"):
        guarded = re.sub(r"\b" + table + r"\b", "public." + table, guarded)
    return guarded + """
REVOKE ALL ON FUNCTION box1902_private.box_guard() FROM PUBLIC;
CREATE TRIGGER historical_box_guard BEFORE UPDATE ON public.cultures_box
FOR EACH ROW EXECUTE FUNCTION box1902_private.box_guard();
"""


@skipUnless(LEASE, "Requires the fresh PG18 cluster lease from the disposable production-test harness")
class Box1902AuthorityPostgreSQLTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        config = settings.DATABASES["default"]
        lease = Path(LEASE).resolve()
        if (not lease.is_dir() or lease.name != "data"
                or not lease.parent.name.startswith("polypbase-ht-production-synthetic-")
                or config["HOST"] != "127.0.0.1" or config["NAME"] != "subculture_qa"
                or config["USER"] != "subculture_qa"):
            raise RuntimeError("Refuse any database outside the newly allocated harness")
        cls.port = int(config["PORT"])
        with cls.connect("subculture_qa", "subculture_qa") as conn:
            version, directory, administrator = conn.execute(
                "SELECT current_setting('server_version_num')::int, "
                "current_setting('data_directory'), current_user"
            ).fetchone()
            if not 180000 <= version < 190000 or Path(directory).resolve() != lease or administrator != "subculture_qa":
                raise RuntimeError("Fresh cluster lease or PostgreSQL version mismatch")
            print(f"Authority proof: PostgreSQL {conn.info.server_version}, two disposable databases", flush=True)
            for role in ROLES:
                login = "NOLOGIN" if role == OWNER else "LOGIN"
                conn.execute(sql.SQL(
                    "CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(role), sql.SQL(login)))
            conn.execute(sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE").format(
                sql.Identifier(OWNER), sql.Identifier(MIGRATOR)
            ))
            cls.addClassCleanup(cls.drop_databases)
            for database in (OWNER_DB, HARD_DB):
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER polypbase").format(sql.Identifier(database)))
        cls.fixture = (ROOT / "scripts/box_1902_authority_fixture.sql").read_text(encoding="utf-8") + guard_sql()

    @classmethod
    def connect(cls, user=RUNTIME, db=HARD_DB):
        return psycopg.connect(
            host="127.0.0.1", port=cls.port, dbname=db, user=user, password="",
            sslmode="disable", connect_timeout=5, autocommit=True,
            options=("-c statement_timeout=120000 -c lock_timeout=10000" if user == "subculture_qa"
                     else "-c statement_timeout=10000 -c lock_timeout=5000"),
        )

    @classmethod
    def execute(cls, statement, *, user="subculture_qa", db=HARD_DB, params=None):
        with cls.connect(user, db) as conn:
            cursor = conn.execute(statement, params)
            return cursor.fetchall() if cursor.description else []

    def setUp(self):
        super().setUp()
        for database in (OWNER_DB, HARD_DB):
            self.execute(sql.SQL("ALTER DATABASE {} OWNER TO polypbase").format(sql.Identifier(database)), db=database)
            self.execute("DROP SCHEMA IF EXISTS box1902_private CASCADE; DROP SCHEMA public CASCADE; "
                         "CREATE SCHEMA public AUTHORIZATION polypbase", db=database)
            self.execute(self.fixture, user=RUNTIME, db=database)
        self.harden()

    @classmethod
    def drop_databases(cls):
        for database in (OWNER_DB, HARD_DB):
            cls.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)),
                        db="subculture_qa")

    def harden(self):
        # Explicit ownership transfer avoids REASSIGN OWNED touching the other test database.
        self.execute("ALTER DATABASE box1902_hardened_proof OWNER TO box1902_owner")
        for schema in ("public", "box1902_private"):
            self.execute(sql.SQL("ALTER SCHEMA {} OWNER TO box1902_owner").format(sql.Identifier(schema)))
        for table in TABLES:
            self.execute(sql.SQL("ALTER TABLE {} OWNER TO box1902_owner").format(sql.Identifier(*table.split("."))))
        for function in ("box1902_private.apply(integer)", "box1902_private.box_guard()"):
            self.execute(f"ALTER FUNCTION {function} OWNER TO box1902_owner")
        self.execute(f"""
REVOKE ALL ON DATABASE box1902_hardened_proof FROM PUBLIC, polypbase;
GRANT CONNECT ON DATABASE box1902_hardened_proof TO polypbase, box1902_approver,
    box1902_executor, box1902_migrator, box1902_other;
REVOKE ALL ON SCHEMA public, box1902_private FROM PUBLIC, polypbase;
GRANT USAGE ON SCHEMA public TO polypbase;
GRANT USAGE ON SCHEMA box1902_private TO box1902_approver, box1902_executor, box1902_other;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO polypbase;
GRANT UPDATE (organization_id, global_code, strain_id, local_code, status, box_number, notes)
    ON public.cultures_box TO polypbase;
GRANT INSERT ({APPROVAL_COLUMNS}) ON box1902_private.approval TO box1902_approver;
GRANT EXECUTE ON FUNCTION box1902_private.apply(integer) TO box1902_executor;
ALTER DEFAULT PRIVILEGES FOR ROLE box1902_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
""")

    def issue(self):
        self.execute(ISSUE, user=APPROVER)

    def apply(self):
        self.execute(CALL, user=EXECUTOR)

    def denied(self, statement, *, user=RUNTIME, state="42501"):
        with self.assertRaises(psycopg.Error) as raised:
            self.execute(statement, user=user)
        self.assertEqual(raised.exception.sqlstate, state, str(raised.exception))

    def snapshot(self):
        return {table: self.execute(f"SELECT to_jsonb(t) FROM {table} t ORDER BY to_jsonb(t)::text")
                for table in TABLES}

    def test_starting_topology_catalogs_show_runtime_ownership_not_security(self):
        self.assertEqual(self.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()",
                                      db=OWNER_DB), [(RUNTIME,)])
        self.assertEqual(self.execute("SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class "
                                      "WHERE oid IN ('cultures_box'::regclass, 'box1902_private.approval'::regclass)",
                                      db=OWNER_DB), [(RUNTIME,)])
        self.assertEqual(self.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname='polypbase'"),
                         [(False, False, False)])
        self.assertEqual(self.execute("SELECT count(*) FROM pg_auth_members WHERE member='polypbase'::regrole"), [(0,)])
        self.assertEqual(self.execute("SELECT has_schema_privilege('polypbase','public','CREATE')", db=OWNER_DB), [(True,)])

    def test_starting_runtime_owner_can_forge_approval_and_context_despite_revokes(self):
        self.execute(ISSUE, user=RUNTIME, db=OWNER_DB)
        self.execute("INSERT INTO box1902_private.context VALUES "
                     "(1, pg_current_xact_id(), pg_backend_pid(), session_user, '{}'::jsonb, '{}'::jsonb)",
                     user=RUNTIME, db=OWNER_DB)
        self.execute("UPDATE box1902_private.approval SET consumed_at=clock_timestamp()", user=RUNTIME, db=OWNER_DB)
        self.assertEqual(self.execute("SELECT count(*) FROM box1902_private.context", db=OWNER_DB), [(1,)])

    def test_starting_runtime_owner_can_replace_function_and_disable_trigger(self):
        self.execute("ALTER TABLE public.cultures_box DISABLE TRIGGER historical_box_guard", user=RUNTIME, db=OWNER_DB)
        self.execute("CREATE OR REPLACE FUNCTION box1902_private.box_guard() RETURNS trigger "
                     "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$", user=RUNTIME, db=OWNER_DB)
        self.assertEqual(self.execute("SELECT tgenabled FROM pg_trigger WHERE tgname='historical_box_guard'", db=OWNER_DB),
                         [("D",)])

    def test_hardened_catalog_owners_membership_acl_and_definer_configuration(self):
        self.assertEqual(self.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname", params=(list(ROLES),)
        ), [(role, role != OWNER, False, False, False, False, False, False) for role in sorted(ROLES)])
        self.assertEqual(self.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"),
                         [(OWNER,)])
        self.assertEqual(self.execute("SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c "
                                      "JOIN pg_namespace n ON n.oid=c.relnamespace "
                                      "WHERE n.nspname IN ('public','box1902_private')"), [(OWNER,)])
        self.assertEqual(self.execute("SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace "
                                      "WHERE nspname IN ('public','box1902_private') ORDER BY 1"),
                         [("box1902_private", OWNER), ("public", OWNER)])
        self.assertEqual(self.execute("SELECT pg_get_userbyid(roleid), pg_get_userbyid(member), admin_option, "
                                      "inherit_option, set_option FROM pg_auth_members "
                                      "WHERE member IN (SELECT oid FROM pg_roles WHERE rolname = ANY(%s)) "
                                      "OR roleid IN (SELECT oid FROM pg_roles WHERE rolname = ANY(%s))",
                                      params=(list(ROLES), list(ROLES))),
                         [(OWNER, MIGRATOR, False, False, True)])
        for role in (RUNTIME, APPROVER, EXECUTOR, OTHER):
            for target in (OWNER, APPROVER, MIGRATOR, EXECUTOR):
                if role != target:
                    self.assertEqual(self.execute("SELECT pg_has_role(%s,%s,'MEMBER'), pg_has_role(%s,%s,'USAGE'), "
                                                  "pg_has_role(%s,%s,'SET')", params=(role, target) * 3),
                                     [(False, False, False)])
        self.assertEqual(self.execute("SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig "
                                      "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                      "WHERE n.nspname='box1902_private' ORDER BY p.proname"),
                         [("apply", OWNER, True, ["search_path=pg_catalog, pg_temp"]),
                          ("box_guard", OWNER, True, ["search_path=pg_catalog, pg_temp"])])
        self.assertEqual(self.execute("SELECT p.proname, a.grantee::regrole::text, a.privilege_type "
                                      "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                      "CROSS JOIN LATERAL aclexplode(p.proacl) a "
                                      "WHERE n.nspname='box1902_private' ORDER BY 1,2"),
                         [("apply", EXECUTOR, "EXECUTE"), ("apply", OWNER, "EXECUTE"), ("box_guard", OWNER, "EXECUTE")])
        self.assertEqual(self.execute("SELECT tgenabled, tgfoid::regprocedure::text FROM pg_trigger "
                                      "WHERE tgname='historical_box_guard'"), [("O", "box1902_private.box_guard()")])
        for role in (RUNTIME, APPROVER, EXECUTOR, OTHER):
            self.assertEqual(self.execute("SELECT has_schema_privilege(%s,'public','CREATE'), "
                                          "has_schema_privilege(%s,'box1902_private','CREATE'), "
                                          "has_database_privilege(%s,current_database(),'CREATE,TEMP')", params=(role,) * 3),
                             [(False, False, False)])
        self.assertEqual(self.execute("SELECT attname FROM pg_attribute WHERE attrelid='box1902_private.approval'::regclass "
                                      "AND attnum>0 AND has_column_privilege('box1902_approver', attrelid, attnum, 'INSERT') "
                                      "ORDER BY attnum"), [(c.strip(),) for c in APPROVAL_COLUMNS.split(",")])
        self.assertEqual(self.execute("SELECT has_table_privilege('box1902_approver','box1902_private.approval','UPDATE,DELETE,TRUNCATE')"),
                         [(False,)])

    def test_genuine_logins_cannot_set_owner_approval_executor_or_session_authorization(self):
        for role in (RUNTIME, APPROVER, EXECUTOR, MIGRATOR, OTHER):
            self.assertEqual(self.execute("SELECT session_user, current_user", user=role), [(role, role)])
        for target in (OWNER, APPROVER, EXECUTOR, MIGRATOR, "subculture_qa", "pg_database_owner"):
            self.denied(sql.SQL("SET ROLE {}").format(sql.Identifier(target)))
        self.denied("SET SESSION AUTHORIZATION box1902_owner")
        self.denied("GRANT box1902_owner TO polypbase")
        self.denied("ALTER ROLE polypbase SUPERUSER")

    def test_runtime_cannot_create_replace_disable_or_drop_protected_objects(self):
        statements = (
            "CREATE SCHEMA forged", "CREATE TABLE public.forged(id int)", "CREATE TEMP TABLE forged(id int)",
            "CREATE FUNCTION public.forged() RETURNS int LANGUAGE sql AS 'SELECT 1'",
            "ALTER DATABASE box1902_hardened_proof OWNER TO polypbase",
            "ALTER TABLE public.cultures_box DISABLE TRIGGER historical_box_guard",
            "DROP TRIGGER historical_box_guard ON public.cultures_box",
            "ALTER FUNCTION box1902_private.box_guard() SECURITY INVOKER",
            "CREATE OR REPLACE FUNCTION box1902_private.box_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
            "SET session_replication_role = replica",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.denied(statement)

    def test_runtime_cannot_read_or_write_approval_or_context(self):
        self.issue()
        # Exercise table ACLs independently of the normal private-schema denial.
        self.execute("GRANT USAGE ON SCHEMA box1902_private TO polypbase")
        before = self.snapshot()
        for statement in (ISSUE, "SELECT * FROM box1902_private.approval",
                          "UPDATE box1902_private.approval SET consumed_at=NULL",
                          "DELETE FROM box1902_private.approval", "TRUNCATE box1902_private.approval",
                          "SELECT * FROM box1902_private.context",
                          "INSERT INTO box1902_private.context VALUES (1,pg_current_xact_id(),pg_backend_pid(),session_user,'{}','{}')",
                          "UPDATE box1902_private.context SET executor=session_user", "DELETE FROM box1902_private.context"):
            with self.subTest(statement=statement):
                self.denied(statement)
        self.assertEqual(self.snapshot(), before)

    def test_approval_authority_and_executor_have_no_cross_authority(self):
        self.issue()
        for statement in ("UPDATE box1902_private.approval SET consumed_at=NULL",
                          "DELETE FROM box1902_private.approval", "TRUNCATE box1902_private.approval",
                          "INSERT INTO box1902_private.context VALUES (1,pg_current_xact_id(),pg_backend_pid(),session_user,'{}','{}')"):
            for role in (APPROVER, EXECUTOR):
                with self.subTest(role=role, statement=statement):
                    self.denied(statement, user=role)
        self.denied(ISSUE, user=EXECUTOR)
        self.denied("UPDATE public.cultures_box SET strain_id=285 WHERE id=1902", user=EXECUTOR)

    def test_unauthorized_roles_cannot_invoke_capability(self):
        self.issue()
        for role in (RUNTIME, APPROVER, MIGRATOR, OTHER):
            self.assertEqual(self.execute("SELECT has_function_privilege(%s,'box1902_private.apply(integer)','EXECUTE')",
                                          params=(role,)), [(False,)])
            self.denied(CALL, user=role)

    def test_unapproved_and_expired_execution_is_rejected_without_changes(self):
        before = self.snapshot()
        self.denied(CALL, user=EXECUTOR)
        self.assertEqual(self.snapshot(), before)
        self.execute(ISSUE.replace("+ interval '10 minutes'", "- interval '1 second'"), user=APPROVER)
        before = self.snapshot()
        self.denied(CALL, user=EXECUTOR)
        self.assertEqual(self.snapshot(), before)

    def test_approved_exact_operation_preserves_41_statuses_values_evidence_and_other_boxes(self):
        self.issue()
        before = self.snapshot()
        self.apply()
        after = self.snapshot()
        for table in TABLES:
            if table not in ("public.cultures_box", "box1902_private.approval", "box1902_private.outcome"):
                self.assertEqual(after[table], before[table], table)
        self.assertEqual(self.execute("SELECT count(*) FROM public.measurements_historicaltemperaturereading "
                                      "WHERE id BETWEEN 12173 AND 12213 AND association_status='confirmed'"), [(41,)])
        expected = [dict(row[0]) for row in before["public.cultures_box"]]
        for row in expected:
            if row["id"] == 1902:
                row.update(global_code="CLC-JKA-1.009", strain_id=285)
        self.assertEqual(sorted(expected, key=lambda r: r["id"]),
                         sorted([r[0] for r in after["public.cultures_box"]], key=lambda r: r["id"]))
        self.assertEqual(self.execute("SELECT executor, outcome, before_box->>'global_code', after_box->>'global_code' "
                                      "FROM box1902_private.outcome"),
                         [(EXECUTOR, "applied", "CLA-JKA-1.009", "CLC-JKA-1.009")])
        self.assertEqual(self.execute("SELECT consumed_at IS NOT NULL FROM box1902_private.approval"), [(True,)])

    def test_approval_cannot_be_replayed_or_reissued(self):
        self.issue()
        self.apply()
        before = self.snapshot()
        self.denied(CALL, user=EXECUTOR)
        self.denied(ISSUE, user=APPROVER, state="23505")
        self.assertEqual(self.snapshot(), before)

    def test_ordinary_runtime_mutations_retain_the_original_guard_behavior(self):
        self.execute("UPDATE public.cultures_box SET notes='ordinary note' WHERE id=1902", user=RUNTIME)
        self.assertEqual(self.execute("SELECT DISTINCT association_status FROM public.measurements_historicaltemperaturereading"),
                         [("confirmed",)])
        self.execute("UPDATE public.cultures_box SET global_code='CLC-JKA-1.009', strain_id=285 WHERE id=1902", user=RUNTIME)
        self.assertEqual(self.execute("SELECT DISTINCT association_status FROM public.measurements_historicaltemperaturereading "
                                      "WHERE id BETWEEN 12173 AND 12213"), [("pending_review",)])
        self.assertEqual(self.execute("SELECT association_status FROM public.measurements_historicaltemperaturereading "
                                      "WHERE id=29020"), [("confirmed",)])
        self.denied("UPDATE public.cultures_box SET organization_id=2 WHERE id=1902", state="23514")
        self.assertEqual(self.execute("SELECT count(*) FROM box1902_private.context"), [(0,)])

    def test_invoker_trigger_reproduces_private_context_failure_then_definer_solves_it(self):
        self.execute("ALTER FUNCTION box1902_private.box_guard() SECURITY INVOKER")
        before = self.snapshot()
        self.denied("UPDATE public.cultures_box SET box_number='90' WHERE id=1902")
        self.assertEqual(self.snapshot(), before)
        self.execute("ALTER FUNCTION box1902_private.box_guard() SECURITY DEFINER")
        self.execute("UPDATE public.cultures_box SET box_number='90' WHERE id=1902", user=RUNTIME)
        self.assertEqual(self.execute("SELECT DISTINCT association_status FROM public.measurements_historicaltemperaturereading "
                                      "WHERE id BETWEEN 12173 AND 12213"), [("pending_review",)])

    def inject_failure(self, table, event):
        self.execute(f"""
CREATE FUNCTION box1902_private.inject_failure() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'synthetic failure after Box update'; END $$;
CREATE TRIGGER synthetic_failure BEFORE {event} ON {table}
FOR EACH ROW EXECUTE FUNCTION box1902_private.inject_failure();
""")

    def test_audit_failure_after_box_update_rolls_back_everything(self):
        self.issue()
        self.inject_failure("box1902_private.outcome", "INSERT")
        before = self.snapshot()
        self.denied(CALL, user=EXECUTOR, state="P0001")
        self.assertEqual(self.snapshot(), before)
        self.execute("DROP TRIGGER synthetic_failure ON box1902_private.outcome")
        self.apply()

    def test_approval_consumption_failure_rolls_back_box_context_and_audit(self):
        self.issue()
        self.inject_failure("box1902_private.approval", "UPDATE")
        before = self.snapshot()
        self.denied(CALL, user=EXECUTOR, state="P0001")
        self.assertEqual(self.snapshot(), before)
        self.execute("DROP TRIGGER synthetic_failure ON box1902_private.approval")
        self.apply()

    def test_outer_transaction_rollback_restores_approval_and_all_rows(self):
        self.issue()
        before = self.snapshot()
        with self.connect(EXECUTOR) as conn:
            conn.execute("BEGIN")
            conn.execute(CALL)
            conn.execute("ROLLBACK")
        self.assertEqual(self.snapshot(), before)
        self.apply()

    def test_concurrent_execution_consumes_only_once(self):
        self.issue()
        barrier = Barrier(2)

        def worker():
            with self.connect(EXECUTOR) as conn:
                barrier.wait(timeout=10)
                try:
                    conn.execute(CALL)
                    return "applied"
                except psycopg.Error as error:
                    return error.sqlstate

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sorted(results), ["42501", "applied"])
        self.assertEqual(self.execute("SELECT count(*) FROM box1902_private.outcome"), [(1,)])
        self.assertEqual(self.execute("SELECT count(*) FROM box1902_private.context"), [(0,)])

    def test_caller_gucs_cannot_forge_exception_and_acl_drift_does_not_spoof_session_user(self):
        self.issue()
        with self.connect(RUNTIME) as conn:
            conn.execute("SET polypbase.box1902_approved='true'")
            conn.execute("SET search_path=pg_temp,public")
            conn.execute("UPDATE public.cultures_box SET box_number='90' WHERE id=1902")
        self.assertEqual(self.execute("SELECT DISTINCT association_status FROM public.measurements_historicaltemperaturereading "
                                      "WHERE id BETWEEN 12173 AND 12213"), [("pending_review",)])
        self.execute("GRANT USAGE ON SCHEMA box1902_private TO polypbase; "
                     "GRANT EXECUTE ON FUNCTION box1902_private.apply(integer) TO polypbase")
        self.denied(CALL)

    def test_future_migrations_require_explicit_trusted_owner_role(self):
        self.denied("ALTER TABLE public.cultures_box ADD COLUMN synthetic_migration integer", user=MIGRATOR)
        with self.connect(MIGRATOR) as conn:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL ROLE box1902_owner")
            self.assertEqual(conn.execute("SELECT session_user, current_user").fetchone(), (MIGRATOR, OWNER))
            conn.execute("ALTER TABLE public.cultures_box ADD COLUMN synthetic_migration integer")
            conn.execute("CREATE FUNCTION box1902_private.future_migration() RETURNS int LANGUAGE sql AS 'SELECT 1'")
            self.assertFalse(conn.execute("SELECT has_function_privilege('polypbase','box1902_private.future_migration()','EXECUTE')").fetchone()[0])
            conn.execute("ROLLBACK")
        self.assertEqual(self.execute("SELECT current_user", user=MIGRATOR), [(MIGRATOR,)])

    def test_lock_wait_cannot_turn_expired_approval_into_successful_invalidation(self):
        self.issue()
        # A trusted test-only expiry adjustment makes the wait deterministic.
        self.execute("UPDATE box1902_private.approval SET expires_at=clock_timestamp()+interval '2 seconds'")
        before = self.snapshot()
        with self.connect("subculture_qa") as blocker:
            blocker.execute("BEGIN")
            blocker.execute("LOCK TABLE public.cultures_box IN ACCESS EXCLUSIVE MODE")
            with self.connect(EXECUTOR) as executor, ThreadPoolExecutor(max_workers=1) as pool:
                pid = executor.info.backend_pid

                def waiting_call():
                    try:
                        executor.execute(CALL)
                        return "applied"
                    except psycopg.Error as error:
                        return error.sqlstate

                future = pool.submit(waiting_call)
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if self.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", params=(pid,)) == [("Lock",)]:
                            break
                        time.sleep(0.02)
                    else:
                        self.fail("Executor did not reach the conflicting maintenance lock")
                    while self.execute("SELECT expires_at > clock_timestamp() FROM box1902_private.approval") == [(True,)]:
                        time.sleep(0.05)
                finally:
                    blocker.execute("ROLLBACK")
                self.assertEqual(future.result(timeout=10), "42501")
        self.assertEqual(self.snapshot(), before)

    def test_writer_rejects_if_trigger_does_not_acknowledge_the_exception(self):
        self.issue()
        # Simulate a missing validation acknowledgement at the protected UPDATE.
        self.execute("CREATE OR REPLACE FUNCTION box1902_private.box_guard() RETURNS trigger "
                     "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp "
                     "AS $$ BEGIN RETURN NEW; END $$")
        before = self.snapshot()
        self.denied(CALL, user=EXECUTOR, state="23514")
        self.assertEqual(self.snapshot(), before)

    def test_context_binding_rejects_wrong_transaction_backend_and_box_images(self):
        self.issue()
        # Trusted fixture-only grants/inserts isolate the context predicate from the ACL barrier.
        self.execute("GRANT USAGE ON SCHEMA public TO box1902_executor; "
                     "GRANT SELECT, UPDATE ON public.cultures_box TO box1902_executor")
        before_box = self.execute("SELECT to_jsonb(b) FROM public.cultures_box b WHERE id=1902")[0][0]
        after_box = {**before_box, "global_code": "CLC-JKA-1.009", "strain_id": 285}
        for mismatch in ("xid", "pid", "before", "after", "executor"):
            with self.subTest(mismatch=mismatch), self.connect(EXECUTOR) as executor:
                executor.execute("BEGIN")
                xid, pid = executor.execute("SELECT pg_current_xact_id()::text, pg_backend_pid()").fetchone()
                self.execute(
                    "INSERT INTO box1902_private.context "
                    "(approval_id,xid,backend_pid,executor,before_box,after_box) VALUES (1,%s::xid8,%s,%s,%s,%s)",
                    params=(str(int(xid) + (mismatch == "xid")), pid + (mismatch == "pid"),
                            RUNTIME if mismatch == "executor" else EXECUTOR,
                            Jsonb({**before_box, "notes": "forged"} if mismatch == "before" else before_box),
                            Jsonb({**after_box, "notes": "forged"} if mismatch == "after" else after_box)),
                )
                executor.execute("UPDATE public.cultures_box SET global_code='CLC-JKA-1.009',strain_id=285 WHERE id=1902")
                self.assertEqual(self.execute("SELECT DISTINCT association_status FROM public.measurements_historicaltemperaturereading "
                                              "WHERE id BETWEEN 12173 AND 12213", user="subculture_qa"), [("confirmed",)])
                # Read our uncommitted changes from this same genuine executor connection.
                self.execute("GRANT SELECT ON public.measurements_historicaltemperaturereading TO box1902_executor")
                self.assertEqual(executor.execute("SELECT DISTINCT association_status FROM public.measurements_historicaltemperaturereading "
                                                  "WHERE id BETWEEN 12173 AND 12213").fetchall(), [("pending_review",)])
                executor.execute("ROLLBACK")
                self.assertEqual(self.execute("SELECT validated FROM box1902_private.context"), [(False,)])
                self.execute("DELETE FROM box1902_private.context")

    def test_wrong_isolation_and_overlong_approval_are_rejected(self):
        self.execute(ISSUE.replace("interval '10 minutes'", "interval '2 hours'"), user=APPROVER)
        before = self.snapshot()
        self.denied(CALL, user=EXECUTOR)
        self.assertEqual(self.snapshot(), before)
        for isolation in ("REPEATABLE READ", "SERIALIZABLE"):
            with self.subTest(isolation=isolation), self.connect(EXECUTOR) as conn:
                conn.execute(f"BEGIN ISOLATION LEVEL {isolation}")
                with self.assertRaises(psycopg.Error) as raised:
                    conn.execute(CALL)
                self.assertEqual(raised.exception.sqlstate, "25001")
                conn.execute("ROLLBACK")
        self.assertEqual(self.snapshot(), before)

    def test_one_off_writer_can_be_retired_without_breaking_ordinary_guards(self):
        self.issue()
        self.apply()
        with self.connect(EXECUTOR) as existing_executor:
            try:
                self.execute("REVOKE EXECUTE ON FUNCTION box1902_private.apply(integer) FROM box1902_executor; "
                             "ALTER ROLE box1902_executor NOLOGIN")
                self.assertEqual(existing_executor.execute(
                    "SELECT has_function_privilege(current_user,'box1902_private.apply(integer)','EXECUTE')"
                ).fetchone(), (False,))
                with self.assertRaises(psycopg.errors.InsufficientPrivilege) as raised:
                    existing_executor.execute(CALL)
                self.assertEqual(raised.exception.diag.message_primary, "permission denied for function apply")
                with self.assertRaises(psycopg.Error):
                    self.connect(EXECUTOR).close()
                self.execute("DROP FUNCTION box1902_private.apply(integer)")
                self.assertEqual(self.execute("SELECT to_regprocedure('box1902_private.apply(integer)')"), [(None,)])
                self.execute("UPDATE public.cultures_box SET box_number='90' WHERE id=1902", user=RUNTIME)
                self.assertEqual(self.execute("SELECT DISTINCT association_status FROM public.measurements_historicaltemperaturereading "
                                              "WHERE id BETWEEN 12173 AND 12213"), [("pending_review",)])
                self.assertEqual(self.execute("SELECT count(*) FROM box1902_private.outcome"), [(1,)])
            finally:
                self.execute("ALTER ROLE box1902_executor LOGIN")
