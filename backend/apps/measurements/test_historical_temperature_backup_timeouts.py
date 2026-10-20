"""Connection-free backup timeout admission tests; never export or run pg_dump."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from . import historical_temperature_backup_timeouts as timeouts


class BackupTimeoutTests(SimpleTestCase):
    def database(self, idle=1_500_000, transaction=3_600_000, *, rows=None):
        database = MagicMock()
        cursor = database.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = rows if rows is not None else [
            ("idle_in_transaction_session_timeout", idle, "ms"),
            ("transaction_timeout", transaction, "ms"),
        ]
        return database, cursor

    def test_budget_matches_two_archive_commands_plus_margin(self):
        self.assertEqual(timeouts.database_backup.TIMEOUT_SECONDS, 600)
        self.assertEqual(timeouts.IDLE_MARGIN_SECONDS, 300)
        self.assertEqual(timeouts.MIN_TRANSACTION_SECONDS, 3600)

    def test_idle_boundary_and_existing_600_second_default(self):
        for idle in (1, 600_000, 1_200_000, 1_499_000, 1_499_999):
            database, _ = self.database(idle=idle)
            with self.subTest(idle=idle), self.assertRaisesRegex(
                ValueError, "idle_in_transaction_session_timeout=.*1500000ms"
            ):
                timeouts.check_backup_timeouts(database)
        for idle in (1_500_000, 1_500_001, 3_600_000):
            database, _ = self.database(idle=idle)
            with self.subTest(idle=idle):
                self.assertEqual(
                    timeouts.check_backup_timeouts(database)["idle_in_transaction_session_timeout"], idle
                )

    def test_transaction_retains_existing_one_hour_minimum(self):
        for transaction in (1, 1_500_000, 1_800_000, 3_599_999):
            database, _ = self.database(transaction=transaction)
            with self.subTest(transaction=transaction), self.assertRaisesRegex(
                ValueError, "transaction_timeout=.*3600000ms"
            ):
                timeouts.check_backup_timeouts(database)
        for transaction in (3_600_000, 3_600_001):
            database, _ = self.database(transaction=transaction)
            with self.subTest(transaction=transaction):
                self.assertEqual(timeouts.check_backup_timeouts(database)["transaction_timeout"], transaction)

    def test_disabled_timeouts_are_accepted_independently(self):
        for idle, transaction in ((0, 0), (0, 3_600_000), (1_500_000, 0)):
            database, _ = self.database(idle, transaction)
            with self.subTest(idle=idle, transaction=transaction):
                self.assertEqual(timeouts.check_backup_timeouts(database), {
                    "idle_in_transaction_session_timeout": idle,
                    "transaction_timeout": transaction,
                })

    def test_budgets_follow_archive_timeout_instead_of_stale_literals(self):
        with patch.object(timeouts.database_backup, "TIMEOUT_SECONDS", 2000):
            for idle, transaction, expected in (
                (4_299_999, 4_300_000, "idle_in_transaction_session_timeout"),
                (4_300_000, 4_299_999, "transaction_timeout"),
            ):
                database, _ = self.database(idle, transaction)
                with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                    timeouts.check_backup_timeouts(database)
            database, _ = self.database(4_300_000, 4_300_000)
            self.assertEqual(timeouts.check_backup_timeouts(database)["transaction_timeout"], 4_300_000)

    def test_missing_duplicate_and_unknown_settings_fail_closed(self):
        valid = [
            ("idle_in_transaction_session_timeout", 1_500_000, "ms"),
            ("transaction_timeout", 3_600_000, "ms"),
        ]
        for rows in ([], valid[:1], valid[1:], valid + valid[:1],
                     valid + [("statement_timeout", 0, "ms")]):
            database, _ = self.database(rows=rows)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                timeouts.check_backup_timeouts(database)

    def test_malformed_values_or_units_are_not_disabled_or_converted(self):
        for name in ("idle_in_transaction_session_timeout", "transaction_timeout"):
            for value, unit in ((None, "ms"), (-1, "ms"), (False, "ms"),
                                (True, "ms"), (1_500_000.0, "ms"), ("1500000", "ms"),
                                ("600s", "ms"), (0, None), (0, "s"), (0, "")):
                rows = [("idle_in_transaction_session_timeout", 0, "ms"),
                        ("transaction_timeout", 0, "ms")]
                rows = [(key, value, unit) if key == name else (key, current, current_unit)
                        for key, current, current_unit in rows]
                database, _ = self.database(rows=rows)
                with self.subTest(name=name, value=value, unit=unit), self.assertRaises(ValueError):
                    timeouts.check_backup_timeouts(database)

    def test_reads_effective_milliseconds_and_never_changes_session(self):
        database, cursor = self.database()
        timeouts.check_backup_timeouts(database)
        database.cursor.assert_called_once_with()
        cursor.execute.assert_called_once_with(timeouts._TIMEOUT_SQL)
        self.assertIn("setting::bigint", timeouts._TIMEOUT_SQL)
        self.assertIn("pg_catalog.pg_settings", timeouts._TIMEOUT_SQL)
        self.assertEqual([call[0] for call in database.method_calls], ["cursor"])
        self.assertEqual([call[0] for call in cursor.method_calls], ["execute", "fetchall"])

    def test_query_and_connection_failures_propagate_before_dump(self):
        for operation in ("execute", "fetchall"):
            database, cursor = self.database()
            getattr(cursor, operation).side_effect = RuntimeError("session unavailable")
            dump = MagicMock()
            with self.subTest(operation=operation), self.assertRaisesRegex(RuntimeError, "session unavailable"):
                timeouts.check_backup_timeouts(database)
                dump()
            dump.assert_not_called()
        database, _ = self.database()
        database.cursor.side_effect = RuntimeError("disconnected")
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            timeouts.check_backup_timeouts(database)

    def test_insufficient_either_control_session_prevents_following_export_or_dump(self):
        for invalid_index in (0, 1):
            databases = [self.database()[0], self.database()[0]]
            databases[invalid_index] = self.database(idle=600_000)[0]
            export_or_dump = MagicMock()
            with self.subTest(invalid_index=invalid_index), self.assertRaises(ValueError):
                for database in databases:
                    timeouts.check_backup_timeouts(database)
                export_or_dump()
            export_or_dump.assert_not_called()
