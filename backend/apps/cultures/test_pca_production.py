"""Production orchestration proofs, synthetic data on the guarded PostgreSQL QA only."""

import json
import os
import tempfile
from contextlib import contextmanager, nullcontext
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from config import database_backup
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement

from . import pca_identity_unification as merge
from . import pca_production as production
from .models import Box
from .test_pca_identity_unification import PcaFixtureMixin

MACHINE = "a" * 32


def synthetic_target():
    return {"schema": "pca_target_v3", "connection_mode": "tcp", "socket_uid": None, "socket_gid": None,
            "machine_id": MACHINE, "configured_host": "127.0.0.1", "configured_port": "65432",
            "database": "synthetic_pca", "role": "synthetic_runtime", "server_address": "127.0.0.1",
            "server_port": 65432, "database_oid": 1234, "role_oid": 5678,
            "postmaster_started_at": "2026-01-01T00:00:00.000000Z", "server_version_num": 180000}


@override_settings(SETTINGS_MODULE="config.settings", DEBUG=False,
                   HISTORICAL_TEMPERATURE_PGQA=False, DATABASE_ROUTERS=[])
class ProductionRefusalTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.target = synthetic_target()
        self.attestation = patch.object(production.pca_protected_artifacts, "read_attestation", return_value=self.target)
        self.trusted_reader = self.attestation.start()
        self.addCleanup(self.attestation.stop)
        self.config = {"ENGINE": "django.db.backends.postgresql", "HOST": "127.0.0.1", "PORT": "65432",
                       "NAME": "synthetic_pca", "USER": "synthetic_runtime", "OPTIONS": {"sslmode": "prefer"}}
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.machine = patch.object(production, "machine_id", return_value=MACHINE)
        self.machine.start()
        self.addCleanup(self.machine.stop)
        self.configured = self.settings(DATABASES={"default": self.config}, SETTINGS_MODULE="config.settings")
        self.configured.enable()
        self.addCleanup(self.configured.disable)

    def test_missing_or_mismatched_attestation_is_preconnect(self):
        for change in ({"database": "other"}, {"role": "postgres"}, {"database_oid": None},
                       {"configured_host": "shared.example.test"}, {"server_address": "192.0.2.1"},
                       {"machine_id": "b" * 32}, {"schema": "pca_target_v1"}, {"configured_port": "65433"}):
            with self.subTest(change=change), patch.object(production, "connection") as mocked:
                mocked.settings_dict = self.config
                with self.assertRaises(ValueError):
                    production.require_target({**self.target, **change})
                mocked.ensure_connection.assert_not_called()
                mocked.cursor.assert_not_called()
        with self.assertRaises(ValueError):
            production.require_target({})

    def test_self_declared_target_is_rejected_before_connecting(self):
        with patch.object(production, "connection") as mocked:
            mocked.settings_dict = self.config
            with self.assertRaisesMessage(ValueError, "root-authenticated"):
                production.require_target({**self.target, "database_oid": 99999})
            mocked.ensure_connection.assert_not_called()
        with patch.object(production, "read_artifact") as read:
            with self.assertRaisesMessage(ValueError, "pinned root-owned"):
                production.read_target("/srv/polypbase/self-declared-target.json")
            read.assert_not_called()

    def test_authenticated_target_and_pinned_path_are_accepted(self):
        with patch.object(production, "connection") as mocked:
            mocked.settings_dict = self.config
            self.assertFalse(production.require_target(self.target))
            mocked.ensure_connection.assert_not_called()
        self.assertEqual(production.read_target(production.pca_protected_artifacts.PINNED_ATTESTATION), self.target)
        self.trusted_reader.assert_called()

    def test_untrusted_source_failure_cannot_reach_the_database(self):
        self.trusted_reader.side_effect = ValueError("Unsafe directory ownership")
        with patch.object(production, "connection") as mocked:
            mocked.settings_dict = self.config
            with self.assertRaisesMessage(ValueError, "Unsafe directory"):
                production.require_target(self.target)
            mocked.ensure_connection.assert_not_called()
            mocked.cursor.assert_not_called()

    def test_production_manifests_use_protected_directory_io(self):
        with patch.object(production.pca_protected_artifacts, "read_json", return_value={"plan": 1}) as read:
            self.assertEqual(production.read_artifact("/srv/polypbase/private/plan.json"), {"plan": 1})
            read.assert_called_once_with("/srv/polypbase/private/plan.json")
        with patch.object(production.pca_protected_artifacts, "write_json") as write:
            production.write_artifact("/srv/polypbase/private/new.json", {"plan": 1})
            write.assert_called_once_with("/srv/polypbase/private/new.json", {"plan": 1})

    def test_unsafe_archive_ancestors_fail_before_archive_tools(self):
        evidence = {"archive": {"path": "/srv/unsafe/archive.dump"}}
        with patch.object(production.pca_protected_artifacts, "protected_file",
                          side_effect=ValueError("Unsafe directory ownership")):
            with patch.object(database_backup, "validate_evidence") as verify:
                with self.assertRaisesMessage(ValueError, "Unsafe directory"):
                    production._validate_backup(evidence, self.target, "a" * 64)
                verify.assert_not_called()

    def test_archive_replacement_during_verification_is_rejected(self):
        evidence = {"archive": {"path": "/srv/private/archive.dump"}}
        @contextmanager
        def replaced_ancestor(path):
            self.assertEqual(path, evidence["archive"]["path"])
            yield
            raise ValueError("Directory changed or was replaced")

        with patch.object(production.pca_protected_artifacts, "protected_file", replaced_ancestor):
            with patch.object(database_backup, "validate_evidence", return_value=evidence) as verify:
                with self.assertRaisesMessage(ValueError, "Directory changed"):
                    production._validate_backup(evidence, self.target, "a" * 64)
                verify.assert_called_once()

    def test_backup_holds_directory_bindings_across_creation(self):
        plan = {"ready": True, "blockers": [], "operation": merge.OPERATION, "organization_id": 1,
                "counts": {"duplicates": 18, "moved": 92, "untouched": 2}}
        document = production.envelope(self.target, plan)
        active = {"directory": False}
        evidence = {"archive": {"path": "/srv/private/archive.dump"}}

        @contextmanager
        def directory_guard(path):
            self.assertEqual(path, "/srv/private")
            active["directory"] = True
            yield
            active["directory"] = False

        def archive(*args, **kwargs):
            self.assertTrue(active["directory"])
            return evidence

        with patch.object(production, "connection") as mocked, \
                patch.object(production, "verify_target"), \
                patch.object(production.transaction, "atomic"), \
                patch.object(merge, "_authorize", return_value=(object(), object())), \
                patch.object(merge, "_make_plan", return_value=plan), \
                patch.object(production.pca_protected_artifacts, "protected_directory", directory_guard), \
                patch.object(production.pca_protected_artifacts, "validate_file"), \
                patch.object(database_backup, "create_evidence", side_effect=archive):
            mocked.settings_dict = self.config
            mocked.in_atomic_block = False
            result = production.backup_plan(1, actor=SimpleNamespace(pk=1), document=document,
                                            approved_hash=merge.fingerprint(document), output_dir="/srv/private")
        self.assertEqual(result, evidence)
        self.assertFalse(active["directory"])

    def test_libpq_redirects_options_and_nonproduction_settings_fail_preconnect(self):
        for variable in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGPASSWORD"):
            with patch.dict(os.environ, {variable: "synthetic"}), self.assertRaises(ValueError):
                production.require_target(self.target)
        for options in ({"service": "other"}, {"sslmode": "prefer", "hostaddr": "192.0.2.1"}):
            with patch.object(production, "connection") as mocked:
                mocked.settings_dict = {**self.config, "OPTIONS": options}
                with self.assertRaises(ValueError):
                    production.require_target(self.target)
                mocked.ensure_connection.assert_not_called()
        with self.settings(DEBUG=True), self.assertRaises(ValueError):
            production.require_target(self.target)
        with self.settings(SETTINGS_MODULE="config.test_settings"), self.assertRaises(ValueError):
            production.require_target(self.target)

    def test_effective_connection_database_and_role_are_checked(self):
        for change in ({"hostaddr": "::1"}, {"dbname": "other"}, {"user": "postgres"}, {"port": 65433}):
            with self.subTest(change=change), patch.object(production, "connection") as mocked:
                mocked.settings_dict = self.config
                info = {"hostaddr": "127.0.0.1", "dbname": "synthetic_pca", "user": "synthetic_runtime", "port": 65432}
                mocked.connection.info = SimpleNamespace(**{**info, **change})
                self.assertEqual((settings.SETTINGS_MODULE, settings.DEBUG,
                                  settings.HISTORICAL_TEMPERATURE_PGQA, mocked.settings_dict),
                                 ("config.settings", False, False, self.config))
                with self.assertRaisesMessage(ValueError, "Effective connection"):
                    production.verify_target(self.target)
                mocked.cursor.assert_not_called()

    def test_live_identity_and_unsafe_roles_are_refused(self):
        row = ("synthetic_pca", 1234, "synthetic_runtime", "synthetic_runtime", 5678, "127.0.0.1",
               65432, self.target["postmaster_started_at"], 180000, "origin", ["public"], False, 65432)
        cases = [(("other", *row[1:]), []), ((*row[:3], "other", *row[4:]), []),
                 ((*row[:11], True, row[12]), []), (row, [("unsafe_inherited_role",)])]
        for live, unsafe in cases:
            with self.subTest(live=live, unsafe=unsafe), patch.object(production, "connection") as mocked:
                mocked.settings_dict = self.config
                mocked.connection.info = SimpleNamespace(hostaddr="127.0.0.1", dbname="synthetic_pca",
                                                          user="synthetic_runtime", port=65432)
                cursor = mocked.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = live
                cursor.fetchall.return_value = unsafe
                with self.assertRaises(ValueError):
                    production.verify_target(self.target)

    def unix_target(self):
        self.target.update(connection_mode="unix", configured_host="/var/run/postgresql",
                           server_address=None, server_port=None, socket_uid=110, socket_gid=115)
        self.config["HOST"] = self.target["configured_host"]
        return self.target

    def test_unix_libpq_redirections_and_options_fail_before_socket_or_connect(self):
        target = self.unix_target()
        for variable in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGSERVICE", "PGOPTIONS"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "other"}), \
                    patch.object(production, "connection") as mocked, \
                    patch.object(production.pca_protected_artifacts, "protected_postgresql_socket") as guard:
                mocked.settings_dict = self.config
                with self.assertRaises(ValueError):
                    production.require_target(target)
                guard.assert_not_called()
                mocked.ensure_connection.assert_not_called()
        with patch.object(production, "connection") as mocked:
            mocked.settings_dict = {**self.config, "OPTIONS": {"sslmode": "prefer", "hostaddr": "127.0.0.1"}}
            with self.assertRaises(ValueError):
                production.require_target(target)
            mocked.ensure_connection.assert_not_called()

    def test_tcp_localhost_and_numeric_loopback_keep_verified_support(self):
        for host, address in (("127.0.0.1", "127.0.0.1"), ("localhost", "127.0.0.1"), ("::1", "::1")):
            with self.subTest(host=host), patch.object(production, "connection") as mocked:
                self.target.update(configured_host=host, server_address=address)
                self.config["HOST"] = host
                mocked.settings_dict = self.config
                mocked.connection.info = SimpleNamespace(host=host, hostaddr=address, port=65432,
                                                          dbname=self.target["database"], user=self.target["role"])
                cursor = mocked.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = (self.target["database"], 1234, self.target["role"], self.target["role"],
                                                5678, address, 65432, self.target["postmaster_started_at"],
                                                180000, "origin", ["public"], False, 65432)
                cursor.fetchall.return_value = []
                production.verify_target(self.target)

    def test_unix_target_requires_complete_transport_evidence_before_connect(self):
        target = self.unix_target()
        changes = ({"configured_host": "/tmp/postgresql"}, {"server_address": "127.0.0.1"},
                   {"server_port": 65432}, {"socket_uid": None}, {"socket_gid": 0},
                   {"connection_mode": "other"}, {"configured_port": "65433"},
                   {"role": "other"}, {"database": "other"}, {"machine_id": "b" * 32})
        for change in changes:
            with self.subTest(change=change), patch.object(production, "connection") as mocked:
                mocked.settings_dict = self.config
                with self.assertRaises(ValueError):
                    production.require_target({**target, **change})
                mocked.ensure_connection.assert_not_called()
        with patch.object(production, "connection") as mocked, \
                patch.object(production.pca_protected_artifacts, "protected_postgresql_socket",
                             side_effect=ValueError("Unsafe PostgreSQL socket")):
            mocked.settings_dict = self.config
            with self.assertRaisesMessage(ValueError, "Unsafe PostgreSQL socket"):
                production.require_target(target)
            mocked.ensure_connection.assert_not_called()

    def test_unix_backup_keeps_socket_bound_and_forwards_snapshot_without_hostaddr(self):
        target = self.unix_target()
        plan = {"ready": True, "blockers": [], "operation": merge.OPERATION, "organization_id": 1,
                "counts": {"duplicates": 18, "moved": 92, "untouched": 2}}
        document = production.envelope(target, plan)
        active = []

        @contextmanager
        def socket_guard(*args):
            active.append(args)
            try:
                yield
            finally:
                active.pop()

        def archive(database, reviewed_target, approved_hash, output_dir, **kwargs):
            self.assertTrue(active)
            self.assertEqual(database["HOST"], target["configured_host"])
            self.assertEqual(database["PORT"], target["configured_port"])
            self.assertEqual(reviewed_target, target)
            self.assertEqual(kwargs, {"snapshot": "synthetic-exported-snapshot", "hostaddr": None})
            return {"archive": {"path": "/srv/private/archive.dump"}}

        with patch.object(production, "connection") as mocked, \
                patch.object(production, "verify_target") as verify, \
                patch.object(production.transaction, "atomic"), \
                patch.object(merge, "_authorize", return_value=(object(), object())), \
                patch.object(merge, "_make_plan", return_value=plan), \
                patch.object(production.pca_protected_artifacts, "protected_postgresql_socket", socket_guard), \
                patch.object(production.pca_protected_artifacts, "protected_directory"), \
                patch.object(production.pca_protected_artifacts, "validate_file"), \
                patch.object(database_backup, "create_evidence", side_effect=archive):
            mocked.settings_dict = self.config
            mocked.in_atomic_block = False
            mocked.cursor.return_value.__enter__.return_value.fetchone.return_value = ("synthetic-exported-snapshot",)
            production.backup_plan(1, actor=SimpleNamespace(pk=1), document=document,
                                   approved_hash=merge.fingerprint(document), output_dir="/srv/private")
            self.assertEqual([call.kwargs for call in verify.call_args_list],
                             [{}, {"in_snapshot": True}, {"in_snapshot": True}])
        self.assertEqual(active, [])

    def test_qa_backup_does_not_revalidate_read_committed_inside_snapshot(self):
        target = self.unix_target()
        plan = {"ready": True, "blockers": [], "operation": merge.OPERATION, "organization_id": 1,
                "counts": {"duplicates": 18, "moved": 92, "untouched": 2}}
        document = production.envelope(target, plan)
        state = {"isolation": "read committed"}
        checks = []

        def qa_verifier():
            checks.append(state["isolation"])
            if state["isolation"] != "read committed":
                raise ValueError("QA verifier requires READ COMMITTED")

        def execute(sql, *args):
            if "REPEATABLE READ" in sql:
                state["isolation"] = "repeatable read"

        with patch.object(production, "connection") as mocked,                 patch.object(production, "require_target", return_value=True),                 patch.object(production, "_is_disposable_qa", return_value=True),                 patch.object(production, "socket_guard", return_value=nullcontext()),                 patch.object(production, "_verify_live_target") as live,                 patch.object(production.transaction, "atomic", return_value=nullcontext()),                 patch.object(merge, "_verify_connection", side_effect=qa_verifier),                 patch.object(merge, "_authorize", return_value=(object(), object())),                 patch.object(merge, "_make_plan", return_value=plan),                 patch.object(database_backup, "create_evidence", return_value={"archive": {}}):
            mocked.in_atomic_block = False
            cursor = mocked.cursor.return_value.__enter__.return_value
            cursor.execute.side_effect = execute
            cursor.fetchone.return_value = ("snapshot",)
            production.backup_plan(1, actor=SimpleNamespace(pk=1), document=document,
                                   approved_hash=merge.fingerprint(document), output_dir="/synthetic")
        # QA is verified only before the snapshot; live identity is still rechecked inside it.
        self.assertEqual(checks, ["read committed"])
        self.assertEqual(live.call_count, 3)

    def test_unix_effective_endpoint_and_live_identity_are_exact(self):
        target = self.unix_target()
        info = dict(host="/var/run/postgresql", hostaddr="", port=65432,
                    dbname=target["database"], user=target["role"])
        row = (target["database"], 1234, target["role"], target["role"], 5678, None, None,
               target["postmaster_started_at"], 180000, "origin", ["public"], False, 65432)
        cases = [(info, row, [])]
        cases += [({**info, **change}, row, []) for change in (
            {"host": "/run/other"}, {"hostaddr": "127.0.0.1"}, {"hostaddr": None},
            {"port": 65433}, {"dbname": "other"}, {"user": "other"})]
        cases += [(info, (*row[:index], value, *row[index + 1:]), [])
                  for index, value in ((0, "other"), (1, 99), (2, "other"), (3, "other"),
                                       (4, 99), (5, "127.0.0.1"), (6, 65432), (7, "other"),
                                       (8, 170000), (12, 65433))]
        cases.append((info, row, [("unsafe_role",)]))
        for index, (effective, live, unsafe) in enumerate(cases):
            with self.subTest(index=index), patch.object(production, "connection") as mocked, \
                    patch.object(production.pca_protected_artifacts, "protected_postgresql_socket") as guard:
                mocked.settings_dict = self.config
                mocked.connection.info = SimpleNamespace(**effective)
                cursor = mocked.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = live
                cursor.fetchall.return_value = unsafe
                if index == 0:
                    production.verify_target(target)
                    guard.assert_called_with("/var/run/postgresql", "65432", 110, 115)
                else:
                    with self.assertRaises(ValueError):
                        production.verify_target(target)

    def test_explicit_command_mode_is_required_before_reading_any_artifact(self):
        with patch.object(production, "read_artifact") as read:
            with self.assertRaises(CommandError):
                call_command("pca_production", "plan", organization_id=1, actor_id=1,
                             target="not-opened", plan_file="not-written")
            read.assert_not_called()


