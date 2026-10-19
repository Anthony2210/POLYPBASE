"""Connection-free regression tests for disposable target selection."""

import os
from unittest import TestCase, main
from unittest.mock import patch

from .runtime_config import load_target


class RuntimeTargetTests(TestCase):
    def environment(self):
        # Synthetic parser fixtures only; these do not describe a real cluster.
        return {
            "POLYPBASE_QA_HOST": "127.0.0.1",
            "POLYPBASE_QA_PORT": "65432",
            "POLYPBASE_QA_DATABASE": "synthetic_qa",
            "POLYPBASE_QA_SYSTEM_IDENTIFIER": "1",
            "POLYPBASE_QA_OWNER": "synthetic_owner",
            "POLYPBASE_QA_RUNTIME": "synthetic_runtime",
        }

    def test_explicit_loopback_targets(self):
        for host in ("127.0.0.1", "::1"):
            with self.subTest(host=host), patch.dict(os.environ, self.environment(), clear=True):
                os.environ["POLYPBASE_QA_HOST"] = host
                target = load_target()
                self.assertEqual(target[0], host)
                self.assertEqual(len(target), 6)

    def test_every_identity_is_required(self):
        for name in self.environment():
            with self.subTest(name=name), patch.dict(os.environ, self.environment(), clear=True):
                del os.environ[name]
                with self.assertRaises(RuntimeError):
                    load_target()

    def test_invalid_routes_and_identities_fail_closed(self):
        invalid = {
            "HOST": ("localhost", "192.0.2.1", "127.0.0.1,127.0.0.2", "/tmp/socket", ""),
            "PORT": ("55438", "0", "65536", "065432", "65432,65433", "invalid", " 65432"),
            "DATABASE": ("postgres", "template0", "template1", "database name", "db?host=remote"),
            "OWNER": ("postgres", "pg_admin", "name with spaces"),
            "RUNTIME": ("postgres", "pg_admin", "synthetic_owner"),
            "SYSTEM_IDENTIFIER": ("0", "-1", "1.0", "18446744073709551616", ""),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), patch.dict(os.environ, self.environment(), clear=True):
                    os.environ["POLYPBASE_QA_" + field] = value
                    with self.assertRaises(RuntimeError):
                        load_target()

    def test_inherited_application_configuration_is_not_a_fallback(self):
        with patch.dict(os.environ, {"POSTGRES_DB": "synthetic_qa", "PGHOST": "127.0.0.1"}, clear=True):
            with self.assertRaises(RuntimeError):
                load_target()

    def test_ui_prefix_is_independent(self):
        environment = {name.replace("POLYPBASE_QA_", "POLYPBASE_UI_TEMP_DB_"): value for name, value in self.environment().items()}
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(load_target("POLYPBASE_UI_TEMP_DB_")[2], "synthetic_qa")
            with self.assertRaises(RuntimeError):
                load_target()


if __name__ == "__main__":
    main()
