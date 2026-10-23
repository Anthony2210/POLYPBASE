"""Execute the real emitted CCA-EOB provisioning SQL on a disposable PostgreSQL 18 cluster.

The cluster is created from scratch in a temporary directory on a free loopback port,
migrated with the real migrations as the table owner `polypbase`, and deleted at the
end. It never touches an existing cluster, production, Neon or a shared database.
Nothing is skipped silently: set POLYPBASE_PG18_BIN to the PostgreSQL 18 `bin`
directory (initdb, postgres, psql) to enable the class; without it the class is
reported as skipped, which is not coverage.

The production preflight (Unix socket, port 5433, login `postgres`) cannot hold on a
TCP-only disposable cluster, so it is executed for real as a refusal test and the
remaining statements are executed through `body()`, which is exactly what `emit()`
appends after that preflight.
"""

import importlib.util
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from config import cca_eob_database_role as contract

BIN = os.environ.get("POLYPBASE_PG18_BIN", "")
ROOT = Path(__file__).resolve().parents[3]
ROLE = contract.ROLE


def _emitter():
    spec = importlib.util.spec_from_file_location("cca_eob_role_sql_executed", ROOT / "deploy" / "scripts" / "cca_eob_role_sql.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exe(name):
    return str(Path(BIN) / (name + (".exe" if os.name == "nt" else "")))


def _invalid_releases(release):
    """Malformed metadata only; actual archive bytes are the root integration's job."""
    for key in release:
        yield f"missing release {key}", {k: v for k, v in release.items() if k != key}
    for change in ({"extra": True}, {"actor_id": True}, {"restoration_verified": 1},
                   {"plan_hash": "b" * 64}, {"target": {}}, {"restore_report": " "}, {"review_ref": ""}):
        yield f"release {change}", {**release, **change}
    for field in ("restore_report", "review_ref"):
        for whitespace in ("\v", "\u00a0"):
            yield f"{field} whitespace={whitespace!r}", {**release, field: whitespace}
    for archive in (None, [], {}, {**release["archive"], "extra": True}):
        yield f"archive {archive}", {**release, "archive": archive}
    for key in release["archive"]:
        yield f"missing archive {key}", {
            **release, "archive": {k: v for k, v in release["archive"].items() if k != key},
        }
    invalid = {
        "path": (None, 1, "", "relative.dump", "C:\\backup\\qa.dump", "/qa\\dump", "/", "/qa/",
                 "//qa/dump", "/qa//dump", "/qa/./dump", "/qa/../dump", "/qa/dump\n", "/qa/\x01dump",
                 "/qa/dump\t", "/qa/dump\r", "/qa/\x7fdump"),
        "sha256": (None, 1, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64),
        "size_bytes": (None, False, True, 0, -1, 1.5, 123.0, "123", 9223372036854775808),
    }
    for key, values in invalid.items():
        for value in values:
            yield f"archive {key}={value!r}", {**release, "archive": {**release["archive"], key: value}}


class ApprovalEmitterContractTests(SimpleTestCase):
    """Offline emitter checks also run when the disposable PG18 cluster is unavailable."""

    def setUp(self):
        self.emitter = _emitter()

    def test_both_import_emitters_reject_malformed_release_metadata(self):
        document, reviewed_hash, release, expiry = ProvisioningSqlExecutionTests.synthetic_approval()
        final_expiry = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        for label, malformed in _invalid_releases(release):
            for action, deadline in (("approval_sql", expiry), ("final_authorization_sql", final_expiry)):
                with self.subTest(label=label, action=action), self.assertRaises(ValueError):
                    getattr(self.emitter, action)(document, reviewed_hash, malformed, deadline)

    def test_missing_or_ambiguous_thermal_state_requires_authenticated_evidence(self):
        document, _, release, expiry = ProvisioningSqlExecutionTests.synthetic_approval()
        self.assertIs(document["plan"]["database_snapshot"]["historical_readings_exist"], False)
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        for snapshot in ({}, {"historical_readings_exist": True},
                         {"historical_readings_exist": None}, {"historical_readings_exist": 0}):
            changed = deepcopy(document)
            changed["plan"]["database_snapshot"] = snapshot
            reviewed_hash = hashlib.sha256(json.dumps(changed, sort_keys=True, default=str).encode()).hexdigest()
            evidence = {**release, "plan_hash": reviewed_hash}
            for action, expires in (("approval_sql", expiry), ("final_authorization_sql", deadline)):
                with self.subTest(snapshot=snapshot, action=action), self.assertRaisesMessage(
                        ValueError, "A protected authenticated original 72-move plan is required."):
                    getattr(self.emitter, action)(changed, reviewed_hash, evidence, expires)

    def test_final_emitter_requires_complete_reviewed_document_and_explicit_future_expiry(self):
        document, reviewed_hash, release, _ = ProvisioningSqlExecutionTests.synthetic_approval()
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        for fingerprint in ("b" * 64, reviewed_hash.upper(), "not-a-hash"):
            with self.subTest(hash=fingerprint), self.assertRaises(ValueError):
                self.emitter.final_authorization_sql(document, fingerprint, release, deadline)
        changed = deepcopy(document)
        changed["plan"]["write_recipe"] = {"different": True}
        with self.assertRaises(ValueError):
            self.emitter.final_authorization_sql(changed, reviewed_hash, release, deadline)
        for expiry in ("", "2020-01-01T00:00:00Z", "2099-01-01T00:00:00", "not-a-date"):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                self.emitter.final_authorization_sql(document, reviewed_hash, release, expiry)

    def test_valid_archive_metadata_does_not_require_a_local_file_and_accepts_bigint_max(self):
        document, reviewed_hash, release, expiry = ProvisioningSqlExecutionTests.synthetic_approval()
        release["archive"]["size_bytes"] = 9223372036854775807
        self.assertEqual(set(release), {"schema", "target", "plan_hash", "actor_id", "archive",
                                        "restoration_verified", "restore_report", "review_ref"})
        for action, deadline in (("approval_sql", expiry), ("final_authorization_sql",
                                 (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat())):
            with self.subTest(action=action):
                emitted = getattr(self.emitter, action)(document, reviewed_hash, release, deadline)
                self.assertTrue(emitted.startswith(self.emitter.PREFLIGHT))

    def test_release_reference_whitespace_matches_python_strip_and_accepts_letter_v(self):
        expected = "".join(chr(code) for code in (
            *range(9, 14), *range(28, 33), 133, 160, 5760, *range(8192, 8203),
            8232, 8233, 8239, 8287, 12288,
        ))
        self.assertEqual(contract.RELEASE_WHITESPACE, expected)
        document, reviewed_hash, release, expiry = ProvisioningSqlExecutionTests.synthetic_approval()
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        for field in ("restore_report", "review_ref"):
            for text in ("v", "\v", "\u00a0", expected):
                evidence = {**release, field: text}
                for action, expires in (("approval_sql", expiry), ("final_authorization_sql", deadline)):
                    with self.subTest(field=field, text=repr(text), action=action):
                        if text == "v":
                            contract.validate_release(evidence, document, reviewed_hash)
                            self.assertTrue(getattr(self.emitter, action)(
                                document, reviewed_hash, evidence, expires).startswith(self.emitter.PREFLIGHT))
                        else:
                            with self.assertRaises(ValueError):
                                contract.validate_release(evidence, document, reviewed_hash)
                            with self.assertRaises(ValueError):
                                getattr(self.emitter, action)(document, reviewed_hash, evidence, expires)

    def test_authorize_apply_cli_emits_the_same_independent_sql(self):
        document, reviewed_hash, release, _ = ProvisioningSqlExecutionTests.synthetic_approval()
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        with tempfile.TemporaryDirectory(prefix="cca_eob_offline_emitter_") as directory:
            document_path = Path(directory) / "document.json"
            release_path = Path(directory) / "release.json"
            document_path.write_text(json.dumps(document), encoding="utf-8")
            release_path.write_text(json.dumps(release), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "deploy" / "scripts" / "cca_eob_role_sql.py"),
                 "authorize-apply", "--document", str(document_path), "--release", str(release_path),
                 "--reviewed-hash", reviewed_hash, "--expires-at", deadline],
                capture_output=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(result.stdout.decode("utf-8"),
                         self.emitter.final_authorization_sql(document, reviewed_hash, release, deadline))


@skipUnless(BIN, "Set POLYPBASE_PG18_BIN to run the executable provisioning proofs on a disposable PostgreSQL 18")
class ProvisioningSqlExecutionTests(SimpleTestCase):
    emitter = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.emitter = _emitter()
        cls.tmp = tempfile.mkdtemp(prefix="cca_eob_pg18_")
        cls.server = None
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                cls.port = probe.getsockname()[1]
            data = os.path.join(cls.tmp, "data")
            subprocess.run([_exe("initdb"), "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--locale=C"],
                           check=True, capture_output=True, timeout=300)
            options = ["-p", str(cls.port), "-c", "listen_addresses=127.0.0.1", "-c", "fsync=off"]
            if os.name != "nt":
                options += ["-c", f"unix_socket_directories={cls.tmp}"]
            cls.log = open(os.path.join(cls.tmp, "server.log"), "wb")
            cls.server = subprocess.Popen([_exe("postgres"), "-D", data, *options], stdout=cls.log, stderr=cls.log)
            cls._wait_ready()
            cls.sql("CREATE ROLE polypbase LOGIN; CREATE DATABASE polypbase OWNER polypbase;", db="postgres").check_returncode()
            cls.sql("ALTER SCHEMA public OWNER TO polypbase; REVOKE ALL ON DATABASE polypbase FROM PUBLIC;").check_returncode()
            environment = {
                **{k: v for k, v in os.environ.items() if not k.upper().startswith(("PG", "POSTGRES_"))},
                "PYTHON_DOTENV_DISABLED": "1", "DJANGO_DEBUG": "1", "DJANGO_SECRET_KEY": "disposable-provisioning-test",
                "POSTGRES_DB": "polypbase", "POSTGRES_USER": "polypbase", "POSTGRES_PASSWORD": "",
                "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": str(cls.port), "POSTGRES_SSLMODE": "disable",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            cls.environment = environment
            subprocess.run([sys.executable, "-B", str(ROOT / "backend" / "manage.py"), "migrate", "--noinput",
                            "--settings=config.settings"], check=True, capture_output=True, timeout=900, env=environment,
                           cwd=str(ROOT / "backend"))
        except BaseException:
            cls._shutdown()
            raise

    @classmethod
    def _wait_ready(cls):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError("Disposable PostgreSQL exited early")
            if cls.sql("SELECT 1", db="postgres").returncode == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("Disposable PostgreSQL did not become ready")

    @classmethod
    def _shutdown(cls):
        if cls.server is not None and cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=60)
            except subprocess.TimeoutExpired:
                cls.server.kill()
        if getattr(cls, "log", None):
            cls.log.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def tearDownClass(cls):
        cls._shutdown()
        super().tearDownClass()

    @classmethod
    def sql(cls, text, *, user="postgres", db="polypbase"):
        return subprocess.run(
            [_exe("psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p", str(cls.port),
             "-U", user, "-d", db, "-At"],
            input=text, capture_output=True, text=True, timeout=120,
            env={**{k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}, "PGCONNECT_TIMEOUT": "10"},
        )

    def setUp(self):
        super().setUp()
        self.reset_cluster_state()
        self.addCleanup(self.reset_cluster_state)

    def reset_cluster_state(self):
        """Unconditional test cleanup (not one of the reviewed artifacts)."""
        result = self.sql(f"""
DROP SCHEMA IF EXISTS extra_qa CASCADE;
SELECT lo_unlink(oid) FROM pg_largeobject_metadata WHERE oid = 424242;
DROP FUNCTION IF EXISTS {contract.WRITER};
DROP FUNCTION IF EXISTS {contract.READER};
DROP FUNCTION IF EXISTS {contract.FUNCTION};
DROP TABLE IF EXISTS {contract.APPROVAL};
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
        EXECUTE 'DROP OWNED BY {ROLE}'; EXECUTE 'DROP ROLE {ROLE}'; END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'other_login') THEN
        EXECUTE 'DROP OWNED BY other_login'; EXECUTE 'DROP ROLE other_login'; END IF;
END $$;""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_action(self, action):
        return self.sql(self.emitter.body(action))

    def provision(self):
        result = self.run_action("provision")
        self.assertEqual(result.returncode, 0, result.stderr)

    def assertStops(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def value(self, query):
        result = self.sql(query)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def transportless(self, emitted):
        """Remove only the exact transport preflight, retaining administrator guards."""
        self.assertTrue(emitted.startswith(self.emitter.PREFLIGHT))
        return emitted[len(self.emitter.PREFLIGHT):]

    def approval_row(self):
        return self.value(f"SELECT to_jsonb(a) FROM {contract.APPROVAL} a")

    def assertWriterRefusesBeforeTarget(self, reviewed_hash, *, prefix=""):
        result = self.sql(prefix + f"SELECT public.cca_eob_apply_v1('{reviewed_hash}')", user=ROLE)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("live CCA-EOB target differs", result.stderr)
        self.assertNotIn("cca_eob_lock_v1", result.stderr)
        self.assertNotIn("cca_eob_read_v1", result.stderr)
        return result

    def import_plan(self, document, reviewed_hash, release, expiry):
        emitted = self.emitter.approval_sql(document, reviewed_hash, release, expiry)
        result = self.sql(self.transportless(emitted))
        self.assertEqual(result.returncode, 0, result.stderr)

    def install_raw_final(self, document, reviewed_hash, release):
        """Administrator-only fixture, bypassing the emitter to challenge the writer."""
        final = {"schema": "cca_eob_final_apply_v1", "plan_hash": reviewed_hash,
                 "document": document, "release": release}
        result = self.sql(f"UPDATE {contract.APPROVAL} SET "
                          f"final_authorization={self.emitter.literal(json.dumps(final))}::jsonb,"
                          "final_authorized_at=clock_timestamp(),"
                          "final_expires_at=clock_timestamp()+interval '60 seconds'")
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- the real preflight ---------------------------------------------------------

    def test_real_preflight_refuses_a_tcp_cluster_without_creating_anything(self):
        for action in ("provision", "verify", "rollback"):
            with self.subTest(action=action):
                self.assertStops(self.sql(self.emitter.emit(action)), "STOP: expected local primary PostgreSQL 18")
        self.assertEqual(self.value("SELECT count(*) FROM pg_roles WHERE rolname = '%s'" % ROLE), "0")
        self.assertEqual(self.emitter.emit("provision"), self.emitter.PREFLIGHT + self.emitter.body("provision"))

    # --- provision, verify and the dedicated login ----------------------------------

    def test_provision_verify_and_idempotent_reprovision(self):
        self.provision()
        self.assertEqual(self.run_action("verify").returncode, 0)
        self.provision()
        self.assertEqual(self.run_action("verify").returncode, 0)
        self.assertEqual(self.value(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit, rolconnlimit"
            " FROM pg_roles WHERE rolname = '%s'" % ROLE), "t|f|f|f|f|f|f|1")
        self.assertEqual(self.value("SELECT count(*) FROM pg_auth_members WHERE member = '%s'::regrole OR roleid = '%s'::regrole" % (ROLE, ROLE)), "0")
        self.assertEqual(self.value("SELECT count(*) FROM pg_shdepend WHERE refclassid = 'pg_authid'::regclass AND refobjid = '%s'::regrole AND deptype = 'o'" % ROLE), "0")
        self.assertEqual(self.value(
            "SELECT pg_get_userbyid(proowner), prosecdef, proconfig FROM pg_proc WHERE oid = to_regprocedure('%s')" % contract.FUNCTION),
            'polypbase|t|{"search_path=pg_catalog, pg_temp"}')
        self.assertEqual(self.value("SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = '%s'" % ROLE),
                         self.emitter.MARKER)

    def test_session_user_guard_and_least_privilege_are_enforced_by_the_real_role(self):
        self.provision()
        call = "SELECT public.cca_eob_lock_v1(%d)"
        # The table owner is refused by the session_user literal even with a plausible actor.
        self.assertStops(self.sql(call % 1, user="polypbase"), "Dedicated CCA-EOB login and explicit actor required")
        # The dedicated login passes that guard and stops at the fixed box identities
        # (the disposable schema is empty); a missing actor stops at the first guard.
        self.assertStops(self.sql(call % 1, user=ROLE), "CCA-EOB Box identities changed")
        self.assertStops(self.sql(call % 0, user=ROLE), "Dedicated CCA-EOB login and explicit actor required")
        # No other login can execute the helper at all.
        self.sql("CREATE ROLE other_login LOGIN; GRANT CONNECT ON DATABASE polypbase TO other_login").check_returncode()
        self.assertStops(self.sql(call % 1, user="other_login"), "permission denied for function cca_eob_lock_v1")
        denied = (
            "UPDATE auth_user SET is_active = is_active WHERE false",
            "UPDATE accounts_organizationmembership SET role = role WHERE false",
            "UPDATE cultures_box SET notes = notes WHERE false",
            "SELECT id FROM auth_user FOR UPDATE",
            "SELECT password FROM auth_user",
            "BEGIN; LOCK TABLE measurements_biologicalmeasurement IN ROW EXCLUSIVE MODE; ROLLBACK",
            "DELETE FROM audit_auditlog WHERE false",
            "CREATE TABLE public.cca_should_not_exist (id integer)",
            "SELECT * FROM measurements_historicaltemperaturereading",
            "SELECT temperature_c FROM measurements_historicaltemperaturereading",
            "SELECT id FROM measurements_historicaltemperaturereading",
            "SELECT iso_year FROM measurements_historicaltemperaturereading",
            "SELECT biological_measurement_id, association_status FROM measurements_historicaltemperaturereading",
            "SELECT 1 FROM measurements_historicaltemperaturereading LIMIT 1",
            "SELECT id, is_active FROM auth_user",
            "SELECT * FROM cultures_box",
            "SELECT * FROM measurements_biologicalmeasurement",
            "SELECT id FROM django_migrations",
            "UPDATE cultures_box SET polyp_state_revision = polyp_state_revision WHERE false",
            "UPDATE measurements_biologicalmeasurement SET box_id = box_id WHERE false",
            "INSERT INTO audit_auditlog (organization_id) VALUES (1)",
        )
        for statement in denied:
            with self.subTest(statement=statement):
                self.assertStops(self.sql(statement, user=ROLE), "permission denied")
        allowed = (
            "SELECT app, name FROM django_migrations",
        )
        for statement in allowed:
            with self.subTest(statement=statement):
                result = self.sql(statement, user=ROLE)
                self.assertEqual(result.returncode, 0, result.stderr)

    def cleanup_business_records(self):
        """Bypass immutable guards only for trusted cleanup in this disposable cluster."""
        result = self.sql("""
BEGIN;
SET LOCAL session_replication_role = replica;
DELETE FROM audit_auditlog WHERE user_id IN (1,2);
DELETE FROM measurements_biologicalmeasurement WHERE id IN (1835,1836,1837,2835);
DELETE FROM cultures_boxlocation WHERE box_id IN (1835,1836,1837,2835);
DELETE FROM cultures_box WHERE id IN (1835,1836,1837,2835);
DELETE FROM cultures_thermalzone WHERE organization_id IN (1,2) AND name IN ('QA zone 1','QA zone 2');
DELETE FROM taxonomy_strain WHERE organization_id IN (1,2) AND code IN ('CCA-EOB-2','CCA-EOB-3');
DELETE FROM taxonomy_species WHERE scientific_name = 'Synthetic CCA QA';
DELETE FROM accounts_organizationmembership WHERE organization_id IN (1,2) AND user_id IN (1,2);
DELETE FROM auth_user WHERE id IN (1,2) AND username IN ('qa-admin-1','qa-admin-2');
DELETE FROM organizations_organization WHERE id IN (1,2) AND name IN ('QA institution 1','QA institution 2');
SET LOCAL session_replication_role = origin;
SELECT current_setting('session_replication_role');
COMMIT;
""", user="postgres")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "origin")

    def seed_business_records(self):
        """Use migrated models in a subprocess connected only to this private cluster."""
        self.addCleanup(self.cleanup_business_records)
        script = """
from datetime import date
from django.contrib.auth import get_user_model
from apps.accounts.models import OrganizationMembership
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain
from apps.cultures.models import Box, BoxLocation, ThermalZone
from apps.measurements.models import BiologicalMeasurement
from apps.audit.models import AuditLog
species = Species.objects.create(scientific_name='Synthetic CCA QA')
for org_id in (1, 2):
    org, _ = Organization.objects.get_or_create(pk=org_id, defaults={'name': f'QA institution {org_id}'})
    actor = get_user_model().objects.create_user(pk=org_id, username=f'qa-admin-{org_id}',
        email=f'qa-admin-{org_id}@example.org')
    OrganizationMembership.objects.create(organization=org, user=actor, role='admin')
    strain = Strain.objects.create(organization=org, species=species, code=f'CCA-EOB-{org_id + 1}')
    zone = ThermalZone.objects.create(organization=org, name=f'QA zone {org_id}')
    for pk, number in ((1835, 4), (1836, 5), (1837, 6)) if org_id == 1 else ((2835, 4),):
        box = Box.objects.create(pk=pk, organization=org, strain=strain, thermal_zone=zone,
            global_code=f'{strain.code}.{number:03}', box_number=f'{number:03}', notes=f'private-org-{org_id}')
        BoxLocation.objects.create(box=box, thermal_zone=zone)
        BiologicalMeasurement.objects.create(pk=pk, box=box, measured_on=date(2026, 8, 3),
            polyp_count=0, ephyrae_count=0, strobila_count=None, notes=f'private-org-{org_id}')
    AuditLog.objects.create(organization=org, user=actor, action='update', object_type='box',
        object_id=str(org_id), description=f'private-org-{org_id}', metadata={})
"""
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "backend" / "manage.py"), "shell",
             "--settings=config.settings", "-c", script],
            capture_output=True, text=True, timeout=120, env=self.environment, cwd=str(ROOT / "backend"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def business_snapshot(self):
        return self.value("SELECT jsonb_build_object(" + ",".join(
            f"'{table}',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb) FROM {table} t)"
            for table in ("cultures_box", "measurements_biologicalmeasurement", "audit_auditlog",
                          "auth_user", "accounts_organizationmembership")
        ) + ")")

    def test_reader_writer_acl_and_private_trusted_approval_table(self):
        self.provision()
        for signature, _, result_type, _ in contract.helper_specs():
            with self.subTest(signature=signature):
                self.assertEqual(self.value(
                    "SELECT pg_get_userbyid(proowner),prosecdef,prorettype::regtype::text "
                    f"FROM pg_proc WHERE oid=to_regprocedure('{signature}')"), f"polypbase|t|{result_type}")
                self.assertEqual(self.value(
                    f"SELECT has_function_privilege('{ROLE}','{signature}','EXECUTE'),"
                    f" EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a "
                    f"WHERE p.oid=to_regprocedure('{signature}') AND a.grantee=0)"), "t|f")
        self.assertEqual(self.value(
            f"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='{contract.APPROVAL}'::regclass"), "postgres")
        self.assertIn("trusted approval v2", contract.APPROVAL_MARKER)
        self.assertEqual(self.value(
            "SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),attnotnull) ORDER BY attnum) "
            f"FROM pg_attribute WHERE attrelid='{contract.APPROVAL}'::regclass AND attnum>6 AND NOT attisdropped"),
            '[["final_authorization", "jsonb", false], ["final_authorized_at", "timestamp with time zone", false], '
            '["final_expires_at", "timestamp with time zone", false]]')
        self.assertEqual(self.value(f"SELECT has_table_privilege('polypbase','{contract.APPROVAL}','SELECT'),"
                                   f"has_column_privilege('polypbase','{contract.APPROVAL}','consumed_at','UPDATE')"), "t|t")
        self.sql("CREATE ROLE other_login LOGIN; GRANT CONNECT ON DATABASE polypbase TO other_login").check_returncode()
        for user in (ROLE, "other_login"):
            for statement in (
                f"SELECT * FROM {contract.APPROVAL}",
                f"INSERT INTO {contract.APPROVAL} (singleton,plan_hash,document,release,expires_at,consumed_at) "
                "VALUES (true,'forged','{}','{}',now(),NULL)",
                f"UPDATE {contract.APPROVAL} SET document='{{}}' WHERE false",
                f"DELETE FROM {contract.APPROVAL} WHERE false",
                f"TRUNCATE {contract.APPROVAL}",
            ):
                with self.subTest(user=user, statement=statement):
                    self.assertStops(self.sql(statement, user=user), "permission denied")
        for statement in (
            f"UPDATE {contract.APPROVAL} SET plan_hash='forged' WHERE false",
            f"UPDATE {contract.APPROVAL} SET document='{{}}' WHERE false",
            f"INSERT INTO {contract.APPROVAL} (singleton,plan_hash,document,release,expires_at,consumed_at) "
            "VALUES (true,'forged','{}','{}',now(),NULL)",
            f"DELETE FROM {contract.APPROVAL} WHERE false",
        ):
            self.assertStops(self.sql(statement, user="polypbase"), "permission denied")
        for user in (ROLE, "polypbase", "other_login"):
            for column, expression in (("final_authorization", "'{}'::jsonb"),
                                       ("final_authorized_at", "clock_timestamp()"),
                                       ("final_expires_at", "clock_timestamp()+interval '5 minutes'")):
                with self.subTest(user=user, column=column):
                    self.assertStops(self.sql(f"UPDATE {contract.APPROVAL} SET {column}={expression} WHERE false",
                                              user=user), "permission denied")
        for signature, argument in ((contract.READER, "1"), (contract.WRITER, "'" + "a" * 64 + "'")):
            call = f"SELECT {signature.split('(')[0]}({argument})"
            self.assertStops(self.sql(call, user="other_login"), "permission denied for function")
            self.assertStops(self.sql(call, user="polypbase"), "Dedicated CCA-EOB login")

    def test_reader_is_fixed_to_organization_one_with_real_other_organization_records(self):
        self.provision()
        self.seed_business_records()
        before = self.business_snapshot()
        result = self.sql("SELECT public.cca_eob_read_v1(1)", user=ROLE)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = json.loads(result.stdout)
        self.assertEqual([b["id"] for b in snapshot["boxes"]], [1835, 1836, 1837])
        self.assertEqual([m["id"] for m in snapshot["measurements"]], [1835, 1836, 1837])
        self.assertTrue(all(b["organization_id"] == 1 for b in snapshot["boxes"]))
        self.assertTrue(all(z["organization_id"] == 1 for z in snapshot["zones"]))
        self.assertTrue(all(m["polyp_count"] == 0 and m["strobila_count"] is None for m in snapshot["measurements"]))
        self.assertNotIn("private-org-2", result.stdout)
        for actor in ("NULL", "0", "2"):
            self.assertStops(self.sql(f"SELECT public.cca_eob_read_v1({actor})", user=ROLE),
                             "active organization 1 admin required")
        for statement in (
            "UPDATE cultures_box SET polyp_state_revision=999 WHERE id IN (1835,2835)",
            "UPDATE measurements_biologicalmeasurement SET box_id=1836 WHERE id=2835",
            "SELECT * FROM audit_auditlog WHERE organization_id=2",
            "DELETE FROM measurements_biologicalmeasurement WHERE id=2835",
            "INSERT INTO cultures_box (id) VALUES (9999)",
        ):
            self.assertStops(self.sql(statement, user=ROLE), "permission denied")
        self.assertEqual(self.business_snapshot(), before)

    def test_direct_helpers_cannot_mutate_without_a_trusted_bound_approval(self):
        self.provision()
        self.seed_business_records()
        before = self.business_snapshot()
        valid_hash = "a" * 64
        for argument in ("NULL", "''", "'not-a-hash'", "'" + "A" * 64 + "'", f"'{valid_hash}'"):
            with self.subTest(argument=argument):
                result = self.sql(
                    "SET cca_eob.actor_id='1'; SET app.actor_id='1'; "
                    "SET cca_eob.plan_hash='" + valid_hash + "'; "
                    f"SELECT public.cca_eob_apply_v1({argument})", user=ROLE)
                self.assertStops(result, "query returned no rows" if argument == f"'{valid_hash}'"
                                 else "reviewed hash required")
                self.assertEqual(self.business_snapshot(), before)
        # Lock/read are callable capabilities, not mutation authority, even in one transaction.
        result = self.sql("BEGIN; SELECT public.cca_eob_lock_v1(1); SELECT public.cca_eob_read_v1(1); COMMIT", user=ROLE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.business_snapshot(), before)
        # An administrator-only malformed row must not become writable via caller GUCs.
        self.sql(f"INSERT INTO {contract.APPROVAL} (singleton,plan_hash,document,release,expires_at,consumed_at) "
                 f"VALUES (true,'{valid_hash}',"
                 "'{\"actor_id\":2}','{}',now()+interval '1 hour',NULL)").check_returncode()
        self.assertWriterRefusesBeforeTarget(valid_hash,
            prefix="SET cca_eob.actor_id='1'; SET app.actor_id='1'; ")
        self.assertStops(self.sql(f"SELECT public.cca_eob_apply_v1('{'b' * 64}')", user=ROLE), "query returned no rows")
        self.assertEqual(self.business_snapshot(), before)
        self.assertEqual(self.value(f"SELECT consumed_at IS NULL FROM {contract.APPROVAL}"), "t")
        self.sql(f"UPDATE {contract.APPROVAL} SET document='{{\"actor_id\":1}}'").check_returncode()
        self.assertWriterRefusesBeforeTarget(valid_hash)
        for change in ("expires_at=now()-interval '1 second'", "consumed_at=now()"):
            self.sql(f"UPDATE {contract.APPROVAL} SET {change}").check_returncode()
            self.assertStops(self.sql(f"SELECT public.cca_eob_apply_v1('{valid_hash}')", user=ROLE),
                             "query returned no rows")
            self.assertEqual(self.business_snapshot(), before)
            self.sql(f"UPDATE {contract.APPROVAL} SET consumed_at=NULL, expires_at=now()+interval '1 hour'").check_returncode()

    @staticmethod
    def synthetic_approval():
        """Non-thermal emitter evidence with an intentionally incomplete synthetic target."""
        document = {
            "schema": "cca_eob_production_plan_v2", "actor_id": 1, "target": {"synthetic": True},
            "plan": {"database_snapshot": {"historical_readings_exist": False}, "write_recipe": {}, "correction": {
                "operation": "cca_eob_v2_20261016_v3", "organization_id": 1,
                "v2_sha256": contract.V2_SHA256, "ready": True, "blockers": [],
            }},
        }
        reviewed_hash = hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()
        release = {
            "schema": "cca_eob_release_v1", "target": document["target"], "plan_hash": reviewed_hash,
            "actor_id": 1, "archive": {"path": "/synthetic/qa.dump", "sha256": "a" * 64, "size_bytes": 123},
            "restoration_verified": True, "restore_report": "synthetic QA restore",
            "review_ref": "synthetic independent review",
        }
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        return document, reviewed_hash, release, expiry

    def test_administrator_import_requires_exact_document_hash_and_bound_release(self):
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        approved_sql = self.emitter.approval_sql(document, reviewed_hash, release, expiry)
        self.assertTrue(approved_sql.startswith(self.emitter.PREFLIGHT))
        self.assertIn(f"INSERT INTO {contract.APPROVAL}", approved_sql)
        for forged_hash in ("b" * 64, reviewed_hash.upper(), "not-a-hash"):
            with self.subTest(hash=forged_hash), self.assertRaises(ValueError):
                self.emitter.approval_sql(document, forged_hash, release, expiry)
        forged_document = deepcopy(document)
        forged_document["actor_id"] = 2
        with self.assertRaises(ValueError):
            self.emitter.approval_sql(forged_document, reviewed_hash, release, expiry)
        for change in ({"actor_id": 2}, {"plan_hash": "b" * 64}, {"target": {"synthetic": False}},
                       {"restoration_verified": False}, {"review_ref": ""}, {"restore_report": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.emitter.approval_sql(document, reviewed_hash, {**release, **change}, expiry)
        for bad_expiry in ("2020-01-01T00:00:00Z", "2099-01-01T00:00:00"):
            with self.subTest(expiry=bad_expiry), self.assertRaises(ValueError):
                self.emitter.approval_sql(document, reviewed_hash, release, bad_expiry)

    def test_emitted_approval_import_inserts_once_and_never_refreshes_consumed_evidence(self):
        self.provision()
        self.seed_business_records()
        before = self.business_snapshot()
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        emitted = self.emitter.approval_sql(document, reviewed_hash, release, expiry)
        self.assertTrue(emitted.startswith(self.emitter.PREFLIGHT))
        self.assertStops(self.sql(emitted), "STOP: expected local primary PostgreSQL 18")
        self.assertEqual(self.value(f"SELECT count(*) FROM {contract.APPROVAL}"), "0")
        # Only the exact production transport preflight is excluded on this TCP cluster.
        body = emitted[len(self.emitter.PREFLIGHT):]
        for user in (ROLE, "polypbase"):
            with self.subTest(user=user):
                refused = self.sql(body, user=user)
                self.assertNotEqual(refused.returncode, 0, refused.stdout)
                self.assertIn("postgres", refused.stderr)
                self.assertEqual(self.value(f"SELECT count(*) FROM {contract.APPROVAL}"), "0")
        result = self.sql(body, user="postgres")
        self.assertEqual(result.returncode, 0, result.stderr)
        approval_query = f"SELECT to_jsonb(a) FROM {contract.APPROVAL} a"
        imported = self.value(approval_query)
        row = json.loads(imported)
        self.assertEqual(row["singleton"], True)
        self.assertEqual(row["plan_hash"], reviewed_hash)
        self.assertEqual(row["document"], document)
        self.assertEqual(row["release"], release)
        self.assertEqual(datetime.fromisoformat(row["expires_at"]), datetime.fromisoformat(expiry))
        self.assertIsNone(row["consumed_at"])
        for column in ("final_authorization", "final_authorized_at", "final_expires_at"):
            self.assertIsNone(row[column])
        self.assertStops(self.sql(body), "duplicate key value violates unique constraint")
        self.assertEqual(self.value(approval_query), imported)
        self.provision()
        self.assertEqual(self.value(approval_query), imported)
        self.assertWriterRefusesBeforeTarget(reviewed_hash)
        self.assertEqual(self.business_snapshot(), before)
        self.assertEqual(self.value(approval_query), imported)
        # Simulate consumption as the trusted administrator, not through a bypassed writer.
        self.sql(f"UPDATE {contract.APPROVAL} SET consumed_at=clock_timestamp()", user="postgres").check_returncode()
        consumed = self.value(approval_query)
        self.assertIsNotNone(json.loads(consumed)["consumed_at"])
        later_expiry = (datetime.fromisoformat(expiry) + timedelta(hours=1)).isoformat()
        refreshed = self.emitter.approval_sql(document, reviewed_hash, release, later_expiry)
        self.assertTrue(refreshed.startswith(self.emitter.PREFLIGHT))
        self.assertStops(self.sql(refreshed[len(self.emitter.PREFLIGHT):]),
                         "duplicate key value violates unique constraint")
        self.assertEqual(self.value(approval_query), consumed)
        self.provision()
        self.assertEqual(self.value(approval_query), consumed)
        self.assertStops(self.sql(f"SELECT public.cca_eob_apply_v1('{reviewed_hash}')", user=ROLE),
                         "query returned no rows")
        self.assertEqual(self.business_snapshot(), before)
        self.assertEqual(self.value(approval_query), consumed)

    def test_final_authorization_is_independent_postgres_only_and_installed_once(self):
        self.provision()
        self.seed_business_records()
        before = self.business_snapshot()
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        self.import_plan(document, reviewed_hash, release, expiry)
        plan_only = self.approval_row()
        self.assertWriterRefusesBeforeTarget(reviewed_hash,
            prefix=f"SET cca_eob.actor_id='1'; SET app.actor_id='1'; SET cca_eob.plan_hash='{reviewed_hash}'; "
                   "SET cca_eob.final_authorized='true'; SET cca_eob.authorization='APPLY'; ")
        self.assertEqual(self.approval_row(), plan_only)
        final_expiry = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        emitted = self.emitter.final_authorization_sql(document, reviewed_hash, release, final_expiry)
        body = self.transportless(emitted)
        # Verification contains the writer's quoted source, including its audit
        # INSERT. Inspect the administrative mutation block, not that inert text.
        authorization_block = body.split('DO $authorize_apply$', 1)[1].split('$authorize_apply$;', 1)[0]
        self.assertNotIn("INSERT INTO", authorization_block.upper())
        self.assertNotIn("ON CONFLICT", body.upper())
        self.assertStops(self.sql(emitted), "STOP: expected local primary PostgreSQL 18")
        # The transport bypass must not bypass the independent session/current-user guard.
        for user in (ROLE, "polypbase"):
            with self.subTest(user=user):
                result = self.sql(body, user=user)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("postgres", result.stderr)
                self.assertEqual(self.approval_row(), plan_only)
        result = self.sql(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        authorized = self.approval_row()
        row = json.loads(authorized)
        self.assertEqual(row["final_authorization"], {
            "schema": "cca_eob_final_apply_v1", "plan_hash": reviewed_hash,
            "document": document, "release": release,
        })
        installed = datetime.fromisoformat(row["final_authorized_at"])
        deadline = datetime.fromisoformat(row["final_expires_at"])
        self.assertGreater(deadline, installed)
        self.assertLessEqual((deadline - installed).total_seconds(), 300)
        self.assertEqual(deadline, datetime.fromisoformat(final_expiry))
        self.assertLessEqual(deadline, datetime.fromisoformat(row["expires_at"]))
        for key in ("singleton", "plan_hash", "document", "release", "expires_at", "consumed_at"):
            self.assertEqual(row[key], json.loads(plan_only)[key])
        self.assertNotEqual(self.sql(body).returncode, 0)
        later = (datetime.now(timezone.utc) + timedelta(seconds=240)).isoformat()
        extension = self.transportless(self.emitter.final_authorization_sql(document, reviewed_hash, release, later))
        self.assertNotEqual(self.sql(extension).returncode, 0)
        self.assertEqual(self.approval_row(), authorized)
        self.provision()
        self.assertEqual(self.approval_row(), authorized)
        # Final authority is necessary, not sufficient: this synthetic target is never live.
        self.assertStops(self.sql(f"SELECT public.cca_eob_apply_v1('{reviewed_hash}')", user=ROLE),
                         "STOP: live CCA-EOB target differs from trusted approval")
        self.assertEqual(self.business_snapshot(), before)
        self.assertEqual(self.approval_row(), authorized)
        for change in ("final_expires_at=clock_timestamp()-interval '1 second'", "consumed_at=clock_timestamp()"):
            with self.subTest(change=change):
                self.sql(f"UPDATE {contract.APPROVAL} SET {change}").check_returncode()
                used = self.approval_row()
                self.assertNotEqual(self.sql(extension).returncode, 0)
                self.assertEqual(self.approval_row(), used)
                self.assertWriterRefusesBeforeTarget(reviewed_hash)
                self.assertEqual(self.business_snapshot(), before)

    def test_final_authorization_delimiter_collision_preserves_exact_evidence_without_injection(self):
        self.provision()
        self.seed_business_records()
        business = self.business_snapshot()
        document, _, release, expiry = self.synthetic_approval()
        payload = "apostrophe' $authorize_apply$; CREATE TABLE public.cca_injected (id integer); -- $authorize_apply_$"
        document["review_note"] = payload
        reviewed_hash = hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()
        release = {**release, "plan_hash": reviewed_hash, "restore_report": payload, "review_ref": payload,
                   "archive": {**release["archive"], "path": "/synthetic/" + payload + ".dump"}}
        self.import_plan(document, reviewed_hash, release, expiry)
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        body = self.transportless(self.emitter.final_authorization_sql(document, reviewed_hash, release, deadline))
        delimiter = "$authorize_apply__$"
        self.assertIn("DO " + delimiter, body)
        authorization_block, closing = body.split("DO " + delimiter, 1)[1].split(delimiter + ";", 1)
        self.assertTrue(authorization_block.rstrip().endswith("END;"))
        self.assertNotIn(delimiter, authorization_block)
        self.assertEqual(closing.strip(), "COMMIT;")
        result = self.sql(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        authorized = self.approval_row()
        row = json.loads(authorized)
        self.assertEqual(row["document"], document)
        self.assertEqual(row["release"], release)
        self.assertEqual(row["final_authorization"], {
            "schema": "cca_eob_final_apply_v1", "plan_hash": reviewed_hash,
            "document": document, "release": release,
        })
        self.assertEqual(self.value("SELECT to_regclass('public.cca_injected') IS NULL"), "t")
        self.assertStops(self.sql(f"SELECT public.cca_eob_apply_v1('{reviewed_hash}')", user=ROLE),
                         "STOP: live CCA-EOB target differs from trusted approval")
        self.assertEqual(self.approval_row(), authorized)
        self.assertEqual(self.business_snapshot(), business)

    def test_release_reference_letter_v_passes_sql_metadata_validation(self):
        self.provision()
        self.seed_business_records()
        business = self.business_snapshot()
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        self.import_plan(document, reviewed_hash, release, expiry)
        for field in ("restore_report", "review_ref"):
            with self.subTest(field=field):
                evidence = {**release, field: "v"}
                self.sql(f"UPDATE {contract.APPROVAL} SET release="
                         f"{self.emitter.literal(json.dumps(evidence))}::jsonb").check_returncode()
                self.install_raw_final(document, reviewed_hash, evidence)
                before = self.approval_row()
                self.assertStops(self.sql(f"SELECT public.cca_eob_apply_v1('{reviewed_hash}')", user=ROLE),
                                 "STOP: live CCA-EOB target differs from trusted approval")
                self.assertEqual(self.approval_row(), before)
                self.assertEqual(self.business_snapshot(), business)

    def test_plan_only_approval_denies_before_lock_helper_even_without_live_boxes(self):
        self.provision()
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        self.import_plan(document, reviewed_hash, release, expiry)
        before = self.approval_row()
        result = self.assertWriterRefusesBeforeTarget(reviewed_hash)
        self.assertNotIn("Box identities changed", result.stderr)
        self.assertEqual(self.approval_row(), before)

    def test_final_authorization_refuses_missing_partial_expired_and_consumed_approvals(self):
        self.provision()
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        body = self.transportless(self.emitter.final_authorization_sql(document, reviewed_hash, release, deadline))
        self.assertNotEqual(self.sql(body).returncode, 0)
        self.assertEqual(self.value(f"SELECT count(*) FROM {contract.APPROVAL}"), "0")
        self.import_plan(document, reviewed_hash, release, expiry)
        pristine = self.approval_row()
        changes = (
            "final_authorization='{}'::jsonb",
            "final_authorized_at=clock_timestamp()",
            "final_expires_at=clock_timestamp()-interval '1 second'",
            "expires_at=clock_timestamp()-interval '1 second'",
            "expires_at=clock_timestamp()+interval '30 seconds'",
            "consumed_at=clock_timestamp()",
            "final_authorization='{}'::jsonb,final_authorized_at=clock_timestamp()-interval '2 minutes',"
            "final_expires_at=clock_timestamp()-interval '1 minute'",
        )
        for change in changes:
            with self.subTest(change=change):
                self.sql(f"UPDATE {contract.APPROVAL} SET {change}").check_returncode()
                before = self.approval_row()
                self.assertNotEqual(self.sql(body).returncode, 0)
                self.assertEqual(self.approval_row(), before)
                self.sql(f"UPDATE {contract.APPROVAL} SET final_authorization=NULL,final_authorized_at=NULL,"
                         "final_expires_at=NULL,consumed_at=NULL,"
                         f"expires_at={self.emitter.literal(expiry)}::timestamptz").check_returncode()
                self.assertEqual(self.approval_row(), pristine)
        # Installation must recheck the deadline, not just trust emitter-time validation.
        stale_deadline = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        stale = self.transportless(self.emitter.final_authorization_sql(document, reviewed_hash, release, stale_deadline))
        self.assertNotEqual(self.sql("SELECT pg_sleep(1.1);\n" + stale).returncode, 0)
        self.assertEqual(self.approval_row(), pristine)
        # A valid emitter-time deadline may be over five minutes away at installation.
        far = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        try:
            too_long = self.emitter.final_authorization_sql(document, reviewed_hash, release, far)
        except ValueError:
            # Challenge the SQL-time bound as well, changing only its expiry literal.
            too_long = body.replace(self.emitter.literal(deadline), self.emitter.literal(far))
            self.assertNotEqual(too_long, body)
        else:
            too_long = self.transportless(too_long)
        self.assertNotEqual(self.sql(too_long).returncode, 0)
        self.assertEqual(self.approval_row(), pristine)

    def test_final_authorization_requires_exact_stored_hash_document_and_release(self):
        self.provision()
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        self.import_plan(document, reviewed_hash, release, expiry)
        before = self.approval_row()
        changed_doc = deepcopy(document)
        changed_doc["plan"]["write_recipe"] = {"changed": True}
        changed_hash = hashlib.sha256(json.dumps(changed_doc, sort_keys=True, default=str).encode()).hexdigest()
        changed_release = {**release, "plan_hash": changed_hash}
        candidates = [(changed_doc, changed_hash, changed_release)]
        for archive_change in ({"sha256": "b" * 64}, {"size_bytes": 124}, {"path": "/synthetic/other.dump"}):
            candidates.append((document, reviewed_hash,
                               {**release, "archive": {**release["archive"], **archive_change}}))
        candidates.append((document, reviewed_hash, {**release, "review_ref": "other valid review"}))
        for doc, fingerprint, evidence in candidates:
            with self.subTest(release=evidence):
                deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
                # These are structurally valid; SQL must compare the exact stored evidence.
                body = self.transportless(self.emitter.final_authorization_sql(doc, fingerprint, evidence, deadline))
                self.assertNotEqual(self.sql(body).returncode, 0)
                self.assertEqual(self.approval_row(), before)
        # Even a same-hash document forged by the administrator must not match by hash alone.
        self.sql(f"UPDATE {contract.APPROVAL} SET document="
                 f"{self.emitter.literal(json.dumps(changed_doc))}::jsonb").check_returncode()
        before = self.approval_row()
        body = self.transportless(self.emitter.final_authorization_sql(
            document, reviewed_hash, release, (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()))
        self.assertNotEqual(self.sql(body).returncode, 0)
        self.assertEqual(self.approval_row(), before)

    def test_direct_sql_malformed_release_and_final_document_never_reach_live_target(self):
        self.provision()
        self.seed_business_records()
        business = self.business_snapshot()
        document, reviewed_hash, release, expiry = self.synthetic_approval()
        self.import_plan(document, reviewed_hash, release, expiry)
        for label, malformed in _invalid_releases(release):
            with self.subTest(label=label):
                self.sql(f"UPDATE {contract.APPROVAL} SET release="
                         f"{self.emitter.literal(json.dumps(malformed))}::jsonb").check_returncode()
                self.install_raw_final(document, reviewed_hash, malformed)
                before = self.approval_row()
                refused = self.assertWriterRefusesBeforeTarget(reviewed_hash)
                if label.startswith(("restore_report whitespace=", "review_ref whitespace=")):
                    self.assertIn("Incomplete, malformed or unbound CCA-EOB archive/release evidence", refused.stderr)
                self.assertEqual(self.approval_row(), before)
                self.assertEqual(self.business_snapshot(), business)
        self.sql(f"UPDATE {contract.APPROVAL} SET release="
                 f"{self.emitter.literal(json.dumps(release))}::jsonb").check_returncode()
        self.install_raw_final(document, reviewed_hash, release)
        pristine = self.approval_row()
        changes = (
            "final_authorization=NULL", "final_authorized_at=NULL", "final_expires_at=NULL",
            "final_authorization=jsonb_set(final_authorization,'{schema}','\"forged\"')",
            "final_authorization=jsonb_set(final_authorization,'{plan_hash}','\"forged\"')",
            "final_authorization=jsonb_set(final_authorization,'{document}','{}')",
            "final_authorization=jsonb_set(final_authorization,'{release}','{}')",
            "final_expires_at=clock_timestamp()-interval '1 second'",
            "final_expires_at=final_authorized_at+interval '301 seconds'",
            "final_expires_at=expires_at+interval '1 second'",
        )
        for change in changes:
            with self.subTest(change=change):
                row = json.loads(pristine)
                self.sql(f"UPDATE {contract.APPROVAL} SET " + ",".join(
                    f"{key}={self.emitter.literal(json.dumps(row[key]) if key == 'final_authorization' else row[key])}"
                    f"::{'jsonb' if key == 'final_authorization' else 'timestamptz'}"
                    for key in ("final_authorization", "final_authorized_at", "final_expires_at")
                )).check_returncode()
                self.sql(f"UPDATE {contract.APPROVAL} SET {change}").check_returncode()
                before = self.approval_row()
                self.assertWriterRefusesBeforeTarget(reviewed_hash)
                self.assertEqual(self.approval_row(), before)
                self.assertEqual(self.business_snapshot(), business)

    def test_direct_reader_and_writer_refuse_privilege_drift_even_with_forged_approval(self):
        self.provision()
        self.seed_business_records()
        before = self.business_snapshot()
        reviewed_hash = "a" * 64
        self.sql(f"INSERT INTO {contract.APPROVAL} (singleton,plan_hash,document,release,expires_at,consumed_at) "
                 f"VALUES (true,'{reviewed_hash}',"
                 "'{\"actor_id\":1}','{}',now()+interval '1 hour',NULL)").check_returncode()
        cases = (
            ("PUBLIC business write", "GRANT UPDATE ON cultures_box TO PUBLIC",
             "REVOKE UPDATE ON cultures_box FROM PUBLIC", None,
             "broad table privilege", "broad table privilege"),
            ("PUBLIC large object", "SELECT lo_create(424242); GRANT SELECT ON LARGE OBJECT 424242 TO PUBLIC",
             "SELECT lo_unlink(424242)", None,
             "unexpected authority: large-object privilege", "unexpected authority: large-object privilege"),
            ("PUBLIC approval document write", f"GRANT UPDATE (document) ON {contract.APPROVAL} TO PUBLIC",
             f"REVOKE UPDATE (document) ON {contract.APPROVAL} FROM PUBLIC", "polypbase",
             "direct write privilege", "approval table ACL drift"),
            ("runtime approval document write", f"GRANT UPDATE (document) ON {contract.APPROVAL} TO {ROLE}",
             f"REVOKE UPDATE (document) ON {contract.APPROVAL} FROM {ROLE}", ROLE,
             "direct write privilege", "approval table ACL drift"),
        )
        for label, grant, revoke, forger, reader_error, writer_error in cases:
            with self.subTest(label=label):
                self.sql(grant).check_returncode()
                try:
                    if forger:
                        forged = self.sql(f"UPDATE {contract.APPROVAL} SET document="
                                          "'{\"actor_id\":1,\"forged\":true}'::jsonb", user=forger)
                        self.assertEqual(forged.returncode, 0, forged.stderr)
                    approval = self.value(f"SELECT to_jsonb(a) FROM {contract.APPROVAL} a")
                    self.assertStops(self.sql("SELECT public.cca_eob_read_v1(1)", user=ROLE), reader_error)
                    self.assertStops(self.sql(f"SELECT public.cca_eob_apply_v1('{reviewed_hash}')", user=ROLE),
                                     writer_error)
                    self.assertEqual(self.business_snapshot(), before)
                    self.assertEqual(self.value(f"SELECT to_jsonb(a) FROM {contract.APPROVAL} a"), approval)
                finally:
                    self.sql(revoke).check_returncode()
        result = self.sql("SELECT public.cca_eob_read_v1(1)", user=ROLE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.business_snapshot(), before)

    # --- refusals -------------------------------------------------------------------

    def test_privilege_and_authority_drift_is_refused_by_verify_and_cleaned_by_rollback(self):
        drifts = (
            ("column update", "GRANT UPDATE (role) ON accounts_organizationmembership TO %s", "direct write privilege"),
            ("table update", "GRANT UPDATE ON cultures_box TO %s", "broad table privilege"),
            ("table insert", "GRANT INSERT ON audit_auditlog TO %s", "broad table privilege"),
            ("password column", "GRANT SELECT (password) ON auth_user TO %s", "wrong SELECT privilege"),
            ("reading table select", "GRANT SELECT ON measurements_historicaltemperaturereading TO %s", "wrong SELECT privilege"),
            ("reading link and status columns only: temperature", "GRANT SELECT (temperature_c) ON measurements_historicaltemperaturereading TO %s", "wrong SELECT privilege"),
            ("reading id column", "GRANT SELECT (id) ON measurements_historicaltemperaturereading TO %s", "wrong SELECT privilege"),
            ("schema create", "GRANT CREATE ON SCHEMA public TO %s", "can CREATE"),
            ("database create", "GRANT CREATE ON DATABASE polypbase TO %s", "can CREATE"),
            ("owner membership", "GRANT polypbase TO %s", "no memberships"),
            ("predefined role", "GRANT pg_read_all_data TO %s", "no memberships"),
            ("public helper execute", "GRANT EXECUTE ON FUNCTION public.cca_eob_lock_v1(bigint) TO PUBLIC", "unexpected helper ACL"),
            ("sequence usage", "GRANT USAGE ON SEQUENCE cultures_box_id_seq TO %s", "sequence privilege"),
            ("inherit", "ALTER ROLE %s INHERIT", "unexpected correction role identity/attributes"),
            ("createdb", "ALTER ROLE %s CREATEDB", "unexpected correction role identity/attributes"),
            ("connection limit", "ALTER ROLE %s CONNECTION LIMIT -1", "unexpected correction role identity/attributes"),
            ("role setting", "ALTER ROLE %s SET search_path = public, pg_temp", "unexpected authority: role-level setting"),
            ("large object", "SELECT lo_create(424242); GRANT SELECT ON LARGE OBJECT 424242 TO %s", "unexpected authority: large-object privilege"),
            ("PUBLIC large object", "SELECT lo_create(424242); GRANT SELECT ON LARGE OBJECT 424242 TO PUBLIC",
             "unexpected authority: large-object privilege"),
            ("other schema usage", "CREATE SCHEMA extra_qa; GRANT USAGE ON SCHEMA extra_qa TO %s", "unexpected authority: schema authority: extra_qa"),
            ("other security definer", "CREATE SCHEMA extra_qa; CREATE FUNCTION extra_qa.leak() RETURNS int LANGUAGE sql "
                                       "SECURITY DEFINER AS 'SELECT 1'; GRANT USAGE ON SCHEMA extra_qa TO PUBLIC",
             "unexpected authority"),
        )
        for label, setup, expected in drifts:
            with self.subTest(label):
                self.provision()
                self.assertEqual(self.run_action("verify").returncode, 0)
                self.sql(setup % ROLE if "%s" in setup else setup).check_returncode()
                result = self.run_action("verify")
                self.assertNotEqual(result.returncode, 0, label)
                self.assertIn(expected, result.stderr, label)
                # Recognized direct grants are revoked, but helper/authority drift fails closed.
                repairable = {"column update", "table update", "table insert", "password column",
                              "reading table select", "reading link and status columns only: temperature",
                              "reading id column", "sequence usage"}
                reprovisioned = self.run_action("provision")
                if label in repairable:
                    self.assertEqual(reprovisioned.returncode, 0, reprovisioned.stderr)
                    verified = self.run_action("verify")
                    self.assertEqual(verified.returncode, 0, verified.stderr)
                else:
                    self.assertNotEqual(reprovisioned.returncode, 0, label)
                rolled = self.run_action("rollback")
                if label == "public helper execute":
                    self.assertStops(rolled, "unexpected helper ACL")
                    self.assertEqual(self.value(f"SELECT rolcanlogin FROM pg_roles WHERE rolname='{ROLE}'"), "f")
                    self.sql(f"REVOKE EXECUTE ON FUNCTION {contract.FUNCTION} FROM PUBLIC").check_returncode()
                    rolled = self.run_action("rollback")
                self.assertEqual(rolled.returncode, 0, rolled.stderr)
                self.reset_cluster_state()

    def test_unrecognized_role_helper_ownership_and_memberships_refuse_provisioning(self):
        # A same-named role without the marker is never adopted or altered.
        self.sql("CREATE ROLE %s LOGIN" % ROLE).check_returncode()
        self.assertStops(self.run_action("provision"), "STOP: preexisting unrecognized role; do not overwrite")
        self.assertStops(self.run_action("rollback"), "STOP: unexpected role identity")
        self.assertEqual(self.value("SELECT count(*) FROM pg_roles WHERE rolname = '%s'" % ROLE), "1")
        self.sql("DROP ROLE %s" % ROLE).check_returncode()
        # A same-named helper owned by the administrator (or any non-owner) is refused too.
        self.sql("CREATE ROLE %s LOGIN;" % ROLE).check_returncode()
        self.sql(contract.lock_definition()).check_returncode()
        self.assertEqual(self.value("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = to_regprocedure('%s')" % contract.FUNCTION), "postgres")
        self.sql("DROP FUNCTION %s; DROP OWNED BY %s; DROP ROLE %s" % (contract.FUNCTION, ROLE, ROLE)).check_returncode()
        self.provision()
        self.sql("ALTER FUNCTION %s OWNER TO postgres" % contract.FUNCTION).check_returncode()
        self.assertStops(self.run_action("provision"), "STOP: unexpected helper definition/metadata")
        self.assertStops(self.run_action("rollback"), "STOP: unexpected helper definition/metadata")
        # NOLOGIN is committed before phase two refuses the unrecognized helper.
        self.assertEqual(self.value("SELECT rolcanlogin FROM pg_roles WHERE rolname = '%s'" % ROLE), "f")
        self.sql("ALTER FUNCTION %s OWNER TO polypbase" % contract.FUNCTION).check_returncode()
        self.assertEqual(self.run_action("rollback").returncode, 0)

    def test_tampered_helper_body_is_never_dropped_or_replaced(self):
        self.provision()
        self.sql("CREATE OR REPLACE FUNCTION public.cca_eob_lock_v1(actor_id bigint) RETURNS void LANGUAGE plpgsql "
                 "SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$ BEGIN NULL; END $$").check_returncode()
        self.assertStops(self.run_action("provision"), "STOP: unexpected helper definition/metadata")
        self.assertStops(self.run_action("rollback"), "STOP: unexpected helper definition/metadata")
        self.assertEqual(self.value("SELECT rolcanlogin FROM pg_roles WHERE rolname = '%s'" % ROLE), "f")
        self.assertEqual(self.value("SELECT count(*) FROM pg_proc WHERE proname = 'cca_eob_lock_v1'"), "1")
        self.sql("DROP FUNCTION %s" % contract.FUNCTION).check_returncode()
        self.assertEqual(self.run_action("rollback").returncode, 0)

    # --- rollback, re-provisioning, sessions and timeouts ---------------------------

    def test_rollback_removes_everything_is_idempotent_and_reprovisioning_works(self):
        self.provision()
        self.assertEqual(self.run_action("rollback").returncode, 0)
        self.assertEqual(self.run_action("rollback").returncode, 0)
        self.assertEqual(self.value(
            "SELECT (SELECT count(*) FROM pg_roles WHERE rolname = '%s'), (SELECT count(*) FROM pg_proc WHERE proname LIKE 'cca_eob_%%'),"
            " (SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = '%s'),"
            " (SELECT count(*) FROM information_schema.role_column_grants WHERE grantee = '%s')" % (ROLE, ROLE, ROLE)), "0|0|0|0")
        self.assertEqual(self.value(f"SELECT to_regclass('{contract.APPROVAL}') IS NULL"), "t")
        self.assertEqual(self.value("SELECT has_database_privilege('postgres', 'polypbase', 'CONNECT')"), "t")
        self.provision()
        self.assertEqual(self.run_action("verify").returncode, 0)
        self.assertStops(self.sql("SELECT public.cca_eob_lock_v1(1)", user=ROLE), "CCA-EOB Box identities changed")

    def test_live_session_blocks_rollback_after_login_is_disabled_and_nothing_is_terminated(self):
        self.provision()
        session = subprocess.Popen(
            [_exe("psql"), "-X", "-q", "-h", "127.0.0.1", "-p", str(self.port), "-U", ROLE, "-d", "polypbase",
             "-c", "SELECT pg_sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 30
            while self.value("SELECT count(*) FROM pg_stat_activity WHERE usename = '%s'" % ROLE) == "0":
                self.assertIsNone(session.poll(), session.stderr.read() if session.poll() is not None else "")
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.2)
            self.assertStops(self.run_action("provision"), "STOP: correction login has live sessions")
            self.assertStops(self.run_action("rollback"), "STOP: correction login has live sessions")
            # Phase one committed: no new session can start, and the live one still runs.
            self.assertEqual(self.value("SELECT rolcanlogin FROM pg_roles WHERE rolname = '%s'" % ROLE), "f")
            self.assertEqual(self.value("SELECT count(*) FROM pg_stat_activity WHERE usename = '%s'" % ROLE), "1")
            self.assertNotEqual(self.sql("SELECT 1", user=ROLE).returncode, 0)
            self.assertIsNone(session.poll())
        finally:
            session.terminate()
            session.wait(timeout=30)
            session.stderr.close()
            # Test cleanup only: end this test's own client backend (the script never does).
            self.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '%s'" % ROLE)
        deadline = time.monotonic() + 30
        while self.value("SELECT count(*) FROM pg_stat_activity WHERE usename = '%s'" % ROLE) != "0":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        self.assertEqual(self.run_action("rollback").returncode, 0)

    def test_provisioning_fails_closed_instead_of_queueing_behind_a_table_lock(self):
        holder = subprocess.Popen(
            [_exe("psql"), "-X", "-q", "-h", "127.0.0.1", "-p", str(self.port), "-U", "postgres", "-d", "polypbase",
             "-c", "BEGIN; LOCK TABLE cultures_box IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(60); COMMIT"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 30
            while self.value("SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
                             "WHERE c.relname = 'cultures_box' AND l.mode = 'AccessExclusiveLock' AND l.granted") == "0":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.2)
            started = time.monotonic()
            result = self.run_action("provision")
            elapsed = time.monotonic() - started
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lock timeout", result.stderr)
            self.assertGreaterEqual(elapsed, 9)
            self.assertLess(elapsed, 30)
        finally:
            holder.terminate()
            holder.wait(timeout=30)
            # Killing the client does not interrupt the server-side pg_sleep: end that backend
            # (this test's own holder session) so later tests are not blocked by its lock.
            self.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query LIKE '%pg_sleep(60)%' "
                     "AND pid <> pg_backend_pid()").check_returncode()
            deadline = time.monotonic() + 30
            while self.value("SELECT count(*) FROM pg_stat_activity WHERE query LIKE '%pg_sleep(60)%' "
                             "AND pid <> pg_backend_pid()") != "0":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.2)
        # Everything rolled back: no role, no helper, no partial grants.
        self.assertEqual(self.value("SELECT count(*) FROM pg_roles WHERE rolname = '%s'" % ROLE), "0")
        self.assertEqual(self.value("SELECT count(*) FROM pg_proc WHERE proname = 'cca_eob_lock_v1'"), "0")
