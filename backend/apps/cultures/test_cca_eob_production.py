"""Synthetic CCA production-boundary proofs; never read production artifacts.

Database tests require the leased historical_temperature_pgqa_settings PG18
harness. Fixtures/cleanup use its owner; workflows use its real runtime login.
Only source loaders and root-document/protected-archive IO are simulated, not
connection verification, authorization, deployed guards, planning or writes.
Approvals are imported separately through a real owner session. The leased
workflow table may be owner-owned; exact postgres ownership and production ACLs
are covered by test_cca_eob_role_provisioning, not weakened by this analogue.
"""

import json
import os
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from io import StringIO
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from config import cca_eob_database_role as role_contract
from django.conf import settings
from django.contrib.auth import get_user_model

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
)
from apps.measurements.test_historical_temperature_plan import evidence
from apps.organizations.models import Organization

from .historical_2026 import cca_eob_correction as correction
from .historical_2026 import cca_eob_production as production
from .management.commands import cca_eob_production as production_command
from .models import Box, BoxLocation, BoxMovement, ThermalZone
from .test_cca_eob_correction import CcaEobFixtureMixin, json_snapshot


MACHINE = "a" * 32
_DEFAULT_RELEASE = object()
_ENABLED = (
    settings.SETTINGS_MODULE == "config.historical_temperature_pgqa_settings"
    and getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
    and os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
    and os.getenv("POLYPBASE_HISTORICAL_POSTGRES_QA") == "1"
)


def socket_target():
    """A fabricated attestation for mock-only boundary tests, not an artifact."""
    return {
        "schema": "cca_eob_target_v2", "machine_id": MACHINE,
        "configured_host": "/var/run/postgresql", "configured_port": "5433",
        "database": "polypbase", "role": role_contract.ROLE, "database_oid": 1234,
        "role_oid": 5678, "server_address": None, "server_port": None,
        "postmaster_started_at": "2026-01-01T00:00:00.000000Z", "server_version_num": 180000,
        "socket_uid": 110, "socket_gid": 115,
    }


def synthetic_release(document):
    return {
        "schema": "cca_eob_release_v1", "target": deepcopy(document["target"]),
        "plan_hash": correction.fingerprint(document), "actor_id": document["actor_id"],
        "archive": {"path": "/synthetic-qa/never-created.dump", "sha256": "b" * 64, "size_bytes": 123},
        "restoration_verified": True, "restore_report": "synthetic-full-restore-report",
        "review_ref": "synthetic-independent-review",
    }


def malformed_releases(document):
    """Shared syntax cases for Python and independently seeded SQL approvals."""
    release = synthetic_release(document)
    for value in (None, [], "release", True):
        yield f"release type {value!r}", value
    for key in release:
        candidate = deepcopy(release)
        del candidate[key]
        yield f"missing release {key}", candidate
        yield f"null release {key}", {**deepcopy(release), key: None}
    for change in (
        {"extra": True}, {"restoration_verified": 1}, {"restoration_verified": "true"},
        {"restore_report": "\t "}, {"review_ref": "\n"}, {"actor_id": True},
    ):
        yield f"release fields {change!r}", {**deepcopy(release), **change}
    archive = release["archive"]
    for value in ([], "archive", True):
        yield f"archive type {value!r}", {**deepcopy(release), "archive": value}
    for key in archive:
        candidate = deepcopy(archive)
        del candidate[key]
        yield f"missing archive {key}", {**deepcopy(release), "archive": candidate}
    for key, values in (
        ("path", (None, True, 123, "", "relative.dump", "/", "//qa/archive.dump",
                  "/qa//archive.dump", "/qa/./archive.dump", "/qa/../archive.dump", "/qa/.", "/qa/..",
                  "/qa/archive.dump/", "/qa\\archive.dump", "/qa/archive\ndump",
                  "/qa/archive\tdump", "/qa/archive\x01dump", "/qa/archive\x7fdump")),
        ("sha256", (None, True, 123, "", "b" * 63, "b" * 65, "B" * 64,
                    "g" * 64, "b" * 64 + "\n")),
        ("size_bytes", (None, True, False, "123", 0, -1, 1.0, 1.5, 123.0, 9223372036854775808)),
    ):
        for value in values:
            yield f"archive {key}={value!r}", {
                **deepcopy(release), "archive": {**archive, key: value},
            }
    yield "extra archive field", {**deepcopy(release), "archive": {**archive, "extra": True}}


@override_settings(SETTINGS_MODULE=role_contract.SETTINGS, DEBUG=False, DATABASE_ROUTERS=[])
class ProductionBoundaryTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.target = socket_target()
        self.config = {
            "ENGINE": "django.db.backends.postgresql", "HOST": "/var/run/postgresql",
            "PORT": "5433", "NAME": "polypbase", "USER": role_contract.ROLE,
            "OPTIONS": {"sslmode": "disable"},
        }
        self.enterContext(self.settings(DATABASES={"default": self.config}, SETTINGS_MODULE=role_contract.SETTINGS))
        self.enterContext(patch.object(production, "runtime_identity", return_value=role_contract.ROLE))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch.object(production, "machine_id", return_value=MACHINE))
        self.root = self.enterContext(patch.object(production, "_root_document", return_value=self.target))
        self.db = self.enterContext(patch.object(production, "connection"))
        self.db.settings_dict = self.config
        self.db.in_atomic_block = False
        self.db.connection.info = SimpleNamespace(
            host="/var/run/postgresql", port=5433, dbname="polypbase", user=role_contract.ROLE, hostaddr=None,
        )
        # The dedicated protected-socket verifier has its own filesystem tests.
        self.socket = self.enterContext(patch.object(production.socket_artifacts, "protected_postgresql_socket"))

    def assert_preconnect_rejection(self, target=None):
        self.db.reset_mock()
        with self.assertRaises(ValueError):
            production.verify_target(self.target if target is None else target)
        self.db.ensure_connection.assert_not_called()
        self.db.cursor.assert_not_called()

    def test_pinned_root_attestation_and_exact_socket_configuration(self):
        self.assertFalse(production.require_target(self.target))
        self.assertEqual(production.read_target(production.TARGET_PATH), self.target)
        self.root.assert_called_with(production.TARGET_PATH)
        self.root.reset_mock()
        with self.assertRaisesMessage(ValueError, "pinned root-owned"):
            production.read_target("/synthetic-qa/self-declared.json")
        self.root.assert_not_called()
        self.db.ensure_connection.assert_not_called()
        self.db.cursor.assert_not_called()

    def test_missing_malformed_or_self_declared_attestations_fail_preconnect(self):
        self.assert_preconnect_rejection({})
        for change in (
            {"schema": "cca_eob_target_v0"}, {"database_oid": True}, {"role_oid": 0},
            {"postmaster_started_at": "2026-01-01"}, {"machine_id": "invalid"},
            {"database_oid": 9999}, {"machine_id": "b" * 32}, {"extra": "unreviewed"},
        ):
            with self.subTest(change=change):
                self.assert_preconnect_rejection({**self.target, **change})
        self.root.side_effect = ValueError("Unsafe root document")
        with self.assertRaisesMessage(ValueError, "Unsafe root document"):
            production.verify_target(self.target)
        self.db.ensure_connection.assert_not_called()

    def test_libpq_environment_options_and_wrong_settings_fail_preconnect(self):
        for variable in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPASSWORD"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "synthetic"}):
                self.assert_preconnect_rejection()
        for change in (
            {"OPTIONS": {"sslmode": "prefer"}}, {"OPTIONS": {"service": "other"}},
            {"OPTIONS": {"sslmode": "disable", "hostaddr": "192.0.2.1"}},
            {"HOST": "127.0.0.1"}, {"PORT": "5432"}, {"NAME": "other"}, {"USER": "postgres"},
            {"ENGINE": "django.db.backends.sqlite3"},
        ):
            with self.subTest(change=change):
                self.db.settings_dict = {**self.config, **change}
                self.assert_preconnect_rejection()
        self.db.settings_dict = self.config
        for changes in (
            {"DEBUG": True}, {"SETTINGS_MODULE": "config.test_settings"},
            {"DATABASE_ROUTERS": [object()]},
            {"DATABASES": {"default": self.config, "other": self.config}},
        ):
            with self.subTest(settings=changes), self.settings(**changes):
                self.assert_preconnect_rejection()

    def test_even_root_attested_tcp_or_non_pg18_target_is_rejected(self):
        for change in (
            {"configured_host": "127.0.0.1"}, {"configured_port": "5432"},
            {"server_address": "127.0.0.1", "server_port": 5433}, {"server_version_num": 170000},
        ):
            with self.subTest(change=change):
                target = {**self.target, **change}
                self.root.return_value = target
                self.assert_preconnect_rejection(target)

    def test_effective_socket_database_role_and_port_are_checked_before_sql(self):
        original = vars(self.db.connection.info).copy()
        for change in (
            {"host": "localhost"}, {"hostaddr": "127.0.0.1"}, {"port": 5432},
            {"dbname": "other"}, {"user": "postgres"},
        ):
            with self.subTest(change=change):
                self.db.connection.info = SimpleNamespace(**{**original, **change})
                with self.assertRaises(ValueError):
                    production.verify_target(self.target)
                self.db.cursor.assert_not_called()

    def test_all_live_identity_session_fields_and_inherited_privileges_are_checked(self):
        row = (
            "polypbase", 1234, role_contract.ROLE, role_contract.ROLE, 5678, None, None,
            self.target["postmaster_started_at"], 180000, "origin", ["public"], False,
        )
        cursor = self.db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = row
        cursor.fetchall.return_value = []
        production.verify_target(self.target)
        for index, replacement in enumerate((
            "other", 99, "other", "other", 99, "127.0.0.1", 5433,
            "2026-02-01T00:00:00.000000Z", 180001, "replica", ["other"], True,
        )):
            with self.subTest(index=index):
                changed = list(row)
                changed[index] = replacement
                cursor.fetchone.return_value = changed
                with self.assertRaisesMessage(ValueError, "Live PostgreSQL identity"):
                    production.verify_target(self.target)
        cursor.fetchone.return_value = row
        for role in ("unsafe_superuser", "unsafe_createdb", "unsafe_createrole", "unsafe_replication",
                     "unsafe_bypassrls", "pg_read_all_data"):
            with self.subTest(role=role):
                cursor.fetchall.return_value = [(role,)]
                with self.assertRaisesMessage(ValueError, "Unsafe PostgreSQL privileges"):
                    production.verify_target(self.target)
        sql = next(call.args[0] for call in cursor.execute.call_args_list if "rolsuper" in call.args[0])
        for attribute in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls", "pg_database_owner"):
            self.assertIn(attribute, sql)

    def test_set_reachable_nonowner_create_authority_is_rejected(self):
        cursor = self.db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (
            "polypbase", 1234, role_contract.ROLE, role_contract.ROLE, 5678, None, None,
            self.target["postmaster_started_at"], 180000, "origin", ["public"], False,
        )
        # No privileged role attributes or ownership: the non-inherited SET role
        # can still carry CREATE authority. The catalog query must test that role.
        cursor.fetchall.side_effect = [[], [("database/schema authority",)]]
        with self.assertRaisesMessage(ValueError, "Runtime must not own or assume ownership"):
            production.verify_target(self.target)
        sql = next(call.args[0] for call in cursor.execute.call_args_list if "database/schema" in call.args[0])
        self.assertIn("FROM pg_roles WHERE pg_has_role(current_user, oid, 'SET')", sql)
        self.assertIn("has_database_privilege(oid, current_database(), 'CREATE')", sql)
        self.assertIn("has_schema_privilege(oid, 'public', 'CREATE')", sql)


    def identity_cursor(self):
        cursor = self.db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (
            "polypbase", 1234, role_contract.ROLE, role_contract.ROLE, 5678, None, None,
            self.target["postmaster_started_at"], 180000, "origin", ["public"], False,
        )
        cursor.fetchall.return_value = []
        return cursor

    def test_socket_owner_must_be_root_attested_and_is_passed_exactly(self):
        for change in (
            {"socket_uid": None}, {"socket_gid": None}, {"socket_uid": 0}, {"socket_gid": -1},
            {"socket_uid": True}, {"socket_gid": "115"}, {"socket_uid": 110.0},
        ):
            with self.subTest(change=change):
                self.assert_preconnect_rejection({**self.target, **change})
        for missing in ("socket_uid", "socket_gid"):
            with self.subTest(missing=missing):
                self.assert_preconnect_rejection({k: v for k, v in self.target.items() if k != missing})
        self.socket.assert_not_called()
        self.identity_cursor()
        production.verify_target(self.target)
        self.socket.assert_called_with("/var/run/postgresql", "5433", 110, 115)

    def test_connection_and_every_identity_query_happen_inside_the_socket_window(self):
        events = []

        class Window:
            def __enter__(self):
                events.append("enter")

            def __exit__(self, *exc):
                events.append("exit")

        self.socket.side_effect = lambda *args: Window()
        cursor = self.identity_cursor()
        self.db.ensure_connection.side_effect = lambda: events.append("connect")
        cursor.execute.side_effect = lambda sql, *a: events.append("sql")
        production.verify_target(self.target)
        self.assertEqual(events[0], "enter")
        self.assertEqual(events[-1], "exit")
        self.assertEqual(events.count("enter"), 1)
        self.assertLess(events.index("enter"), events.index("connect"))
        self.assertTrue(all(e in ("sql", "connect") for e in events[1:-1]))

    def test_tampered_socket_evidence_fails_closed_before_or_after_the_checks(self):
        from config.pca_protected_artifacts import ArtifactSecurityError

        self.identity_cursor()
        self.socket.side_effect = ArtifactSecurityError("Unsafe PostgreSQL socket directory")
        self.db.reset_mock()
        with self.assertRaisesMessage(ValueError, "Unsafe PostgreSQL socket directory"):
            production.verify_target(self.target)
        self.db.ensure_connection.assert_not_called()
        self.db.cursor.assert_not_called()

        class ChangedOnExit:
            def __enter__(self):
                pass

            def __exit__(self, *exc):
                if exc[0] is None:
                    raise ArtifactSecurityError("PostgreSQL socket endpoint changed or was replaced")

        self.socket.side_effect = lambda *args: ChangedOnExit()
        with self.assertRaisesMessage(ValueError, "endpoint changed or was replaced"):
            production.verify_target(self.target)

    def test_role_level_settings_and_extra_schema_function_or_large_object_authority_are_rejected(self):
        cursor = self.identity_cursor()
        production.verify_target(self.target)
        sql = next(call.args[0] for call in cursor.execute.call_args_list if "pg_db_role_setting" in call.args[0])
        for fragment in ("pg_db_role_setting", "pg_largeobject_metadata", "lo_compat_privileges",
                         "has_schema_privilege", "prosecdef", "has_function_privilege"):
            self.assertIn(fragment, sql)
        cursor.fetchall.side_effect = [[], [], [("role-level setting",)]]
        with self.assertRaisesMessage(ValueError, "role-level settings"):
            production.verify_target(self.target)


