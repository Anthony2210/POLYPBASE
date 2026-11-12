"""Local guard tests: temporary files, fake connections and in-memory SQLite only."""

import ast
import contextlib
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_dedicated_migrations.sh"
SOURCE = SCRIPT.read_text(encoding="utf-8")
PYTHON_SOURCE = SOURCE.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
PREFLIGHT_SOURCE = SOURCE.split("<<'PREFLIGHT'\n", 1)[1].split("\nPREFLIGHT\n", 1)[0]
TARGET = "a" * 40
DATA = b'{"migrations": ["measurements.0018_box_1902_preservation"]}\n'


def logic():
    namespace = {"__name__": "dedicated_migrations_test"}
    exec(compile(PYTHON_SOURCE, str(SCRIPT), "exec"), namespace)
    return namespace


def token(data=DATA):
    return f"{TARGET}:{hashlib.sha256(data).hexdigest()}"


class BashGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if not cls.bash:
            raise unittest.SkipTest("Bash unavailable")
        cls.prologue = SOURCE.split('\nvalidate_arguments "$@"', 1)[0]

    def run_shell(self, command, **variables):
        environment = os.environ.copy()
        environment.update(variables)
        return subprocess.run(
            [self.bash, "--noprofile", "--norc"],
            input=(self.prologue + "\n" + command).encode(), env=environment,
            capture_output=True, timeout=10,
        )

    def args(self, mode="--apply", approval=None, extra=""):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "plan").write_bytes(DATA)
            return self.run_args_in_directory(directory, mode, approval, extra)

    def run_args_in_directory(self, directory, mode, approval, extra):
        env = os.environ.copy()
        env.update(TEST_MODE=mode, TEST_TARGET=TARGET,
                   TEST_APPROVAL=token() if approval is None else approval)
        code = self.prologue + '\nPLAN_PATH="$(pwd)/plan"\nMODE="$TEST_MODE"\nTARGET_COMMIT="$TEST_TARGET"\nREVIEWED_MIGRATION_APPROVAL="$TEST_APPROVAL"\nset -- "$MODE" "$TARGET_COMMIT" "$PLAN_PATH"\nif [[ "$MODE" == --apply ]]; then set -- "$@" "$REVIEWED_MIGRATION_APPROVAL"; fi\n' + extra + '\nvalidate_arguments "$@"\n'
        return subprocess.run([self.bash, "--noprofile", "--norc"], input=code.encode(),
                              env=env, cwd=directory, capture_output=True, timeout=10)

    def test_shell_syntax(self):
        result = subprocess.run([self.bash, "-n"], input=SOURCE.encode(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_apply_requires_explicit_well_formed_matching_approval(self):
        self.assertEqual(self.args().returncode, 0)
        for approval in ("", "yes", "A" * 40 + ":" + "0" * 64, "b" * 40 + ":" + "0" * 64):
            with self.subTest(approval=approval):
                self.assertNotEqual(self.args(approval=approval).returncode, 0)
        self.assertNotEqual(self.args(extra='set -- "$1" "$2" "$3"').returncode, 0)
        self.assertNotEqual(self.args(extra='set -- "$@" extra').returncode, 0)

    def test_plan_refuses_overwrite_and_approval(self):
        self.assertNotEqual(self.args(mode="--plan").returncode, 0)
        self.assertEqual(self.args(mode="--plan", extra='PLAN_PATH="$(pwd)/new-plan"; set -- "$MODE" "$TARGET_COMMIT" "$PLAN_PATH"').returncode, 0)
        self.assertNotEqual(self.args(mode="--plan", extra='set -- "$@" approval').returncode, 0)

    def test_missing_and_relative_plans_rejected(self):
        for extra in ('PLAN_PATH=relative', 'PLAN_PATH="$(pwd)/missing"'):
            with self.subTest(extra=extra):
                self.assertNotEqual(self.args(extra=extra).returncode, 0)

    @unittest.skipIf(os.name == "nt", "Git Bash ln may copy instead of creating a symlink")
    def test_symlink_plan_rejected(self):
        self.assertNotEqual(self.args(extra='mv plan original; ln -s original plan').returncode, 0)

    def test_real_checkout_guard_rejects_dirty_wrong_commit_and_branch(self):
        for branch, status, commit, expected in (
            ("main", "", TARGET, 0), ("feature", "", TARGET, 1),
            ("main", "?? unknown", TARGET, 1), ("main", "", "b" * 40, 1),
        ):
            result = self.run_shell(
                'TARGET_COMMIT="$TEST_TARGET"\n'
                'trusted_git() { case "$3" in symbolic-ref) printf "%s" "$TEST_BRANCH";; status) printf "%s" "$TEST_STATUS";; rev-parse) printf "%s" "$TEST_COMMIT";; *) return 9;; esac; }\n'
                'assert_checkout\n', TEST_TARGET=TARGET, TEST_BRANCH=branch,
                TEST_STATUS=status, TEST_COMMIT=commit,
            )
            self.assertEqual(bool(result.returncode), bool(expected), result.stderr)

    def git_invocations(self, app_dir):
        commands = (("symbolic-ref", "--short", "HEAD"),
                    ("status", "--porcelain", "--untracked-files=all"),
                    ("rev-parse", "HEAD"))
        bash_calls = []
        for args in commands:
            # Capture the real wrapper's argv without requiring Debian binary paths.
            result = self.run_shell(
                '/usr/bin/env() { printf "%s\\0" "$@"; }\n'
                'trusted_git -C "$APP_DIR" ' + " ".join(args),
                POLYPBASE_MIGRATION_APP_DIR=app_dir,
                POLYPBASE_APP_DIR="/srv/runtime-owned/app",
                GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0="*",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            fields = result.stdout.decode().split("\0")[:-1]
            self.assertEqual(fields[0], "-i")
            self.assertEqual(fields[4], "/usr/bin/git")
            environment = dict(field.split("=", 1) for field in fields[1:4])
            bash_calls.append((fields[4:], environment))
        ns = logic()
        with patch.object(ns["subprocess"], "check_output",
                          side_effect=[b"main\n", b"", TARGET.encode()]) as git:
            ns["assert_checkout"](PurePosixPath(app_dir), TARGET)
        python_calls = [(call.args[0], call.kwargs["env"]) for call in git.call_args_list]
        return bash_calls, python_calls

    def test_both_git_wrappers_authorize_only_the_exact_snapshot_and_isolate_config(self):
        app_dir = "/opt/polypbase-migrations/reviewed snapshot"
        bash_calls, python_calls = self.git_invocations(app_dir)
        self.assertEqual(len(python_calls), 3)
        for bash_call, python_call in zip(bash_calls, python_calls):
            argv, environment = python_call
            self.assertEqual(bash_call, ([arg for arg in argv if arg != "--no-pager"], environment))
            self.assertEqual(environment, {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                                           "GIT_CONFIG_GLOBAL": "/dev/null"})
            self.assertEqual([arg for arg in argv if arg.startswith("safe.directory=")],
                             [f"safe.directory={app_dir}"])
            self.assertEqual(argv[argv.index("-C") + 1], app_dir)
            self.assertIn("core.fsmonitor=false", argv)
        preflight_end = SOURCE.index("\nPREFLIGHT\n")
        self.assertLess(preflight_end, SOURCE.index("\nassert_checkout\n"))
        self.assertLess(preflight_end, SOURCE.index('"$APP_DIR/.venv/bin/python" -I -B -'))

    def test_real_git_accepts_only_exact_snapshot_with_different_owner(self):
        git_binary = shutil.which("git")
        if not git_binary:
            self.skipTest("Git unavailable")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory, "reviewed snapshot")
            child = snapshot / "nested"
            sibling = Path(directory, "other snapshot")
            for repository in (snapshot, child, sibling):
                subprocess.run([git_binary, "init", "--initial-branch=main", str(repository)],
                               check=True, capture_output=True, timeout=10)
            app_dir = snapshot.as_posix()
            bash_calls, python_calls = self.git_invocations(app_dir)
            for implementation, calls in (("bash", bash_calls), ("python", python_calls)):
                argv, environment = calls[0]
                # Git's test hook exercises its real dubious-ownership boundary
                # without chown/root. Only the executable and test hook are adapted.
                environment = dict(environment, GIT_TEST_ASSUME_DIFFERENT_OWNER="1")
                if os.name == "nt":
                    environment["SystemRoot"] = os.environ["SystemRoot"]
                argv = [git_binary, *argv[1:]]
                for repository, accepted in ((snapshot, True), (child, False), (sibling, False)):
                    command = argv.copy()
                    command[command.index("-C") + 1] = repository.as_posix()
                    with self.subTest(implementation=implementation, repository=repository):
                        result = subprocess.run(command, env=environment, capture_output=True, timeout=10)
                        self.assertEqual(result.returncode == 0, accepted, result.stderr)
                        if accepted:
                            self.assertEqual(result.stdout.strip(), b"main")
                        else:
                            self.assertIn(b"dubious ownership", result.stderr)
                index = argv.index(f"safe.directory={app_dir}")
                del argv[index - 1:index + 1]
                result = subprocess.run(argv, env=environment, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"dubious ownership", result.stderr)

    def test_snapshot_is_mandatory_with_no_live_checkout_fallback(self):
        gate = SOURCE.split('\nvalidate_arguments "$@"\n', 1)[1].split('\nCURRENT_STEP=', 1)[0]
        self.assertNotEqual(self.run_shell(gate, POLYPBASE_MIGRATION_APP_DIR="", POLYPBASE_APP_DIR="/srv/web").returncode, 0)
        self.assertNotEqual(self.run_shell(gate, POLYPBASE_MIGRATION_APP_DIR="relative").returncode, 0)
        self.assertEqual(self.run_shell(gate, POLYPBASE_MIGRATION_APP_DIR="/srv/trusted/snapshot").returncode, 0)

    def test_preflight_uses_only_isolated_system_python_before_snapshot_code(self):
        trusted = SOURCE.index('/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -I -S -B -')
        snapshot = SOURCE.index('"$APP_DIR/.venv/bin/python" -I -B -')
        self.assertLess(trusted, SOURCE.index('assert_checkout\n'))
        self.assertLess(trusted, snapshot)
        imports = set()
        for node in ast.walk(ast.parse(PREFLIGHT_SOURCE)):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {"grp", "os", "pathlib", "pwd", "stat", "sys"})
        self.assertIn('sys.path.insert(0, str(app_dir / "backend"))', PYTHON_SOURCE)

    def test_real_connection_environment_guards(self):
        gate = SOURCE.split('CURRENT_STEP="dedicated connection"\n', 1)[1].split('\ncd "$APP_DIR/backend"', 1)[0]
        baseline = {"POSTGRES_USER": "box1902_migrator", "POSTGRES_HOST": "127.0.0.1",
                    "PGOPTIONS": "-c role=box1902_owner"}
        self.assertEqual(self.run_shell(gate, **baseline).returncode, 0)
        for key, value in (("POSTGRES_USER", "polypbase"), ("POSTGRES_USER", ""),
                           ("POSTGRES_HOST", "/var/run/postgresql"), ("POSTGRES_HOST", ""),
                           ("PGOPTIONS", ""), ("PGOPTIONS", "-c role=box1902_owner -c session_replication_role=replica")):
            env = dict(baseline, **{key: value})
            with self.subTest(key=key, value=value):
                self.assertNotEqual(self.run_shell(gate, **env).returncode, 0)

    def test_lock_and_connection_gate_order(self):
        self.assertIn('LOCK_FILE="${POLYPBASE_DEPLOY_LOCK:-/srv/polypbase/deploy.lock}"', SOURCE)
        self.assertIn('BACKUP_DIR="${POLYPBASE_BACKUP_DIR:-/var/lib/polypbase/migration-backups}"', SOURCE)
        lock = SOURCE.index('flock -n 9 || fail')
        self.assertLess(lock, SOURCE.index('CURRENT_STEP="dedicated connection"'))
        self.assertLess(lock, SOURCE.index("<<'PY'"))
        self.assertNotIn("backend/manage.py migrate --noinput", SOURCE)
        self.assertNotIn("assert_migration_recorded", SOURCE)

    def test_real_lock_contention_when_flock_available(self):
        probe = subprocess.run([self.bash, "-c", "command -v flock"], capture_output=True, timeout=10)
        if probe.returncode:
            self.skipTest("flock unavailable (Git Bash on Windows); Linux contention not exercised")
        with tempfile.TemporaryDirectory() as directory:
            code = 'exec 8>lock; flock -n 8; exec 9>lock; flock -n 9'
            result = subprocess.run([self.bash, "-c", code], cwd=directory, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)


class TrustedPreflightTests(unittest.TestCase):
    """Synthetic POSIX metadata: no chown, privileged process or real PG access."""

    def setUp(self):
        self.ns = {"__name__": "preflight_test"}
        with patch.dict(sys.modules, {"pwd": Mock(), "grp": Mock()}):
            exec(compile(PREFLIGHT_SOURCE, str(SCRIPT), "exec"), self.ns)
        self.nodes = {}
        self.links = {}
        nodes, links = self.nodes, self.links

        class SyntheticPath(PurePosixPath):
            def lstat(self):
                if str(self) not in nodes:
                    raise FileNotFoundError(str(self))
                return nodes[str(self)]

            def iterdir(self):
                return iter([SyntheticPath(name) for name in nodes
                             if name != str(self) and SyntheticPath(name).parent == self])

            def resolve(self):
                return self

            def absolute(self):
                return self

            def is_dir(self):
                return stat.S_ISDIR(self.lstat().st_mode)

            def is_file(self):
                path = self
                while str(path) in links:
                    target = SyntheticPath(links[str(path)])
                    path = target if target.is_absolute() else path.parent / target
                return stat.S_ISREG(path.lstat().st_mode)

        self.Path = SyntheticPath
        self.ns["Path"] = SyntheticPath
        for directory in ("/", "/srv", "/srv/trusted", "/srv/trusted/app", "/srv/trusted/app/.git",
                          "/srv/trusted/app/.venv", "/srv/trusted/app/.venv/bin", "/srv/trusted/app/backend",
                          "/srv/trusted/control", "/srv/trusted/control/backend", "/srv/trusted/control/backend/config",
                          "/srv/trusted/control/deploy", "/srv/trusted/control/deploy/scripts", "/usr", "/usr/bin"):
            self.add(directory, stat.S_IFDIR | 0o755)
        self.add("/srv/trusted/app/.venv/bin/python", stat.S_IFREG | 0o755)
        self.add("/srv/trusted/control/backend/config/database_backup.py")
        self.add("/srv/trusted/control/deploy/scripts/run_dedicated_migrations.sh")
        self.os_stack = contextlib.ExitStack()
        self.addCleanup(self.os_stack.close)
        self.os_stack.enter_context(patch.object(self.ns["os"], "readlink", side_effect=lambda path: links[str(path)]))
        self.os_stack.enter_context(patch.object(self.ns["os"], "access", return_value=True))
        self.ns["check_identity"] = Mock()

    def add(self, name, mode=stat.S_IFREG | 0o644, uid=0):
        self.nodes[name] = types.SimpleNamespace(st_uid=uid, st_mode=mode, st_dev=1, st_ino=len(self.nodes) + 1)

    def preflight(self, live="/srv/web", runner="/srv/trusted/control/deploy/scripts/run_dedicated_migrations.sh"):
        self.ns["preflight"](self.Path("/srv/trusted/app"), self.Path("/srv/trusted/control"),
                             self.Path(live), self.Path(runner))

    def test_root_controlled_snapshot_and_control_are_accepted(self):
        self.preflight()
        self.ns["check_identity"].assert_called_once()

    def test_every_ignored_git_venv_control_file_and_ancestor_is_checked(self):
        for name in ("/srv/trusted/app/ignored.pyc", "/srv/trusted/app/.venv/malicious.pth",
                     "/srv/trusted/app/.git/config", "/srv/trusted/control/ignored.pyc"):
            for mode, uid in ((stat.S_IFREG | 0o644, 1000), (stat.S_IFREG | 0o664, 0),
                              (stat.S_IFREG | 0o646, 0), (stat.S_IFIFO | 0o600, 0)):
                with self.subTest(name=name, mode=mode, uid=uid):
                    self.add(name, mode, uid)
                    with self.assertRaises(RuntimeError):
                        self.preflight()
                    del self.nodes[name]
        for name in ("/", "/srv", "/srv/trusted", "/srv/trusted/app/.venv", "/srv/trusted/app/.git",
                     "/srv/trusted/control"):
            original = self.nodes[name]
            for uid, mode in ((1000, stat.S_IFDIR | 0o755), (0, stat.S_IFDIR | 0o775)):
                self.add(name, mode, uid)
                with self.subTest(ancestor=name, uid=uid), self.assertRaises(RuntimeError):
                    self.preflight()
            self.nodes[name] = original

    def test_system_interpreter_symlink_is_allowed_only_with_controlled_ancestors(self):
        name = "/srv/trusted/app/.venv/bin/python"
        self.add(name, stat.S_IFLNK | 0o777)
        self.links[name] = "/usr/bin/python3"
        self.add("/usr/bin/python3", stat.S_IFLNK | 0o777)
        self.links["/usr/bin/python3"] = "python3.13"
        self.add("/usr/bin/python3.13", stat.S_IFREG | 0o755)
        self.preflight()
        for unsafe in (name, "/usr", "/usr/bin", "/usr/bin/python3.13"):
            info = self.nodes[unsafe]
            self.nodes[unsafe] = types.SimpleNamespace(**{**vars(info), "st_uid": 1000})
            with self.subTest(unsafe=unsafe), self.assertRaises(RuntimeError):
                self.preflight()
            self.nodes[unsafe] = info

    def test_linked_directories_are_recursively_checked(self):
        self.add("/srv/trusted/app/.venv/site", stat.S_IFLNK | 0o777)
        self.links["/srv/trusted/app/.venv/site"] = "/usr/packages"
        self.add("/usr/packages", stat.S_IFDIR | 0o755)
        self.add("/usr/packages/unsafe.pth", uid=1000)
        with self.assertRaises(RuntimeError):
            self.preflight()

    def test_no_live_venv_reuse_even_when_live_files_are_root_owned(self):
        self.add("/srv/web", stat.S_IFDIR | 0o755)
        self.add("/srv/web/python", stat.S_IFREG | 0o755)
        name = "/srv/trusted/app/.venv/bin/python"
        self.add(name, stat.S_IFLNK | 0o777)
        self.links[name] = "/srv/web/python"
        with self.assertRaisesRegex(RuntimeError, "live web"):
            self.preflight()

    def test_relative_link_parent_traversal_checks_intermediate_hops(self):
        self.add("/srv/trusted/app/alias", stat.S_IFLNK | 0o777)
        self.links["/srv/trusted/app/alias"] = "/usr/unsafe/../bin/python3"
        self.add("/usr/unsafe", stat.S_IFDIR | 0o777)
        self.add("/usr/bin/python3", stat.S_IFREG | 0o755)
        with self.assertRaises(RuntimeError):
            self.preflight()

    def test_missing_files_cycles_live_overlap_and_external_runner_are_rejected(self):
        for live in ("/srv/trusted/app", "/srv/trusted", "/srv/trusted/app/backend"):
            with self.subTest(live=live), self.assertRaises(RuntimeError):
                self.preflight(live=live)
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            self.preflight(runner="/srv/external-runner")
        name = "/srv/trusted/app/.venv/bin/python"
        original = self.nodes.pop(name)
        with self.assertRaises(FileNotFoundError):
            self.preflight()
        self.nodes[name] = original
        self.add(name, stat.S_IFLNK | 0o777)
        self.links[name] = "python"
        with self.assertRaisesRegex(RuntimeError, "cycle"):
            self.preflight()

    def test_dedicated_nonroot_identity_and_group_separation(self):
        # Reload only the identity function replaced by setUp's filesystem fixture.
        identity_ns = {"__name__": "identity_test"}
        with patch.dict(sys.modules, {"pwd": Mock(), "grp": Mock()}):
            exec(compile(PREFLIGHT_SOURCE, str(SCRIPT), "exec"), identity_ns)
        dedicated = types.SimpleNamespace(pw_uid=2000, pw_gid=2000, pw_name="polypbase_migrator")
        web = types.SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="polypbase")
        def lookup(name):
            if name == "polypbase_migrator":
                return dedicated
            if name == "polypbase":
                return web
            raise KeyError(name)
        identity_ns["pwd"].getpwnam.side_effect = lookup
        identity_ns["grp"].getgrnam.return_value = types.SimpleNamespace(gr_gid=2000)
        with contextlib.ExitStack() as stack:
            mocks = {}
            for name, value in (("getuid", 2000), ("geteuid", 2000), ("getgid", 2000),
                                ("getegid", 2000), ("getgroups", [2000]), ("getgrouplist", [1000])):
                mocks[name] = stack.enter_context(patch.object(identity_ns["os"], name, return_value=value, create=True))
            identity_ns["check_identity"]()
            for name, value in (("getuid", 0), ("getuid", 1000), ("geteuid", 0),
                                ("getgid", 1000), ("getegid", 0), ("getgroups", [2000, 1000]),
                                ("getgroups", [2000, 0]), ("getgrouplist", [1000, 2000])):
                original = mocks[name].return_value
                mocks[name].return_value = value
                with self.subTest(name=name, value=value), self.assertRaises(RuntimeError):
                    identity_ns["check_identity"]()
                mocks[name].return_value = original
            mocks["getgroups"].return_value = [2000, 3000]
            mocks["getgrouplist"].return_value = [1000, 3000]
            with self.assertRaises(RuntimeError):
                identity_ns["check_identity"]()


