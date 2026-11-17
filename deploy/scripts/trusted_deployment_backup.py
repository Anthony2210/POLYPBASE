#!/usr/bin/python3 -I
"""Root-installed deployment backup broker; never install from an unreviewed tree.

Installation contract (operator/root, not the deployment account):
* Install this file as /usr/local/libexec/polypbase-deployment-backup, root:root
  0755. Install Debian python3-django and python3-psycopg (system Python only).
* Create polypbase_backup as a locked, no-login system account with its own
  primary group, no supplementary groups, no sudo rights, and no shared UID.
* Install reviewed target commit copies of database_backup.py and
  backup_database.py in /usr/local/lib/polypbase-deployment-backup, root:root
  0755 directory, 0644 files. No checkout, venv, symlinks or writable ancestors.
* Install /etc/polypbase/deployment-backup.json root:polypbase_backup 0640:
  {"approved_commit": "<40 lowercase hex>",
   "sha256": {"database_backup.py": "<64 lowercase hex>",
              "backup_database.py": "<64 lowercase hex>"},
   "database": {"ENGINE": "django.db.backends.postgresql", "NAME": "polypbase",
                "HOST": "/var/run/postgresql", "PORT": "5433",
                "USER": "box1902_backup", "PASSWORD": "",
                "OPTIONS": {"sslmode": "disable", "passfile": "/dev/null"}}}
  Pin hashes computed independently from reviewed installation bytes. Parent
  directories must be root-owned, not group/other writable. Never copy app .env.
* mkdir /var/lib/polypbase/deployment-backups; chown polypbase_backup:polypbase_backup
  that directory; chmod 0700. Its ancestors must be root-owned and not writable.
* Provision a dedicated direct PostgreSQL backup login with complete read
  authority, including protected schemas/RLS. Prefer local peer authentication
  mapping only this OS identity; otherwise keep its password only in the JSON.
  Do not grant runtime membership in this role or put secrets in sudo arguments.
* With visudo install exactly (no interpreter permission, no SETENV):
  polypbase ALL=(polypbase_backup) NOPASSWD: NOSETENV: /usr/local/libexec/polypbase-deployment-backup
  The broker itself requires exactly one approved commit argument. Review sudo
  policy for other routes to this identity. No writable Python import paths.

Operator command handoff for parent deployment documentation (NOT executed here):
Run as root from an independently reviewed, root-controlled source snapshot. Set
APPROVED_COMMIT to its full reviewed SHA first; never derive approval from the
web-owned checkout. These commands intentionally do not provision database grants.

    apt-get install python3-django python3-psycopg
    useradd --system --user-group --home-dir /nonexistent --no-create-home \\
        --shell /usr/sbin/nologin polypbase_backup
    install -d -o root -g root -m 0755 /usr/local/libexec
    install -d -o root -g root -m 0755 /usr/local/lib/polypbase-deployment-backup
    install -o root -g root -m 0755 deploy/scripts/trusted_deployment_backup.py \\
        /usr/local/libexec/polypbase-deployment-backup
    install -o root -g root -m 0644 backend/config/database_backup.py \\
        /usr/local/lib/polypbase-deployment-backup/database_backup.py
    install -o root -g root -m 0644 deploy/scripts/backup_database.py \\
        /usr/local/lib/polypbase-deployment-backup/backup_database.py
    install -d -o root -g root -m 0755 /etc/polypbase
    install -d -o root -g root -m 0755 /var/lib/polypbase
    install -d -o polypbase_backup -g polypbase_backup -m 0700 \\
        /var/lib/polypbase/deployment-backups
    sha256sum /usr/local/lib/polypbase-deployment-backup/database_backup.py \\
        /usr/local/lib/polypbase-deployment-backup/backup_database.py
    install -o root -g polypbase_backup -m 0640 /dev/null \\
        /etc/polypbase/deployment-backup.json
    editor /etc/polypbase/deployment-backup.json
    visudo -f /etc/sudoers.d/polypbase-deployment-backup
    chmod 0440 /etc/sudoers.d/polypbase-deployment-backup
    visudo -cf /etc/sudoers.d/polypbase-deployment-backup

Fill the JSON schema above with the reviewed commit and independently checked
hashes; enter the exact sudo rule above in visudo. Inspect ownership/ACLs of every
ancestor first (notably /srv/polypbase); the broker refuses web-owned parents.
Do not blindly rerun install /dev/null against an existing approved config.
For peer auth the DBA must put this rule before broader matching pg_hba rules:
    local polypbase box1902_backup peer map=polypbase_deployment_backup
Add exactly this OS-to-database identity mapping in pg_ident.conf:
    polypbase_deployment_backup polypbase_backup box1902_backup
The OS account is polypbase_backup; the provisioned database login is
box1902_backup. Separately verify its complete-read authority. Never map the
web OS account polypbase to box1902_backup or grant it membership in that role.
Review host rules too: no trust route from the web identity to the backup login.
DB grants/HBA reload require the separately approved operator procedure, not this
installer. No general-purpose sudo python, shell, editor or env permission.

Root must explicitly repin approved_commit and both hashes for each reviewed
release. This is an installation approval, not automatic trust in origin/main.
No application code/settings, app venv, or caller environment is used. Archives
remain private; BACKUP_OK is an attestation, not permission for web to read them.
"""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path

import re
import stat
import sys
import types