class RootDocumentTests(SimpleTestCase):
    def test_pure_release_validator_rejects_malformed_metadata_without_io(self):
        document = {"target": socket_target(), "actor_id": 7, "synthetic": True}
        digest = correction.fingerprint(document)
        for label, release in malformed_releases(document):
            with self.subTest(label=label), self.assertRaises(ValueError):
                role_contract.validate_release(release, document, digest)
        for size in (1, 9223372036854775807):
            release = synthetic_release(document)
            release["archive"]["size_bytes"] = size
            role_contract.validate_release(release, document, digest)

    def test_root_release_rejects_malformed_metadata_before_any_archive_io(self):
        document = {"target": socket_target(), "actor_id": 7, "synthetic": True}
        digest = correction.fingerprint(document)
        with patch.object(production, "_root_document") as root, \
                patch.object(production.artifacts, "protected_file") as protected, \
                patch.object(production.database_backup, "verify_archive") as archive:
            for label, release in malformed_releases(document):
                with self.subTest(label=label):
                    root.return_value = release
                    with self.assertRaises(ValueError):
                        production._release(document, digest)
                    protected.assert_not_called()
                    archive.assert_not_called()

    def test_root_document_delegates_to_strict_root_only_reader(self):
        with patch.object(production.artifacts, "assert_unprivileged_runtime") as runtime, \
                patch.object(production.artifacts, "_read_json", return_value={"synthetic": True}) as read:
            self.assertEqual(production._root_document(production.RELEASE_PATH), {"synthetic": True})
            runtime.assert_called_once_with()
            read.assert_called_once_with(production.RELEASE_PATH, {0}, False)
        with patch.object(production.artifacts, "assert_unprivileged_runtime", side_effect=ValueError("root runtime")), \
                patch.object(production.artifacts, "_read_json") as read:
            with self.assertRaisesMessage(ValueError, "root runtime"):
                production._root_document(production.TARGET_PATH)
            read.assert_not_called()

    def test_release_requires_literal_true_restoration_and_exact_root_bound_review(self):
        document = {"target": socket_target(), "actor_id": 7, "synthetic": True}
        digest = correction.fingerprint(document)
        release = synthetic_release(document)
        with patch.object(production, "_root_document", return_value=release) as root, \
                patch.object(production.artifacts, "protected_file") as protected, \
                patch.object(production.database_backup, "verify_archive") as archive:
            self.assertEqual(production._release(document, digest), release)
            root.assert_called_once_with(production.RELEASE_PATH)
            protected.assert_called_once_with(release["archive"]["path"])
            archive.assert_called_once_with(release["archive"])
            for change in (
                {"restoration_verified": False}, {"restoration_verified": 1}, {"restoration_verified": "true"},
                {"plan_hash": "c" * 64}, {"actor_id": 8}, {"target": {}}, {"schema": "other"},
                {"restore_report": " "}, {"review_ref": ""}, {"archive": {}}, {"extra": True},
            ):
                with self.subTest(change=change):
                    root.return_value = {**release, **change}
                    protected.reset_mock()
                    archive.reset_mock()
                    with self.assertRaisesMessage(ValueError, "full restoration evidence"):
                        production._release(document, digest)
                    protected.assert_not_called()
                    archive.assert_not_called()
            root.return_value = release
            protected.side_effect = ValueError("Unsafe archive ownership")
            with self.assertRaisesMessage(ValueError, "Unsafe archive ownership"):
                production._release(document, digest)
            archive.assert_not_called()
            protected.side_effect = None
            archive.side_effect = ValueError("Archive digest mismatch")
            with self.assertRaisesMessage(ValueError, "Archive digest mismatch"):
                production._release(document, digest)


class ProductionCommandTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.target = socket_target()
        self.document = {
            "schema": production.SCHEMA, "target": self.target, "actor_id": 7,
            "plan": {"correction": {"ready": True, "counts": {correction.MOVE: 3}, "move_order": [1, 2, 3]}},
        }
        # Command-routing tests only: no database, root files or archive tools.
        self.root = self.enterContext(patch.object(production, "_root_document", return_value=self.target))
        self.require = self.enterContext(patch.object(production, "require_target"))
        self.build = self.enterContext(patch.object(production, "build_plan", return_value=self.document))
        self.apply = self.enterContext(patch.object(production, "apply_plan", return_value={"move_order": [1, 2, 3]}))
        self.read = self.enterContext(patch.object(production_command.artifacts, "read_json", return_value=self.document))
        self.write = self.enterContext(patch.object(production_command.artifacts, "write_json"))
        self.release = self.enterContext(patch.object(production, "_release"))

    def command(self, action, *extra):
        output = StringIO()
        call_command(
            "cca_eob_production", action, "--organization-id", "1", "--actor-id", "7",
            "--target", production.TARGET_PATH, "--workbook", "synthetic.xlsx",
            "--plan-file", "/synthetic-qa/plan.json", *extra, stdout=output,
        )
        return json.loads(output.getvalue())

    def assert_no_artifacts_or_execution(self):
        for mocked in (self.root, self.require, self.read, self.write, self.build, self.apply, self.release):
            mocked.assert_not_called()

    def test_explicit_production_flag_is_required_before_artifact_io(self):
        for action in ("plan", "apply"):
            with self.subTest(action=action), self.assertRaisesMessage(CommandError, "explicit --production"):
                self.command(action)
            self.assert_no_artifacts_or_execution()

    def test_apply_options_are_forbidden_on_plan_even_when_empty(self):
        for extra in (("--expected-plan-hash", "a" * 64), ("--authorization", "deliberate"),
                      ("--expected-plan-hash", ""), ("--authorization", "")):
            with self.subTest(extra=extra), self.assertRaisesMessage(CommandError, "forbidden during read-only planning"):
                self.command("plan", "--production", *extra)
            self.assert_no_artifacts_or_execution()

    def test_apply_requires_both_deliberate_options_before_artifact_io(self):
        for extra in ((), ("--expected-plan-hash", "a" * 64), ("--authorization", "deliberate")):
            with self.subTest(extra=extra), self.assertRaisesMessage(CommandError, "reviewed hash and deliberate"):
                self.command("apply", "--production", *extra)
            self.assert_no_artifacts_or_execution()

    def test_plan_reads_pinned_root_target_writes_private_plan_and_never_applies(self):
        result = self.command("plan", "--production")
        self.root.assert_called_once_with(production.TARGET_PATH)
        self.require.assert_called_once_with(self.target)
        self.build.assert_called_once_with(
            organization_id=1, actor=SimpleNamespace(pk=7), workbook_path="synthetic.xlsx", target=self.target,
        )
        self.write.assert_called_once_with("/synthetic-qa/plan.json", self.document)
        self.assertEqual(result["plan_hash"], correction.fingerprint(self.document))
        self.read.assert_not_called()
        self.apply.assert_not_called()
        self.release.assert_not_called()

    def test_apply_reads_reviewed_artifact_without_building_or_overwriting_plan(self):
        digest = correction.fingerprint(self.document)
        authorization = f"APPLY_CCA_EOB_ORGANIZATION_1:{digest}"
        result = self.command("apply", "--production", "--expected-plan-hash", digest,
                              "--authorization", authorization)
        self.root.assert_called_once_with(production.TARGET_PATH)
        self.require.assert_called_once_with(self.target)
        self.read.assert_called_once_with("/synthetic-qa/plan.json")
        self.apply.assert_called_once_with(
            organization_id=1, actor=SimpleNamespace(pk=7), workbook_path="synthetic.xlsx",
            document=self.document, approved_hash=digest, authorization=authorization,
        )
        self.assertEqual(result, {"applied": 3, "production_plan_hash": digest})
        self.build.assert_not_called()
        self.write.assert_not_called()

    def test_wrong_target_or_untrusted_root_artifact_cannot_reach_apply(self):
        self.read.return_value = {**self.document, "target": {}}
        with self.assertRaisesMessage(CommandError, "independent target attestation"):
            self.command("apply", "--production", "--expected-plan-hash", "a" * 64,
                         "--authorization", "deliberate")
        self.apply.assert_not_called()
        self.root.side_effect = ValueError("Unsafe root target document")
        with self.assertRaisesMessage(CommandError, "Unsafe root target document"):
            self.command("plan", "--production")
        self.build.assert_not_called()
        self.write.assert_not_called()
        self.release.assert_not_called()


def live_target():
    """Attest the current guarded login without copying or exposing credentials."""
    with connection.cursor() as cursor:
        cursor.execute(production.IDENTITY_SQL)
        row = cursor.fetchone()
    config = connection.settings_dict
    return {
        "schema": "cca_eob_target_v2", "machine_id": MACHINE,
        "configured_host": config["HOST"], "configured_port": str(config["PORT"]),
        "database": row[0], "database_oid": row[1], "role": row[2], "role_oid": row[4],
        "server_address": row[5], "server_port": row[6],
        "postmaster_started_at": row[7], "server_version_num": row[8],
        # The disposable QA backend is TCP-only: there is no socket directory to attest.
        "socket_uid": None, "socket_gid": None,
    }


def runtime_workflow(method):
    @wraps(method)
    def run(self):
        from config import historical_temperature_pgqa as guard

        with guard.runtime_role():
            guard.require_run_lease()
            with connection.cursor() as cursor:
                cursor.execute(production.IDENTITY_SQL)
                row = cursor.fetchone()
            self.assertEqual((row[2], row[3]), (guard.RUNTIME, guard.RUNTIME))
            self.target = live_target()
            self.assertTrue(production._qa())  # Real leased harness, never patched.
        # Fixture mutations and whole-state assertions use the owner. Only the
        # production entry points below open the dedicated runtime login.
        return method(self)
    return run


