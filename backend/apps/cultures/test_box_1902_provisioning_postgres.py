"""First-install provisioning and role collisions on the leased PostgreSQL 18 cluster.

No Django migration or correction is run here. The cluster's own administrator
creates only synthetic roles/objects; security assertions use genuine SQL roles.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, skipUnless

import psycopg
from psycopg import sql
from django.conf import settings

from apps.measurements import box_1902_sql as contract


LEASE = os.environ.get("POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER", "")
DATABASE = "box1902_provisioning_test"
WITNESS = "box1902_collision_witness"
RESERVED = tuple(role for role in contract.ROLES if role != contract.RUNTIME)


@skipUnless(LEASE, "Requires the disposable PostgreSQL 18 runner and its fresh cluster lease")
class Box1902ProvisioningPostgreSQLTests(TestCase):
    @classmethod
    def connect(cls, user="subculture_qa", database=DATABASE):
        return psycopg.connect(
            host="127.0.0.1", port=cls.port, dbname=database, user=user, password="",
            sslmode="disable", connect_timeout=5, autocommit=True,
            options="-c statement_timeout=60000 -c lock_timeout=5000",
        )

    @classmethod
    def execute(cls, statement, params=None, *, user="subculture_qa", database=DATABASE):
        with cls.connect(user, database) as conn:
            cursor = conn.execute(statement, params)
            return cursor.fetchall() if cursor.description else []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        config = settings.DATABASES["default"]
        lease = Path(LEASE).resolve()
        if (settings.SETTINGS_MODULE != "ht_disposable_settings"
                or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"
                or not lease.is_dir() or lease.name != "data"
                or not lease.parent.name.startswith("polypbase-ht-production-synthetic-")
                or (config["HOST"], config["NAME"], config["USER"], config["PASSWORD"])
                != ("127.0.0.1", "subculture_qa", "subculture_qa", "")):
            raise RuntimeError("Refuse provisioning tests outside the fresh local harness")
        cls.port = int(config["PORT"])
        version, directory, login = cls.execute(
            "SELECT current_setting('server_version_num')::int, current_setting('data_directory'), session_user",
            database="subculture_qa",
        )[0]
        if not 180000 <= version < 190000 or Path(directory).resolve() != lease or login != "subculture_qa":
            raise RuntimeError("PostgreSQL 18 cluster lease mismatch")

    def setUp(self):
        super().setUp()
        roles = (*contract.ROLES, WITNESS)
        if self.execute("SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)", [list(roles)], database="subculture_qa"):
            raise RuntimeError("Provisioning fixture requires absent roles; do not adopt another suite's roles")
        if self.execute("SELECT 1 FROM pg_database WHERE datname=%s", [DATABASE], database="subculture_qa"):
            raise RuntimeError("Provisioning fixture database already exists")
        self.addCleanup(self.cleanup)
        for role in (contract.RUNTIME, WITNESS):
            self.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                 "NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role)), database="subculture_qa")
        self.execute(sql.SQL("CREATE DATABASE {} OWNER polypbase").format(sql.Identifier(DATABASE)),
                     database="subculture_qa")
        self.execute("CREATE TABLE public.provision_probe (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, reading numeric); "
                     "INSERT INTO public.provision_probe(reading) VALUES (0),(NULL); "
                     "CREATE FUNCTION public.provision_probe() RETURNS int LANGUAGE sql AS 'SELECT 1'",
                     user=contract.RUNTIME)

    def cleanup(self):
        # Trusted test lifecycle only; never part of the emitted operator procedure.
        self.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(DATABASE)),
                     database="subculture_qa")
        for role in (*RESERVED, contract.RUNTIME, WITNESS):
            self.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)), database="subculture_qa")

    def catalog_snapshot(self):
        # No auth secrets: pg_roles masks passwords, and the masked field is omitted too.
        queries = {
            "roles": "SELECT to_jsonb(r)-'rolpassword' FROM pg_roles r ORDER BY oid",
            "memberships": "SELECT to_jsonb(m) FROM pg_auth_members m ORDER BY oid",
            "settings": "SELECT to_jsonb(s) FROM pg_db_role_setting s ORDER BY setdatabase,setrole",
            "shared_dependencies": "SELECT to_jsonb(s) FROM pg_shdepend s ORDER BY dbid,classid,objid,objsubid,refclassid,refobjid,deptype",
            "databases": "SELECT oid,datname,datdba,datacl FROM pg_database ORDER BY oid",
            "schemas": "SELECT oid,nspname,nspowner,nspacl FROM pg_namespace ORDER BY oid",
            "relations": "SELECT oid,relname,relowner,relacl FROM pg_class ORDER BY oid",
            "columns": "SELECT attrelid,attnum,attacl FROM pg_attribute WHERE attacl IS NOT NULL ORDER BY attrelid,attnum",
            "functions": "SELECT oid,proname,proowner,proacl FROM pg_proc ORDER BY oid",
            "defaults": "SELECT to_jsonb(d) FROM pg_default_acl d ORDER BY oid",
            "parameters": "SELECT to_jsonb(p) FROM pg_parameter_acl p ORDER BY oid",
            "comments": "SELECT to_jsonb(d) FROM pg_shdescription d ORDER BY classoid,objoid",
            "data": "SELECT * FROM public.provision_probe ORDER BY id",
        }
        with self.connect() as conn:
            return {name: conn.execute(query).fetchall() for name, query in queries.items()}

    def assert_refused_unchanged(self, message):
        before = self.catalog_snapshot()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege) as raised:
            self.execute(contract.provision_sql())
        self.assertIn(message, raised.exception.diag.message_primary)
        self.assertEqual(self.catalog_snapshot(), before)

    def add_collision(self, role):
        self.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEROLE INHERIT CONNECTION LIMIT 3").format(sql.Identifier(role)))
        self.execute(sql.SQL("ALTER ROLE {} SET work_mem='1MB'").format(sql.Identifier(role)))
        self.execute(sql.SQL("GRANT pg_read_all_data TO {}; GRANT {} TO {} WITH ADMIN TRUE, INHERIT TRUE, SET TRUE").format(
            sql.Identifier(role), sql.Identifier(role), sql.Identifier(WITNESS)))
        self.execute(sql.SQL("CREATE SCHEMA collision_evidence AUTHORIZATION {}; "
                             "CREATE TABLE collision_evidence.owned_probe(id int); "
                             "ALTER TABLE collision_evidence.owned_probe OWNER TO {}; "
                             "GRANT SELECT ON public.provision_probe TO {} WITH GRANT OPTION; "
                             "GRANT UPDATE(reading) ON public.provision_probe TO {}").format(
                                 *[sql.Identifier(role)] * 4))
        self.assert_refused_unchanged("Reserved Box 1902 role already exists")

    def test_clean_initial_provisioning_succeeds_and_preserves_scientific_sentinels(self):
        emitted = contract.provision_sql()
        self.assertLess(emitted.index("Reserved Box 1902 role already exists"), emitted.index("CREATE ROLE"))
        self.assertNotIn("IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles", emitted)
        self.assertNotIn("ALTER ROLE box1902_", emitted)
        self.assertNotIn("REVOKE %I FROM %I", emitted)
        self.assertLess(emitted.index("Unexpected runtime schema CREATE privilege"), emitted.index("CREATE ROLE"))
        # Cover both explicit grants and implicit pg_database_owner privileges.
        self.execute("GRANT CREATE ON SCHEMA public TO PUBLIC, polypbase")
        self.assertEqual(self.execute(
            "SELECT has_schema_privilege('polypbase','public','CREATE'), "
            "pg_has_role('polypbase','pg_database_owner','USAGE')"), [(True, True)])
        self.execute(emitted)
        self.assertEqual(self.execute(
            "SELECT has_schema_privilege('polypbase','public','CREATE'), "
            "has_database_privilege('polypbase',current_database(),'CREATE,TEMP'), "
            "pg_has_role('polypbase','pg_database_owner','USAGE'), "
            "EXISTS (SELECT 1 FROM pg_namespace WHERE nspname !~ '^pg_' "
            "AND nspname <> 'information_schema' AND has_schema_privilege('polypbase',oid,'CREATE'))"
        ), [(False, False, False, False)])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.execute("CREATE TABLE public.denied_after_provisioning(id int)", user=contract.RUNTIME)
        with self.connect(contract.EXECUTOR) as conn:
            self.assertTrue(contract.assert_topology(SimpleNamespace(vendor="postgresql", cursor=conn.cursor)))
        self.assertEqual(self.execute("SELECT reading FROM public.provision_probe ORDER BY id"), [(0,), (None,)])
        self.assertEqual(self.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"),
                         [(contract.OWNER,)])

    def test_preexisting_owner_is_rejected_without_mutation(self):
        self.add_collision(contract.OWNER)

    def test_preexisting_executor_is_rejected_without_mutation(self):
        self.add_collision(contract.EXECUTOR)

    def test_preexisting_approver_is_rejected_without_mutation(self):
        self.add_collision(contract.APPROVER)

    def test_preexisting_migrator_is_rejected_before_any_other_role_creation(self):
        self.add_collision(contract.MIGRATOR)

    def test_matching_attributes_and_owner_marker_do_not_establish_identity(self):
        self.execute("CREATE ROLE box1902_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                     "NOINHERIT NOREPLICATION NOBYPASSRLS; "
                     "COMMENT ON ROLE box1902_owner IS 'Polypbase Box 1902 protected owner v2'")
        self.assert_refused_unchanged("Reserved Box 1902 role already exists")

    def test_repeat_of_valid_provisioning_is_rejected_without_changes(self):
        self.execute(contract.provision_sql())
        with self.connect(contract.EXECUTOR) as conn:
            self.assertTrue(contract.assert_topology(SimpleNamespace(vendor="postgresql", cursor=conn.cursor)))
        self.assert_refused_unchanged("Reserved Box 1902 role already exists")

    def test_unexpected_runtime_memberships_are_not_silently_revoked(self):
        self.execute("GRANT pg_read_all_data TO polypbase; GRANT polypbase TO box1902_collision_witness")
        self.assert_refused_unchanged("Unexpected runtime membership")

    def test_unexpected_runtime_attributes_and_privileges_are_not_repaired(self):
        self.execute("ALTER ROLE polypbase CREATEROLE")
        self.assert_refused_unchanged("Expected unprivileged existing application runtime")
        self.execute("ALTER ROLE polypbase NOCREATEROLE; GRANT SET ON PARAMETER session_replication_role TO polypbase")
        try:
            self.assert_refused_unchanged("Unexpected runtime trigger-bypass privilege")
        finally:
            self.execute("REVOKE SET ON PARAMETER session_replication_role FROM polypbase")

    def test_unexpected_initial_ownership_is_rejected_without_transfer(self):
        for alter, restore in (
            ("ALTER TABLE public.provision_probe OWNER TO box1902_collision_witness",
             "ALTER TABLE public.provision_probe OWNER TO polypbase"),
            ("ALTER SCHEMA public OWNER TO box1902_collision_witness",
             "ALTER SCHEMA public OWNER TO pg_database_owner"),
            (f"ALTER DATABASE {DATABASE} OWNER TO box1902_collision_witness",
             f"ALTER DATABASE {DATABASE} OWNER TO polypbase"),
        ):
            with self.subTest(alter=alter):
                self.execute(alter)
                self.assert_refused_unchanged("Unexpected initial database/schema/object ownership")
                self.execute(restore)

    def test_unexpected_direct_schema_create_without_usage_is_rejected_unchanged(self):
        self.execute("CREATE SCHEMA unexpected AUTHORIZATION box1902_collision_witness; "
                     "GRANT CREATE ON SCHEMA unexpected TO polypbase")
        self.assertEqual(self.execute(
            "SELECT has_schema_privilege('polypbase','unexpected','CREATE'), "
            "has_schema_privilege('polypbase','unexpected','USAGE')"), [(True, False)])
        self.assert_refused_unchanged("Unexpected runtime schema CREATE privilege")

    def test_unexpected_public_schema_create_grant_is_rejected_unchanged(self):
        self.execute("CREATE SCHEMA unexpected AUTHORIZATION box1902_collision_witness; "
                     "GRANT CREATE ON SCHEMA unexpected TO PUBLIC")
        self.assertEqual(self.execute("SELECT has_schema_privilege('polypbase','unexpected','CREATE')"), [(True,)])
        self.assert_refused_unchanged("Unexpected runtime schema CREATE privilege")

    def test_unexpected_runtime_owned_schema_is_rejected_unchanged(self):
        self.execute("CREATE SCHEMA unexpected AUTHORIZATION polypbase")
        self.assertEqual(self.execute("SELECT has_schema_privilege('polypbase','unexpected','CREATE')"), [(True,)])
        self.assert_refused_unchanged("Unexpected runtime schema CREATE privilege")

    def test_unexpected_database_owner_schema_is_rejected_unchanged(self):
        self.execute("CREATE SCHEMA unexpected AUTHORIZATION pg_database_owner")
        self.assertEqual(self.execute("SELECT has_schema_privilege('polypbase','unexpected','CREATE')"), [(True,)])
        self.assert_refused_unchanged("Unexpected runtime schema CREATE privilege")

    def test_clean_provisioning_still_prevents_runtime_escalation(self):
        self.execute(contract.provision_sql())
        for statement in (
            "SET ROLE box1902_owner", "SET ROLE box1902_approver", "SET ROLE box1902_executor",
            "SET ROLE box1902_migrator", "GRANT box1902_owner TO polypbase",
            "SET SESSION AUTHORIZATION box1902_owner", "SET session_replication_role=replica",
            "CREATE TABLE public.runtime_forgery(id int)",
            "CREATE OR REPLACE FUNCTION public.provision_probe() RETURNS int LANGUAGE sql AS 'SELECT 2'",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.execute(statement, user=contract.RUNTIME)
        with self.connect(contract.MIGRATOR) as conn:
            conn.execute("SET ROLE box1902_owner")
            self.assertEqual(conn.execute("SELECT session_user,current_user").fetchone(), (contract.MIGRATOR, contract.OWNER))
