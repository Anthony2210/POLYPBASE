"""Exercise deployment guards only; never run the executor or contact a database/VM."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


DEPLOY_DIR = Path(__file__).resolve().parents[1]
EXECUTOR = DEPLOY_DIR / "scripts" / "deploy_release.sh"
TARGET = "23a9f3edd37da19d2d11d4378009f339e6d14db8"


class MigrationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if not cls.bash:
            raise unittest.SkipTest("Bash is unavailable")
        cls.source = EXECUTOR.read_text(encoding="utf-8")
    def test_no_pending_migrations_continue(self):
        readiness = self.source.split('CURRENT_STEP="database migration readiness"', 1)[1]
        self.assertIn('"$PYTHON" backend/manage.py migrate --check --noinput || \\', readiness)
        self.assertLess(
            readiness.index('"$PYTHON" backend/manage.py migrate --check --noinput'),
            readiness.index('CURRENT_STEP="static files"'),
        )

    def test_every_pending_plan_is_blocked_without_keyword_exceptions(self):
        for operation in ("Create model", "RunPython", "RunSQL"):
            with self.subTest(operation=operation):
                self.assertNotIn(operation, self.source)
        self.assertIn(
            'fail "pending migrations require the separately authorized dedicated migrator; resume this release after it reports MIGRATION_COMPLETE"',
            self.source,
        )
        self.assertNotIn("grep -Eiq", self.source)

    def test_obsolete_approval_argument_is_rejected(self):
        self.assertIn(
            '(( $# == 1 )) || fail "expected exactly one target commit; migrations require the dedicated runner"',
            self.source,
        )
        self.assertNotIn("REVIEWED_MIGRATION_APPROVAL", self.source)

    def test_plan_readiness_is_read_only_and_precedes_publication(self):
        readiness = self.source.index('"$PYTHON" backend/manage.py migrate --check --noinput')
        publication = self.source.index('CURRENT_STEP="frontend publication"')
        self.assertLess(readiness, publication)
        self.assertNotIn('"$PYTHON" backend/manage.py migrate --noinput', self.source)
        self.assertNotIn("check_migration_plan", self.source)
        self.assertNotIn("REVIEWED_MIGRATION_APPROVAL", self.source)
        self.assertNotIn("grep -Eiq", self.source)

    def test_runtime_never_executes_migrations(self):
        self.assertIn('pending migrations require the separately authorized dedicated migrator', self.source)
        self.assertIn('run_dedicated_migrations.sh', self.source)

    def test_dedicated_migration_executor_requires_real_identity(self):
        executor = (DEPLOY_DIR / "scripts" / "run_dedicated_migrations.sh").read_text(encoding="utf-8")
        self.assertIn('POSTGRES_USER must be explicitly set to $EXPECTED_MIGRATOR', executor)
        self.assertIn('PGOPTIONS must explicitly request SET ROLE $EXPECTED_OWNER', executor)
        self.assertIn('SELECT session_user::text, current_user::text', executor)
        self.assertIn('MIGRATION_COMPLETE', executor)
        result = subprocess.run(
            [self.bash, "-n"], input=executor.encode("utf-8"),
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def backup_section(self):
        return self.source.split('CURRENT_STEP="database backup"', 1)[1].split(
            'CURRENT_STEP="fast-forward update"', 1)[0]

    def test_release_requires_fixed_broker_before_update(self):
        section = self.backup_section()
        self.assertEqual(section.count('/usr/bin/sudo'), 1)
        self.assertIn('/usr/bin/sudo -n -u polypbase_backup', section)
        self.assertIn('/usr/local/libexec/polypbase-deployment-backup "$TARGET_COMMIT"', section)
        for forbidden in ('git -C', '.venv', 'runpy', 'importlib', 'POLYPBASE_BACKUP_PASSWORD',
                          'verify_archive', '[[ -s', '--output-dir'):
            self.assertNotIn(forbidden, section)
        self.assertNotIn("/usr/bin/pg_restore", self.source)
        self.assertNotIn("/usr/bin/pg_dump", self.source)

    def run_backup_section(self, directory, output, *, status=0):
        # Replace only sudo's executable in the extracted section. No real sudo,
        # broker, Git, Python backup payload or database command can be executed.
        section = self.backup_section()
        self.assertEqual(section.count('/usr/bin/sudo'), 1)
        command = '''set -Eeuo pipefail
fail() { printf '%s\\n' "$*" >&2; exit 1; }
TARGET_COMMIT="$TEST_TARGET"
SHORT_COMMIT="${TARGET_COMMIT:0:8}"
sudo_mock() {
    [[ "$#" == 5 && "$1" == -n && "$2" == -u && "$3" == polypbase_backup &&
       "$4" == /usr/local/libexec/polypbase-deployment-backup &&
       "$5" == "$TARGET_COMMIT" ]] || { printf 'BROKER_ARGUMENTS_INVALID\\n' >&2; return 99; }
    printf 'BROKER_CALLED\\n' >> broker-calls.txt
    printf '%b' "$TEST_BROKER_OUTPUT"
    return "$TEST_BROKER_STATUS"
}
'''
        command += section.replace('/usr/bin/sudo', 'sudo_mock')
        # The sentinel stands in for the boundary immediately before Git update.
        command += '\nprintf "UPDATE_REACHED path=%s name=%s\\n" "$BACKUP_PATH" "$BACKUP_NAME"\n'
        # Windows-to-Bash environment transport may normalize literal CRLF.
        # Reconstruct control characters inside the mock to test the actual bytes.
        escaped_output = output.replace('\\', '\\\\').replace('\r', '\\r').replace('\n', '\\n')
        return subprocess.run(
            [self.bash, "--noprofile", "--norc", "-c", command], cwd=directory,
            env={**os.environ, 'TEST_TARGET': TARGET, 'TEST_BROKER_OUTPUT': escaped_output,
                 'TEST_BROKER_STATUS': str(status), 'POLYPBASE_BACKUP_DIR': '/tmp/caller-override'},
            capture_output=True, text=True, timeout=10,
        )

    def backup_attestation(self):
        return f'BACKUP_OK /var/lib/polypbase/deployment-backups/pre-{TARGET[:8]}-20261112T120000Z.dump'

    def test_verified_broker_result_reaches_update_without_web_archive_access(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_backup_section(directory, self.backup_attestation() + '\n')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            path = self.backup_attestation().removeprefix('BACKUP_OK ')
            self.assertIn(f'UPDATE_REACHED path={path} name={path.rsplit("/", 1)[1]}', result.stdout)
            self.assertEqual(Path(directory, 'broker-calls.txt').read_text(), 'BROKER_CALLED\n')

    def test_broker_failure_stops_before_update_even_with_success_output(self):
        for status in (1, 8, 124):
            for output in ('', 'BACKUP_ERROR refused\n', self.backup_attestation() + '\n'):
                with self.subTest(status=status, output=output), tempfile.TemporaryDirectory() as directory:
                    result = self.run_backup_section(directory, output, status=status)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn('UPDATE_REACHED', result.stdout)

    def test_tampered_broker_attestation_stops_before_update(self):
        valid = self.backup_attestation()
        outputs = (
            '', 'BACKUP_OK', valid.replace(TARGET[:8], 'ffffffff'),
            valid.replace('/deployment-backups/', '/backups/'),
            valid.replace('/pre-', '/../pre-'), valid + '.extra',
            valid + ' 1 bytes sha256=' + 'a' * 64,
            valid + '\n' + valid, 'unexpected output\n' + valid,
            valid + '\nunexpected output',
            valid.replace('20261112T120000Z', '20261112T120000'),
        )
        for output in outputs:
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                result = self.run_backup_section(directory, output)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('UPDATE_REACHED', result.stdout)
                self.assertIn('trusted backup broker did not return the expected BACKUP_OK path', result.stderr)

    @unittest.skipIf(os.name == 'nt', 'Windows Bash normalizes CRLF in command substitution')
    def test_crlf_broker_attestation_stops_before_update(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_backup_section(directory, self.backup_attestation() + '\r\n')
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('UPDATE_REACHED', result.stdout)

    def test_retry_requires_fresh_successful_broker_result(self):
        with tempfile.TemporaryDirectory() as directory:
            attempts = (
                (1, self.backup_attestation(), False),
                (0, self.backup_attestation(), True),
                (1, self.backup_attestation(), False),
                (0, '', False),
                (0, self.backup_attestation(), True),
            )
            for number, (status, output, allowed) in enumerate(attempts, start=1):
                with self.subTest(attempt=number):
                    result = self.run_backup_section(directory, output, status=status)
                    self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
                    self.assertEqual('UPDATE_REACHED' in result.stdout, allowed)
                    self.assertEqual(Path(directory, 'broker-calls.txt').read_text().splitlines(),
                                     ['BROKER_CALLED'] * number)

    def test_shell_syntax(self):
        result = subprocess.run(
            [self.bash, "-n"], input=self.source.encode("utf-8"),
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))


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
                    "JOURNAL_DIAGNOSTIC_VISIBLE", "PREFLIGHT_ARGUMENTS_OK", "POWERSHELL_TESTS_OK",
                ):
                    self.assertIn(marker, output)


if __name__ == "__main__":
    unittest.main()