@skipUnless(_ENABLED, "Requires the explicitly authorized, leased disposable PG18 harness")
class ProductionWorkflowTests(CcaEobFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def _fixture_teardown(self):
        from config import historical_temperature_pgqa as guard

        # Ordinary TransactionTestCase TRUNCATE violates immutable-history guards.
        guard.require_owner(connection)
        guard.require_run_lease()
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {role_contract.APPROVAL} RESTRICT")
        guard.cleanup_test_data()

    @contextmanager
    def owner_session(self):
        """Independent leased owner connection, also safe while runtime holds locks."""
        from config import historical_temperature_pgqa as guard

        guard.require_run_lease()
        owner = connection.copy(alias="cca_eob_fixture_owner")
        owner.settings_dict.update(USER=guard.OWNER, PASSWORD=guard.CREDENTIALS[guard.OWNER])
        try:
            guard.assert_disposable(owner)
            yield owner
        finally:
            owner.close()

    @contextmanager
    def production_runtime(self):
        from config import historical_temperature_pgqa as guard

        if connection.settings_dict["USER"] == guard.RUNTIME:
            yield
        else:
            with guard.runtime_role():
                yield

    def database_snapshot(self):
        # Approval consumption is checked separately; scientific/account/audit
        # whole-state comparisons must not need runtime SELECT privileges.
        with self.owner_session() as owner, owner.cursor() as cursor:
            result = {}
            for table in owner.introspection.table_names(cursor):
                if table == role_contract.APPROVAL.split(".")[-1]:
                    continue
                cursor.execute(f"SELECT * FROM {owner.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def install_boundary(self):
        from config import historical_temperature_pgqa as guard
        from psycopg import sql

        guard.require_owner(connection)
        # Workflow-only analogue. Real postgres ownership and exact production
        # ACLs are proven in test_cca_eob_role_provisioning, not relaxed there.
        with connection.cursor() as cursor:
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS {role_contract.APPROVAL} (
                singleton boolean PRIMARY KEY CHECK (singleton),
                plan_hash text NOT NULL, document jsonb NOT NULL, release jsonb NOT NULL,
                expires_at timestamptz NOT NULL, consumed_at timestamptz,
                final_authorization jsonb, final_authorized_at timestamptz,
                final_expires_at timestamptz)""")
            cursor.execute(f"COMMENT ON TABLE {role_contract.APPROVAL} IS '{role_contract.APPROVAL_MARKER}'")
            cursor.execute(sql.SQL("REVOKE ALL ON {} FROM PUBLIC, {}").format(
                sql.Identifier("public", "cca_eob_approval_v1"), sql.Identifier(guard.RUNTIME)))
            cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(
                sql.Identifier(guard.RUNTIME)))
            cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(
                sql.Identifier(guard.RUNTIME)))
            cursor.execute(role_contract.grants(guard.RUNTIME))
            cursor.execute(role_contract.boundary_definition(guard.RUNTIME).replace(
                "OWNER TO polypbase;", f"OWNER TO {role_contract.identifier(guard.OWNER)};"))

    def seed_approval(self, document, *, release=_DEFAULT_RELEASE, expires_at=None, reviewed_hash=None):
        """Administrator import of independent reviewed JSON; never runtime approval."""
        reviewed_hash = reviewed_hash or correction.fingerprint(document)
        release = synthetic_release(document) if release is _DEFAULT_RELEASE else release
        expires_at = expires_at or datetime.now(timezone.utc) + timedelta(hours=1)
        with self.owner_session() as owner, owner.cursor() as cursor:
            cursor.execute(f"SELECT document, release FROM {role_contract.APPROVAL} WHERE singleton")
            existing = cursor.fetchone()
            if existing is not None:
                # Do not reset consumption or silently replace an approval.
                self.assertEqual(json.loads(existing[0]) if isinstance(existing[0], str) else existing[0], document)
                self.assertEqual(json.loads(existing[1]) if isinstance(existing[1], str) else existing[1], release)
                return
            cursor.execute(f"""INSERT INTO {role_contract.APPROVAL}
                (singleton, plan_hash, document, release, expires_at, consumed_at)
                VALUES (true, %s, %s::jsonb, %s::jsonb, %s, NULL)""",
                [reviewed_hash, json.dumps(document), json.dumps(release), expires_at])

    def seed_final_authorization(self, document, *, release=_DEFAULT_RELEASE, expires_at=None, reviewed_hash=None):
        """Separate trusted owner action; never refresh issued or consumed authority.

        Like seed_approval, this deliberately permits invalid fixture JSON so SQL
        validation can be tested independently of the administrator's Python gate.
        """
        release = synthetic_release(document) if release is _DEFAULT_RELEASE else release
        authorization = {
            "schema": "cca_eob_final_apply_v1",
            "plan_hash": reviewed_hash or correction.fingerprint(document),
            "document": document, "release": release,
        }
        with self.owner_session() as owner, owner.cursor() as cursor:
            cursor.execute(f"""UPDATE {role_contract.APPROVAL}
                SET final_authorization=%s::jsonb, final_authorized_at=clock_timestamp(),
                    final_expires_at=COALESCE(%s::timestamptz,
                        LEAST(expires_at, clock_timestamp()+interval '240 seconds'))
                WHERE singleton AND consumed_at IS NULL AND final_authorization IS NULL
                    AND final_authorized_at IS NULL AND final_expires_at IS NULL""",
                [json.dumps(authorization), expires_at])
            if cursor.rowcount != 1:
                raise ValueError("Final authorization is absent, already issued, or consumed.")

    def final_authorization_state(self):
        with self.owner_session() as owner, owner.cursor() as cursor:
            cursor.execute(f"""SELECT final_authorization, final_authorized_at, final_expires_at
                FROM {role_contract.APPROVAL} WHERE singleton""")
            return cursor.fetchone()

    def approval_consumed_at(self):
        with self.owner_session() as owner, owner.cursor() as cursor:
            cursor.execute(f"SELECT consumed_at FROM {role_contract.APPROVAL} WHERE singleton")
            row = cursor.fetchone()
            return row[0] if row else None

    def setUp(self):
        from config import historical_temperature_pgqa as guard

        guard.require_owner(connection)
        guard.require_run_lease()
        self.install_boundary()
        create_box = Box.objects.create
        create_org = Organization.objects.create

        def exact_box(**values):
            code = values.get("global_code")
            ids = {code: pk for pk, code in production.BOXES.items()}
            if code in ids:
                values["pk"] = ids[code]
            return create_box(**values)

        def exact_organization(**values):
            values["pk"] = {"cca-qa": 1, "other-cca-qa": 2}[values["slug"]]
            return create_org(**values)

        # Remap at insertion time: no FK rewrites or changes to shared fixtures.
        with patch.object(Box.objects, "create", side_effect=exact_box), \
                patch.object(Organization.objects, "create", side_effect=exact_organization):
            super().setUp()
        self.assertEqual(self.org.pk, 1)
        self.assertEqual({box.pk: box.global_code for box in self.boxes.values()}, production.BOXES)
        self.old_audit = AuditLog.objects.create(
            organization=self.org, user=self.writer, action="entry", object_type="box",
            object_id=self.boxes[5].global_code, metadata={"synthetic_legacy": True},
        )

    def production_plan(self, **overrides):
        values = {"actor": self.admin, "target": self.target, "workbook_path": self.workbook_path}
        values.update(overrides)
        with self.production_runtime():
            return production.build_plan(1, **values)

    @contextmanager
    def synthetic_release_io(self, document):
        """Test-only admin import followed by root/archive IO simulation.

        The reviewed document is generated before entering this context. SQL
        authorization is real; no production code auto-approves a runtime plan.
        """
        release = synthetic_release(document)
        self.seed_approval(document, release=release)
        if self.final_authorization_state() == (None, None, None):
            self.seed_final_authorization(document, release=release)
        with patch.object(production, "_root_document", return_value=release), \
                patch.object(production.artifacts, "protected_file"), \
                patch.object(production.database_backup, "verify_archive"):
            yield release

    def production_apply(self, reviewed_document, *, organization_id=1, **overrides):
        digest = correction.fingerprint(reviewed_document)
        values = {
            "actor": self.admin, "document": reviewed_document, "approved_hash": digest,
            "workbook_path": self.workbook_path,
            "authorization": f"APPLY_CCA_EOB_ORGANIZATION_1:{digest}",
        }
        values.update(overrides)
        with self.production_runtime():
            return production.apply_plan(organization_id, **values)

    def assert_production_blocked(self, reason):
        before = self.database_snapshot()
        document = self.production_plan()
        plan = document["plan"]["correction"]
        self.assertFalse(plan["ready"])
        self.assertTrue(any(reason in blocker for blocker in plan["blockers"]), plan["blockers"])
        with self.synthetic_release_io(document), self.assertRaises(ValueError):
            self.production_apply(document)
        self.assertEqual(before, self.database_snapshot())

    def assert_weakened_biology_guard_rejected(self, *, update="UPDATE", condition="", foreign=False):
        from config import historical_temperature_pgqa as guard
        from psycopg import sql

        guard.require_owner(connection)
        guard.require_run_lease()
        original_state = production._schema_state()
        before = self.database_snapshot()
        with connection.cursor() as cursor:
            cursor.execute("""SELECT pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid)
                FROM pg_trigger t WHERE t.tgrelid='public.measurements_biologicalmeasurement'::regclass
                AND t.tgname='historical_biology_guard'""")
            original_trigger, original_function = cursor.fetchone()
        schema_created = False
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                function = "public.historical_temperature_biology_guard"
                if foreign:
                    cursor.execute("CREATE SCHEMA cca_eob_trigger_qa")
                    function = "cca_eob_trigger_qa.historical_temperature_biology_guard"
                    cursor.execute(original_function.replace(
                        "public.historical_temperature_biology_guard", function, 1,
                    ))
                    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA cca_eob_trigger_qa TO {}").format(
                        sql.Identifier(guard.RUNTIME),
                    ))
                    cursor.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {}() TO {}").format(
                        sql.Identifier("cca_eob_trigger_qa", "historical_temperature_biology_guard"),
                        sql.Identifier(guard.RUNTIME),
                    ))
                cursor.execute("DROP TRIGGER historical_biology_guard ON public.measurements_biologicalmeasurement")
                # Only fixed test constants reach this DDL; no user input.
                cursor.execute(f"""CREATE TRIGGER historical_biology_guard BEFORE {update}
                    ON public.measurements_biologicalmeasurement FOR EACH ROW {condition}
                    EXECUTE FUNCTION {function}()""")
            schema_created = foreign

            @runtime_workflow
            def reject(test):
                # The binding check itself must reject the weakened trigger. A temporary
                # extra schema is additionally refused earlier by the authority check.
                with test.assertRaisesMessage(ValueError, "Historical trigger binding differs"):
                    production._schema_state()
                with test.assertRaises(ValueError):
                    test.production_plan()
                test.assertEqual(before, test.database_snapshot())

            reject(self)
        finally:
            # Restore on the real owner login even if runtime verification fails.
            guard.require_owner(connection)
            guard.require_run_lease()
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER IF EXISTS historical_biology_guard ON public.measurements_biologicalmeasurement")
                cursor.execute(original_trigger)
                if schema_created:
                    cursor.execute("DROP FUNCTION cca_eob_trigger_qa.historical_temperature_biology_guard()")
                    cursor.execute("DROP SCHEMA cca_eob_trigger_qa")
            guard.verify_schema(connection)
            self.assertEqual(original_state, production._schema_state())
        self.assertEqual(before, self.database_snapshot())

    def test_conditional_biology_guard_is_rejected_by_real_runtime_plan(self):
        self.assert_weakened_biology_guard_rejected(condition="WHEN (false)")

    def test_restricted_column_biology_guard_is_rejected_by_real_runtime_plan(self):
        self.assert_weakened_biology_guard_rejected(update="UPDATE OF polyp_count")

    def test_foreign_schema_biology_guard_is_rejected_by_real_runtime_plan(self):
        self.assert_weakened_biology_guard_rejected(foreign=True)

    def test_non_superuser_owner_is_rejected_from_real_catalog_metadata(self):
        from config import historical_temperature_pgqa as guard

        guard.require_owner(connection)
        guard.require_run_lease()
        with connection.cursor() as cursor:
            cursor.execute("""SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname=current_user""")
            self.assertEqual(cursor.fetchone(), (False,) * 5)
            cursor.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname='measurements_biologicalmeasurement'
                AND pg_has_role(current_user, c.relowner, 'MEMBER')""")
            self.assertEqual(cursor.fetchone(), (1,))
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Runtime must not own or assume ownership"):
            production.build_plan(1, actor=self.admin, target=live_target(), workbook_path=self.workbook_path)
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_apply_table_locks_reject_concurrent_incoming_relations_and_temperature(self):
        from config import historical_temperature_pgqa as guard

        decoy = Box.objects.create(
            organization=self.other_org, strain=self.strain, global_code="SYN-QAA-1.001", box_number="001",
        )
        incoming = BiologicalMeasurement.objects.create(
            box=decoy, measured_on=date(2026, 1, 7), polyp_count=0, ephyrae_count=0,
        )
        document = self.production_plan()
        before = self.database_snapshot()
        # A competing application owner can write these rows; runtime cannot.
        probe = connection.copy(alias="cca_eob_lock_probe")
        probe.settings_dict.update(USER=guard.OWNER, PASSWORD=guard.CREDENTIALS[guard.OWNER])
        attempts = []
        original_plan = production._plan
        try:
            guard.assert_disposable(probe)
            with probe.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid(), session_user, current_user")
                probe_pid, session, current = cursor.fetchone()
            self.assertEqual((session, current), (guard.OWNER, guard.OWNER))
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                self.assertNotEqual(probe_pid, cursor.fetchone()[0])

            def inspect_locked_plan(path, actor):
                self.assertTrue(connection.in_atomic_block)
                with connection.cursor() as cursor:
                    cursor.execute("""SELECT c.relname, l.mode FROM pg_locks l
                        JOIN pg_class c ON c.oid=l.relation
                        WHERE l.pid=pg_backend_pid() AND l.granted
                        AND c.oid IN ('public.measurements_biologicalmeasurement'::regclass,
                                      'public.measurements_historicaltemperaturereading'::regclass)""")
                    locks = set(cursor.fetchall())
                self.assertIn(("measurements_biologicalmeasurement", "ShareRowExclusiveLock"), locks)
                self.assertIn(("measurements_historicaltemperaturereading", "ShareLock"), locks)
                statements = (
                    ("environment", "UPDATE public.measurements_biologicalmeasurement SET environment_source_box_id=%s WHERE id=%s",
                     [1835, incoming.pk]),
                    ("duplicate", "UPDATE public.measurements_biologicalmeasurement SET duplicate_of_id=%s WHERE id=%s",
                     [self.measurements[0].pk, incoming.pk]),
                    ("temperature", """INSERT INTO public.measurements_historicaltemperaturereading
                        (biological_measurement_id, association_status, iso_year, iso_week, temperature_c, created_at)
                        VALUES (%s, 'confirmed', 2026, 33, '0', CURRENT_TIMESTAMP)""",
                     [self.measurements[3].pk]),
                )
                for label, statement, params in statements:
                    # Synchronous execution is intentional: the apply transaction
                    # remains open while this other backend waits and times out.
                    with self.assertRaises(DatabaseError) as caught:
                        with probe.connection.transaction(), probe.cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '250ms'")
                            cursor.execute("SET LOCAL statement_timeout = '3s'")
                            cursor.execute(statement, params)
                    self.assertEqual(getattr(caught.exception.__cause__, "sqlstate", None), "55P03", label)
                    with probe.cursor() as cursor:
                        cursor.execute("SELECT 1")
                        self.assertEqual(cursor.fetchone(), (1,))
                    attempts.append(label)
                self.assertEqual(before, self.database_snapshot())
                # Keep the real final planner/envelope verification before writes.
                return original_plan(path, actor)

            with self.synthetic_release_io(document), patch.object(production, "_plan", side_effect=inspect_locked_plan):
                self.assertEqual(self.production_apply(document), document["plan"]["correction"])
        finally:
            probe.close()
        self.assertEqual(attempts, ["environment", "duplicate", "temperature"])
        incoming.refresh_from_db()
        self.assertIsNone(incoming.environment_source_box_id)
        self.assertIsNone(incoming.duplicate_of_id)
        self.assertFalse(HistoricalTemperatureReading.objects.exists())

    @runtime_workflow
    def test_real_read_only_repeatable_read_plan_preserves_every_table(self):
        before = self.database_snapshot()
        original = production._plan

        def inspect_transaction(path, actor):
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation')")
                self.assertEqual(cursor.fetchone(), ("on", "repeatable read"))
            return original(path, actor)

        with patch.object(production, "_plan", side_effect=inspect_transaction) as planner:
            document = self.production_plan()
            planner.assert_called_once_with(self.workbook_path, self.admin)
        self.assertEqual(document, self.production_plan())
        plan = document["plan"]["correction"]
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan["counts"], {correction.MOVE: 3, correction.MATCH: 1})
        self.assertEqual({box["id"]: box["global_code"] for box in plan["baseline"]["boxes"]}, production.BOXES)
        self.assertTrue(document["plan"]["deployed_guards"]["triggers"])
        self.assertFalse(HistoricalTemperatureReading.objects.exists())
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_apply_changes_only_exact_box_ids_revisions_and_new_audits(self):
        BiologicalMeasurement.objects.create(
            box=self.boxes[4], measured_on=date.fromisocalendar(2026, 42, 4),
            polyp_count=0, ephyrae_count=0, strobila_count=None, notes=None,
        )
        decoy = Box.objects.create(
            organization=self.other_org, strain=self.strain, global_code="SYN-QAA-1.001", box_number="001",
        )
        BiologicalMeasurement.objects.create(
            box=decoy, measured_on=date(2026, 1, 7), polyp_count=0, ephyrae_count=0,
        )
        document = self.production_plan()
        plan = document["plan"]["correction"]
        before = self.database_snapshot()
        measurements = {m.pk: json_snapshot(m) for m in BiologicalMeasurement.objects.all()}
        boxes = {b.pk: json_snapshot(b) for b in Box.objects.all()}
        old_audit_ids = set(AuditLog.objects.values_list("pk", flat=True))
        with self.synthetic_release_io(document) as release, patch.object(
                correction, '_apply_reviewed_plan', side_effect=AssertionError(
                    'Production must use the SQL writer, not the ORM apply engine')) as orm_apply:
            self.assertEqual(self.production_apply(document), plan)
            orm_apply.assert_not_called()
        targets = {self.measurements[0].pk: 1835, self.measurements[1].pk: 1836, self.measurements[2].pk: 1835}
        self.assertEqual(BiologicalMeasurement.objects.count(), len(measurements))
        for measurement in BiologicalMeasurement.objects.all():
            expected = {**measurements[measurement.pk]}
            expected["box_id"] = targets.get(measurement.pk, expected["box_id"])
            self.assertEqual(json_snapshot(measurement), expected)
        revisions = {entry["box_id"]: entry["after"] for entry in plan["revision_changes"]}
        self.assertEqual(revisions, {1835: 307, 1836: 307, 1837: 308})
        self.assertEqual(Box.objects.count(), len(boxes))
        for box in Box.objects.all():
            expected = {**boxes[box.pk]}
            expected["polyp_state_revision"] = revisions.get(box.pk, expected["polyp_state_revision"])
            self.assertEqual(json_snapshot(box), expected)
        after = self.database_snapshot()
        mutable = {Box._meta.db_table, BiologicalMeasurement._meta.db_table, AuditLog._meta.db_table}
        self.assertEqual(before.keys(), after.keys())
        for table in before.keys() - mutable:
            self.assertEqual(before[table], after[table], table)
        for row in before[AuditLog._meta.db_table]:
            self.assertIn(row, after[AuditLog._meta.db_table])
        audits = list(AuditLog.objects.exclude(pk__in=old_audit_ids).order_by("pk"))
        self.assertEqual(len(audits), 4)
        self.assertEqual([a.metadata.get("measurement_id") for a in audits[:-1]], plan["move_order"])
        for audit in audits:
            self.assertEqual(audit.original_values, {})
            self.assertIsNone(audit.edited_at)
            self.assertIsNone(audit.edited_by_id)
            self.assertEqual((audit.organization_id, audit.user_id), (1, self.admin.pk))
            evidence = audit.metadata["execution_evidence"]
            for key, expected in {
                "target": self.target, "production_plan_hash": correction.fingerprint(document),
                "authorization": f"APPLY_CCA_EOB_ORGANIZATION_1:{correction.fingerprint(document)}", "release": release,
            }.items():
                self.assertEqual(evidence[key], expected, key)
            self.assertEqual(audit.metadata["plan_hash"], correction.fingerprint(plan))
        for audit in audits[:-1]:
            pk = audit.metadata["measurement_id"]
            self.assertEqual(audit.metadata["before"], measurements[pk])
            self.assertEqual(audit.metadata["after"], {**measurements[pk], "box_id": targets[pk]})
        self.assertEqual(audits[-1].object_type, "historical_correction")
        self.assertEqual(audits[-1].metadata["moved"], 3)
        # Full-row comparisons above preserve zeros, NULLs, dates and attribution.
        zero = BiologicalMeasurement.objects.get(pk=self.measurements[2].pk)
        nullable = BiologicalMeasurement.objects.get(pk=self.measurements[1].pk)
        self.assertEqual((zero.polyp_count, zero.ephyrae_count, zero.strobila_count), (0, 0, 0))
        self.assertIsNone(nullable.strobila_count)
        self.assertIsNone(nullable.salinity_psu)
        self.assertFalse(HistoricalTemperatureReading.objects.exists())

    def test_owner_orm_engine_matches_scoped_core_and_preserves_shared_audits(self):
        from config import historical_temperature_pgqa as guard

        guard.require_owner(connection)
        guard.require_run_lease()
        before = self.database_snapshot()
        owner_plan = correction._build_plan(1, workbook_path=self.workbook_path)
        self.assertTrue(owner_plan['ready'], owner_plan['blockers'])
        with self.production_runtime():
            payload = production._read_scope(self.admin)
            scoped_plan = correction._build_plan(
                1, workbook_path=self.workbook_path, data=production._planner_data(payload))
        normalized_owner = deepcopy(owner_plan)
        # The ORM preserves history IDs; the scoped reader exposes existence only.
        normalized_owner['baseline']['histories'] = {
            name: bool(rows) for name, rows in owner_plan['baseline']['histories'].items()
        }
        self.assertEqual(scoped_plan, normalized_owner)
        self.assertEqual(before, self.database_snapshot())
        digest = correction.fingerprint(owner_plan)
        # This harness is not an approved public local-QA command target.
        with self.assertRaises(ValueError):
            correction.build_plan(1, workbook_path=self.workbook_path)
        with self.assertRaises(ValueError):
            correction.apply_plan(1, workbook_path=self.workbook_path, actor=self.admin,
                                  expected_plan_hash=digest)
        measurements = list(BiologicalMeasurement.objects.order_by('pk'))
        recipe = correction.write_recipe(owner_plan, measurements, self.admin.pk)
        old_audit_ids = set(AuditLog.objects.values_list('pk', flat=True))
        verification_windows = []

        def verify_owner_execution():
            guard.require_run_lease()
            # require_owner also requires an idle connection. Within the engine's
            # transaction, verify the same real owner session via the raw guard.
            if connection.in_atomic_block:
                guard.assert_disposable(connection)
                self.assertEqual(connection.settings_dict['USER'], guard.OWNER)
            else:
                guard.require_owner(connection)
            verification_windows.append(connection.in_atomic_block)

        applied = correction._apply_reviewed_plan(
            1, workbook_path=self.workbook_path, actor=self.admin,
            expected_plan_hash=digest, verify_execution=verify_owner_execution)
        self.assertEqual(applied, owner_plan)
        self.assertEqual(verification_windows, [False, True, True])
        audits = list(AuditLog.objects.exclude(pk__in=old_audit_ids).order_by('pk'))
        self.assertEqual(len(audits), 4)
        self.assertEqual(len(audits), len(recipe['audits']))
        for audit, expected in zip(audits, recipe['audits'], strict=True):
            for name, value in expected.items():
                self.assertEqual(getattr(audit, name), value, name)
            self.assertNotIn('execution_evidence', audit.metadata)
            self.assertEqual(audit.original_values, {})
            self.assertIsNone(audit.edited_at)
            self.assertIsNone(audit.edited_by_id)
        destinations = {move['id']: move['target_box_id'] for move in recipe['moves']}
        for measurement in measurements:
            self.assertEqual(json_snapshot(BiologicalMeasurement.objects.get(pk=measurement.pk)),
                             {**json_snapshot(measurement),
                              'box_id': destinations.get(measurement.pk, measurement.box_id)})
        for revision in recipe['revisions']:
            self.assertEqual(Box.objects.get(pk=revision['box_id']).polyp_state_revision,
                             revision['after'])
        after = self.database_snapshot()
        mutable = {'cultures_box', 'measurements_biologicalmeasurement', 'audit_auditlog'}
        for table in before.keys() - mutable:
            self.assertEqual(before[table], after[table], table)
        for row in before['audit_auditlog']:
            self.assertIn(row, after['audit_auditlog'])

    @runtime_workflow
    def test_stale_envelope_hash_and_deliberate_authorization_fail_before_io(self):
        document = self.production_plan()
        before = self.database_snapshot()
        for changes in (
            {"approved_hash": "c" * 64}, {"authorization": None}, {"authorization": "APPLY_CCA_EOB"},
            {"actor": self.writer}, {"document": {**document, "actor_id": self.writer.pk}},
            {"document": {**document, "schema": "unreviewed"}},
        ):
            with self.subTest(changes=changes), patch.object(production, "_root_document") as root:
                with self.assertRaises(ValueError):
                    self.production_apply(document, **changes)
                root.assert_not_called()
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_stale_database_hash_aborts_without_partial_changes(self):
        document = self.production_plan()
        Box.objects.filter(pk=1837).update(notes="Concurrent persisted correction")
        before = self.database_snapshot()
        with self.synthetic_release_io(document), self.assertRaisesMessage(ValueError, "Stale production plan"):
            self.production_apply(document)
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_unauthorized_actor_foreign_scope_and_persisted_revocation(self):
        before = self.database_snapshot()
        with self.assertRaises(DatabaseError):
            self.production_plan(actor=self.writer)
        with self.assertRaises(ValueError):
            production.build_plan(2, actor=self.admin, target=self.target, workbook_path=self.workbook_path)
        document = self.production_plan()
        with self.synthetic_release_io(document):
            with self.assertRaises(ValueError):
                self.production_apply(document, organization_id=2)
        self.assertEqual(before, self.database_snapshot())
        OrganizationMembership.objects.filter(user=self.admin, organization=self.org).update(is_active=False)
        before = self.database_snapshot()
        with self.synthetic_release_io(document), self.assertRaises(DatabaseError):
            self.production_apply(document)
        with self.assertRaises(DatabaseError):
            self.production_plan()
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_foreign_admin_membership_cannot_authorize_organization_one(self):
        document = self.production_plan()
        OrganizationMembership.objects.filter(user=self.admin, organization=self.org).update(
            organization=self.other_org,
        )
        before = self.database_snapshot()
        with self.synthetic_release_io(document), self.assertRaises(DatabaseError):
            self.production_apply(document)
        with self.assertRaises(DatabaseError):
            self.production_plan()
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_inactive_persisted_actor_cannot_use_stale_active_user_object(self):
        document = self.production_plan()
        type(self.admin).objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertTrue(self.admin.is_active)
        before = self.database_snapshot()
        with self.synthetic_release_io(document), self.assertRaises(DatabaseError):
            self.production_apply(document)
        with self.assertRaises(DatabaseError):
            self.production_plan()
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_foreign_institution_box_cannot_complete_exact_scope(self):
        Box.objects.filter(pk=1837).update(organization=self.other_org)
        before = self.database_snapshot()
        with self.assertRaises(DatabaseError) as caught:
            self.production_plan()
        self.assertEqual(getattr(caught.exception.__cause__, "sqlstate", None), "23514")
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_occupied_destination_conflicts_block_without_merging(self):
        BiologicalMeasurement.objects.create(
            box=self.boxes[4], measured_on=date.fromisocalendar(2026, 32, 4), polyp_count=999, ephyrae_count=0,
        )
        self.assert_production_blocked("non-conflicting correspondence")

    @runtime_workflow
    def test_absent_biological_observation_is_not_inferred_from_source_zero(self):
        BiologicalMeasurement.objects.filter(pk=self.measurements[2].pk).delete()
        self.assert_production_blocked("non-conflicting correspondence")

    @runtime_workflow
    def test_duplicate_evidence_blocks_without_rewriting_associations(self):
        BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(duplicate_of_id=self.measurements[1].pk)
        self.assert_production_blocked("Duplicate evidence or explicit environmental source")

    @runtime_workflow
    def test_explicit_environment_source_blocks_without_rewriting_associations(self):
        BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(environment_source_box_id=1837)
        self.assert_production_blocked("Duplicate evidence or explicit environmental source")

    @runtime_workflow
    def test_incoming_foreign_duplicate_and_environment_relations_block(self):
        decoy = Box.objects.create(
            organization=self.other_org, strain=self.strain, global_code="SYN-QAA-1.001", box_number="001",
        )
        incoming = BiologicalMeasurement.objects.create(
            box=decoy, measured_on=date(2026, 1, 7), polyp_count=0, ephyrae_count=0,
        )
        for relation in ({"duplicate_of_id": self.measurements[0].pk}, {"environment_source_box_id": 1835}):
            BiologicalMeasurement.objects.filter(pk=incoming.pk).update(**{
                "duplicate_of_id": None, "environment_source_box_id": None, **relation,
            })
            self.assert_production_blocked("Incoming evidence relations")

    @runtime_workflow
    def test_existing_historical_temperature_blocks_with_real_immutable_guards(self):
        biology = self.measurements[0]
        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=biology, iso_year=2026, iso_week=32, temperature_c="0",
            )
            HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(
                source_box=biology.box.global_code, source_value="0", source_iso_week=32, week_value="32",
                polyp_value=str(biology.polyp_count), ephyrae_value=str(biology.ephyrae_count),
            ))
        self.assert_production_blocked("Historical temperature state changed")
        reading.refresh_from_db()
        self.assertEqual(reading.temperature_c, "0")
        self.assertEqual(reading.association_status, "confirmed")
        self.assertEqual(reading.biological_measurement_id, biology.pk)

    @runtime_workflow
    def test_release_rechecked_under_locks_and_changed_evidence_rolls_back(self):
        document = self.production_plan()
        before = self.database_snapshot()
        release = synthetic_release(document)
        self.seed_approval(document, release=release)
        self.seed_final_authorization(document, release=release)
        changed = {**release, "review_ref": "different-review"}
        with patch.object(production, "_root_document", side_effect=[release, changed]) as root, \
                patch.object(production.artifacts, "protected_file"), \
                patch.object(production.database_backup, "verify_archive"):
            with self.assertRaisesMessage(ValueError, "release evidence changed"):
                self.production_apply(document)
            self.assertEqual(root.call_count, 2)
        self.assertEqual(before, self.database_snapshot())

    @runtime_workflow
    def test_late_summary_receipt_failure_rolls_back_moves_revisions_and_audits(self):
        document = self.production_plan()
        before = self.database_snapshot()
        # The SQL writer bypasses ORM create(). A real trigger fails only after
        # all moves, revisions and three per-measurement audits have been written.
        expected_revision = next(row['after'] for row in document['plan']['write_recipe']['revisions']
                                 if row['box_id'] == 1835)
        with connection.cursor() as cursor:
            cursor.execute(f"""CREATE FUNCTION public.cca_qa_fail_receipt() RETURNS trigger
                LANGUAGE plpgsql AS $qa$ BEGIN
                IF NEW.object_type='historical_correction' THEN
                    IF (SELECT count(*) FROM public.audit_auditlog
                        WHERE metadata->>'operation'='{correction.OPERATION}')<>3
                        OR (SELECT box_id FROM public.measurements_biologicalmeasurement
                            WHERE id={self.measurements[0].pk})<>1835
                        OR (SELECT polyp_state_revision FROM public.cultures_box WHERE id=1835)<>{expected_revision} THEN
                        RAISE EXCEPTION 'QA receipt failed before expected writes';
                    END IF;
                    RAISE EXCEPTION 'Synthetic late receipt failure';
                END IF;
                RETURN NEW; END $qa$;
                CREATE TRIGGER cca_qa_fail_receipt BEFORE INSERT ON public.audit_auditlog
                FOR EACH ROW EXECUTE FUNCTION public.cca_qa_fail_receipt()""")
        try:
            with self.synthetic_release_io(document):
                final_before = self.final_authorization_state()
                with self.assertRaisesMessage(DatabaseError, "Synthetic late receipt failure"):
                    self.production_apply(document)
            self.assertIsNone(self.approval_consumed_at())
            self.assertEqual(final_before, self.final_authorization_state())
            self.assertEqual(before, self.database_snapshot())
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER cca_qa_fail_receipt ON public.audit_auditlog")
                cursor.execute("DROP FUNCTION public.cca_qa_fail_receipt()")