class PythonLogicTests(unittest.TestCase):
    def setUp(self):
        self.ns = logic()

    def test_exact_plan_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            approved = Path(directory, "plan")
            approved.write_bytes(DATA)
            # Execute cmp for real via Bash when Windows has no native cmp.
            native_run = subprocess.run
            def run(command, **kwargs):
                if os.name == "nt" and command[0] == "cmp":
                    env = os.environ.copy()
                    env.update(CMP_A=command[2], CMP_B=command[3])
                    return native_run([shutil.which("bash"), "-c", 'cmp --silent "$CMP_A" "$CMP_B"'], env=env, **kwargs)
                return native_run(command, **kwargs)
            with patch.object(self.ns["subprocess"], "run", side_effect=run):
                verify = self.ns["verify_plan"]
                self.assertEqual(verify(DATA, approved, token(), TARGET), token().split(":")[1])
                for data, approval in ((DATA + b"\n", token()), (DATA, token(DATA + b"x")),
                                       (DATA, ""), (DATA, "b" * 40 + token()[40:])):
                    with self.subTest(data=data, approval=approval), self.assertRaises(RuntimeError):
                        verify(data, approved, approval, TARGET)
                approved.unlink()
                with self.assertRaises(RuntimeError):
                    verify(DATA, approved, token(), TARGET)

    def connection(self, identity=("box1902_migrator", "box1902_owner", "origin", 123)):
        connection = MagicMock(vendor="postgresql", alias="default")
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = identity
        return connection

    def test_identity_is_real_and_same_physical_connection(self):
        identity = self.ns["assert_identity"]
        connection = self.connection()
        physical = identity(connection)
        identity(connection, physical)
        for row in (("polypbase", "box1902_owner", "origin", 123),
                    ("box1902_migrator", "polypbase", "origin", 123),
                    ("box1902_migrator", "box1902_owner", "replica", 123),
                    ("box1902_migrator", "box1902_owner", "origin", 456)):
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = row
            with self.assertRaises(RuntimeError):
                identity(connection, physical)
        connection = self.connection()
        with self.assertRaises(RuntimeError):
            identity(connection, (object(), 123))
        connection.vendor = "sqlite"
        with self.assertRaises(RuntimeError):
            identity(connection)

    def test_python_checkout_guard_rechecks_all_properties(self):
        check = self.ns["assert_checkout"]
        for outputs in ([b"main\n", b"", TARGET.encode()],
                        [b"other\n"], [b"main\n", b" M file\n"],
                        [b"main\n", b"", b"b" * 40]):
            with patch.object(self.ns["subprocess"], "check_output", side_effect=outputs) as git:
                if len(outputs) == 3 and outputs[-1] == TARGET.encode():
                    check(Path("app"), TARGET)
                    self.assertEqual(git.call_count, 3)
                else:
                    with self.assertRaises(RuntimeError):
                        check(Path("app"), TARGET)

    def test_backup_uses_separate_auth_complete_verification_and_restores_environment(self):
        connection = self.connection()
        connection.settings_dict = {
            "ENGINE": "django.db.backends.postgresql", "USER": "box1902_migrator",
            "PASSWORD": "synthetic-migration-secret", "NAME": "synthetic", "HOST": "localhost",
            "OPTIONS": {"sslmode": "require", "options": "-c role=box1902_owner"},
        }
        helper = Mock()
        helper.create_complete_archive.return_value = {"path": "/tmp/fake.dump", "sha256": "c" * 64}
        def complete(database, *args, **kwargs):
            self.assertFalse(any(k.upper().startswith("PG") for k in os.environ))
            self.assertEqual(database["USER"], "synthetic_backup")
            self.assertEqual(database["PASSWORD"], "synthetic-backup-secret")
            self.assertEqual(database["OPTIONS"], {"sslmode": "require", "passfile": os.devnull})
            self.assertEqual(database["NAME"], "synthetic")
            return {"path": "/tmp/fake.dump", "sha256": "c" * 64}
        helper.create_complete_archive.side_effect = complete
        env = {"PGOPTIONS": "-c role=box1902_owner", "PGSERVICE": "synthetic",
               "POLYPBASE_BACKUP_USER": "synthetic_backup", "POLYPBASE_BACKUP_PASSWORD": "synthetic-backup-secret"}
        with patch.dict(os.environ, env, clear=True), patch.object(
            self.ns["importlib"].util, "module_from_spec", return_value=helper
        ), patch.object(self.ns["importlib"].util, "spec_from_file_location", return_value=Mock()):
            with contextlib.redirect_stdout(io.StringIO()):
                self.ns["backup"](connection, Path("backups"), Path("control"), TARGET)
            helper.verify_archive.assert_called_once_with(helper.create_complete_archive.return_value, client_major=18)
            self.assertEqual(os.environ["PGOPTIONS"], env["PGOPTIONS"])
            helper.create_complete_archive.side_effect = ValueError("synthetic incomplete backup")
            with self.assertRaises(ValueError):
                self.ns["backup"](connection, Path("backups"), Path("control"), TARGET)
            self.assertEqual(os.environ["PGOPTIONS"], env["PGOPTIONS"])
            self.assertEqual(connection.settings_dict["USER"], "box1902_migrator")

    def test_capture_includes_custom_0018_and_future_ordinary_migrations(self):
        import json
        migrations = [
            types.SimpleNamespace(app_label="measurements", name="0018_box_1902_preservation",
                                  operations=[Mock(describe=lambda: "Raw Python operation")]),
            types.SimpleNamespace(app_label="measurements", name="0019_future",
                                  operations=[Mock(describe=lambda: "Add field future to measurement")]),
        ]
        executor = Mock()
        executor.loader.detect_conflicts.return_value = {}
        executor.loader.graph.leaf_nodes.return_value = [("measurements", "0019_future")]
        executor.migration_plan.return_value = [(migration, False) for migration in migrations]
        module = types.ModuleType("django.db.migrations.executor")
        module.MigrationExecutor = Mock(return_value=executor)
        with patch.dict(sys.modules, {module.__name__: module}):
            result = self.ns["capture_plan"](object(), TARGET)
            document = json.loads(result[3])
            self.assertEqual([item["name"] for item in document["migrations"]],
                             ["0018_box_1902_preservation", "0019_future"])
            self.assertEqual(document["commit"], TARGET)
            executor.loader.check_consistent_history.assert_called_once()
            executor.loader.detect_conflicts.return_value = {"measurements": ["0019_a", "0019_b"]}
            with self.assertRaisesRegex(RuntimeError, "conflicting"):
                self.ns["capture_plan"](object(), TARGET)
            executor.loader.detect_conflicts.return_value = {}
            executor.migration_plan.return_value = [(migrations[0], True)]
            with self.assertRaisesRegex(RuntimeError, "backwards"):
                self.ns["capture_plan"](object(), TARGET)
            executor.loader.check_consistent_history.side_effect = RuntimeError("inconsistent history")
            with self.assertRaisesRegex(RuntimeError, "inconsistent history"):
                self.ns["capture_plan"](object(), TARGET)

    def test_final_execution_boundary_refuses_drift_and_rechecks_identity_and_checkout(self):
        sql = types.ModuleType("django.core.management.sql")
        sql.emit_pre_migrate_signal = Mock()
        sql.emit_post_migrate_signal = Mock()
        executor = Mock()
        pending = [object()]
        original = (executor, ["target"], pending, DATA)
        empty = (executor, ["target"], [], DATA)
        changed = (executor, ["target"], pending, DATA + b"changed")
        with patch.dict(sys.modules, {sql.__name__: sql}):
            for captures, fail_git, fail_identity in (
                ([empty], False, False), ([changed], False, False),
                ([original, empty], False, False), ([original, changed], False, False),
                ([original, original], True, False), ([original, original], False, True),
                ([original, original, empty], False, False),
            ):
                with self.subTest(captures=len(captures), git=fail_git, identity=fail_identity):
                    events = []
                    executor.reset_mock()
                    self.ns["capture_plan"] = Mock(side_effect=captures)
                    def verify(data, *args):
                        if data != DATA:
                            raise RuntimeError("plan changed")
                        events.append("verify")
                        return token().split(":")[1]
                    def checkout(*args):
                        events.append("git")
                        if fail_git:
                            raise RuntimeError("dirty checkout")
                    def identity(*args):
                        events.append("identity")
                        if fail_identity:
                            raise RuntimeError("identity changed")
                    self.ns.update(verify_plan=verify, assert_checkout=checkout, assert_identity=identity)
                    executor.migrate.side_effect = lambda *args, **kwargs: events.append("migrate") or Mock()
                    with contextlib.redirect_stdout(io.StringIO()):
                        if len(captures) < 3:
                            with self.assertRaises(RuntimeError):
                                self.ns["execute"](Mock(alias="default"), TARGET, Path("plan"), token(), Path("app"), None)
                            executor.migrate.assert_not_called()
                        else:
                            self.ns["execute"](Mock(alias="default"), TARGET, Path("plan"), token(), Path("app"), None)
                            boundary = events.index("migrate")
                            self.assertEqual(events[boundary - 3:boundary], ["verify", "git", "identity"])
                            self.assertFalse(executor.migrate.call_args.kwargs["fake"])
                            self.assertFalse(executor.migrate.call_args.kwargs["fake_initial"])

    def test_apply_orchestration_refuses_empty_or_changed_plan_before_backup(self):
        connection = self.connection()
        fake_django = types.ModuleType("django")
        fake_django.setup = Mock()
        modules = {"django": fake_django, "django.apps": Mock(), "django.db": Mock(connection=connection),
                   "django.core.management.commands.migrate": Mock(),
                   "django.utils.module_loading": Mock()}
        modules["django.apps"].apps.get_app_configs.return_value = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules):
            approved = Path(directory, "plan")
            approved.write_bytes(DATA)
            self.ns.update(assert_identity=Mock(return_value=(object(), 1)),
                           assert_checkout=Mock(), verify_plan=Mock(), backup=Mock(), execute=Mock())
            for pending, fail_verify in ((False, False), (True, True), (True, False)):
                self.ns["backup"].reset_mock()
                self.ns["execute"].reset_mock()
                self.ns["capture_plan"] = Mock(return_value=(None, [], [object()] if pending else [], DATA))
                self.ns["verify_plan"].side_effect = RuntimeError("changed plan") if fail_verify else None
                argv = ["executor", "--apply", TARGET, str(approved), token(), str(Path(directory, "app")), "backup", "control"]
                with patch.object(sys, "argv", argv):
                    if not pending or fail_verify:
                        with self.assertRaises(RuntimeError):
                            self.ns["main"]()
                        self.ns["backup"].assert_not_called()
                        self.ns["execute"].assert_not_called()
                    else:
                        events = []
                        self.ns["backup"].side_effect = lambda *a: events.append("backup")
                        self.ns["execute"].side_effect = lambda *a: events.append("execute")
                        self.ns["main"]()
                        self.assertEqual(events, ["backup", "execute"])
                        self.ns["backup"].side_effect = RuntimeError("backup failed")
                        with self.assertRaises(RuntimeError):
                            self.ns["main"]()
                        self.assertEqual(events, ["backup", "execute"])

    def test_plan_mode_never_verifies_approval_backs_up_or_executes(self):
        connection = self.connection()
        modules = {"django": Mock(), "django.apps": Mock(), "django.db": Mock(connection=connection),
                   "django.core.management.commands.migrate": Mock(), "django.utils.module_loading": Mock()}
        modules["django.apps"].apps.get_app_configs.return_value = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules):
            approved = Path(directory, "plan")
            self.ns.update(assert_identity=Mock(), assert_checkout=Mock(),
                           capture_plan=Mock(return_value=(None, [], [], DATA)),
                           verify_plan=Mock(), backup=Mock(), execute=Mock())
            with patch.object(sys, "argv", ["executor", "--plan", TARGET, str(approved), "", str(Path(directory, "app")), "backup", "control"]), contextlib.redirect_stdout(io.StringIO()):
                self.ns["main"]()
            self.assertEqual(approved.read_bytes(), DATA)
            for name in ("verify_plan", "backup", "execute"):
                self.ns[name].assert_not_called()


