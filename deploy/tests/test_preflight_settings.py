"""Run real Django commands with copied settings and synthetic data only."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


CONFIG_DIR = Path(__file__).resolve().parents[2] / "backend" / "config"
SYNTHETIC_ENV = {
    "POSTGRES_DB": "synthetic_database",
    "POSTGRES_USER": "synthetic_user",
    "POSTGRES_PASSWORD": "synthetic_password",
    "POSTGRES_HOST": "database.invalid",
    "POSTGRES_PORT": "5432",
    "POSTGRES_SSLMODE": "require",
    "DATABASE_URL": "postgresql://synthetic_user:synthetic_password@database.invalid/synthetic_database",
    "DJANGO_SETTINGS_MODULE": "config.settings",
    "POLYPBASE_TEST_POSTGRES": "1",
}
SQLITE = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Installed before importing Django or the copied settings. Even a broken
# preflight override must not reach a database or resolve a network address.
BOOTSTRAP = """
import importlib.abc
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(root))
sys.dont_write_bytecode = True

def guard(event, args):
    if event.startswith('socket.'):
        raise RuntimeError('ISOLATION_GUARD: sockets are forbidden')
    if event == 'sqlite3.connect':
        name = str(args[0])
        if name != ':memory:' and not (
            name.startswith('file:memorydb_') and 'mode=memory' in name
        ):
            raise RuntimeError('ISOLATION_GUARD: file-backed SQLite is forbidden')

sys.addaudithook(guard)

class BlockPostgres(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in (
            'django.db.backends.postgresql', 'psycopg', 'psycopg2', 'pg8000',
        )):
            raise RuntimeError('ISOLATION_GUARD: PostgreSQL import is forbidden')

sys.meta_path.insert(0, BlockPostgres())
from config import preflight_settings
assert Path(preflight_settings.__file__).parent == root / 'config'
print('RAW_DATABASES=' + json.dumps(preflight_settings.DATABASES), flush=True)

# Keep the real settings/database override, but exclude project apps, URLs and
# middleware: the temporary directory deliberately contains no production code.
preflight_settings.INSTALLED_APPS = ['fixture']
preflight_settings.ROOT_URLCONF = 'fixture.urls'
preflight_settings.MIDDLEWARE = []
preflight_settings.AUTH_USER_MODEL = 'fixture.Thing'

from django.core.management import execute_from_command_line
execute_from_command_line(['isolated-manage.py', *sys.argv[1:]])

from django.conf import settings
print('EFFECTIVE_SETTINGS=' + json.dumps({
    'module': settings.SETTINGS_MODULE,
    'engine': settings.DATABASES['default']['ENGINE'],
    'name': settings.DATABASES['default']['NAME'],
    'aliases': list(settings.DATABASES),
    'hashers': settings.PASSWORD_HASHERS,
    'environment': {key: os.getenv(key) for key in (
        'POSTGRES_DB', 'DATABASE_URL', 'POLYPBASE_TEST_POSTGRES',
    )},
}), flush=True)
"""

MODEL = """
from django.db import models

class Thing(models.Model):
    value = models.IntegerField(default=0)
"""
MIGRATION = """
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.CreateModel(
        name='Thing',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                      serialize=False, verbose_name='ID')),
            ('value', models.IntegerField(default=0)),
        ],
    )]
"""
FIXTURE_TEST = """
import os
from django.conf import settings
from django.db import connection
from django.test import TestCase
from .models import Thing

class SQLiteTests(TestCase):
    def test_database_and_failure(self):
        self.assertEqual(settings.SETTINGS_MODULE, 'config.preflight_settings')
        self.assertEqual(connection.vendor, 'sqlite')
        name = str(connection.settings_dict['NAME'])
        self.assertTrue(name == ':memory:' or (
            name.startswith('file:memorydb_') and 'mode=memory' in name
        ))
        Thing.objects.create(value=0)
        self.assertEqual(Thing.objects.get().value, 0)
        print('SQLITE_MEMORY_TEST_EXECUTED', flush=True)
        if os.getenv('FIXTURE_TEST_FAILURE') == '1':
            self.fail('intentional fixture test failure')