class MinimumPrivilegeBase(CcaEobFixtureMixin, TransactionTestCase):
    """Fixture with capability-only authority; defines no tests."""

    reset_sequences = True
    _fixture_teardown = ProductionWorkflowTests._fixture_teardown
    owner_session = ProductionWorkflowTests.owner_session
    production_runtime = ProductionWorkflowTests.production_runtime
    database_snapshot = ProductionWorkflowTests.database_snapshot
    install_boundary = ProductionWorkflowTests.install_boundary
    seed_approval = ProductionWorkflowTests.seed_approval
    seed_final_authorization = ProductionWorkflowTests.seed_final_authorization
    final_authorization_state = ProductionWorkflowTests.final_authorization_state
    approval_consumed_at = ProductionWorkflowTests.approval_consumed_at
    production_plan = ProductionWorkflowTests.production_plan
    production_apply = ProductionWorkflowTests.production_apply
    synthetic_release_io = ProductionWorkflowTests.synthetic_release_io

    def setUp(self):
        from config import historical_temperature_pgqa as guard

        guard.require_owner(connection)
        guard.require_run_lease()
        create_box, create_org = Box.objects.create, Organization.objects.create

        def box(**values):
            values["pk"] = {code: pk for pk, code in production.BOXES.items()}[values["global_code"]]
            return create_box(**values)

        def organization(**values):
            values["pk"] = {"cca-qa": 1, "other-cca-qa": 2}[values["slug"]]
            return create_org(**values)

        with patch.object(Box.objects, "create", side_effect=box), patch.object(
                Organization.objects, "create", side_effect=organization):
            super().setUp()
        self.install_boundary()


