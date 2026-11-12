"""Disposable PG18 runner and backup/restore proof; never an existing endpoint.

Run with --bin-dir pointing to local PostgreSQL 18 binaries. Runner planning,
byte comparison, execution and SQL identity checks are real; only assert_checkout
is replaced for this undeployed fixture. The backup proof separately substitutes
the Debian root-owned client resolver; pg_dump/pg_restore are real.
"""

import argparse
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', required=True, type=Path)
    args = parser.parse_args()
    binaries = args.bin_dir.resolve()
    suffix = '.exe' if os.name == 'nt' else ''
    tools = {name: str(binaries / (name + suffix)) for name in
             ('initdb', 'pg_ctl', 'pg_dump', 'pg_restore')}
    for executable in tools.values():
        result = subprocess.run([executable, '--version'], check=True,
                                capture_output=True, timeout=10)
        if b'(PostgreSQL) 18.' not in result.stdout:
            raise RuntimeError('Only PostgreSQL 18 binaries are allowed')
    for key in list(os.environ):
        if key.upper().startswith(('PG', 'POSTGRES_', 'POLYPBASE_BACKUP_')):
            del os.environ[key]
    os.environ.update(PYTHON_DOTENV_DISABLED='1', POLYPBASE_TEST_POSTGRES='0', DJANGO_DEBUG='1')
    sys.path.insert(0, str(ROOT / 'backend'))
    import psycopg
    from psycopg import sql
    from config import database_backup as backup

    with tempfile.TemporaryDirectory(prefix='polypbase-backup-pg18-') as temporary:
        root = Path(temporary)
        data = root / 'data'
        # Install a real temporary app, initially with no migrations. Its later
        # CreateModel/RunPython migration must be discovered by the real loader.
        app = root / 'backup_runner_probe'
        migrations = app / 'migrations'
        migrations.mkdir(parents=True)
        for path in (app / '__init__.py', app / 'models.py', migrations / '__init__.py'):
            path.write_text('', encoding='utf-8')
        sys.path.insert(0, str(root))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        if port in (5432, 5433, 55432, 55439):
            raise RuntimeError('Refuse reserved/shared ports')

        def tool(name, *arguments):
            # pg_ctl descendants on Windows can retain PIPE handles indefinitely.
            with (root / 'tools.log').open('ab') as output:
                subprocess.run([tools[name], *map(str, arguments)], check=True,
                               stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                               timeout=120)

        def connect(user='backup_qa_admin', database='backup_source', login_password='', startup_role=None):
            options = '-c statement_timeout=60000 -c lock_timeout=5000'
            if startup_role is not None:
                options += f' -c role={startup_role}'
            return psycopg.connect(host='127.0.0.1', port=port, user=user,
                                   dbname=database, password=login_password, passfile=os.devnull,
                                   sslmode='disable', connect_timeout=5, autocommit=True,
                                   options=options)

        started = False
        try:
            tool('initdb', '-D', data, '-U', 'backup_qa_admin', '--no-locale',
                 '--encoding=UTF8', '--auth-local=trust', '--auth-host=trust')
            configuration = ["listen_addresses = '127.0.0.1'", f'port = {port}',
                             "shared_buffers = '16MB'", 'max_connections = 15']
            if os.name != 'nt':
                configuration.append("unix_socket_directories = '" + root.as_posix().replace("'", "''") + "'")
            (data / 'postgresql.conf').write_text('\n'.join(configuration) + '\n', encoding='utf-8')
            (data / 'pg_hba.conf').write_text('host all all 127.0.0.1/32 trust\n', encoding='utf-8')
            started = True
            tool('pg_ctl', '-D', data, '-l', root / 'server.log', '-w', '-t', '30', 'start')
            with connect(database='postgres') as admin:
                version, directory = admin.execute(
                    "SELECT current_setting('server_version_num')::int, current_setting('data_directory')"
                ).fetchone()
                if not 180000 <= version < 190000 or Path(directory).resolve() != data.resolve():
                    raise RuntimeError('Fresh cluster identity mismatch')
                admin.execute('CREATE ROLE polypbase LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
                admin.execute('CREATE DATABASE backup_source OWNER polypbase')
                admin.execute('CREATE DATABASE backup_restored')

            from django.conf import settings
            from config import test_settings
            database = {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'backup_source',
                        'USER': 'polypbase', 'PASSWORD': '', 'HOST': '127.0.0.1', 'PORT': str(port),
                        'OPTIONS': {'sslmode': 'disable', 'passfile': os.devnull}}
            configuration = {name: getattr(test_settings, name) for name in dir(test_settings) if name.isupper()}
            configuration['DATABASES'] = {'default': database.copy()}
            configuration['INSTALLED_APPS'] = [*configuration['INSTALLED_APPS'], 'backup_runner_probe']
            settings.configure(**configuration)
            import django
            django.setup()
            from django.db import connection
            from django.db.migrations.executor import MigrationExecutor
            from apps.measurements import box_1902_sql as contract
            migration = ('measurements', '0018_box_1902_preservation')
            executor = MigrationExecutor(connection)
            targets = [('measurements', '0017_bucket_probe_last_measured_index') if node == migration else node
                       for node in executor.loader.graph.leaf_nodes()]
            executor.migrate(targets)
            with connect('polypbase') as runtime:
                runtime.execute('CREATE TABLE public.backup_science_probe '
                                '(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, reading numeric)')
                runtime.execute('INSERT INTO public.backup_science_probe(reading) VALUES (0),(NULL)')
            connection.close()
            with connect() as admin:
                admin.execute(contract.provision_sql())
            # Authenticate as the genuine migrator, not an administrator using
            # SET SESSION AUTHORIZATION. Activate the SET-only owner membership
            # using libpq startup options, as the dedicated runner requires.
            password = secrets.token_urlsafe(32)
            with connect() as admin:
                admin.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(
                    sql.Identifier(contract.MIGRATOR), sql.Literal(password)))
                (data / 'pg_hba.conf').write_text(
                    f'host all {contract.MIGRATOR} 127.0.0.1/32 scram-sha-256\n'
                    'host all all 127.0.0.1/32 trust\n', encoding='utf-8')
                if not admin.execute('SELECT pg_reload_conf()').fetchone()[0]:
                    raise AssertionError('Private HBA reload failed')
            # pg_ctl reload completion alone does not wait for HBA activation.
            # Poll only this newly verified cluster, never an inherited endpoint.
            import time
            deadline = time.monotonic() + 10
            while True:
                try:
                    with connect(contract.MIGRATOR):
                        pass
                except psycopg.OperationalError:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError('Migrator SCRAM rule was not activated')
                time.sleep(0.05)

            def select_login(user, login_password='', owner=False):
                connection.close()
                options = dict(database['OPTIONS'])
                if owner:
                    options['options'] = '-c role=box1902_owner'
                selected = {'USER': user, 'PASSWORD': login_password, 'OPTIONS': options}
                settings.DATABASES['default'].update(selected)
                connection.settings_dict.update(selected)

            runner_path = ROOT / 'deploy/scripts/run_dedicated_migrations.sh'
            runner_source = runner_path.read_text(encoding='utf-8')
            embedded = runner_source.split("<<'PY'\n", 1)[1].rsplit('\nPY\n', 1)[0]
            runner = {'__name__': 'dedicated_runner_pg18_proof'}
            exec(compile(embedded, str(runner_path), 'exec'), runner)
            target = subprocess.check_output(
                ['git', '--no-pager', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                stdin=subprocess.DEVNULL, timeout=30,
            ).decode('ascii').strip()
            if len(target) != 40:
                raise AssertionError('Expected the actual full application commit')
            print(f'RUNNER_EVIDENCE commit={target} embedded_sha256='
                  f'{hashlib.sha256(embedded.encode()).hexdigest()}', flush=True)

            def boundary_state():
                with connect() as admin:
                    history = admin.execute('SELECT app,name,applied FROM public.django_migrations ORDER BY app,name').fetchall()
                    relations = admin.execute(
                        "SELECT n.nspname,c.relname,c.relkind,c.relowner,c.relacl::text "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname IN ('public','box1902_private') ORDER BY 1,2").fetchall()
                    private = admin.execute("SELECT to_regnamespace('box1902_private')::oid").fetchone()
                    science = admin.execute('SELECT id,reading FROM public.backup_science_probe ORDER BY id').fetchall()
                    return history, relations, private, science

            def refused(message, action):
                before = boundary_state()
                output = io.StringIO()
                with redirect_stdout(output):
                    try:
                        action()
                    except runner['GuardError'] as error:
                        if message not in str(error):
                            raise
                    else:
                        raise AssertionError(f'Runner did not refuse: {message}')
                if boundary_state() != before:
                    raise AssertionError('Refused runner operation mutated history/schema/science')
                if 'MIGRATION_COMPLETE' in output.getvalue() or 'MIGRATION_REVIEW_APPROVED' in output.getvalue():
                    raise AssertionError('Refused operation emitted a success marker')

            try:
                with connect(contract.MIGRATOR, login_password='wrong-disposable-password'):
                    pass
            except psycopg.OperationalError:
                pass
            else:
                raise AssertionError('Migrator authenticated with a wrong password')
            select_login(contract.RUNTIME)
            refused('database identity mismatch', lambda: runner['assert_identity'](connection))
            select_login(contract.MIGRATOR, password)
            refused('database identity mismatch', lambda: runner['assert_identity'](connection))
            select_login(contract.MIGRATOR, password, owner=True)
            physical = runner['assert_identity'](connection)
            with connection.cursor() as cursor:
                cursor.execute('SELECT session_user,current_user,current_setting(\'session_replication_role\')')
                if cursor.fetchone() != (contract.MIGRATOR, contract.OWNER, 'origin'):
                    raise AssertionError('Startup role did not establish the dedicated identity')
            witness = connection.copy(alias='runner_identity_witness')
            try:
                witness_identity = runner['assert_identity'](witness)
                refused('migration connection changed', lambda: runner['assert_identity'](connection, witness_identity))
            finally:
                witness.close()
            print('PASS: migrator SCRAM login + startup owner role; wrong password/runtime/unset owner/other physical connection refused', flush=True)

            approved = root / '0018-reviewed-plan.json'
            _, _, pending, plan_bytes = runner['capture_plan'](connection, target)
            if [(item.app_label, item.name) for item, backwards in pending if not backwards] != [migration]:
                raise AssertionError('Fixture must have exactly the real 0018 pending')
            document = json.loads(plan_bytes)
            if document['commit'] != target or [(item['app'], item['name']) for item in document['migrations']] != [migration]:
                raise AssertionError('Captured plan bytes do not describe the real commit and 0018')
            approved.write_bytes(plan_bytes)
            approval = f'{target}:{hashlib.sha256(plan_bytes).hexdigest()}'
            if runner['verify_plan'](plan_bytes, approved, approval, target) != approval.split(':')[1]:
                raise AssertionError('Real cmp/hash verification failed')

            def run_reviewed(path=approved, token=approval):
                runner['execute'](connection, target, path, token, ROOT, physical)

            # assert_checkout is the only replaced runner function: this fixture
            # intentionally runs unstaged local test code, not a deployed checkout.
            with patch.dict(runner, {'assert_checkout': lambda app_dir, commit: None}):
                for token in ('', f'{target}:{"0" * 64}', f'{"0" * 40}:{hashlib.sha256(plan_bytes).hexdigest()}'):
                    refused('approval does not match', lambda token=token: run_reviewed(token=token))
                approved.write_bytes(plan_bytes + b'\n')
                refused('current plan differs', run_reviewed)
                approved.write_bytes(plan_bytes)
                refused('current plan differs', lambda: runner['verify_plan'](
                    plan_bytes + b' ', approved, approval, target))
                print('PASS: 0018 no/wrong approval, wrong commit and changed bytes refuse without mutation', flush=True)
                run_reviewed()
                runner['assert_identity'](connection, physical)
                contract.attest(connection, allow_retired=False)
                if runner['capture_plan'](connection, target)[2]:
                    raise AssertionError('0018 remained pending')
                refused('no pending migrations', run_reviewed)
                print('PASS: embedded runner executes actual 0018, attests, and refuses stale no-pending approval', flush=True)

                (migrations / '0001_runner_probe.py').write_text('''from django.db import migrations, models


def seed(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT session_user,current_user,current_setting('session_replication_role'),pg_backend_pid()")
        login, owner, replication, pid = cursor.fetchone()
    if (login, owner, replication) != ('box1902_migrator', 'box1902_owner', 'origin'):
        raise RuntimeError('Future migration ran with the wrong authority')
    Probe = apps.get_model('backup_runner_probe', 'RunnerProbe')
    Probe.objects.using(schema_editor.connection.alias).bulk_create([
        Probe(reading=value, login=login, owner=owner, replication=replication, backend_pid=pid)
        for value in (0, None)
    ])


class Migration(migrations.Migration):
    initial = True
    dependencies = [('measurements', '0018_box_1902_preservation')]
    operations = [
        migrations.CreateModel(name='RunnerProbe', fields=[
            ('id', models.BigAutoField(primary_key=True)),
            ('reading', models.DecimalField(max_digits=8, decimal_places=2, null=True)),
            ('login', models.CharField(max_length=63)),
            ('owner', models.CharField(max_length=63)),
            ('replication', models.CharField(max_length=16)),
            ('backend_pid', models.IntegerField()),
        ]),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
''', encoding='utf-8')
                importlib.invalidate_caches()
                _, _, future_pending, future_bytes = runner['capture_plan'](connection, target)
                if [(item.app_label, item.name) for item, backwards in future_pending if not backwards] != [
                        ('backup_runner_probe', '0001_runner_probe')]:
                    raise AssertionError('Runner failed to discover the real future migration')
                refused('current plan differs', run_reviewed)
                future_approved = root / 'future-reviewed-plan.json'
                future_approved.write_bytes(future_bytes)
                future_token = f'{target}:{hashlib.sha256(future_bytes).hexdigest()}'
                run_reviewed(future_approved, future_token)
                runner['assert_identity'](connection, physical)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT reading,login,owner,replication,backend_pid '
                                   'FROM public.backup_runner_probe_runnerprobe ORDER BY id')
                    expected = [(value, contract.MIGRATOR, contract.OWNER, 'origin', physical[1]) for value in (0, None)]
                    if cursor.fetchall() != expected:
                        raise AssertionError('Future migration changed role, connection or zero/null')
                refused('no pending migrations', lambda: run_reviewed(future_approved, future_token))
                contract.attest(connection, allow_retired=False)
                print('PASS: future CreateModel + RunPython discovered, reviewed and executed on the same migrator connection', flush=True)

            select_login(contract.RUNTIME)
            refused('database identity mismatch', lambda: runner['assert_identity'](connection))
            try:
                with connect(contract.RUNTIME, startup_role=contract.OWNER):
                    pass
            except psycopg.OperationalError:
                pass
            else:
                raise AssertionError('Runtime acquired owner through startup options')
            with connect(contract.RUNTIME) as runtime:
                for statement in ('SET ROLE box1902_owner', 'SET session_replication_role = replica',
                                  'CREATE TABLE public.runtime_forbidden(id int)',
                                  'SELECT * FROM box1902_private.context_v2'):
                    try:
                        runtime.execute(statement)
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError('Runtime acquired protected authority')
            connection.close()
            print('PASS: runtime cannot SET owner/disable triggers/create schema objects/read private provenance', flush=True)

            # Privileged synthetic fixture only: not a correction or an approval.
            # Populate all three immutable tables to prove their data is restored.
            with connect() as admin:
                admin.execute('SET session_replication_role = replica')
                admin.execute("INSERT INTO box1902_private.approval_v2 VALUES "
                              "(1,%s,1,'{}',encode(sha256(convert_to('{}','UTF8')),'hex'),'{}',now(),now())",
                              [contract.OPERATION])
                admin.execute("INSERT INTO box1902_private.context_v2 VALUES "
                              "(1,pg_current_xact_id(),pg_backend_pid(),'box1902_executor','{}','{}',true)")
                admin.execute("INSERT INTO box1902_private.correction_v2 VALUES "
                              "(1,%s,1,'box1902_executor','Synthetic backup fixture','{}','{}','{}','{}',"
                              "encode(sha256(convert_to('{}','UTF8')),'hex'),now())", [contract.OPERATION])
                admin.execute('SET session_replication_role = origin')

            spec = importlib.util.spec_from_file_location('backup_script_pg18', ROOT / 'deploy/scripts/backup_database.py')
            script = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(script)
            settings.DATABASES['default']['USER'] = contract.RUNTIME

            def invoke(label, user=None):
                environment = {} if user is None else {'POLYPBASE_BACKUP_USER': user}
                output = io.StringIO()
                with patch.dict(os.environ, environment), \
                        patch.object(sys, 'argv', ['backup_database.py', '--output-dir', str(root / 'archives'),
                                                   '--label', label, '--retention-days', '1']), \
                        redirect_stdout(output):
                    script.main()
                return output.getvalue()

            # Only bypass Debian filesystem ownership checks in this local proof.
            # No dump, restore, SQL, inventory or completeness result is mocked.
            def local_client(name, major):
                if major != 18:
                    raise AssertionError('Wrong client major')
                return tools[name]

            # Exercise the preflight directly too, so SQL/client failures in this
            # synthetic cluster are distinguishable from an intended ACL refusal.
            with backup._connect_backup(database) as preflight:
                with preflight.cursor() as cursor:
                    try:
                        backup._complete_inventory(cursor)
                    except ValueError as error:
                        if 'Incomplete backup authority' not in str(error):
                            raise
                    else:
                        raise AssertionError('Runtime inventory unexpectedly succeeded')

            with patch.object(backup, 'require_client', side_effect=local_client):
                for label, user in [('runtime-default', None), ('runtime-explicit', contract.RUNTIME)]:
                    try:
                        invoke(label, user)
                    except ValueError as error:
                        if 'Incomplete backup authority' not in str(error):
                            raise
                    else:
                        raise AssertionError('Runtime backup unexpectedly succeeded')
                print('PASS: normal script call and explicit runtime fail closed on private data', flush=True)
                output = invoke('complete', contract.BACKUP)
                if not output.startswith('BACKUP_OK '):
                    raise AssertionError('Complete backup did not return success')
                archives = list((root / 'archives').glob('*.dump'))
                if len(archives) != 1:
                    raise AssertionError('Failed backup left an archive')
                archive = archives[0]
                backup.verify_archive({'path': str(archive)}, client_major=18)
                print('PASS: separate read-only login, exported snapshot, complete TOC + checksum', flush=True)
                os.utime(archive, (1, 1))
                with connect() as admin:
                    admin.execute('REVOKE SELECT ON box1902_private.context_v2 FROM box1902_backup')
                try:
                    invoke('missing-grant', contract.BACKUP)
                except ValueError as error:
                    if 'Incomplete backup authority' not in str(error):
                        raise
                else:
                    raise AssertionError('Incomplete dedicated backup unexpectedly succeeded')
                with connect() as admin:
                    admin.execute('GRANT SELECT ON box1902_private.context_v2 TO box1902_backup')
                if not archive.is_file():
                    raise AssertionError('Failed backup applied retention')
                print('PASS: missing private SELECT fails closed without retention', flush=True)
                with connect() as admin:
                    admin.execute('CREATE TABLE public.backup_rls_probe (reading int); '
                                  'ALTER TABLE public.backup_rls_probe ENABLE ROW LEVEL SECURITY; '
                                  'CREATE POLICY readable ON public.backup_rls_probe FOR SELECT USING (true); '
                                  'GRANT SELECT ON public.backup_rls_probe TO box1902_backup')
                try:
                    invoke('rls', contract.BACKUP)
                except ValueError as error:
                    if 'Incomplete backup authority' not in str(error):
                        raise
                else:
                    raise AssertionError('RLS backup unexpectedly succeeded')
                with connect() as admin:
                    admin.execute('DROP TABLE public.backup_rls_probe')
                print('PASS: RLS relation rejected even with an allowing policy', flush=True)

            # A database archive references cluster-global roles; it does not
            # create them or preserve their passwords/memberships. This isolated
            # same-cluster restore deliberately retains the source role topology.
            # --create also restores database ownership/ACL, unlike restoring into
            # a differently named precreated database with --dbname alone.
            with connect(database='postgres') as admin:
                admin.execute('ALTER DATABASE backup_source RENAME TO backup_source_original')
                admin.execute('DROP DATABASE backup_restored')
            tool('pg_restore', '--exit-on-error', '--create', '--no-password',
                 '--host', '127.0.0.1', '--port', port, '--username', 'backup_qa_admin',
                 '--dbname', 'postgres', archive)
            with connect(database='postgres') as admin:
                admin.execute('ALTER DATABASE backup_source RENAME TO backup_restored')
                admin.execute('ALTER DATABASE backup_source_original RENAME TO backup_source')

            def authority(database_name):
                # Normalize ACL array order, keeping effective default ACLs for
                # NULL catalog entries. Compare role names, never catalog OIDs.
                with connect(database=database_name) as admin:
                    queries = {
                        'database': """SELECT pg_get_userbyid(datdba),
                            ARRAY(SELECT x::text FROM unnest(coalesce(datacl,acldefault('d',datdba))) x ORDER BY x::text)
                            FROM pg_database WHERE datname=current_database()""",
                        'schemas': """SELECT nspname,pg_get_userbyid(nspowner),
                            ARRAY(SELECT x::text FROM unnest(coalesce(nspacl,acldefault('n',nspowner))) x ORDER BY x::text)
                            FROM pg_namespace WHERE nspname IN ('public','box1902_private') ORDER BY 1""",
                        'relations': """SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner),
                            c.relrowsecurity,c.relforcerowsecurity,
                            ARRAY(SELECT x::text FROM unnest(coalesce(c.relacl,
                                acldefault(CASE WHEN c.relkind='S' THEN 's'::\"char\" ELSE 'r'::\"char\" END,c.relowner))) x ORDER BY x::text)
                            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                            WHERE n.nspname IN ('public','box1902_private') AND c.relkind IN ('r','p','S','v','m','f')
                            ORDER BY 1,2""",
                        'columns': """SELECT n.nspname,c.relname,a.attname,
                            ARRAY(SELECT x::text FROM unnest(a.attacl) x ORDER BY x::text)
                            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                            JOIN pg_namespace n ON n.oid=c.relnamespace
                            WHERE n.nspname IN ('public','box1902_private') AND a.attacl IS NOT NULL
                            AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,3""",
                        'functions': """SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),
                            pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,
                            ARRAY(SELECT x::text FROM unnest(coalesce(p.proacl,acldefault('f',p.proowner))) x ORDER BY x::text)
                            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                            WHERE n.nspname IN ('public','box1902_private') ORDER BY 1,2,3""",
                        'defaults': """SELECT pg_get_userbyid(d.defaclrole),coalesce(n.nspname,''),d.defaclobjtype,
                            ARRAY(SELECT x::text FROM unnest(d.defaclacl) x ORDER BY x::text)
                            FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
                            ORDER BY 1,2,3""",
                    }
                    return {name: admin.execute(query).fetchall() for name, query in queries.items()}

            source_authority = authority('backup_source')
            restored_authority = authority('backup_restored')
            for category in source_authority:
                if source_authority[category] != restored_authority[category]:
                    raise AssertionError(f'Restored {category} ownership/ACL/security metadata differs')
            if not source_authority['defaults'] or not source_authority['columns']:
                raise AssertionError('Authority fixture did not exercise default and column ACLs')
            if not any(row[4] for row in source_authority['functions']):
                raise AssertionError('Authority fixture did not exercise SECURITY DEFINER functions')
            print('PASS: restored database/schema/table/sequence/function owners and ACLs, column/default ACLs, SECURITY DEFINER/config identical', flush=True)

            restored_connection = connection.copy(alias='restore_authority')
            restored_connection.settings_dict.update(
                NAME='backup_restored', USER=contract.MIGRATOR, PASSWORD=password,
                OPTIONS={**database['OPTIONS'], 'options': '-c role=box1902_owner'})
            try:
                runner['assert_identity'](restored_connection)
                contract.attest(restored_connection, allow_retired=False)
            finally:
                restored_connection.close()
            with connect(contract.RUNTIME, database='backup_restored') as runtime:
                if runtime.execute('SELECT reading FROM public.backup_science_probe ORDER BY id').fetchall() != [(0,), (None,)]:
                    raise AssertionError('Restored runtime lost ordinary read access')
                for statement in ('SET ROLE box1902_owner', 'SET session_replication_role = replica',
                                  'CREATE TABLE public.runtime_forbidden(id int)',
                                  'SELECT * FROM box1902_private.context_v2',
                                  'UPDATE box1902_private.context_v2 SET validated=false'):
                    try:
                        runtime.execute(statement)
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError('Restored runtime acquired protected authority')
            with connect(contract.BACKUP, database='backup_restored') as readonly:
                for table in contract.PRIVATE_TABLES:
                    if readonly.execute(sql.SQL('SELECT count(*) FROM box1902_private.{}').format(
                            sql.Identifier(table))).fetchone() != (1,):
                        raise AssertionError('Restored backup role lost private SELECT')
                try:
                    readonly.execute('UPDATE public.backup_science_probe SET reading=1')
                except psycopg.errors.InsufficientPrivilege:
                    pass
                else:
                    raise AssertionError('Restored backup role acquired write authority')
            print('PASS: restored contract attests; runtime remains restricted and backup login remains read-only', flush=True)

            def state(database_name):
                with connect(database=database_name) as admin:
                    tables = admin.execute("SELECT n.nspname,c.relname,c.relkind FROM pg_class c "
                                           "JOIN pg_namespace n ON n.oid=c.relnamespace "
                                           "WHERE n.nspname IN ('public','box1902_private') "
                                           "AND c.relkind IN ('r','S') ORDER BY 1,2").fetchall()
                    rows = {}
                    for schema, table, kind in tables:
                        query = 'SELECT last_value,is_called FROM {}.{}' if kind == 'S' else \
                                'SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text'
                        rows[(schema, table)] = admin.execute(
                            sql.SQL(query).format(sql.Identifier(schema), sql.Identifier(table))).fetchall()
                    functions = admin.execute("SELECT n.nspname,p.proname,pg_get_functiondef(p.oid) "
                                              "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                              "WHERE n.nspname IN ('public','box1902_private') ORDER BY 1,2,3").fetchall()
                    triggers = admin.execute("SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid) "
                                             "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                                             "JOIN pg_namespace n ON n.oid=c.relnamespace "
                                             "WHERE NOT t.tgisinternal AND n.nspname IN ('public','box1902_private') "
                                             "ORDER BY 1,2,3").fetchall()
                    return rows, functions, triggers

            source = state('backup_source')
            restored = state('backup_restored')
            if source != restored:
                raise AssertionError('Restored rows, sequences, functions or triggers differ')
            with connect(database='backup_restored') as admin:
                if admin.execute('SELECT reading FROM public.backup_science_probe ORDER BY id').fetchall() != [(0,), (None,)]:
                    raise AssertionError('Scientific zero/null changed')
                for table in contract.PRIVATE_TABLES:
                    if admin.execute(sql.SQL('SELECT count(*) FROM box1902_private.{}').format(
                            sql.Identifier(table))).fetchone() != (1,):
                        raise AssertionError('Private provenance was not restored')
            print(f'PASS: real restore; all {len(source[0])} tables/sequences, '
                  f'{len(source[1])} functions, {len(source[2])} triggers identical; private rows and zero/null preserved', flush=True)

            # A second brand-new cluster has no source roles. An authority-aware
            # restore must stop, never silently re-own objects to its administrator.
            missing_data = root / 'roles-absent-data'
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                missing_port = listener.getsockname()[1]
            if missing_port in (port, 5432, 5433, 55432, 55439):
                raise RuntimeError('Refuse reserved/shared ports for missing-role proof')
            missing_started = False
            try:
                tool('initdb', '-D', missing_data, '-U', 'backup_qa_admin', '--no-locale',
                     '--encoding=UTF8', '--auth-local=trust', '--auth-host=trust')
                (missing_data / 'postgresql.conf').write_text(
                    "listen_addresses = '127.0.0.1'\n"
                    f'port = {missing_port}\n'
                    "unix_socket_directories = ''\nshared_buffers = '16MB'\nmax_connections = 10\n", encoding='utf-8')
                (missing_data / 'pg_hba.conf').write_text('host all all 127.0.0.1/32 trust\n', encoding='utf-8')
                missing_started = True
                tool('pg_ctl', '-D', missing_data, '-l', root / 'roles-absent-server.log', '-w', '-t', '30', 'start')
                with psycopg.connect(host='127.0.0.1', port=missing_port, dbname='postgres',
                                     user='backup_qa_admin', password='', passfile=os.devnull,
                                     sslmode='disable', connect_timeout=5, autocommit=True) as admin:
                    version, directory = admin.execute(
                        "SELECT current_setting('server_version_num')::int,current_setting('data_directory')").fetchone()
                    if not 180000 <= version < 190000 or Path(directory).resolve() != missing_data.resolve():
                        raise RuntimeError('Fresh missing-role cluster identity mismatch')
                    if admin.execute('SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)', [list(contract.ROLES)]).fetchall():
                        raise AssertionError('Source roles unexpectedly present in missing-role cluster')
                offset = (root / 'tools.log').stat().st_size
                try:
                    tool('pg_restore', '--exit-on-error', '--create', '--no-password',
                         '--host', '127.0.0.1', '--port', missing_port, '--username', 'backup_qa_admin',
                         '--dbname', 'postgres', archive)
                except subprocess.CalledProcessError:
                    with (root / 'tools.log').open('rb') as log:
                        log.seek(offset)
                        diagnostic = log.read().decode('utf-8', errors='replace')
                    if 'role "box1902_owner" does not exist' not in diagnostic:
                        raise AssertionError('Restore failed for a reason other than the missing required owner')
                else:
                    raise AssertionError('Authority-aware restore succeeded without source roles')
                print('PASS: real restore into a second fresh PG18 cluster fails on missing required owner role', flush=True)
            finally:
                if missing_started:
                    tool('pg_ctl', '-D', missing_data, '-m', 'immediate', '-w', '-t', '30', 'stop')
                    print('CLEANUP: missing-role cluster stopped', flush=True)
        finally:
            if 'django.db' in sys.modules:
                from django.db import connections
                connections.close_all()
            if started:
                tool('pg_ctl', '-D', data, '-m', 'immediate', '-w', '-t', '30', 'stop')
                print('CLEANUP: fresh private cluster stopped; temporary directory removed on exit', flush=True)


if __name__ == '__main__':
    main()
