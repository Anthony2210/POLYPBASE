"""Local mocked broker guards only: no sudo, VM, database or installed config."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'trusted_backup', ROOT / 'deploy/scripts/trusted_deployment_backup.py')
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)
TARGET = 'a' * 40
IDENTITY = types.SimpleNamespace(pw_uid=1234, pw_gid=1234)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.sources = {name: b'# reviewed installation\n' for name in broker.FILES}
        for name, content in self.sources.items():
            (self.bundle / name).write_bytes(content)
        self.database = {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'local_fixture',
                         'HOST': '/var/run/postgresql', 'PORT': '5433',
                         'USER': 'polypbase_backup', 'PASSWORD': '',
                         'OPTIONS': {'sslmode': 'disable', 'passfile': '/dev/null'}}
        self.control = {'approved_commit': TARGET, 'database': self.database,
                        'sha256': {name: hashlib.sha256(content).hexdigest()
                                   for name, content in self.sources.items()}}
        self.config = self.root / 'control.json'
        for name, value in (('CONFIG', self.config), ('BUNDLE', self.bundle)):
            mock = patch.object(broker, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(broker, 'require_path')
        self.guard = mock.start()
        self.addCleanup(mock.stop)

    def load(self, target=TARGET):
        self.config.write_text(json.dumps(self.control), encoding='utf-8')
        return broker.load_control(target)

    def test_exact_approved_manifest(self):
        self.assertEqual(self.load(), (self.database, self.sources))
        self.guard.assert_any_call(broker.LAUNCHER)
        self.guard.assert_any_call(self.config, private=True)
        for name in broker.FILES:
            self.guard.assert_any_call(self.bundle / name)

    def test_invalid_targets_refused_before_read(self):
        for target in ('A' * 40, 'a' * 39, 'a' * 41, TARGET + '\n', '../x', ''):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.load(target)
        self.guard.assert_not_called()

    def test_unapproved_commit(self):
        with self.assertRaises(ValueError):
            self.load('b' * 40)

    def test_bundle_tampering(self):
        for name in broker.FILES:
            original = (self.bundle / name).read_bytes()
            (self.bundle / name).write_bytes(b'# unapproved\n')
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.load()
            (self.bundle / name).write_bytes(original)

    def test_missing_or_extra_manifest_entry(self):
        self.control['sha256']['extra.py'] = 'a' * 64
        with self.assertRaises(ValueError):
            self.load()
        del self.control['sha256']['extra.py']
        del self.control['sha256'][broker.FILES[0]]
        with self.assertRaises(ValueError):
            self.load()

    def test_runtime_login_and_password_file_refused(self):
        self.database['USER'] = 'polypbase'
        with self.assertRaises(ValueError):
            self.load()
        self.database['USER'] = 'polypbase_backup'
        self.database['OPTIONS']['passfile'] = '/tmp/caller-passwords'
        with self.assertRaises(ValueError):
            self.load()

    def test_caller_configuration_keys_refused(self):
        for key in ('output_dir', 'python', 'settings', 'environment'):
            self.control[key] = '/tmp/untrusted'
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load()
            del self.control[key]


class IdentityTests(unittest.TestCase):
    def test_runtime_cannot_share_backup_uid_or_group(self):
        backup = types.SimpleNamespace(pw_name='polypbase_backup', pw_uid=1234, pw_gid=1234)
        runtime = types.SimpleNamespace(pw_name='polypbase', pw_uid=999, pw_gid=999)
        users = {'polypbase_backup': backup, 'polypbase': runtime}
        pwd = types.SimpleNamespace(getpwnam=users.__getitem__, getpwall=lambda: users.values())
        group = types.SimpleNamespace(gr_mem=[])
        grp = types.SimpleNamespace(getgrgid=lambda gid: group)
        with patch.dict(sys.modules, {'pwd': pwd, 'grp': grp}):
            self.assertEqual(broker.backup_identity(), backup)
            for attribute in ('pw_uid', 'pw_gid'):
                setattr(runtime, attribute, 1234)
                with self.subTest(attribute=attribute), self.assertRaises(ValueError):
                    broker.backup_identity()
                setattr(runtime, attribute, 999)
            group.gr_mem = ['polypbase']
            with self.assertRaises(ValueError):
                broker.backup_identity()


class PathTests(unittest.TestCase):
    def check(self, *, mode=stat.S_IFREG | 0o640, uid=0, gid=1234,
              parent_mode=stat.S_IFDIR | 0o755, parent_uid=0, private=True):
        path = Mock()
        parent = Mock()
        path.parents = [parent]
        parent.lstat.return_value = types.SimpleNamespace(st_mode=parent_mode, st_uid=parent_uid)
        path.lstat.return_value = types.SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)
        with patch.object(broker, 'backup_identity', return_value=IDENTITY):
            return broker.require_path(path, private=private)

    def test_root_owned_private_config(self):
        self.check()

    def test_links_writable_paths_wrong_owners_refused(self):
        for options in ({'mode': stat.S_IFLNK | 0o777}, {'uid': 1234},
                        {'mode': stat.S_IFREG | 0o660}, {'mode': stat.S_IFREG | 0o644},
                        {'gid': 999}, {'parent_uid': 1234},
                        {'parent_mode': stat.S_IFDIR | 0o775},
                        {'parent_mode': stat.S_IFLNK | 0o755}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check(**options)


class ExecutionTests(unittest.TestCase):
    def test_real_backup_script_calls_complete_helper_without_app_settings(self):
        archive_name = 'pre-aaaaaaaa-20261112T120000Z.dump'
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / archive_name
            archive.write_bytes(b'fixture archive')
            archive.chmod(0o600)
            database = {'ENGINE': 'django.db.backends.postgresql', 'USER': 'dedicated'}
            settings = types.SimpleNamespace(configure=Mock())
            def configure(**kwargs):
                settings.DATABASES = kwargs['DATABASES']
            settings.configure.side_effect = configure
            django = types.ModuleType('django')
            django.setup = Mock()
            conf = types.ModuleType('django.conf')
            conf.settings = settings
            probe = types.ModuleType('backup_test_probe')
            probe.complete = Mock(return_value={'path': str(archive), 'size_bytes': 15,
                                               'sha256': 'a' * 64})
            sources = {'database_backup.py': b'from backup_test_probe import complete as create_complete_archive\n',
                       'backup_database.py': (ROOT / 'deploy/scripts/backup_database.py').read_bytes()}
            info = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                                         st_uid=1234, st_size=15)
            output = io.StringIO()
            with patch.dict(sys.modules, {'django': django, 'django.conf': conf,
                                          'backup_test_probe': probe}), \
                    patch.object(broker, 'OUTPUT', Path(directory)), \
                    patch.object(broker.os, 'geteuid', return_value=1234, create=True), \
                    patch.object(Path, 'lstat', return_value=info), \
                    patch.object(sys, 'argv', []), patch.dict(os.environ, {}, clear=True), \
                    patch.object(broker.os, 'umask'), redirect_stdout(output):
                broker.run_backup(TARGET, database, sources)
            probe.complete.assert_called_once_with(database, Path(directory), 'pre-aaaaaaaa', client_major=18)
            self.assertEqual(settings.configure.call_args.kwargs['INSTALLED_APPS'], [])
            self.assertEqual(output.getvalue(), 'BACKUP_OK ' + str(archive) + '\n')

    def test_main_scrubs_environment_and_uses_only_fixed_control(self):
        observed = {}
        def run(*args):
            observed.update(os.environ)
        with patch.object(sys, 'flags', types.SimpleNamespace(isolated=1)), \
                patch.object(sys, 'argv', ['broker', TARGET]), \
                patch.object(broker, 'backup_identity', return_value=IDENTITY), \
                patch.multiple(broker.os, getuid=lambda: 1234, geteuid=lambda: 1234,
                               getgid=lambda: 1234, getegid=lambda: 1234,
                               getgroups=lambda: [1234], create=True), \
                patch.object(broker.os, 'chdir'), patch.object(broker.os, 'umask'), \
                patch.dict(os.environ, {'PGSERVICE': 'evil', 'PYTHONPATH': '/tmp/evil',
                                        'POLYPBASE_BACKUP_PASSWORD': 'not-real'}, clear=True), \
                patch.object(broker, 'load_control', return_value=({}, {})) as load, \
                patch.object(broker, 'require_path', return_value=types.SimpleNamespace(st_mode=0o40700)), \
                patch.object(broker, 'run_backup', side_effect=run):
            self.assertEqual(broker.main(), 0)
        load.assert_called_once_with(TARGET)
        self.assertEqual(observed, broker.SAFE_ENV)

    def test_wrong_identity_and_extra_arguments_fail_before_config(self):
        with patch.object(sys, 'flags', types.SimpleNamespace(isolated=1)), \
                patch.object(sys, 'argv', ['broker', TARGET]), \
                patch.object(broker, 'backup_identity', return_value=IDENTITY), \
                patch.object(broker.os, 'getuid', return_value=999, create=True), \
                patch.object(broker, 'load_control') as load, redirect_stderr(io.StringIO()):
            self.assertEqual(broker.main(), 1)
            with patch.object(sys, 'argv', ['broker', TARGET, '--override']):
                self.assertEqual(broker.main(), 1)
        load.assert_not_called()

    def test_errors_do_not_print_secrets(self):
        output = io.StringIO()
        with patch.object(sys, 'flags', types.SimpleNamespace(isolated=1)), \
                patch.object(sys, 'argv', ['broker', TARGET]), \
                patch.object(broker, 'backup_identity', side_effect=RuntimeError('secret-fixture')), \
                redirect_stderr(output):
            self.assertEqual(broker.main(), 1)
        self.assertNotIn('secret-fixture', output.getvalue())

    def test_deploy_uses_fixed_broker_and_no_archive_read_as_web(self):
        source = (ROOT / 'deploy/scripts/deploy_release.sh').read_text(encoding='utf-8')
        section = source.split('CURRENT_STEP="database backup"', 1)[1].split(
            'CURRENT_STEP="fast-forward update"', 1)[0]
        self.assertIn('/usr/bin/sudo -n -u polypbase_backup', section)
        self.assertIn('/usr/local/libexec/polypbase-deployment-backup "$TARGET_COMMIT"', section)
        for forbidden in ('.venv', 'git -C', 'POLYPBASE_BACKUP_PASSWORD', 'verify_archive', '[[ -s'):
            self.assertNotIn(forbidden, section)


if __name__ == '__main__':
    unittest.main()