@skipUnless(_ENABLED, "Requires the explicitly authorized, leased disposable PG18 harness")
class MinimumPrivilegeWorkflowTests(MinimumPrivilegeBase):
    """Real reader/writer capabilities, no business ACLs or generic locking."""

    def test_complete_readonly_plan_and_atomic_apply_with_capabilities_only(self):
        from config import historical_temperature_pgqa as guard

        before = self.database_snapshot()
        with guard.runtime_role():
            self.target = live_target()
            document = self.production_plan()
        self.assertEqual(before, self.database_snapshot())
        with guard.runtime_role():
            with connection.cursor() as cursor:
                cursor.execute("SELECT attidentity FROM pg_attribute WHERE attrelid='audit_auditlog'::regclass AND attname='id'")
                self.assertEqual(cursor.fetchone(), ('d',))
                cursor.execute("SELECT has_sequence_privilege(current_user,pg_get_serial_sequence('audit_auditlog','id'),'USAGE')")
                self.assertEqual(cursor.fetchone(), (False,))
            with self.synthetic_release_io(document):
                self.production_apply(document)
        after = self.database_snapshot()
        changed = {table for table in before if before[table] != after[table]}
        self.assertEqual(changed, {'cultures_box', 'measurements_biologicalmeasurement', 'audit_auditlog'})
        expected = {e['measurement_pk']: e['target_box_pk'] for e in document['plan']['correction']['entries']
                    if e['classification'] == correction.MOVE}
        for row in document['plan']['correction']['baseline']['measurements']:
            current = json_snapshot(BiologicalMeasurement.objects.get(pk=row['id']))
            self.assertEqual(current, {**row, 'box_id': expected.get(row['id'], row['box_id'])})
        self.assertEqual(AuditLog.objects.count(), 4)
        self.assertTrue(all(a.user_id == self.admin.pk and a.organization_id == 1 for a in AuditLog.objects.all()))

    def test_unrelated_writes_ddl_and_generic_locks_are_denied(self):
        from config import historical_temperature_pgqa as guard

        statements = (
            "UPDATE cultures_box SET notes=notes WHERE false",
            "UPDATE measurements_biologicalmeasurement SET polyp_count=polyp_count WHERE false",
            "UPDATE measurements_biologicalmeasurement SET measured_on=measured_on WHERE false",
            "UPDATE measurements_historicaltemperaturereading SET temperature_c=temperature_c WHERE false",
            "UPDATE auth_user SET is_active=is_active WHERE false",
            "UPDATE accounts_organizationmembership SET role=role WHERE false",
            "UPDATE cultures_boxlocation SET box_id=box_id WHERE false",
            "UPDATE cultures_boxmovement SET box_id=box_id WHERE false",
            "UPDATE audit_auditlog SET metadata=metadata WHERE false",
            "DELETE FROM audit_auditlog WHERE false",
            "DELETE FROM measurements_biologicalmeasurement WHERE false",
            "INSERT INTO audit_auditlog (id) VALUES (999999)",
            "SELECT password FROM auth_user WHERE false",
            "SELECT id FROM auth_user FOR UPDATE",
            "LOCK TABLE measurements_biologicalmeasurement IN SHARE ROW EXCLUSIVE MODE",
            "LOCK TABLE measurements_historicaltemperaturereading IN SHARE MODE",
            "ALTER TABLE cultures_box DISABLE TRIGGER historical_box_guard",
            "CREATE TABLE public.cca_should_not_exist (id integer)",
        )
        with guard.runtime_role():
            for statement in statements:
                with self.subTest(statement=statement), self.assertRaises(DatabaseError) as caught:
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement)
                self.assertEqual(getattr(caught.exception.__cause__, 'sqlstate', None), '42501')
            with self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT public.cca_eob_lock_v1(%s)", [self.writer.pk])

    def test_invoker_temperature_trigger_uses_writer_owner_not_runtime_acl(self):
        from config import historical_temperature_pgqa as guard

        before = self.database_snapshot()
        with guard.runtime_role():
            self.target = live_target()
            with connection.cursor() as cursor:
                cursor.execute("""SELECT has_column_privilege(current_user,
                    'public.measurements_historicaltemperaturereading', 'association_status', 'UPDATE'),
                    has_column_privilege(current_user,
                    'public.measurements_biologicalmeasurement', 'box_id', 'UPDATE')""")
                self.assertEqual(cursor.fetchone(), (False, False))
            document = self.production_plan()
            with self.synthetic_release_io(document):
                self.assertEqual(self.production_apply(document), document['plan']['correction'])
        self.assertIsNotNone(self.approval_consumed_at())
        after = self.database_snapshot()
        self.assertEqual(before['measurements_historicaltemperaturereading'],
                         after['measurements_historicaltemperaturereading'])
        self.assertEqual(before['measurements_historicaltemperatureevidence'],
                         after['measurements_historicaltemperatureevidence'])


