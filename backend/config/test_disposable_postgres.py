"""Fixture cleanup must remain confined to an attested disposable test session."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase

from . import disposable_postgres as cleanup


class DisposablePostgresCleanupTests(SimpleTestCase):
    def setUp(self):
        self.connection = MagicMock(vendor="postgresql", in_atomic_block=False)
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.enterContext(patch.object(cleanup, "connection", self.connection))
        self.verify = self.enterContext(patch.object(cleanup, "require_disposable_cluster"))
        self.atomic = self.enterContext(patch.object(cleanup.transaction, "atomic", return_value=nullcontext()))
        self.cursor.fetchone.return_value = ("origin",)
        self.original = Mock()

    def statements(self):
        return [call.args[0] for call in self.cursor.execute.call_args_list]

    def test_unverified_database_never_bypasses_triggers(self):
        self.verify.side_effect = RuntimeError("Not attested")
        with self.assertRaisesMessage(RuntimeError, "Not attested"):
            cleanup._verified_flush(self, self.original)
        self.original.assert_not_called()
        self.atomic.assert_not_called()
        self.assertEqual(self.statements(), [])

    def test_outer_transaction_is_refused_before_bypass(self):
        self.connection.in_atomic_block = True
        with self.assertRaisesMessage(RuntimeError, "own transaction"):
            cleanup._verified_flush(self, self.original)
        self.original.assert_not_called()
        self.atomic.assert_not_called()

    def test_existing_replica_role_is_not_accepted(self):
        self.cursor.fetchone.return_value = ("replica",)
        with self.assertRaisesMessage(RuntimeError, "normal trigger execution"):
            cleanup._verified_flush(self, self.original)
        self.original.assert_not_called()
        self.assertNotIn("SET LOCAL session_replication_role = replica", self.statements())

    def test_only_flush_is_inside_the_local_bypass(self):
        cleanup._verified_flush(self, self.original)
        self.verify.assert_called_once_with()
        self.original.assert_called_once_with(self)
        self.assertEqual(self.statements(), ["SHOW session_replication_role",
                                            "SET LOCAL session_replication_role = replica",
                                            "SHOW session_replication_role"])

    def test_failed_flush_still_checks_restoration_and_propagates_error(self):
        self.original.side_effect = ValueError("Flush failed")
        with self.assertRaisesMessage(ValueError, "Flush failed"):
            cleanup._verified_flush(self, self.original)
        self.assertEqual(self.statements()[-1], "SHOW session_replication_role")

    def test_failed_role_restoration_is_reported(self):
        self.cursor.fetchone.side_effect = [("origin",), ("replica",)]
        with self.assertRaisesMessage(RuntimeError, "not restored"):
            cleanup._verified_flush(self, self.original)

    def test_install_and_uninstall_preserve_the_existing_teardown(self):
        original = cleanup.TransactionTestCase._fixture_teardown
        self.enterContext(patch.object(cleanup, "_ORIGINAL_TEARDOWN", None))
        self.enterContext(patch.object(cleanup, "_launcher_lease", return_value=object()))
        self.addCleanup(cleanup.uninstall_disposable_flush)
        cleanup.install_disposable_flush()
        cleanup.install_disposable_flush()
        self.assertIs(cleanup._ORIGINAL_TEARDOWN, original)
        self.assertIs(cleanup.TransactionTestCase._fixture_teardown, cleanup._installed_teardown)
        cleanup.uninstall_disposable_flush()
        self.assertIs(cleanup.TransactionTestCase._fixture_teardown, original)

    def test_runner_install_requires_a_live_lease_even_with_environment_markers(self):
        with patch.object(cleanup, "_launcher_lease", return_value=None):
            with self.assertRaisesMessage(RuntimeError, "fresh-cluster launcher"):
                cleanup.install_disposable_flush()
        self.verify.assert_not_called()

    def test_missing_lease_is_refused_before_any_database_access(self):
        with patch.object(cleanup, "_launcher_lease", return_value=None):
            with self.assertRaisesMessage(RuntimeError, "live fresh-cluster launcher"):
                self.real_gate()
        self.connection.cursor.assert_not_called()

    def test_live_attestation_rejects_wrong_database_path_or_system_identifier(self):

        # Exercise the real gate rather than the flush wrapper's mock.
        with patch.object(cleanup, "_launcher_lease", return_value=SimpleNamespace(
                data=str(Path("fresh-data").resolve()), system_identifier="123")), \
                patch.object(cleanup, "import_module") as module:
            self.connection.creation._get_test_db_name.return_value = "test_subculture_qa"
            for row in (("shared", str(Path("fresh-data").resolve()), "123"),
                        ("test_subculture_qa", str(Path("other-data").resolve()), "123"),
                        ("test_subculture_qa", str(Path("fresh-data").resolve()), "456")):
                self.cursor.fetchone.return_value = row
                with self.assertRaises(RuntimeError):
                    self.real_gate()
            module.assert_not_called()

    real_gate = staticmethod(cleanup.require_disposable_cluster)
