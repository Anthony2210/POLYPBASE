"""Container-only native socket proofs; never an alternate application QA backend.

The root provisioner supplies a fresh cluster, a protected target and an independent
control-file identity. The unchanged TCP QA harness owns schema/fixture lifecycle;
only production workflow calls switch to the real guarded Unix connection.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path("/opt/pca")
MARKER = Path("/etc/polypbase/pca-unix-ci.json")
DATABASE = "synthetic_pca_ci"
OWNER = "synthetic_pca_owner"
RUNTIME = "synthetic_pca_runtime"
PORT = "65432"


def preflight(payload):
    if (sys.platform != "linux" or os.getuid() != 10001 or os.geteuid() != 10001
            or Path(__file__).resolve() != ROOT / "scripts/pca_unix_ci/run_tests.py"
            or not Path("/.dockerenv").is_file()
            or set(os.listdir("/sys/class/net")) != {"lo"}):
        raise RuntimeError("Native PCA tests require the dedicated network-none CI container")
    if (type(payload) is not dict
            or set(payload) != {"owner_password", "runtime_password", "system_identifier"}
            or any(not isinstance(value, str) or not value for value in payload.values())):
        raise RuntimeError("Missing process-local disposable credentials/control identity")
    for path in (Path("/"), Path("/etc"), MARKER.parent, MARKER):
        info = path.lstat()
        if (info.st_uid != 0 or info.st_mode & 0o022
                or not (stat.S_ISREG(info.st_mode) if path == MARKER else stat.S_ISDIR(info.st_mode))):
            raise RuntimeError("The CI provisioning marker must be independently root-protected")
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    if marker != {"schema": "pca_unix_ci_v1", "container_only": True, "network": "none",
                  "system_identifier": payload["system_identifier"], "database": DATABASE,
                  "port": int(PORT), "owner": OWNER, "runtime": RUNTIME}:
        raise RuntimeError("Disposable target differs from the independent CI provisioning record")
    expected = {"POLYPBASE_QA_HOST": "127.0.0.1", "POLYPBASE_QA_PORT": PORT,
                "POLYPBASE_QA_DATABASE": DATABASE, "POLYPBASE_QA_OWNER": OWNER,
                "POLYPBASE_QA_RUNTIME": RUNTIME,
                "POLYPBASE_QA_SYSTEM_IDENTIFIER": payload["system_identifier"],
                "PYTHON_DOTENV_DISABLED": "1", "POLYPBASE_TEST_POSTGRES": "1",
                "POLYPBASE_HISTORICAL_POSTGRES_QA": "1",
                "DJANGO_SETTINGS_MODULE": "config.historical_temperature_pgqa_settings"}
    if (any(os.environ.get(key) != value for key, value in expected.items())
            or any(key.upper().startswith(("PG", "POSTGRES_")) for key in os.environ)):
        raise RuntimeError("Inherited database configuration is forbidden in native CI")


@contextmanager
def production_connection(runtime_password):
    """Use the production branch without mocking any identity/artifact validator."""
    from django.db import connections
    from django.db.backends.postgresql.base import DatabaseWrapper
    from django.test import override_settings

    previous = connections["default"]
    config = deepcopy(previous.settings_dict)
    config.update(ENGINE="django.db.backends.postgresql", HOST="/var/run/postgresql", PORT=PORT,
                  NAME=DATABASE, USER=RUNTIME, PASSWORD=runtime_password,
                  OPTIONS={"sslmode": "disable"}, CONN_MAX_AGE=0)
    with override_settings(DATABASES={"default": config}, SETTINGS_MODULE="config.settings",
                           DEBUG=False, HISTORICAL_TEMPERATURE_PGQA=False, DATABASE_ROUTERS=[]):
        wrapper = DatabaseWrapper(config, alias="default")
        connections["default"] = wrapper
        try:
            from apps.cultures import pca_production as production

            if production._is_disposable_qa():
                raise RuntimeError("Unix production validation must not use the QA bypass")
            yield wrapper
        finally:
            wrapper.close()
            connections["default"] = previous


def run_probe(case, runtime_password):
    from apps.cultures import pca_production as production

    fragments = {
        "socket-owner": "Unsafe PostgreSQL socket ownership or type",
        "socket-group": "Untrusted PostgreSQL socket endpoint or runtime identity",
        "socket-acl": "Runtime must not be able to write",
        "socket-world-write": "Unsafe PostgreSQL socket directory",
        "server-identity": "Live database identity/session state differs",
        "role-privileges": "Unsafe PostgreSQL role privileges",
    }
    with production_connection(runtime_password):
        target = production.read_target(production.pca_protected_artifacts.PINNED_ATTESTATION)
        try:
            production.verify_target(target)
        except ValueError as error:
            if case == "success" or fragments[case] not in str(error):
                raise AssertionError(f"Unexpected refusal in {case}: {error}") from error
        else:
            if case != "success":
                raise AssertionError(f"Production guards accepted unsafe case: {case}")
    print(f"Native production guard probe passed: {case}", flush=True)


def workflow_suite(runtime_password):
    from io import StringIO
    import unittest
    from unittest.mock import patch

    from django.core.exceptions import PermissionDenied
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import connection

    from apps.audit.models import AuditLog
    from apps.cultures import pca_identity_unification as merge
    from apps.cultures import pca_production as production
    from apps.cultures.test_pca_production import ProductionWorkflowTests
    from config import database_backup

    class NativeUnixWorkflowTests(ProductionWorkflowTests):
        def setUp(self):
            # The unchanged disposable TCP gate protects all fixture writes.
            super().setUp()
            self.machine.stop()
            context = production_connection(runtime_password)
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)
            self.target = production.read_target(production.pca_protected_artifacts.PINNED_ATTESTATION)
            production.verify_target(self.target)

        def test_nonadmin_foreign_scope_and_changed_server_identity_refused(self):
            with self.assertRaises(PermissionDenied):
                production.build_plan(1, actor=self.writer, target=self.target)
            with self.assertRaises(ValueError):
                production.build_plan(2, actor=self.admin, target=self.target)
            # Unlike the TCP QA bypass, production rejects a self-declared identity
            # against the root source before querying SQL. A separate root-parent
            # probe checks a changed attested server boot against live SQL.
            with self.assertRaisesMessage(ValueError, "root-authenticated"):
                production.build_plan(1, actor=self.admin, target={**self.target, "database_oid": 9999999})

        def test_command_requires_separate_actions_and_explicit_artifacts(self):
            plan_file = self.directory / "plan.json"
            evidence_file = self.directory / "backup.json"
            common = {"production": True, "organization_id": 1, "actor_id": self.admin.pk,
                      "target": production.pca_protected_artifacts.PINNED_ATTESTATION,
                      "plan_file": str(plan_file)}
            output = StringIO()
            call_command("pca_production", "plan", stdout=output, **common)
            digest = json.loads(output.getvalue())["plan_hash"]
            self.assertFalse(AuditLog.objects.filter(object_type=merge.RECEIPT_TYPE).exists())
            call_command("pca_production", "backup", stdout=StringIO(), expected_plan_hash=digest,
                         backup_evidence=str(evidence_file), backup_dir=str(self.directory), **common)
            self.assertFalse(AuditLog.objects.filter(object_type=merge.RECEIPT_TYPE).exists())
            with self.assertRaises(CommandError):
                call_command("pca_production", "apply", stdout=StringIO(), expected_plan_hash=digest,
                             backup_evidence=str(evidence_file), **common)
            call_command("pca_production", "apply", stdout=StringIO(), expected_plan_hash=digest,
                         backup_evidence=str(evidence_file), review_ref="independent-synthetic-ci-review",
                         authorization=f"APPLY_PCA_ORGANIZATION_1:{digest}", **common)
            self.assertEqual(AuditLog.objects.filter(object_type=merge.RECEIPT_TYPE).count(), 1)

        def test_real_unix_endpoint_nulls_port_and_independent_target(self):
            info = connection.connection.info
            self.assertEqual((info.host, info.hostaddr, info.port, info.dbname, info.user),
                             ("/var/run/postgresql", "", int(PORT), DATABASE, RUNTIME))
            with connection.cursor() as cursor:
                cursor.execute(production.IDENTITY_SQL)
                row = cursor.fetchone()
                cursor.execute("SELECT system_identifier::text FROM pg_catalog.pg_control_system()")
                control = cursor.fetchone()[0]
            marker = json.loads(MARKER.read_text(encoding="utf-8"))
            self.assertEqual(control, marker["system_identifier"])
            self.assertEqual(row[5:7], (None, None))
            self.assertEqual(row[12], int(PORT))
            self.assertEqual(row[0:5], (DATABASE, self.target["database_oid"], RUNTIME, RUNTIME,
                                       self.target["role_oid"]))
            self.assertEqual(production.machine_id(), self.target["machine_id"])

        def test_mismatched_configured_socket_role_database_and_port_refused(self):
            config = connection.settings_dict
            for field, wrong in (("HOST", "/run/other"), ("USER", OWNER),
                                 ("NAME", "not_the_synthetic_database"), ("PORT", "65433")):
                before = config[field]
                try:
                    config[field] = wrong
                    with self.subTest(field=field), patch.object(connection, "ensure_connection") as ensure:
                        with self.assertRaisesMessage(ValueError, "Configured database"):
                            production.verify_target(self.target)
                        ensure.assert_not_called()
                finally:
                    config[field] = before
            production.verify_target(self.target)

        def test_real_exported_snapshot_reaches_real_dump_and_restore_list(self):
            create = database_backup.create_evidence
            observed = []

            def inspect_snapshot(database, target, digest, output_dir, *, snapshot, hostaddr):
                self.assertIsNone(hostaddr)
                self.assertEqual((database["HOST"], str(database["PORT"]), database["USER"]),
                                 ("/var/run/postgresql", PORT, RUNTIME))
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('transaction_read_only'), "
                                   "current_setting('transaction_isolation')")
                    self.assertEqual(cursor.fetchone(), ("on", "repeatable read"))
                # Do not replace external tools: the wrapped helper really dumps
                # this live snapshot, hashes the archive and runs pg_restore --list.
                result = create(database, target, digest, output_dir, snapshot=snapshot, hostaddr=hostaddr)
                observed.append(snapshot)
                return result

            document = self.production_plan()
            with patch.object(database_backup, "create_evidence", side_effect=inspect_snapshot):
                evidence = self.backup(document)
            self.assertEqual(len(observed), 1)
            self.assertTrue(observed[0])
            self.assertGreater(evidence["archive"]["size_bytes"], 0)
            database_backup.validate_evidence(evidence, self.target, merge.fingerprint(document))

    return unittest.defaultTestLoader.loadTestsFromTestCase(NativeUnixWorkflowTests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", choices=("success", "socket-owner", "socket-group", "socket-acl",
                                           "socket-world-write", "server-identity", "role-privileges"))
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    preflight(payload)
    sys.path.insert(0, str(ROOT / "backend"))
    from config import historical_temperature_pgqa as qa

    qa.CREDENTIALS.update({OWNER: payload["owner_password"], RUNTIME: payload["runtime_password"]})
    qa.AUTHORIZED = True
    import django

    django.setup()
    if args.probe:
        run_probe(args.probe, payload["runtime_password"])
        return 0

    from django.core.management import call_command
    from django.db import connection, connections

    qa.assert_disposable(connection)
    qa.assert_empty_business_data(connection)
    lease = connection.copy(alias="qa_exclusive_lease")
    try:
        qa.assert_disposable(lease)
        with lease.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(20261018,1)")
            if not cursor.fetchone()[0]:
                raise RuntimeError("Disposable CI lease is already held")
        qa.RUN_LEASE = lease
        qa.EMPTY_START_VERIFIED = True
        call_command("migrate", interactive=False, verbosity=1)
        qa.grant_runtime(connection)
        qa.verify_schema(connection)
        qa.assert_empty_business_data(connection)
        runner = qa.IsolatedTemperatureRunner(verbosity=2, interactive=False, parallel=0, failfast=True)
        # Reuse its no-database-lifecycle setup and result accounting, but build
        # only the existing nine workflow proofs plus three native assertions.
        suite = workflow_suite(payload["runtime_password"])
        runner.setup_test_environment()
        try:
            old = runner.setup_databases()
            try:
                result = runner.run_suite(suite)
            finally:
                runner.teardown_databases(old)
        finally:
            runner.teardown_test_environment()
        summary = runner.result_summary
        print(json.dumps({"native_unix_workflow": summary}, sort_keys=True), flush=True)
        if not result.wasSuccessful() or summary["skipped"] or summary["run"] != 12:
            raise RuntimeError("Native Unix workflow requires 12 passing proofs and no skips")
        qa.assert_empty_business_data(connection)
    finally:
        qa.RUN_LEASE = None
        qa.EMPTY_START_VERIFIED = False
        connections.close_all()
        lease.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