@skipUnless(_ENABLED, "Requires the explicitly authorized, leased disposable PG18 harness")
class LockHelperTests(MinimumPrivilegeBase):
    """Lock capability: fixed scope and actor check, alongside reader and writer."""

    production_plan = ProductionWorkflowTests.production_plan
    production_apply = ProductionWorkflowTests.production_apply
    synthetic_release_io = ProductionWorkflowTests.synthetic_release_io

    def setUp(self):
        super().setUp()
        starts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.location = BoxLocation.objects.create(
            box=self.boxes[4], thermal_zone=self.zone, starts_at=starts, notes="Target location")
        self.movement = BoxMovement.objects.create(
            box=self.boxes[4], to_thermal_zone=self.zone, destination_location=self.location,
            moved_at=starts, user=self.writer, notes="Target movement")
        # Everything below is outside the fixed CCA-EOB scope and must never be locked.
        self.other_zone = ThermalZone.objects.create(organization=self.org, name="Unrelated zone")
        self.unrelated = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.other_zone,
            global_code="CCA-EOB-2.007", box_number="007")
        self.unrelated_location = BoxLocation.objects.create(
            box=self.unrelated, thermal_zone=self.other_zone, starts_at=starts, notes="Unrelated")
        self.unrelated_movement = BoxMovement.objects.create(
            box=self.unrelated, to_thermal_zone=self.other_zone,
            destination_location=self.unrelated_location, moved_at=starts, user=self.writer, notes="Unrelated")
        self.unrelated_measurement = BiologicalMeasurement.objects.create(
            box=self.unrelated, measured_on=date(2026, 1, 7), polyp_count=0, ephyrae_count=0)
        self.foreign_box = Box.objects.create(
            organization=self.other_org, strain=self.strain, global_code="SYN-QAA-1.001", box_number="001")
        self.writer_membership = OrganizationMembership.objects.get(organization=self.org, user=self.writer)
        self.admin_membership = OrganizationMembership.objects.get(organization=self.org, user=self.admin)

    def assert_helper_denied(self, actor_id, sqlstate, *, isolation=None):
        from config import historical_temperature_pgqa as guard

        with guard.runtime_role(), self.assertRaises(DatabaseError) as caught:
            with transaction.atomic(), connection.cursor() as cursor:
                if isolation:
                    cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
                cursor.execute("SELECT public.cca_eob_lock_v1(%s)", [actor_id])
        self.assertEqual(getattr(caught.exception.__cause__, "sqlstate", None), sqlstate)

    def assert_helper_allowed(self):
        from config import historical_temperature_pgqa as guard

        with guard.runtime_role(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT public.cca_eob_lock_v1(%s)", [self.admin.pk])

    @staticmethod
    def try_lock(probe, statement, params=()):
        """Return (sqlstate, row count) of a short-timeout statement on a second real login."""
        try:
            with probe.connection.transaction(), probe.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '250ms'")
                cursor.execute(statement, params)
                return None, len(cursor.fetchall())
        except DatabaseError as error:
            return getattr(error.__cause__, "sqlstate", None), 0

    def test_only_the_dedicated_login_and_an_active_org_one_admin_may_call_it(self):
        user_model = get_user_model()
        membership = OrganizationMembership.objects.filter(pk=self.admin_membership.pk)
        users = user_model.objects.filter(pk=self.admin.pk)
        # The application owner (and any other login) is refused; only the CCA login passes.
        with self.assertRaises(DatabaseError) as caught:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT public.cca_eob_lock_v1(%s)", [self.admin.pk])
        self.assertEqual(getattr(caught.exception.__cause__, "sqlstate", None), "42501")
        for actor in (None, 0, -1, 999999, self.writer.pk):
            with self.subTest(actor=actor):
                self.assert_helper_denied(actor, "42501")
        for label, damage, repair in (
            ("inactive user", {"queryset": users, "is_active": False}, {"queryset": users, "is_active": True}),
            ("inactive membership", {"queryset": membership, "is_active": False},
             {"queryset": membership, "is_active": True}),
            ("non-admin role", {"queryset": membership, "role": "lab_technician"},
             {"queryset": membership, "role": "admin"}),
            ("foreign-organization admin", {"queryset": membership, "organization": self.other_org},
             {"queryset": membership, "organization": self.org}),
        ):
            with self.subTest(label):
                damage = dict(damage)
                damage.pop("queryset").update(**damage)
                try:
                    self.assert_helper_denied(self.admin.pk, "42501")
                finally:
                    repair = dict(repair)
                    repair.pop("queryset").update(**repair)
        before = self.database_snapshot()
        self.assert_helper_allowed()
        self.assertEqual(before, self.database_snapshot())

    def test_requires_read_committed_so_the_lock_snapshot_cannot_be_stale(self):
        self.assert_helper_denied(self.admin.pk, "25001", isolation="REPEATABLE READ")
        self.assert_helper_denied(self.admin.pk, "25001", isolation="SERIALIZABLE")

    def test_fixed_box_identities_and_organization_cannot_drift(self):
        for label, damage, repair in (
            ("renamed", {"pk": 1835, "global_code": "CCA-EOB-2.904"}, {"pk": 1835, "global_code": "CCA-EOB-2.004"}),
            ("other organization", {"pk": 1836, "organization": self.other_org},
             {"pk": 1836, "organization": self.org}),
        ):
            with self.subTest(label):
                damage = dict(damage)
                Box.objects.filter(pk=damage.pop("pk")).update(**damage)
                try:
                    self.assert_helper_denied(self.admin.pk, "23514")
                finally:
                    repair = dict(repair)
                    Box.objects.filter(pk=repair.pop("pk")).update(**repair)
        self.assert_helper_allowed()

    def test_locks_exactly_the_fixed_scope_and_never_other_rows(self):
        from config import historical_temperature_pgqa as guard

        lock = "SELECT 1 FROM public.{} WHERE id = %s FOR NO KEY UPDATE"
        scoped = {
            "target box 1835": ("cultures_box", 1835), "target box 1836": ("cultures_box", 1836),
            "target box 1837": ("cultures_box", 1837),
            "organization": ("organizations_organization", 1),
            "actor membership": ("accounts_organizationmembership", self.admin_membership.pk),
            "actor user": ("auth_user", self.admin.pk),
            "target movement": ("cultures_boxmovement", self.movement.pk),
            "target location": ("cultures_boxlocation", self.location.pk),
            "target zone": ("cultures_thermalzone", self.zone.pk),
            "target measurement": ("measurements_biologicalmeasurement", self.measurements[0].pk),
        }
        outside = {
            "foreign organization": ("organizations_organization", self.other_org.pk),
            "other member": ("accounts_organizationmembership", self.writer_membership.pk),
            "other user": ("auth_user", self.writer.pk),
            "unrelated box": ("cultures_box", self.unrelated.pk),
            "foreign box": ("cultures_box", self.foreign_box.pk),
            "unrelated movement": ("cultures_boxmovement", self.unrelated_movement.pk),
            "unrelated location": ("cultures_boxlocation", self.unrelated_location.pk),
            "unrelated zone": ("cultures_thermalzone", self.other_zone.pk),
            "unrelated measurement": ("measurements_biologicalmeasurement", self.unrelated_measurement.pk),
        }
        probe = connection.copy(alias="cca_eob_owner_probe")
        probe.settings_dict.update(USER=guard.OWNER, PASSWORD=guard.CREDENTIALS[guard.OWNER])
        try:
            guard.assert_disposable(probe)
            probe.ensure_connection()
            # Every probe row exists and is initially lockable by a second backend.
            for label, (table, key) in {**scoped, **outside}.items():
                self.assertEqual(self.try_lock(probe, lock.format(table), [key]), (None, 1), label)
            before = self.database_snapshot()
            with guard.runtime_role(), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT public.cca_eob_lock_v1(%s)", [self.admin.pk])
                cursor.execute("""SELECT c.relname, l.mode FROM pg_locks l JOIN pg_class c ON c.oid=l.relation
                    WHERE l.pid=pg_backend_pid() AND l.granted AND c.relname IN
                    ('measurements_biologicalmeasurement', 'measurements_historicaltemperaturereading')
                    AND l.mode IN ('ShareRowExclusiveLock', 'ShareLock')""")
                self.assertEqual(set(cursor.fetchall()), {
                    ("measurements_biologicalmeasurement", "ShareRowExclusiveLock"),
                    ("measurements_historicaltemperaturereading", "ShareLock")})
                for label, (table, key) in scoped.items():
                    self.assertEqual(self.try_lock(probe, lock.format(table), [key]), ("55P03", 0), label)
                for label, (table, key) in outside.items():
                    self.assertEqual(self.try_lock(probe, lock.format(table), [key]), (None, 1), label)
            # Released with the transaction: nothing stays locked and nothing changed.
            for label, (table, key) in scoped.items():
                self.assertEqual(self.try_lock(probe, lock.format(table), [key]), (None, 1), label)
            self.assertEqual(before, self.database_snapshot())
        finally:
            probe.close()

    def test_concurrent_callers_serialize_on_the_box_lock_without_deadlock(self):
        from config import historical_temperature_pgqa as guard

        call = "SELECT public.cca_eob_lock_v1(%s)"
        with guard.runtime_role():
            second = connection.copy(alias="cca_eob_second_runtime")
            try:
                guard.assert_disposable(second)
                second.ensure_connection()
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(call, [self.admin.pk])
                    self.assertEqual(self.try_lock(second, call, [self.admin.pk]), ("55P03", 0))
                self.assertEqual(self.try_lock(second, call, [self.admin.pk]), (None, 1))
            finally:
                second.close()

    def test_tampered_helper_is_rejected_before_it_is_called(self):
        from config import historical_temperature_pgqa as guard

        with guard.runtime_role():
            self.target = live_target()
            document = self.production_plan()
        before = self.database_snapshot()
        for signature, body, _, _ in role_contract.helper_specs(guard.RUNTIME):
            tampered = {
                'invoker security': f'ALTER FUNCTION {signature} SECURITY INVOKER',
                'search_path': f'ALTER FUNCTION {signature} SET search_path = public, pg_catalog',
                'volatility': f'ALTER FUNCTION {signature} STABLE',
                'parallel': f'ALTER FUNCTION {signature} PARALLEL SAFE',
                'strict': f'ALTER FUNCTION {signature} STRICT',
                'cost': f'ALTER FUNCTION {signature} COST 1',
                'extra GUC': f"ALTER FUNCTION {signature} SET statement_timeout = '1s'",
                'PUBLIC execute': f'GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC',
                'body': None,
                'dropped': f'DROP FUNCTION {signature}',
            }
            if signature != role_contract.FUNCTION:
                tampered.update({
                    'timezone': f"ALTER FUNCTION {signature} SET TimeZone = 'Europe/Paris'",
                    'datestyle': f"ALTER FUNCTION {signature} SET DateStyle = 'SQL, DMY'",
                })
            for label, statement in tampered.items():
                with self.subTest(signature=signature, damage=label):
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_get_functiondef(%s::regprocedure)', [signature])
                        definition = cursor.fetchone()[0]
                        if label == 'body':
                            statement = definition.replace(body, body + '\n-- unreviewed body\n', 1)
                        cursor.execute(statement)
                    try:
                        with guard.runtime_role():
                            with self.assertRaises(ValueError):
                                production._schema_state()
                            with self.assertRaises((ValueError, DatabaseError)):
                                self.production_plan()
                            with self.synthetic_release_io(document), self.assertRaises((ValueError, DatabaseError)):
                                self.production_apply(document)
                    finally:
                        with connection.cursor() as cursor:
                            cursor.execute(definition)
                            cursor.execute(f'REVOKE ALL ON FUNCTION {signature} FROM PUBLIC')
                            cursor.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO {guard.RUNTIME}')
                    self.assertEqual(before, self.database_snapshot())
        with guard.runtime_role():
            # Recreated function OIDs legitimately require a fresh review.
            self.assertTrue(self.production_plan()['plan']['correction']['ready'])

    def test_every_capability_grant_is_required_by_a_real_plan_or_apply(self):
        """No direct business ACL is needed; revoking any capability fails closed."""
        from config import historical_temperature_pgqa as guard

        runtime = guard.RUNTIME
        # GRANT USAGE ON SCHEMA public is deliberately absent: PUBLIC holds it by default, so
        # revoking only this role's copy proves nothing. It stays as a defensive grant.
        cases = {f'execute {signature}': f'REVOKE EXECUTE ON FUNCTION {signature} FROM {runtime}'
                 for signature, *_ in role_contract.helper_specs(runtime)}
        cases.update({f"select {table}": f"REVOKE SELECT ON public.{table} FROM {runtime}"
                      for table in role_contract.READ_TABLES})
        cases.update({f"select {table}.{column}": f"REVOKE SELECT ({column}) ON public.{table} FROM {runtime}"
                      for table, columns in role_contract.READ_COLUMNS.items() for column in columns})
        # django_migrations(app,name) is the sole direct table-column read grant.
        with guard.runtime_role():
            self.target = live_target()
            document = self.production_plan()
        before = self.database_snapshot()
        for label, revoke in cases.items():
            with self.subTest(label):
                with connection.cursor() as cursor:
                    cursor.execute(revoke)
                try:
                    with guard.runtime_role():
                        self.target = live_target()
                        with self.assertRaises((DatabaseError, ValueError)):
                            # Reader/migration ACLs deny planning. Writer/lock ACL
                            # drift either invalidates review or denies invocation.
                            self.production_plan()
                            with self.synthetic_release_io(document):
                                self.production_apply(document)
                finally:
                    with connection.cursor() as cursor:
                        cursor.execute(role_contract.grants(runtime))
                        for signature, *_ in role_contract.helper_specs(runtime):
                            cursor.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO {runtime}')
                self.assertEqual(before, self.database_snapshot())


    @staticmethod
    def recording_window(fail_on_exit=None):
        """A socket_guard replacement that records every window and can simulate tampering."""
        events = []

        def factory(target):
            class Window:
                def __enter__(self):
                    events.append(("enter", connection.in_atomic_block))

                def __exit__(self, *exc):
                    events.append(("exit", connection.in_atomic_block))
                    exits = sum(1 for kind, _ in events if kind == "exit")
                    if exc[0] is None and exits == fail_on_exit:
                        raise ValueError("Simulated PostgreSQL socket tampering")
            return Window()
        return events, factory

    def test_socket_window_spans_plan_and_apply_and_midtransaction_tampering_rolls_back(self):
        from config import historical_temperature_pgqa as guard

        with guard.runtime_role():
            self.target = live_target()
            events, factory = self.recording_window()
            with patch.object(production, "socket_guard", side_effect=factory):
                document = self.production_plan()
            # One outer window around the connection and the whole snapshot transaction...
            self.assertEqual(events[0], ("enter", False))
            self.assertEqual(events[-1], ("exit", False))
            self.assertIn(("exit", True), events)
            self.assertEqual(sum(k == "enter" for k, _ in events), sum(k == "exit" for k, _ in events))
        before = self.database_snapshot()
        with guard.runtime_role():
            events, factory = self.recording_window()
            with self.synthetic_release_io(document), patch.object(production, "socket_guard", side_effect=factory):
                self.production_apply(document)
            # ...and one spanning the first connection through the final commit.
            self.assertEqual(events[0], ("enter", False))
            self.assertEqual(events[-1], ("exit", False))
            self.assertIn(("exit", True), events)
        applied = self.database_snapshot()
        self.assertNotEqual(before, applied)

    def test_tampered_socket_inside_the_apply_transaction_rolls_everything_back(self):
        from config import historical_temperature_pgqa as guard

        with guard.runtime_role():
            self.target = live_target()
            document = self.production_plan()
        before = self.database_snapshot()
        # Exit 3 is the first verification inside the locked transaction.
        with guard.runtime_role():
            events, factory = self.recording_window(fail_on_exit=3)
            with self.synthetic_release_io(document), patch.object(production, "socket_guard", side_effect=factory):
                with self.assertRaisesMessage(ValueError, "Simulated PostgreSQL socket tampering"):
                    self.production_apply(document)
            self.assertIn(("exit", True), events)
        self.assertEqual(before, self.database_snapshot())

    def test_transaction_bound_ends_a_stalled_apply_and_releases_its_locks(self):
        import time
        from config import historical_temperature_pgqa as guard

        with guard.runtime_role():
            self.target = live_target()
            document = self.production_plan()
        before = self.database_snapshot()
        original_plan = production._plan

        def stalled(path, actor):
            if connection.in_atomic_block:
                time.sleep(2.5)  # for example a hung external archive check under locks
            return original_plan(path, actor)

        probe = connection.copy(alias="cca_eob_owner_probe")
        probe.settings_dict.update(USER=guard.OWNER, PASSWORD=guard.CREDENTIALS[guard.OWNER])
        try:
            with guard.runtime_role():
                with patch.object(production, "APPLY_TRANSACTION_TIMEOUT_SECONDS", 1), \
                        patch.object(production, "_plan", side_effect=stalled), \
                        self.synthetic_release_io(document), self.assertRaises(DatabaseError):
                    self.production_apply(document)
            guard.assert_disposable(probe)
            probe.ensure_connection()
            # The stalled session is gone, so its row and table locks are gone too.
            self.assertEqual(self.try_lock(
                probe, "SELECT 1 FROM public.cultures_box WHERE id = %s FOR NO KEY UPDATE", [1835]), (None, 1))
            self.assertEqual(self.try_lock(
                probe, "LOCK TABLE public.measurements_biologicalmeasurement IN ROW EXCLUSIVE MODE"), (None, 0))
        finally:
            probe.close()
        self.assertEqual(before, self.database_snapshot())
        self.assertEqual(production.APPLY_TRANSACTION_TIMEOUT_SECONDS, production.database_backup.TIMEOUT_SECONDS + 300)

    def test_reading_table_exposes_no_columns_even_link_and_status(self):
        from config import historical_temperature_pgqa as guard

        table = "public.measurements_historicaltemperaturereading"
        with guard.runtime_role():
            for statement in (f'SELECT biological_measurement_id, association_status FROM {table}',
                              f'SELECT 1 FROM {table} LIMIT 1',
                              f"SELECT temperature_c FROM {table}", f"SELECT id FROM {table}",
                              f"SELECT iso_year FROM {table}", f"SELECT * FROM {table}"):
                with self.subTest(statement=statement):
                    with self.assertRaises(DatabaseError) as caught:
                        with transaction.atomic(), connection.cursor() as cursor:
                            cursor.execute(statement)
                    self.assertEqual(getattr(caught.exception.__cause__, "sqlstate", None), "42501")


@skipUnless(_ENABLED, "Requires the explicitly authorized, leased disposable PG18 harness")
class WriterCapabilityTests(MinimumPrivilegeBase):
    """Call SQL directly: Python validation is not a database permission."""

    def reviewed_document(self):
        with self.production_runtime():
            self.target = live_target()
            return self.production_plan()

    def call_writer(self, digest):
        with self.production_runtime(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SELECT public.cca_eob_apply_v1(%s)', [digest])

    def clear_approval(self):
        with self.owner_session() as owner, owner.cursor() as cursor:
            cursor.execute(f'DELETE FROM {role_contract.APPROVAL}')

    def assert_writer_denied(self, digest):
        before = self.database_snapshot()
        with self.assertRaises(DatabaseError) as caught:
            self.call_writer(digest)
        self.assertEqual(before, self.database_snapshot())
        self.assertIsNone(self.approval_consumed_at())
        return caught.exception

    def assert_final_writer_denied(self, digest):
        # Malformed, mismatched and expired final authority must fail before locks,
        # not just before the first write. Use real contention, not a lock mock.
        with self.owner_session() as blocker:
            blocker.set_autocommit(False)
            try:
                with blocker.cursor() as cursor:
                    cursor.execute('SELECT id FROM public.cultures_box WHERE id=1835 FOR UPDATE')
                with self.production_runtime(), transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout='200ms'")
                    error = self.assert_writer_denied(digest)
                self.assertIn('final', str(error).lower())
                self.assertNotEqual(getattr(error.__cause__, 'sqlstate', None), '55P03')
            finally:
                blocker.rollback()

    def test_direct_sql_plan_approval_alone_cannot_become_final_authority(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        self.seed_approval(document)
        self.assertEqual(self.final_authorization_state(), (None, None, None))
        before = self.database_snapshot()
        # Holding a business lock proves rejection precedes the writer lock phase.
        with self.owner_session() as blocker:
            blocker.set_autocommit(False)
            try:
                with blocker.cursor() as cursor:
                    cursor.execute('SELECT id FROM public.cultures_box WHERE id=1835 FOR UPDATE')
                for forged in (f'APPLY_CCA_EOB_ORGANIZATION_1:{digest}', json.dumps({
                    'schema': 'cca_eob_final_apply_v1', 'plan_hash': digest,
                    'document': document, 'release': synthetic_release(document),
                })):
                    with self.subTest(forged=forged), self.production_runtime():
                        with self.assertRaises(DatabaseError) as caught:
                            with transaction.atomic(), connection.cursor() as cursor:
                                cursor.execute("SET LOCAL lock_timeout='200ms'")
                                for setting in ('polypbase.cca_eob_authorization',
                                                'polypbase.cca_eob_final_authorization'):
                                    cursor.execute('SELECT set_config(%s,%s,true)', [setting, forged])
                                cursor.execute('SELECT public.cca_eob_apply_v1(%s)', [digest])
                        self.assertIn('final', str(caught.exception).lower())
                        self.assertNotEqual(getattr(caught.exception.__cause__, 'sqlstate', None), '55P03')
                with self.production_runtime():
                    for arguments in ([digest, f'APPLY_CCA_EOB_ORGANIZATION_1:{digest}'],
                                      [digest, json.dumps(synthetic_release(document)), json.dumps(document)]):
                        with self.subTest(arguments=arguments), self.assertRaises(DatabaseError) as caught:
                            with transaction.atomic(), connection.cursor() as cursor:
                                placeholders = ','.join(['%s'] * len(arguments))
                                cursor.execute(f'SELECT public.cca_eob_apply_v1({placeholders})', arguments)
                        self.assertEqual(getattr(caught.exception.__cause__, 'sqlstate', None), '42883')
            finally:
                blocker.rollback()
        self.assertEqual(before, self.database_snapshot())
        self.assertIsNone(self.approval_consumed_at())
        self.assertEqual(self.final_authorization_state(), (None, None, None))
        self.seed_final_authorization(document)
        self.call_writer(digest)
        self.assertIsNotNone(self.approval_consumed_at())

    def test_final_authorization_binds_the_complete_document_and_release(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        mutations = (
            ('document target', lambda d, r: d['target'].update(database_oid=d['target']['database_oid'] + 1)),
            ('document actor', lambda d, r: d.update(actor_id=self.writer.pk)),
            ('document baseline', lambda d, r: d['plan']['database_snapshot']['measurements'][0].update(
                notes='Different final baseline')),
            ('release review', lambda d, r: r.update(review_ref='Different final review')),
            ('release target', lambda d, r: r['target'].update(database_oid=r['target']['database_oid'] + 1)),
            ('release actor', lambda d, r: r.update(actor_id=self.writer.pk)),
            ('archive path', lambda d, r: r['archive'].update(path='/synthetic-qa/another.dump')),
            ('archive digest', lambda d, r: r['archive'].update(sha256='c' * 64)),
            ('archive size', lambda d, r: r['archive'].update(size_bytes=124)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                candidate, release = deepcopy(document), synthetic_release(document)
                mutate(candidate, release)
                self.seed_approval(document)
                self.seed_final_authorization(candidate, release=release, reviewed_hash=digest)
                self.assert_final_writer_denied(digest)
                self.clear_approval()
        self.seed_approval(document)
        self.seed_final_authorization(document, reviewed_hash='f' * 64)
        self.assert_final_writer_denied(digest)

    def test_valid_but_rebound_approved_archive_differs_from_final_authority(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        for field, value in (('path', '/synthetic-qa/other.dump'), ('sha256', 'c' * 64), ('size_bytes', 124)):
            with self.subTest(field=field):
                release = synthetic_release(document)
                release['archive'][field] = value
                role_contract.validate_release(release, document, digest)
                self.seed_approval(document, release=release)
                self.seed_final_authorization(document)
                self.assert_final_writer_denied(digest)
                self.clear_approval()

    def test_sql_rejects_malformed_archive_even_when_both_owner_steps_match(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        # SQL validates metadata, not physical archive bytes. No root IO is invoked.
        for label, release in malformed_releases(document):
            with self.subTest(label=label):
                self.seed_approval(document, release=release)
                self.seed_final_authorization(document, release=release)
                self.assertIsNotNone(self.final_authorization_state()[0])
                self.assert_writer_denied(digest)
                self.clear_approval()

    def test_final_authorization_requires_exact_schema_and_all_three_fields(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        valid = {
            'schema': 'cca_eob_final_apply_v1', 'plan_hash': digest,
            'document': document, 'release': synthetic_release(document),
        }
        malformed = [None, [], True, {}, {**valid, 'extra': True}, {**valid, 'schema': 'other'}]
        for key in valid:
            candidate = deepcopy(valid)
            del candidate[key]
            malformed.extend((candidate, {**valid, key: None}))
        for authorization in malformed:
            with self.subTest(authorization=authorization):
                self.seed_approval(document)
                self.seed_final_authorization(document)
                with self.owner_session() as owner, owner.cursor() as cursor:
                    cursor.execute(f'UPDATE {role_contract.APPROVAL} SET final_authorization=%s::jsonb',
                                   [json.dumps(authorization)])
                self.assert_final_writer_denied(digest)
                self.clear_approval()
        for missing in ('final_authorization', 'final_authorized_at', 'final_expires_at'):
            with self.subTest(missing=missing):
                self.seed_approval(document)
                self.seed_final_authorization(document)
                with self.owner_session() as owner, owner.cursor() as cursor:
                    cursor.execute(f'UPDATE {role_contract.APPROVAL} SET {missing}=NULL')
                self.assert_final_writer_denied(digest)
                self.clear_approval()

    def test_final_authorization_expiry_and_five_minute_ceiling_are_enforced(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        for label, assignments in (
            ('expired', "final_authorized_at=clock_timestamp()-interval '60 seconds', "
                        "final_expires_at=clock_timestamp()-interval '1 second'"),
            ('future issuance', "final_authorized_at=clock_timestamp()+interval '60 seconds'"),
            ('before issuance', "final_expires_at=final_authorized_at-interval '1 second'"),
            ('over five minutes', "final_expires_at=final_authorized_at+interval '301 seconds'"),
            ('after plan expiry', "expires_at=clock_timestamp()+interval '10 seconds'"),
        ):
            with self.subTest(label=label):
                self.seed_approval(document)
                self.seed_final_authorization(document)
                with self.owner_session() as owner, owner.cursor() as cursor:
                    cursor.execute(f'UPDATE {role_contract.APPROVAL} SET {assignments}')
                self.assert_final_writer_denied(digest)
                self.clear_approval()

    def test_owner_fixture_never_refreshes_partial_issued_or_consumed_final_authority(self):
        document = self.reviewed_document()
        for field, value in (
            ('final_authorization', "'{}'::jsonb"),
            ('final_authorized_at', 'clock_timestamp()'),
            ('final_expires_at', "clock_timestamp()+interval '60 seconds'"),
            ('consumed_at', 'clock_timestamp()'),
        ):
            with self.subTest(field=field):
                self.seed_approval(document)
                with self.owner_session() as owner, owner.cursor() as cursor:
                    cursor.execute(f'UPDATE {role_contract.APPROVAL} SET {field}={value}')
                before = self.final_authorization_state(), self.approval_consumed_at()
                with self.assertRaisesMessage(ValueError, 'already issued, or consumed'):
                    self.seed_final_authorization(document)
                self.assertEqual(before, (self.final_authorization_state(), self.approval_consumed_at()))
                self.clear_approval()
        self.seed_approval(document)
        self.seed_final_authorization(document)
        before = self.final_authorization_state()
        with self.assertRaisesMessage(ValueError, 'already issued, or consumed'):
            self.seed_final_authorization(document)
        self.assertEqual(before, self.final_authorization_state())

    def test_direct_writer_refuses_missing_forged_and_unreviewed_hashes(self):
        document = self.reviewed_document()
        for digest in (None, '', 'a' * 64, correction.fingerprint(document), json.dumps(document)):
            with self.subTest(digest=digest):
                self.assert_writer_denied(digest)
        self.seed_approval(document)
        self.seed_final_authorization(document)
        self.assert_writer_denied('b' * 64)

    def test_direct_writer_rejects_postmaster_mismatch_after_successful_plan(self):
        document = self.reviewed_document()
        self.assertTrue(document['plan']['correction']['ready'])
        candidate = deepcopy(document)
        candidate['target']['postmaster_started_at'] = '2000-01-01T00:00:00.000000Z'
        self.assertNotEqual(candidate['target']['postmaster_started_at'],
                            document['target']['postmaster_started_at'])
        self.seed_approval(candidate)
        self.seed_final_authorization(candidate)
        before = self.database_snapshot()
        with self.assertRaisesMessage(DatabaseError, 'live CCA-EOB target differs from trusted approval'):
            self.call_writer(correction.fingerprint(candidate))
        self.assertEqual(before, self.database_snapshot())
        self.assertIsNone(self.approval_consumed_at())
        self.clear_approval()
        self.seed_approval(document)
        self.seed_final_authorization(document)
        self.call_writer(correction.fingerprint(document))
        self.assertIsNotNone(self.approval_consumed_at())

    def test_direct_writer_rejects_approval_acl_drift_after_successful_plan(self):
        from config import historical_temperature_pgqa as guard
        from psycopg import sql

        document = self.reviewed_document()
        self.assertTrue(document['plan']['correction']['ready'])
        self.seed_approval(document)
        self.seed_final_authorization(document)
        digest = correction.fingerprint(document)
        before = self.database_snapshot()
        for privilege, grantee in (('SELECT', 'PUBLIC'), ('SELECT', guard.RUNTIME),
                                   ('UPDATE (consumed_at)', guard.RUNTIME)):
            with self.subTest(privilege=privilege, grantee=grantee):
                recipient = sql.SQL('PUBLIC') if grantee == 'PUBLIC' else sql.Identifier(grantee)
                with self.owner_session() as owner, owner.cursor() as cursor:
                    cursor.execute(sql.SQL('GRANT ' + privilege + ' ON ' + role_contract.APPROVAL +
                                           ' TO {}').format(recipient))
                try:
                    with self.assertRaisesMessage(DatabaseError, 'approval table ACL drift'):
                        self.call_writer(digest)
                    self.assertEqual(before, self.database_snapshot())
                    self.assertIsNone(self.approval_consumed_at())
                finally:
                    with self.owner_session() as owner, owner.cursor() as cursor:
                        cursor.execute(sql.SQL('REVOKE ' + privilege + ' ON ' + role_contract.APPROVAL +
                                               ' FROM {}').format(recipient))
        self.call_writer(digest)
        self.assertIsNotNone(self.approval_consumed_at())

    def test_runtime_cannot_insert_replace_read_or_consume_trusted_approval(self):
        document = self.reviewed_document()
        self.seed_approval(document)
        self.seed_final_authorization(document)
        final_before = self.final_authorization_state()
        before = self.database_snapshot()
        statements = (
            f'SELECT * FROM {role_contract.APPROVAL}',
            f'INSERT INTO {role_contract.APPROVAL} (singleton) VALUES (true)',
            f"UPDATE {role_contract.APPROVAL} SET document='{{}}'::jsonb",
            f"UPDATE {role_contract.APPROVAL} SET plan_hash=repeat('a',64)",
            f'UPDATE {role_contract.APPROVAL} SET expires_at=clock_timestamp()',
            f'UPDATE {role_contract.APPROVAL} SET consumed_at=NULL',
            f"UPDATE {role_contract.APPROVAL} SET final_authorization='{{}}'::jsonb",
            f'UPDATE {role_contract.APPROVAL} SET final_authorization=NULL',
            f'UPDATE {role_contract.APPROVAL} SET final_authorized_at=clock_timestamp()',
            f"UPDATE {role_contract.APPROVAL} SET final_expires_at=clock_timestamp()+interval '300 seconds'",
            f'UPDATE {role_contract.APPROVAL} SET final_expires_at=NULL',
            f"INSERT INTO {role_contract.APPROVAL} (singleton, final_authorization, final_authorized_at, "
            "final_expires_at) VALUES (true, '{}'::jsonb, clock_timestamp(), clock_timestamp())",
            f'DELETE FROM {role_contract.APPROVAL}',
            f'TRUNCATE {role_contract.APPROVAL}',
        )
        with self.production_runtime():
            for statement in statements:
                with self.subTest(statement=statement), self.assertRaises(DatabaseError) as caught:
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement)
                self.assertEqual(getattr(caught.exception.__cause__, 'sqlstate', None), '42501')
        self.assertEqual(before, self.database_snapshot())
        self.assertIsNone(self.approval_consumed_at())
        self.assertEqual(final_before, self.final_authorization_state())

    def test_direct_writer_refuses_expired_approval_and_rebound_release_actor(self):
        document = self.reviewed_document()
        self.seed_approval(document, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.seed_final_authorization(document)
        self.assert_writer_denied(correction.fingerprint(document))
        self.clear_approval()
        for damage in (
            {'actor_id': self.writer.pk}, {'plan_hash': 'e' * 64},
            {'target': {}}, {'restoration_verified': False}, {'review_ref': ''},
        ):
            with self.subTest(release=damage):
                release = {**synthetic_release(document), **damage}
                self.seed_approval(document, release=release)
                self.seed_final_authorization(document, release=release)
                self.assert_writer_denied(correction.fingerprint(document))
                self.clear_approval()

    def test_direct_writer_rechecks_persisted_actor_and_both_institutions(self):
        foreign_admin = get_user_model().objects.create_user(
            username='cca-foreign-admin', email='cca-foreign-admin@example.org')
        OrganizationMembership.objects.create(user=foreign_admin, organization=self.other_org, role='admin')
        document = self.reviewed_document()
        for actor_id in (self.writer.pk, foreign_admin.pk, 999999):
            with self.subTest(actor_id=actor_id):
                candidate = {**deepcopy(document), 'actor_id': actor_id}
                self.seed_approval(candidate)
                self.seed_final_authorization(candidate)
                self.assert_writer_denied(correction.fingerprint(candidate))
                self.clear_approval()
        self.seed_approval(document)
        self.seed_final_authorization(document)
        OrganizationMembership.objects.filter(user=self.admin, organization=self.org).update(is_active=False)
        self.assert_writer_denied(correction.fingerprint(document))

    def test_direct_writer_rejects_snapshot_and_recipe_mismatches_without_any_write(self):
        foreign = Box.objects.create(organization=self.other_org, strain=self.strain,
                                     global_code='SYN-QAA-1.001', box_number='001')
        foreign_measurement = BiologicalMeasurement.objects.create(
            box=foreign, measured_on=date(2026, 1, 7), polyp_count=0, ephyrae_count=0)
        document = self.reviewed_document()
        mutations = (
            ('snapshot', lambda p: p['database_snapshot']['measurements'][0].update(notes='Forged baseline')),
            ('foreign destination', lambda p: p['write_recipe']['moves'][0].update(target_box_id=foreign.pk)),
            ('foreign measurement', lambda p: p['write_recipe']['moves'][0].update(id=foreign_measurement.pk)),
            ('scientific value', lambda p: p['correction']['entries'][0]['before'].update(polyp_count=999)),
            ('zero is not absent', lambda p: p['database_snapshot']['measurements'][2].update(polyp_count=None)),
            ('wrong revisions', lambda p: p['write_recipe']['revisions'][0].update(after=999999)),
            ('wrong audit institution', lambda p: p['write_recipe']['audits'][0].update(organization_id=2)),
            ('wrong audit author', lambda p: p['write_recipe']['audits'][0].update(user_id=self.writer.pk)),
            ('missing summary', lambda p: p['write_recipe']['audits'].pop()),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                candidate = deepcopy(document)
                mutate(candidate['plan'])
                self.seed_approval(candidate)
                self.seed_final_authorization(candidate)
                self.assert_writer_denied(correction.fingerprint(candidate))
                self.clear_approval()

    def test_direct_business_access_is_denied_for_real_rows_in_both_institutions(self):
        foreign = Box.objects.create(organization=self.other_org, strain=self.strain,
                                     global_code='SYN-QAA-1.001', box_number='001')
        foreign_measurement = BiologicalMeasurement.objects.create(
            box=foreign, measured_on=date(2026, 1, 7), polyp_count=0, ephyrae_count=0)
        before = self.database_snapshot()
        with self.production_runtime():
            for box_id, measurement_id in ((1835, self.measurements[0].pk),
                                            (foreign.pk, foreign_measurement.pk)):
                for statement, params in (
                    ('SELECT * FROM public.cultures_box WHERE id=%s', [box_id]),
                    ('UPDATE public.cultures_box SET polyp_state_revision=0 WHERE id=%s', [box_id]),
                    ('UPDATE public.measurements_biologicalmeasurement SET box_id=%s WHERE id=%s',
                     [box_id, measurement_id]),
                    ('DELETE FROM public.measurements_biologicalmeasurement WHERE id=%s', [measurement_id]),
                ):
                    with self.subTest(box_id=box_id, sql=statement), self.assertRaises(DatabaseError) as caught:
                        with transaction.atomic(), connection.cursor() as cursor:
                            cursor.execute(statement, params)
                    self.assertEqual(getattr(caught.exception.__cause__, 'sqlstate', None), '42501')
            payload = production._read_scope(self.admin)
            self.assertEqual({row['id'] for row in payload['boxes']}, {1835, 1836, 1837})
            self.assertNotIn(foreign_measurement.pk, {row['id'] for row in payload['measurements']})
        self.assertEqual(before, self.database_snapshot())

    def test_runtime_has_no_business_account_audit_or_approval_column_authority(self):
        with self.production_runtime(), connection.cursor() as cursor:
            cursor.execute("""SELECT c.relname, a.attname, privilege
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid
                CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) privilege
                WHERE n.nspname='public' AND c.relkind IN ('r','p')
                    AND a.attnum>0 AND NOT a.attisdropped
                    AND NOT (c.relname='django_migrations' AND a.attname IN ('app','name')
                             AND privilege='SELECT')
                    AND has_column_privilege(current_user,c.oid,a.attnum,privilege)""")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("""SELECT c.relname, privilege FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                CROSS JOIN unnest(ARRAY['DELETE','TRUNCATE','TRIGGER','MAINTAIN']) privilege
                WHERE n.nspname='public' AND c.relkind IN ('r','p')
                    AND has_table_privilege(current_user,c.oid,privilege)""")
            self.assertEqual(cursor.fetchall(), [])

    @contextmanager
    def pending_writer(self, digest):
        """A second real runtime login; the owner observes actual lock contention."""
        import threading
        import time
        from django.db import connections
        from config import historical_temperature_pgqa as guard

        ready = threading.Event()
        result = {}

        def run():
            probe = connections['default'].copy(alias='cca_eob_concurrent_writer')
            try:
                guard.assert_disposable(probe)
                with probe.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid(), session_user, current_user')
                    pid, session, current = cursor.fetchone()
                    result.update(pid=pid, identity=(session, current))
                    ready.set()
                    cursor.execute('SELECT public.cca_eob_apply_v1(%s)', [digest])
                result['applied'] = True
            except Exception as error:
                result['error'] = str(error)
                result['sqlstate'] = getattr(error.__cause__, 'sqlstate', None)
                ready.set()
            finally:
                probe.close()
                connections.close_all()

        def wait_blocked():
            self.assertTrue(ready.wait(10), 'Concurrent writer did not connect')
            self.assertEqual(result.get('identity'), (guard.RUNTIME, guard.RUNTIME), result)
            deadline = time.monotonic() + 10
            with self.owner_session() as owner, owner.cursor() as cursor:
                while time.monotonic() < deadline:
                    # pg_stat_activity hides other roles' wait details from this
                    # restricted owner; pg_locks exposes actual ungranted locks.
                    cursor.execute('SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted)',
                                   [result['pid']])
                    if cursor.fetchone()[0]:
                        return
                    if 'error' in result or result.get('applied'):
                        break
                    time.sleep(0.05)
            self.fail(f'Concurrent writer did not wait for the held lock: {result}')

        thread = threading.Thread(target=run, name='cca-eob-concurrent-writer', daemon=True)
        thread.start()
        try:
            yield result, wait_blocked
        finally:
            # Callers release their transaction locks before leaving this context.
            guard.join_workers([thread])

    def test_concurrent_writers_consume_the_same_approval_only_once(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        self.seed_approval(document)
        self.seed_final_authorization(document)
        final_before = self.final_authorization_state()
        from config import historical_temperature_pgqa as guard

        with self.production_runtime():
            first = connection.copy(alias='cca_eob_first_writer')
            try:
                guard.assert_disposable(first)
                first.set_autocommit(False)
                with first.cursor() as cursor:
                    cursor.execute('SELECT public.cca_eob_apply_v1(%s)', [digest])
                with self.pending_writer(digest) as (result, wait_blocked):
                    try:
                        wait_blocked()
                    except BaseException:
                        first.rollback()
                        raise
                    else:
                        first.commit()
            finally:
                first.rollback()
                first.close()
        self.assertNotIn('applied', result)
        self.assertIn('error', result)
        self.assertIsNotNone(self.approval_consumed_at())
        self.assertEqual(final_before, self.final_authorization_state())
        self.assertEqual(AuditLog.objects.count(), 4)
        targets = {move['id']: move['target_box_id'] for move in document['plan']['write_recipe']['moves']}
        for raw in document['plan']['correction']['baseline']['measurements']:
            self.assertEqual(json_snapshot(BiologicalMeasurement.objects.get(pk=raw['id'])),
                             {**raw, 'box_id': targets.get(raw['id'], raw['box_id'])})


    def test_approval_expiring_while_waiting_for_box_lock_does_not_write(self):
        import time

        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        before = self.database_snapshot()
        with self.owner_session() as blocker:
            blocker.set_autocommit(False)
            with blocker.cursor() as cursor:
                cursor.execute('SELECT id FROM public.cultures_box WHERE id=1835 FOR UPDATE')
            self.seed_approval(document, expires_at=datetime.now(timezone.utc) + timedelta(seconds=3))
            self.seed_final_authorization(document)
            with self.production_runtime(), self.pending_writer(digest) as (result, wait_blocked):
                try:
                    wait_blocked()
                    with blocker.cursor() as cursor:
                        cursor.execute(f'SELECT expires_at-clock_timestamp() FROM {role_contract.APPROVAL}')
                        remaining = cursor.fetchone()[0].total_seconds()
                    time.sleep(max(0, remaining) + 0.1)
                finally:
                    blocker.rollback()
        self.assertNotIn('applied', result)
        self.assertIn('expired', result.get('error', ''))
        self.assertIsNone(self.approval_consumed_at())
        self.assertEqual(before, self.database_snapshot())

    def test_final_authorization_expiring_while_waiting_for_box_lock_does_not_write(self):
        import time

        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        self.seed_approval(document)
        before = self.database_snapshot()
        with self.owner_session() as blocker:
            blocker.set_autocommit(False)
            with blocker.cursor() as cursor:
                cursor.execute('SELECT id FROM public.cultures_box WHERE id=1835 FOR UPDATE')
            self.seed_final_authorization(document, expires_at=datetime.now(timezone.utc) + timedelta(seconds=3))
            final_before = self.final_authorization_state()
            with self.production_runtime(), self.pending_writer(digest) as (result, wait_blocked):
                try:
                    wait_blocked()
                    with blocker.cursor() as cursor:
                        cursor.execute(f'SELECT final_expires_at-clock_timestamp() FROM {role_contract.APPROVAL}')
                        remaining = cursor.fetchone()[0].total_seconds()
                    time.sleep(max(0, remaining) + 0.1)
                finally:
                    blocker.rollback()
        self.assertNotIn('applied', result)
        self.assertIn('final', result.get('error', '').lower())
        self.assertIsNone(self.approval_consumed_at())
        self.assertEqual(final_before, self.final_authorization_state())
        self.assertEqual(before, self.database_snapshot())

    def test_concurrent_state_change_rejects_the_waiting_reviewed_writer(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        self.seed_approval(document)
        self.seed_final_authorization(document)
        before = self.database_snapshot()
        with self.owner_session() as blocker:
            blocker.set_autocommit(False)
            with blocker.cursor() as cursor:
                cursor.execute('SELECT id FROM public.cultures_box WHERE id=1835 FOR UPDATE')
            with self.production_runtime(), self.pending_writer(digest) as (result, wait_blocked):
                try:
                    wait_blocked()
                    with blocker.cursor() as cursor:
                        cursor.execute("UPDATE public.cultures_box SET notes='Concurrent reviewed writer' WHERE id=1835")
                    blocker.commit()
                finally:
                    blocker.rollback()
        self.assertNotIn('applied', result)
        self.assertIn('Stale or blocked CCA-EOB snapshot', result.get('error', ''))
        self.assertIsNone(self.approval_consumed_at())
        after = self.database_snapshot()
        for table in before.keys() - {'cultures_box'}:
            self.assertEqual(before[table], after[table], table)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_expiry_during_audit_rolls_back_science_audits_and_consumption(self):
        with self.owner_session() as owner, owner.cursor() as cursor:
            cursor.execute("""CREATE FUNCTION public.cca_expiry_audit_probe() RETURNS trigger
                LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
                BEGIN IF NEW.action='import' AND NEW.object_id='cca_eob_v2_20261016_v3' THEN
                    PERFORM pg_catalog.pg_sleep(6); END IF; RETURN NEW; END $$;
                CREATE TRIGGER cca_expiry_audit_probe BEFORE INSERT ON public.audit_auditlog
                FOR EACH ROW EXECUTE FUNCTION public.cca_expiry_audit_probe()""")
        try:
            for scope in ('plan', 'final'):
                with self.subTest(scope=scope):
                    document = self.reviewed_document()
                    before = self.database_snapshot()
                    expiry = datetime.now(timezone.utc) + timedelta(seconds=5)
                    self.seed_approval(document, expires_at=expiry if scope == 'plan' else None)
                    self.seed_final_authorization(document, expires_at=expiry if scope == 'final' else None)
                    final_before = self.final_authorization_state()
                    with self.assertRaises(DatabaseError) as caught:
                        self.call_writer(correction.fingerprint(document))
                    if scope == 'final':
                        self.assertIn('final', str(caught.exception).lower())
                    else:
                        self.assertIn('CCA-EOB approval expired during apply', str(caught.exception))
                    self.assertEqual(before, self.database_snapshot())
                    self.assertIsNone(self.approval_consumed_at())
                    self.assertEqual(final_before, self.final_authorization_state())
                    self.clear_approval()
        finally:
            with self.owner_session() as owner, owner.cursor() as cursor:
                cursor.execute('DROP TRIGGER cca_expiry_audit_probe ON public.audit_auditlog; '
                               'DROP FUNCTION public.cca_expiry_audit_probe()')

    def test_direct_writer_atomic_rollback_then_success_and_replay_denial(self):
        document = self.reviewed_document()
        digest = correction.fingerprint(document)
        self.seed_approval(document)
        self.seed_final_authorization(document)
        final_before = self.final_authorization_state()
        before = self.database_snapshot()
        with self.production_runtime():
            with self.assertRaisesMessage(RuntimeError, 'Rollback the successful capability'):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute('SELECT public.cca_eob_apply_v1(%s)', [digest])
                    raise RuntimeError('Rollback the successful capability')
        self.assertEqual(before, self.database_snapshot())
        self.assertIsNone(self.approval_consumed_at())
        self.assertEqual(final_before, self.final_authorization_state())
        self.call_writer(digest)
        consumed = self.approval_consumed_at()
        self.assertIsNotNone(consumed)
        self.assertEqual(AuditLog.objects.count(), 4)
        self.assertTrue(all(a.organization_id == 1 and a.user_id == self.admin.pk
                            for a in AuditLog.objects.all()))
        after = self.database_snapshot()
        mutable = {'cultures_box', 'measurements_biologicalmeasurement', 'audit_auditlog'}
        for table in before.keys() - mutable:
            self.assertEqual(before[table], after[table], table)
        targets = {move['id']: move['target_box_id'] for move in document['plan']['write_recipe']['moves']}
        for raw in document['plan']['correction']['baseline']['measurements']:
            self.assertEqual(json_snapshot(BiologicalMeasurement.objects.get(pk=raw['id'])),
                             {**raw, 'box_id': targets.get(raw['id'], raw['box_id'])})
        with self.assertRaises(DatabaseError):
            self.call_writer(digest)
        self.assertEqual(after, self.database_snapshot())
        self.assertEqual(consumed, self.approval_consumed_at())
        self.assertEqual(final_before, self.final_authorization_state())
        with self.assertRaisesMessage(ValueError, 'already issued, or consumed'):
            self.seed_final_authorization(document)


class RoleSqlContractTests(SimpleTestCase):
    """Connection-free review gates for the emitted provisioning and rollback SQL."""

    @staticmethod
    def emitter():
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "deploy" / "scripts" / "cca_eob_role_sql.py"
        spec = importlib.util.spec_from_file_location("cca_eob_role_sql_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_every_action_is_pinned_to_the_local_primary_and_postgres_login(self):
        emitter = self.emitter()
        for action in ("provision", "verify", "rollback"):
            with self.subTest(action):
                text = emitter.emit(action)
                self.assertNotIn("\r", text)
                for pin in ("current_database()<>'polypbase'", "session_user<>'postgres'",
                            "inet_server_addr() IS NOT NULL", "current_setting('port') <> '5433'",
                            "pg_is_in_recovery()", "NOT BETWEEN 180000 AND 189999"):
                    self.assertIn(pin, text)

    def test_provisioning_grants_only_capabilities_migration_columns_and_peer_login(self):
        import re

        text = self.emitter().emit("provision")
        for required in (
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 1 PASSWORD NULL",
                "SECURITY DEFINER", "SET search_path = pg_catalog, pg_temp",
                "REVOKE ALL ON FUNCTION public.cca_eob_lock_v1(bigint) FROM PUBLIC",
                "GRANT EXECUTE ON FUNCTION public.cca_eob_lock_v1(bigint) TO polypbase_cca_eob",
                "ALTER FUNCTION public.cca_eob_lock_v1(bigint) OWNER TO polypbase;", "BEGIN;", "COMMIT;"):
            self.assertIn(required, text)
        for forbidden in ("GRANT ALL", "ALL PRIVILEGES", "ON ALL TABLES", "ON ALL SEQUENCES", "GRANT OPTION",
                          "CASCADE", "TO PUBLIC", "DEFAULT PRIVILEGES", "ALTER SYSTEM", "pg_hba", "CREATE USER"):
            self.assertNotIn(forbidden, text)
        for signature, *_ in role_contract.helper_specs():
            self.assertIn(f'REVOKE ALL ON FUNCTION {signature} FROM PUBLIC', text)
            self.assertIn(f'GRANT EXECUTE ON FUNCTION {signature} TO {role_contract.ROLE}', text)
            self.assertIn(f'ALTER FUNCTION {signature} OWNER TO polypbase;', text)
        self.assertEqual(role_contract.READ_TABLES, ())
        self.assertEqual(role_contract.READ_COLUMNS, {'django_migrations': ('app', 'name')})
        self.assertIn('GRANT SELECT (app, name) ON public.django_migrations TO polypbase_cca_eob', text)
        self.assertIn('ALTER TABLE public.cca_eob_approval_v1 OWNER TO postgres', text)
        self.assertIn('GRANT SELECT ON public.cca_eob_approval_v1 TO polypbase', text)
        # Only the writer owner, never the runtime, can consume the trusted approval.
        self.assertEqual(re.findall(
            r"GRANT\s+(?:UPDATE|INSERT|DELETE|TRUNCATE|REFERENCES|TRIGGER|MAINTAIN)\s+ON", text), [])
        written = re.findall(r"GRANT\s+(UPDATE|INSERT)\s*\(([^)]*)\)\s+ON\s+(\S+)", text)
        self.assertEqual(written, [('UPDATE', 'consumed_at', role_contract.APPROVAL)])
        self.assertNotIn('INSERT INTO public.cca_eob_approval_v1', text)

    def test_rollback_is_restricted_marker_guarded_and_never_cascades(self):
        text = self.emitter().emit("rollback")
        for required in ("DROP OWNED BY polypbase_cca_eob RESTRICT", "STOP: unexpected helper definition/metadata:",
                         "STOP: correction login has live sessions", "STOP: unexpected role identity",
                         "STOP: role owns objects; do not use DROP OWNED", "DROP ROLE IF EXISTS polypbase_cca_eob"):
            self.assertIn(required, text)
        self.assertNotIn("CASCADE", text)

    def test_approval_emission_is_offline_bound_expiring_and_administrator_only(self):
        import re

        document = {
            'schema': 'cca_eob_production_plan_v2', 'target': socket_target(), 'actor_id': 7,
            'plan': {
                'correction': {'operation': correction.OPERATION, 'organization_id': 1,
                               'v2_sha256': role_contract.V2_SHA256, 'ready': True, 'blockers': []},
                'database_snapshot': {}, 'write_recipe': {},
            },
        }
        digest = correction.fingerprint(document)
        release = synthetic_release(document)
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        emitter = self.emitter()
        sql = emitter.approval_sql(document, digest, release, expiry)
        for required in (
            "session_user<>'postgres'", "approval_owner oid := 'postgres'::regrole",
            'approval_table.relowner <> approval_owner',
            f'INSERT INTO {role_contract.APPROVAL}', digest, expiry,
            'has_column_privilege', 'consumed_at',
        ):
            self.assertIn(required, sql)
        self.assertNotIn('ON CONFLICT', sql)
        columns = re.search(r'INSERT INTO public\.cca_eob_approval_v1\s*\(([^)]*)\)', sql)
        self.assertIsNotNone(columns)
        self.assertEqual([name.strip() for name in columns.group(1).split(',')], [
            'singleton', 'plan_hash', 'document', 'release', 'expires_at', 'consumed_at',
        ])
        for candidate, reviewed_hash, evidence, expires_at in (
            (document, 'f' * 64, release, expiry),
            ({**document, 'actor_id': 8}, digest, release, expiry),
            (document, digest, {**release, 'actor_id': 8}, expiry),
            (document, digest, {**release, 'restoration_verified': False}, expiry),
            (document, digest, release, '2020-01-01T00:00:00+00:00'),
            (document, digest, release, '2099-01-01T00:00:00'),
        ):
            with self.subTest(hash=reviewed_hash, expires_at=expires_at), self.assertRaises(ValueError):
                emitter.approval_sql(candidate, reviewed_hash, evidence, expires_at)

    def test_helper_body_is_fixed_qualified_lock_only_sql(self):
        import re

        body = role_contract.lock_body()
        self.assertNotIn("EXECUTE", body)
        self.assertNotIn("format(", body)
        stripped = re.sub(r"FOR (?:NO KEY )?UPDATE", "FOR LOCK", body)
        for keyword in ("UPDATE", "INSERT", "DELETE", "TRUNCATE", "ALTER", "DROP", "CREATE", "GRANT", "SET "):
            self.assertNotIn(keyword, stripped)
        self.assertEqual(re.findall(r"(?:FROM|JOIN|LOCK TABLE)\s+(?!public\.)(?!\()\w+", body), [])
        self.assertEqual(sorted(re.findall(r"LOCK TABLE (\S+) IN (.+?);", body)), [
            ("public.measurements_biologicalmeasurement", "SHARE ROW EXCLUSIVE MODE"),
            ("public.measurements_historicaltemperaturereading", "SHARE MODE")])
        # Only the actor id is caller-controlled; the CCA login is compared to a literal.
        self.assertEqual(role_contract.lock_definition().count("actor_id bigint"), 1)
        with self.assertRaises(ValueError):
            role_contract.lock_definition("x; DROP ROLE polypbase")
