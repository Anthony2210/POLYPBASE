"""Offline regression checks for the dedicated PostgreSQL workflow boundary."""

import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "backend/config/historical_movements_ci_settings.py"
WORKFLOW_PATH = ROOT / ".github/workflows/historical-movements-postgres.yml"
SPEC = importlib.util.spec_from_file_location("hm_ci_validation", ROOT / "scripts/validate_historical_movements_ci.py")
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


class CISettingsTests(unittest.TestCase):
    def environment(self):
        return {
            "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux", "HM_CI_POSTGRES": "1", "POSTGRES_DB": "",
            "POLYPBASE_TEST_POSTGRES": "0",
        }

    def load_settings(self, environment, *, dotenv=False):
        package = types.ModuleType("config")
        package.__path__ = []
        base = types.ModuleType("config.test_settings")
        base.__all__ = ["DATABASES", "DATABASE_ROUTERS"]
        base.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        base.DATABASE_ROUTERS = ["must.not.be.used"]
        namespace = {"__file__": str(SETTINGS_PATH), "__name__": validation.SETTINGS_MODULE, "__package__": "config"}
        # Never import real base settings or read dotenv during offline checks.
        with patch.dict(os.environ, environment, clear=True), patch.dict(
            sys.modules, {"config": package, "config.test_settings": base}
        ), patch.object(Path, "exists", return_value=dotenv):
            exec(compile(SETTINGS_PATH.read_text(encoding="utf-8"), str(SETTINGS_PATH), "exec"), namespace)
        return namespace

    def test_exact_postgresql_configuration_replaces_sqlite_without_fallback(self):
        settings = self.load_settings(self.environment())
        database = settings["DATABASES"]["default"]
        self.assertEqual(set(settings["DATABASES"]), {"default"})
        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["HOST"], "127.0.0.1")
        self.assertEqual(database["PORT"], "55439")
        self.assertEqual(database["NAME"], validation.DATABASE_NAME)
        self.assertEqual(database["USER"], validation.DATABASE_NAME)
        self.assertEqual(database["PASSWORD"], "")
        self.assertEqual(database["TEST"]["NAME"], validation.TEST_DATABASE_NAME)
        self.assertEqual(settings["DATABASE_ROUTERS"], [])
        self.assertFalse(settings["EMAIL_DELIVERY_ENABLED"])

    def test_every_missing_gate_fails_closed(self):
        for key in self.environment():
            environment = self.environment()
            del environment[key]
            with self.subTest(key=key), self.assertRaises(ImproperlyConfigured):
                self.load_settings(environment)

    def test_wrong_runner_or_database_gate_fails_closed(self):
        for key, value in (("RUNNER_ENVIRONMENT", "self-hosted"), ("RUNNER_OS", "Windows"),
                           ("HM_CI_POSTGRES", "0"), ("POSTGRES_DB", "unexpected"),
                           ("POLYPBASE_TEST_POSTGRES", "1")):
            with self.subTest(key=key), self.assertRaises(ImproperlyConfigured):
                self.load_settings({**self.environment(), key: value})

    def test_dotenv_and_libpq_overrides_are_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings(self.environment(), dotenv=True)
        for key in ("DATABASE_URL", "PGHOST", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGPASSWORD", "PGOPTIONS"):
            with self.subTest(key=key), self.assertRaises(ImproperlyConfigured):
                self.load_settings({**self.environment(), key: "synthetic-unexpected-value"})

    def test_script_rejects_local_execution_before_setup_or_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(validation, "write_evidence") as evidence:
            self.assertEqual(validation.main(["preflight"]), 1)
        self.assertEqual(evidence.call_args.args[1]["status"], "failed")
        self.assertIn("GITHUB_ACTIONS", evidence.call_args.args[1]["error"])

    def test_postgresql_type_aliases_preserve_column_shape(self):
        for left, right in (("varchar(64)", "character varying(64)"),
                            ("int8", "bigint"), ("bool", "boolean")):
            self.assertEqual(validation.normalize_pg_type(left), validation.normalize_pg_type(right))
        for left, right in (("varchar(64)", "varchar(63)"), ("numeric(10,2)", "numeric(10,3)"),
                            ("bigint", "integer"), ("integer[]", "integer"),
                            ('"varchar"', "varchar")):
            self.assertNotEqual(validation.normalize_pg_type(left), validation.normalize_pg_type(right))


try:
    import yaml
except ImportError:
    yaml = None


@unittest.skipIf(yaml is None, "PyYAML is optional offline tooling, not a production dependency")
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        # BaseLoader keeps YAML 1.1's `on` key from becoming the boolean True.
        self.workflow = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.job = self.workflow["jobs"]["historical-movements-postgres"]

    def assert_job_env_contexts(self, environment):
        # GitHub's context-availability table excludes runner/job/steps/env here:
        # https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability
        allowed = {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}
        for key, value in environment.items():
            for expression in re.findall(r"\$\{\{(.*?)\}\}", value, flags=re.DOTALL):
                roots = set(re.findall(r"(?<![\w.])([a-zA-Z_]\w*)\s*(?:\.|\[)", expression))
                self.assertLessEqual(roots, allowed, f"Unavailable job env context in {key}: {expression}")

    def test_job_environment_uses_only_available_expression_contexts(self):
        self.assert_job_env_contexts(self.job["env"])
        self.assert_job_env_contexts({"CI_SHA": "${{ github.event.after }}"})
        for reference in ("runner.temp", "runner['temp']"):
            with self.subTest(reference=reference), self.assertRaises(AssertionError):
                self.assert_job_env_contexts({
                    "HISTORICAL_MOVEMENTS_EVIDENCE_DIR": "${{ " + reference + " }}/historical-movements-evidence",
                })

    def test_evidence_directory_is_exported_before_first_use_and_survives_failure(self):
        variable = "HISTORICAL_MOVEMENTS_EVIDENCE_DIR"
        self.assertNotIn(variable, self.job["env"])
        steps = self.job["steps"]
        initializer = steps[0]["run"]
        self.assertIn('$RUNNER_TEMP/historical-movements-evidence', initializer)
        self.assertIn('>> "$GITHUB_ENV"', initializer)
        # Windows' bash.exe may launch WSL; use Git's Bash beside sh.exe instead.
        shell = str(Path(shutil.which("sh")).with_name("bash.exe")) if os.name == "nt" else "bash"
        with tempfile.TemporaryDirectory(prefix="hm-ci-") as directory:
            temporary = Path(directory)
            environment_file = temporary / "github-env"
            environment_file.touch()
            environment = {**os.environ, "RUNNER_TEMP": temporary.as_posix(), "GITHUB_ENV": environment_file.as_posix()}
            environment.pop(variable, None)
            subprocess.run([shell, "-euo", "pipefail", "-c", initializer], env=environment,
                           check=True, capture_output=True, text=True, timeout=10)
            expected = temporary / "historical-movements-evidence"
            self.assertTrue(expected.is_dir())
            self.assertEqual(environment_file.read_text(encoding="utf-8"), f"{variable}={expected.as_posix()}\n")
            # GITHUB_ENV exports apply to subsequent steps, even after a failure.
            environment[variable] = expected.as_posix()
            failed_step = subprocess.run(
                [shell, "-euo", "pipefail", "-c", f'test -d "${variable}"; echo synthetic > "${variable}/failure.log"; exit 1'],
                env=environment, check=False, capture_output=True, timeout=10,
            )
            self.assertEqual(failed_step.returncode, 1)
            self.assertEqual((expected / "failure.log").read_text(encoding="utf-8"), "synthetic\n")
        upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(upload["if"], "always()")
        # runner is supported in steps.with, and this fallback does not depend on initialization succeeding.
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/historical-movements-evidence/")

    def test_push_trigger_permissions_service_and_timeout(self):
        self.assertEqual(self.workflow["on"], {"push": {"branches": ["feat/historical-movements"]}})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.job["runs-on"], "ubuntu-latest")
        self.assertEqual(self.job["timeout-minutes"], "20")
        postgres = self.job["services"]["postgres"]
        self.assertEqual(postgres["image"], "postgres:18")
        self.assertEqual(postgres["ports"], ["55439:5432"])
        self.assertNotIn("volumes", postgres)
        self.assertEqual(postgres["env"]["POSTGRES_DB"], validation.DATABASE_NAME)
        self.assertEqual(postgres["env"]["POSTGRES_USER"], validation.DATABASE_NAME)
        self.assertIn("--tmpfs /var/lib/postgresql:rw,nosuid", postgres["options"])
        self.assertIn("pg_isready -h 127.0.0.1", postgres["options"])

    def test_steps_are_locked_guarded_and_publish_failure_evidence(self):
        steps = self.job["steps"]
        commands = [step.get("run", "") for step in steps]
        joined = "\n".join(commands)
        self.assertNotIn("secrets.", WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("docker ", joined)
        self.assertIn("uv sync --frozen", joined)
        self.assertIn("--with pyyaml==6.0.3", joined)
        self.assertIn("test_historical_movements_ci.py", joined)
        self.assertIn("python-version", str(steps))
        self.assertEqual(self.job["env"]["DJANGO_SETTINGS_MODULE"], validation.SETTINGS_MODULE)
        self.assertEqual(self.job["env"]["POLYPBASE_TEST_POSTGRES"], "0")
        self.assertIn("--settings=" + validation.SETTINGS_MODULE, joined)
        self.assertIn("cultures 0018", joined)
        self.assertIn("audit 0004", joined)
        self.assertIn("apps.cultures.test_historical_movements_postgres_ci", joined)
        self.assertNotIn("apps.cultures.test_historical_movements ", joined)
        preflight = next(i for i, command in enumerate(commands) if "validate_historical_movements_ci.py preflight" in command)
        migrate = next(i for i, command in enumerate(commands) if "manage.py migrate --noinput" in command)
        tests = next(i for i, command in enumerate(commands) if "manage.py test" in command)
        self.assertLess(preflight, migrate)
        self.assertLess(migrate, tests)
        for step in steps:
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[0-9a-f]{40}$")
        upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["retention-days"], "7")


if __name__ == "__main__":
    unittest.main()