"""


class PreflightSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [name for name in ('django', 'dotenv') if importlib.util.find_spec(name) is None]
        if missing:
            raise unittest.SkipTest(f"{sys.executable} needs installed dependencies: {', '.join(missing)}")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = self.root / 'config'
        config.mkdir()
        for name in ('settings.py', 'test_settings.py', 'preflight_settings.py', '__init__.py'):
            shutil.copyfile(CONFIG_DIR / name, config / name)
        self.dotenv = {**SYNTHETIC_ENV, 'DJANGO_SECRET_KEY': 'synthetic-test-only-key'}
        self.write_dotenv()
        self.write('fixture/__init__.py', '')
        self.write('fixture/urls.py', 'urlpatterns = []\n')
        self.write('fixture/models.py', MODEL)
        self.write('fixture/migrations/__init__.py', '')
        self.write('fixture/migrations/0001_initial.py', MIGRATION)
        self.write('fixture/tests.py', FIXTURE_TEST)

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding='utf-8')

    def write_dotenv(self):
        self.write('.env', ''.join(f'{key}={value}\n' for key, value in self.dotenv.items()))

    def run_command(self, *arguments, inherited=True, **extra_env):
        # Do not pass real credentials, Python path hooks or application flags.
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LANG', 'LC_ALL'}
        }
        if inherited:
            environment.update(SYNTHETIC_ENV)
            environment['DJANGO_SECRET_KEY'] = 'synthetic-inherited-key'
        environment.update(extra_env)
        result = subprocess.run(
            [sys.executable, '-I', '-c', BOOTSTRAP, str(self.root), *arguments,
             '--settings=config.preflight_settings'],
            cwd=self.root, env=environment, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30,
        )
        output = result.stdout + result.stderr
        self.assertNotIn('ISOLATION_GUARD:', output, output)
        self.assertEqual(self.marker(result, 'RAW_DATABASES'), SQLITE, output)
        self.assertFalse(list(self.root.rglob('*.sqlite3')), output)
        return result

    def marker(self, result, name):
        prefix = name + '='
        lines = [line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]
        self.assertEqual(len(lines), 1, result.stdout + result.stderr)
        return json.loads(lines[0])

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        effective = self.marker(result, 'EFFECTIVE_SETTINGS')
        self.assertEqual(effective['module'], 'config.preflight_settings')
        self.assertEqual(effective['engine'], SQLITE['default']['ENGINE'])
        self.assertEqual(effective['name'], ':memory:')
        self.assertEqual(effective['aliases'], ['default'])
        self.assertEqual(effective['hashers'], ['django.contrib.auth.hashers.MD5PasswordHasher'])
        self.assertEqual(effective['environment'], {
            key: SYNTHETIC_ENV[key] for key in ('POSTGRES_DB', 'DATABASE_URL', 'POLYPBASE_TEST_POSTGRES')
        })

    def test_inherited_database_variables_and_qa_optin_cannot_override_settings(self):
        # Dotenv has different values, proving that inherited values survive.
        self.dotenv.update(POSTGRES_DB='dotenv_database', DATABASE_URL='postgresql://dotenv.invalid/db')
        self.write_dotenv()
        self.assert_success(self.run_command('check'))

    def test_dotenv_fallback_including_qa_optin_cannot_override_settings(self):
        self.assert_success(self.run_command('check', inherited=False))

    def test_matching_migrations_pass_without_database_environment_cleanup(self):
        for inherited in (True, False):
            with self.subTest(inherited=inherited):
                result = self.run_command('makemigrations', '--check', '--dry-run', inherited=inherited)
                self.assert_success(result)
                self.assertIn('No changes detected', result.stdout)

    def test_real_system_check_error_is_not_suppressed(self):
        self.write('fixture/models.py', MODEL + '\n    invalid = models.CharField(max_length=-1)\n')
        result = self.run_command('check')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SystemCheckError', result.stderr)
        self.assertIn('fields.E121', result.stderr)

    def test_real_migration_drift_is_not_suppressed_or_written(self):
        self.write('fixture/models.py', MODEL + '\n    extra = models.IntegerField(default=0)\n')
        result = self.run_command('makemigrations', '--check', '--dry-run')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Add field extra to thing', result.stdout)
        self.assertEqual(
            sorted(path.name for path in (self.root / 'fixture/migrations').iterdir()),
            ['0001_initial.py', '__init__.py'],
        )

    def test_actual_sqlite_memory_test_success_and_failure(self):
        for failure in ('0', '1'):
            with self.subTest(failure=failure):
                result = self.run_command('test', 'fixture', '--noinput', FIXTURE_TEST_FAILURE=failure)
                output = result.stdout + result.stderr
                self.assertIn('SQLITE_MEMORY_TEST_EXECUTED', output)
                if failure == '0':
                    self.assert_success(result)
                    self.assertIn('OK', result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn('intentional fixture test failure', result.stderr)
                    self.assertIn('FAILED (failures=1)', result.stderr)


if __name__ == '__main__':
    unittest.main()
