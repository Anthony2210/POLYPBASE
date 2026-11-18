"""Historical PostgreSQL routing must fail closed without touching real schemas."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from .migration_test_utils import (
    BOX_1902_CAPABILITY, historical_rehearsal_database, rehearsal_latest_targets,
)


class HistoricalRehearsalDatabaseTests(SimpleTestCase):
    def setUp(self):
        self.config = {
            "HOST": "127.0.0.1", "PORT": "54321", "USER": "subculture_qa",
            "PASSWORD": "", "NAME": "test_subculture_qa", "OPTIONS": {"options": ""},
            "TEST": {"NAME": "test_subculture_qa"},
        }
        self.connection = MagicMock(vendor="postgresql", in_atomic_block=False)
        self.connection.settings_dict = deepcopy(self.config)
        self.lease = MagicMock()
        self.lease.require_launch.return_value = SimpleNamespace(port=54321)
        self.admin = self.lease.connect_leased.return_value.__enter__.return_value
        self.admin.execute.return_value.fetchone.return_value = (123,)
        self.connection.cursor.return_value.__enter__.return_value.fetchone.side_effect = (
            lambda: (self.connection.settings_dict["NAME"], 123)
        )
        self.patch = patch("config.migration_test_utils.import_module", return_value=self.lease)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def statements(self):
        return [str(call.args[0]) for call in self.admin.execute.call_args_list]

    def test_sqlite_is_unchanged_and_never_requests_a_lease(self):
        self.connection.vendor = "sqlite"
        with historical_rehearsal_database(self.connection):
            self.assertEqual(self.connection.settings_dict, self.config)
        self.lease.require_launch.assert_not_called()
        self.connection.close.assert_not_called()

    def test_no_live_lease_fails_before_connecting_or_changing_settings(self):
        self.lease.require_launch.side_effect = RuntimeError("No live launcher")
        with self.assertRaisesMessage(RuntimeError, "No live launcher"):
            with historical_rehearsal_database(self.connection):
                self.fail("No rehearsal may run")
        self.lease.connect_leased.assert_not_called()
        self.connection.close.assert_not_called()
        self.assertEqual(self.connection.settings_dict, self.config)

    def test_other_database_or_endpoint_is_rejected(self):
        for key, value in (("NAME", "shared"), ("HOST", "remote"), ("PORT", "54322"),
                           ("USER", "polypbase"), ("PASSWORD", "not-empty")):
            with self.subTest(key=key):
                self.connection.settings_dict = {**deepcopy(self.config), key: value}
                with self.assertRaisesMessage(RuntimeError, "Refuse historical rehearsal"):
                    with historical_rehearsal_database(self.connection):
                        self.fail("No rehearsal may run")
        self.lease.connect_leased.assert_not_called()

    def test_new_database_is_verified_and_original_settings_restored_on_failure(self):
        with self.assertRaisesMessage(ValueError, "Rehearsal failure"):
            with historical_rehearsal_database(self.connection):
                name = self.connection.settings_dict["NAME"]
                self.assertRegex(name, r"^test_history_[0-9a-f]{32}$")
                self.assertNotEqual(name, self.config["NAME"])
                self.lease.verify_server.assert_called_once_with(self.connection.connection)
                raise ValueError("Rehearsal failure")
        self.assertEqual(self.connection.settings_dict, self.config)
        self.assertTrue(self.statements()[0].startswith("Composed([SQL('CREATE DATABASE "))
        self.assertIn(name, self.statements()[-1])
        self.assertIn("DROP DATABASE", self.statements()[-1])
        self.assertFalse(any("FORCE" in statement for statement in self.statements()))
        self.lease.verify_server.assert_called_with(self.admin)

    def test_collision_is_not_adopted_or_dropped(self):
        self.admin.execute.side_effect = RuntimeError("Already exists")
        with self.assertRaisesMessage(RuntimeError, "Already exists"):
            with historical_rehearsal_database(self.connection):
                self.fail("No rehearsal may run")
        self.assertEqual(len(self.statements()), 1)
        self.connection.close.assert_not_called()
        self.assertEqual(self.connection.settings_dict, self.config)

    def test_replaced_database_is_never_dropped(self):
        self.admin.execute.return_value.fetchone.side_effect = [(123,), (456,)]
        with self.assertRaisesMessage(RuntimeError, "Refuse dropping a replaced"):
            with historical_rehearsal_database(self.connection):
                pass
        self.assertFalse(any("DROP DATABASE" in statement for statement in self.statements()))
        self.assertEqual(self.connection.settings_dict, self.config)

    def test_failed_target_attestation_restores_settings_before_cleanup(self):
        self.lease.verify_server.side_effect = [RuntimeError("Wrong server"), None]
        with self.assertRaisesMessage(RuntimeError, "Wrong server"):
            with historical_rehearsal_database(self.connection):
                self.fail("No rehearsal may run")
        self.assertEqual(self.connection.settings_dict, self.config)
        self.assertIn("DROP DATABASE", self.statements()[-1])

    def test_failed_cleanup_attestation_never_drops(self):
        self.lease.verify_server.side_effect = [None, RuntimeError("Stale launch")]
        with self.assertRaisesMessage(RuntimeError, "Stale launch"):
            with historical_rehearsal_database(self.connection):
                pass
        self.assertEqual(self.connection.settings_dict, self.config)
        self.assertFalse(any("DROP DATABASE" in statement for statement in self.statements()))

    def test_latest_targets_exclude_real_capability_only_for_postgresql(self):
        loader = MigrationLoader(None)
        targets = rehearsal_latest_targets(loader, "postgresql")
        closure = {node for target in targets for node in loader.graph.forwards_plan(target)}
        self.assertNotIn(BOX_1902_CAPABILITY, closure)
        self.assertIn(("measurements", "0017_bucket_probe_last_measured_index"), closure)
        self.assertIn(("cultures", "0014_box_movement_week_matches_moved_at"), closure)
        self.assertEqual(rehearsal_latest_targets(loader, "sqlite"), loader.graph.leaf_nodes())
        self.assertEqual(len(loader.graph.nodes[BOX_1902_CAPABILITY].operations), 1)