CONFIG = Path('/etc/polypbase/deployment-backup.json')
BUNDLE = Path('/usr/local/lib/polypbase-deployment-backup')
OUTPUT = Path('/var/lib/polypbase/deployment-backups')
LAUNCHER = Path('/usr/local/libexec/polypbase-deployment-backup')
FILES = ('database_backup.py', 'backup_database.py')
SAFE_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
            'PYTHON_DOTENV_DISABLED': '1'}


def backup_identity():
    import grp
    import pwd
    identity = pwd.getpwnam('polypbase_backup')
    # The service must not share the credential reader's UID or private group.
    runtime = pwd.getpwnam('polypbase')
    group = grp.getgrgid(identity.pw_gid)
    if (identity.pw_uid in (0, runtime.pw_uid)
            or identity.pw_gid in (0, runtime.pw_gid)
            or set(group.gr_mem) - {'polypbase_backup'}
            or any(user.pw_name != 'polypbase_backup'
                   and (user.pw_uid == identity.pw_uid or user.pw_gid == identity.pw_gid)
                   for user in pwd.getpwall())):
        raise ValueError('Backup identity is not isolated.')
    return identity


def require_path(path, *, directory=False, owner=0, private=False):
    """Reject links and writable ancestors before reading or executing anything."""
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe parent directory.')
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ValueError('Unsafe trusted path.')
    if private and (info.st_gid != backup_identity().pw_gid
                    or info.st_mode & 0o007):
        raise ValueError('Private configuration permissions required.')
    return info


def load_control(target):
    if not isinstance(target, str) or re.fullmatch('[0-9a-f]{40}', target) is None:
        raise ValueError('Expected one lowercase full commit.')
    require_path(LAUNCHER)
    require_path(CONFIG, private=True)
    control = json.loads(CONFIG.read_bytes())
    if set(control) != {'approved_commit', 'sha256', 'database'}:
        raise ValueError('Invalid control configuration.')
    if control['approved_commit'] != target:
        raise ValueError('Commit is not approved.')
    require_path(BUNDLE, directory=True)
    if set(control['sha256']) != set(FILES):
        raise ValueError('Invalid control manifest.')
    sources = {}
    for name in FILES:
        require_path(BUNDLE / name)
        source = (BUNDLE / name).read_bytes()
        digest = control['sha256'][name]
        if (not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None
                or hashlib.sha256(source).hexdigest() != digest):
            raise ValueError('Control bundle hash mismatch.')
        sources[name] = source
    database = control['database']
    if (not isinstance(database, dict)
            or set(database) != {'ENGINE', 'NAME', 'HOST', 'PORT', 'USER', 'PASSWORD', 'OPTIONS'}
            or database['ENGINE'] != 'django.db.backends.postgresql'
            or any(not isinstance(database[key], str) or not database[key].strip()
                   for key in ('NAME', 'HOST', 'PORT', 'USER'))
            or not isinstance(database['PASSWORD'], str)
            or database['USER'] == 'polypbase'
            or not isinstance(database['OPTIONS'], dict)
            or set(database['OPTIONS']) != {'sslmode', 'passfile'}
            or database['OPTIONS']['passfile'] != '/dev/null'
            or database['OPTIONS']['sslmode'] not in ('disable', 'require', 'verify-ca', 'verify-full')):
        raise ValueError('Invalid dedicated database configuration.')
    return database, sources


def run_backup(target, database, sources):
    # System packages only, under isolated system Python. No config.settings import.
    from django.conf import settings
    settings.configure(DATABASES={'default': database}, INSTALLED_APPS=[], SECRET_KEY='backup-only')
    package = types.ModuleType('config')
    package.__path__ = []
    helper = types.ModuleType('config.database_backup')
    sys.modules['config'] = package
    sys.modules[helper.__name__] = helper
    exec(compile(sources['database_backup.py'], str(BUNDLE / FILES[0]), 'exec'), helper.__dict__)
    sys.argv = [str(BUNDLE / FILES[1]), '--output-dir', str(OUTPUT),
                '--label', 'pre-' + target[:8]]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(sources['backup_database.py'], sys.argv[0], 'exec'), {'__name__': '__main__'})
    match = re.fullmatch(
        r'BACKUP_OK (pre-' + target[:8] + r'-[0-9]{8}T[0-9]{6}Z\.dump) [1-9][0-9]* bytes sha256=[0-9a-f]{64}\n',
        output.getvalue(),
    )
    if match is None:
        raise ValueError('Missing verified backup result.')
    archive = OUTPUT / match[1]
    info = archive.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_size == 0):
        raise ValueError('Unsafe backup result.')
    print('BACKUP_OK ' + str(archive))


def main():
    try:
        if not sys.flags.isolated or len(sys.argv) != 2:
            raise ValueError('Isolated launcher with one commit required.')
        identity = backup_identity()
        if (identity.pw_uid == 0 or os.getuid() != identity.pw_uid
                or os.geteuid() != identity.pw_uid or os.getgid() != identity.pw_gid
                or os.getegid() != identity.pw_gid
                or set(os.getgroups()) - {identity.pw_gid}):
            raise ValueError('Dedicated backup OS identity required.')
        os.environ.clear()
        os.environ.update(SAFE_ENV)
        os.umask(0o077)
        os.chdir('/')
        database, sources = load_control(sys.argv[1])
        info = require_path(OUTPUT, directory=True, owner=identity.pw_uid)
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Private backup directory required.')
        run_backup(sys.argv[1], database, sources)
    except Exception:
        # Never expose credentials, connection errors, or application data.
        print('BACKUP_ERROR trusted deployment backup refused or failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
