"""Exercise deployment guards only; never run the executor or contact a database/VM."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


DEPLOY_DIR = Path(__file__).resolve().parents[1]
EXECUTOR = DEPLOY_DIR / "scripts" / "deploy_release.sh"
TARGET = "23a9f3edd37da19d2d11d4378009f339e6d14db8"
OTHER_TARGET = "a" * 40
ORDINARY_PLAN = "Planned operations:\ncultures.0008_example\n    Create model Example\n"
REVIEWED_PLAN = (
    "Planned operations:\n"
    "organizations.0002_organization_portable_id\n"
    "    Add field portable_id to organization\n"
    "    Raw Python operation\n"
    "    Alter field portable_id on organization\n"
)


class MigrationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if not cls.bash:
            raise unittest.SkipTest("Bash is unavailable")
        cls.source = EXECUTOR.read_text(encoding="utf-8")
        # Run the real functions and argument validation, stopping before user,
        # filesystem, Git, backup, dependency or database operations can execute.
        cls.prologue = cls.source.split('[[ "$(id -un)"', 1)[0]
        assert "check_migration_plan()" in cls.prologue
        assert "reviewed migration approval does not match the target commit" in cls.prologue

    def run_guard(self, plan, approval="", target=TARGET, *, missing=False, extra=False, grep_error=False):
        with tempfile.TemporaryDirectory() as directory:
            if not missing:
                Path(directory, "plan.txt").write_bytes(plan.encode("utf-8"))
            # Rebuild positional arguments inside Bash so Windows native argument
            # quoting cannot strip malformed whitespace before validation.
            setup = 'set -- "$TEST_TARGET" "$TEST_APPROVAL"\n'
            if extra:
                setup += 'set -- "$@" unexpected\n'
            if grep_error:
                setup += 'grep() { return 2; }\n'
            command = setup + self.prologue + '\nCURRENT_STEP="migration plan test"\ncheck_migration_plan "plan.txt"\n'
            environment = os.environ.copy()
            environment.update(TEST_TARGET=target, TEST_APPROVAL=approval)
            return subprocess.run(
                [self.bash, "--noprofile", "--norc", "-c", command, "guard-test"],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )

    def approval(self, plan, target=TARGET):
        return f"{target}:{hashlib.sha256(plan.encode('utf-8')).hexdigest()}"

    def assert_rejected(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("MIGRATION_REVIEW_APPROVED", result.stdout)

    def test_ordinary_plan_passes_without_approval(self):
        result = self.run_guard(ORDINARY_PLAN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MIGRATION_PLAN commit=", result.stdout)
        self.assertNotIn("MIGRATION_REVIEW_APPROVED", result.stdout)

    def test_all_guarded_operations_fail_without_approval(self):
        for operation in (
            "Remove field", "Delete model", "Rename field", "Rename model",
            "Raw Python operation", "Raw SQL operation",
        ):
            with self.subTest(operation=operation):
                self.assert_rejected(
                    self.run_guard(f"Planned operations:\n    {operation}\n"),
                    "potentially destructive migrations require manual review",
                )

    def test_reviewed_exact_plan_passes(self):
        result = self.run_guard(REVIEWED_PLAN, self.approval(REVIEWED_PLAN))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"MIGRATION_REVIEW_APPROVED commit={TARGET} plan_sha256={self.approval(REVIEWED_PLAN).split(':')[1]}",
            result.stdout,
        )

    def test_destructive_operations_require_exact_explicit_review(self):
        plan = "Planned operations:\n    Delete model Example\n    Raw SQL operation\n"
        result = self.run_guard(plan, self.approval(plan))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MIGRATION_REVIEW_APPROVED", result.stdout)

    def test_mismatched_target_fails_even_for_ordinary_plan(self):
        for plan in (ORDINARY_PLAN, REVIEWED_PLAN):
            with self.subTest(plan=plan):
                self.assert_rejected(
                    self.run_guard(plan, self.approval(plan, OTHER_TARGET)),
                    "does not match the target commit",
                )

    def test_changed_plan_fails_closed(self):
        for plan in (ORDINARY_PLAN, REVIEWED_PLAN + "    Delete model Example\n", REVIEWED_PLAN.replace("\n", "\r\n")):
            with self.subTest(plan=plan):
                self.assert_rejected(
                    self.run_guard(plan, self.approval(REVIEWED_PLAN)),
                    "does not match the captured plan",
                )

    def test_malformed_approval_fails_closed(self):
        for approval in (
            "true", "1", TARGET, ":" + "a" * 64,
            TARGET + ":" + "a" * 63, TARGET + ":" + "g" * 64,
            self.approval(REVIEWED_PLAN).upper(),
            self.approval(REVIEWED_PLAN) + "\n",
            self.approval(REVIEWED_PLAN) + "; echo unsafe",
            " " + self.approval(REVIEWED_PLAN),
        ):
            with self.subTest(approval=approval):
                self.assert_rejected(
                    self.run_guard(REVIEWED_PLAN, approval),
                    "must be COMMIT_SHA:PLAN_SHA256",
                )

    def test_extra_arguments_fail_closed(self):
        self.assert_rejected(
            self.run_guard(ORDINARY_PLAN, extra=True),
            "expected target commit and optional reviewed migration approval",
        )

    def test_missing_plan_fails_closed(self):
        result = self.run_guard(ORDINARY_PLAN, missing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("MIGRATION_REVIEW_APPROVED", result.stdout)

    def test_grep_error_fails_closed(self):
        self.assert_rejected(
            self.run_guard(ORDINARY_PLAN, grep_error=True),
            "could not inspect the migration plan",
        )

    def test_plan_is_captured_before_guard_and_migrations(self):
        capture = self.source.index('tee "$RELEASE_DIR/migrate-plan.txt"')
        guard = self.source.index('check_migration_plan "$RELEASE_DIR/migrate-plan.txt"')
        migrate = self.source.index('"$PYTHON" backend/manage.py migrate --noinput')
        backup = self.source.index('/usr/bin/pg_restore --list "$BACKUP_PATH"')
        update = self.source.index('merge --ff-only "$TARGET_COMMIT"')
        self.assertLess(backup, update)
        self.assertLess(update, capture)
        self.assertLess(capture, guard)
        self.assertLess(guard, migrate)

    def test_shell_syntax(self):
        result = subprocess.run(
            [self.bash, "-n"], input=self.source, text=True,
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class PowerShellTests(unittest.TestCase):
    def test_isolated_powershell_behavior(self):
        names = ("pwsh", "powershell") if os.name == "nt" else ("pwsh",)
        shells = [shutil.which(name) for name in names]
        shells = [shell for shell in shells if shell]
        if not shells:
            self.skipTest("PowerShell is unavailable")
        for shell in shells:
            with self.subTest(shell=shell):
                result = subprocess.run(
                    [shell, "-NoProfile", "-NonInteractive", "-File", str(DEPLOY_DIR / "tests" / "test_deploy_vm.ps1")],
                    capture_output=True, text=True, timeout=30,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                for marker in (
                    "NATIVE_SUCCESS_VISIBLE", "NATIVE_FAILURE_VISIBLE",
                    "NATIVE_STDERR_VISIBLE", "SERVICE_DIAGNOSTIC_VISIBLE",
                    "JOURNAL_DIAGNOSTIC_VISIBLE", "POWERSHELL_TESTS_OK",
                ):
                    self.assertIn(marker, output)


if __name__ == "__main__":
    unittest.main()