@skipUnless(connection.vendor == "postgresql", "Production workflow requires independent PostgreSQL QA, never SQLite.")
class ProductionWorkflowTests(PcaFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()  # The original fixture verifies the real disposable QA guard.
        self.machine = patch.object(production, "machine_id", return_value=MACHINE)
        self.machine.start()
        self.addCleanup(self.machine.stop)
        with connection.cursor() as cursor:
            cursor.execute(production.IDENTITY_SQL)
            row = cursor.fetchone()
        config = connection.settings_dict
        self.target = {"schema": "pca_target_v3", "connection_mode": "tcp", "socket_uid": None, "socket_gid": None,
                       "machine_id": MACHINE, "configured_host": config["HOST"],
                       "configured_port": str(config["PORT"]), "database": row[0], "database_oid": row[1],
                       "role": row[2], "role_oid": row[4], "server_address": row[5], "server_port": row[6],
                       "postmaster_started_at": row[7], "server_version_num": row[8]}
        directory = Path(__file__).resolve().parents[3] / ".local-docs"
        directory.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="pca-synthetic-", dir=directory)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        # Windows QA uses installed PostgreSQL binaries; production paths stay fixed.
        binaries = os.environ.get("POLYPBASE_PCA_QA_PG_BIN")
        if binaries:
            original = database_backup._run

            def installed_binary(command, environment):
                command = [str(Path(binaries) / (Path(command[0]).name + ".exe")), *command[1:]]
                return original(command, environment)

            self.tools = patch.object(database_backup, "_run", side_effect=installed_binary)
            self.tools.start()
            self.addCleanup(self.tools.stop)

    def production_plan(self):
        return production.build_plan(1, actor=self.admin, target=self.target)

    def backup(self, document):
        return production.backup_plan(1, actor=self.admin, document=document,
                                      approved_hash=merge.fingerprint(document), output_dir=self.directory)

    def production_apply(self, document, evidence, **overrides):
        digest = merge.fingerprint(document)
        options = {"actor": self.admin, "document": document, "approved_hash": digest,
                   "backup_evidence": evidence, "review_ref": "independent-review/PCA-1",
                   "authorization": f"APPLY_PCA_ORGANIZATION_1:{digest}"}
        options.update(overrides)
        return production.apply_plan(1, **options)

    def test_real_read_only_plan_is_deterministic_and_preserves_every_table(self):
        before = self.database_snapshot()
        original = merge._make_plan

        def readonly(organization):
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation')")
                self.assertEqual(cursor.fetchone(), ("on", "repeatable read"))
            return original(organization)

        with patch.object(merge, "_make_plan", side_effect=readonly):
            document = self.production_plan()
        self.assertEqual(document, self.production_plan())
        self.assertTrue(document["plan"]["ready"])
        self.assertEqual(document["expected_outcome"]["canonical_measurements"], 94)
        self.assertEqual(document["expected_outcome"]["evidence_retained_duplicates"], 18)
        self.assertEqual(before, self.database_snapshot())

    def test_real_archive_apply_and_idempotent_receipt_preserve_history(self):
        document = self.production_plan()
        before = document["plan"]["baseline"]
        evidence = self.backup(document)
        database_backup.validate_evidence(evidence, self.target, merge.fingerprint(document))
        receipt = self.production_apply(document, evidence)
        self.assertEqual(receipt.metadata["execution_evidence"]["backup"], evidence)
        self.assertEqual(receipt.user_id, self.admin.pk)
        self.assertEqual(BiologicalMeasurement.objects.filter(box_id=2135, duplicate_of__isnull=True).count(), 94)
        self.assertEqual(BiologicalMeasurement.objects.filter(duplicate_of__isnull=False).count(), 18)
        self.assertEqual(Box.objects.get(pk=2141).merged_into_id, 2135)
        for snapshot in before["measurements"]:
            current = merge.snapshot(BiologicalMeasurement.objects.get(pk=snapshot["id"]))
            for field in ("measured_on", "user_id", "original_observer_id", "imported_by_id",
                          "strobila_count", "polyp_count", "ephyrae_count"):
                self.assertEqual(current[field], snapshot[field])
        self.assertEqual(merge._rows(merge.BoxLocation.objects.filter(box_id__in=merge.BOX_IDS)), before["locations"])
        self.assertEqual(self.production_apply(document, evidence).pk, receipt.pk)
        self.assertEqual(AuditLog.objects.filter(object_type=merge.RECEIPT_TYPE).count(), 1)

    def test_authorization_hash_review_and_backup_fail_before_target_connection(self):
        document = self.production_plan()
        for overrides in ({"authorization": None}, {"review_ref": " "}, {"approved_hash": "a" * 64},
                          {"backup_evidence": "arbitrary-backup-reference"}):
            with self.subTest(overrides=overrides), patch.object(production, "verify_target") as verify:
                with self.assertRaises(ValueError):
                    self.production_apply(document, {}, **overrides)
                verify.assert_not_called()

    def test_postbackup_drift_aborts_without_partial_changes(self):
        document = self.production_plan()
        evidence = self.backup(document)
        Box.objects.filter(pk=2135).update(notes="Concurrent correction")
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.production_apply(document, evidence)
        self.assertEqual(before, self.database_snapshot())

    def test_archive_corruption_is_refused_before_database_connection(self):
        document = self.production_plan()
        evidence = self.backup(document)
        with Path(evidence["archive"]["path"]).open("ab") as stream:
            stream.write(b"corrupt")
        with patch.object(production, "verify_target") as verify:
            with self.assertRaises(ValueError):
                self.production_apply(document, evidence)
            verify.assert_not_called()

    def test_nonadmin_foreign_scope_and_changed_server_identity_refused(self):
        with self.assertRaises(PermissionDenied):
            production.build_plan(1, actor=self.writer, target=self.target)
        with self.assertRaises(ValueError):
            production.build_plan(2, actor=self.admin, target=self.target)
        with self.assertRaisesMessage(ValueError, "Live database identity"):
            production.build_plan(1, actor=self.admin, target={**self.target, "database_oid": 9999999})

    def test_stale_plan_cannot_issue_backup_evidence(self):
        document = self.production_plan()
        Box.objects.filter(pk=2141).update(notes="New evidence")
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.backup(document)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_late_receipt_failure_rolls_back_production_apply(self):
        document = self.production_plan()
        evidence = self.backup(document)
        before = self.database_snapshot()
        create = AuditLog.objects.create

        def fail_receipt(**values):
            if values.get("object_type") == merge.RECEIPT_TYPE:
                raise ValueError("Synthetic receipt failure")
            return create(**values)

        with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesMessage(ValueError, "Synthetic receipt failure"):
                self.production_apply(document, evidence)
        self.assertEqual(before, self.database_snapshot())

    def test_command_requires_separate_actions_and_explicit_artifacts(self):
        target_file = self.directory / "target.json"
        plan_file = self.directory / "plan.json"
        evidence_file = self.directory / "backup.json"
        production.write_artifact(target_file, self.target)
        common = {"production": True, "organization_id": 1, "actor_id": self.admin.pk,
                  "target": str(target_file), "plan_file": str(plan_file)}
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
                     backup_evidence=str(evidence_file), review_ref="independent-review/PCA-1",
                     authorization=f"APPLY_PCA_ORGANIZATION_1:{digest}", **common)
        self.assertEqual(AuditLog.objects.filter(object_type=merge.RECEIPT_TYPE).count(), 1)