class IsolatedDjangoTests(unittest.TestCase):
    def test_real_executor_with_in_memory_sqlite_and_post_migrate_hooks(self):
        try:
            import django  # noqa: F401
        except ImportError:
            self.skipTest("Django unavailable; use uv run --no-sync python -m unittest")
        # A separate process cannot load application settings or backend/.env.
        code = '''
import contextlib, io, sys
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
settings.configure(INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    SECRET_KEY="isolated-synthetic-key", USE_TZ=True)
import django
django.setup()
namespace = {"__name__": "isolated_test"}
exec(compile(sys.stdin.read(), "dedicated_executor", "exec"), namespace)
from django.db import connection
from django.contrib.auth.models import Permission
from django.db.migrations.recorder import MigrationRecorder
target = "a" * 40
executor, targets, plan, data = namespace["capture_plan"](connection, target)
assert plan and b"auth" in data
assert not MigrationRecorder(connection).has_table()
namespace["verify_plan"] = lambda *args: None
namespace["assert_identity"] = lambda *args: None
namespace["assert_checkout"] = lambda *args: None
with contextlib.redirect_stdout(io.StringIO()):
    namespace["execute"](connection, target, Path("unused"), "unused", Path("unused"), None)
assert not namespace["capture_plan"](connection, target)[2]
assert Permission.objects.count() > 0
try:
    namespace["execute"](connection, target, Path("unused"), "unused", Path("unused"), None)
except RuntimeError as error:
    assert "stale approval" in str(error)
else:
    raise AssertionError("empty plan accepted")
'''
        result = subprocess.run([sys.executable, "-c", code], input=PYTHON_SOURCE.encode(),
                                capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
