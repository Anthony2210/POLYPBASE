#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT

# Never load privileged Python code from the web service's writable checkout.
APP_DIR="${POLYPBASE_MIGRATION_APP_DIR:-}"
LIVE_APP_DIR="${POLYPBASE_APP_DIR:-/srv/polypbase/app}"
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
BACKUP_DIR="${POLYPBASE_BACKUP_DIR:-/var/lib/polypbase/migration-backups}"
LOCK_FILE="${POLYPBASE_DEPLOY_LOCK:-/srv/polypbase/deploy.lock}"
# The operator authenticates this control bundle separately from the app commit.
CONTROL_DIR="${POLYPBASE_MIGRATION_CONTROL_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}"
MODE="${1:-}"
TARGET_COMMIT="${2:-}"
PLAN_PATH="${3:-}"
REVIEWED_MIGRATION_APPROVAL="${4:-}"
EXPECTED_MIGRATOR="box1902_migrator"
EXPECTED_OWNER="box1902_owner"

fail() {
    printf 'MIGRATION_ERROR step=%s message=%s\n' "${CURRENT_STEP:-initialization}" "$*" >&2
    exit 1
}

trusted_git() {
    /usr/bin/env -i PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        /usr/bin/git --no-optional-locks -c core.fsmonitor=false -c "safe.directory=$APP_DIR" "$@"
}

assert_checkout() {
    [[ "$(trusted_git -C "$APP_DIR" symbolic-ref --short HEAD)" == main ]] || \
        fail "production repository is not on main"
    local changes
    changes="$(trusted_git -C "$APP_DIR" status --porcelain --untracked-files=all)" || \
        fail "could not inspect repository status"
    [[ -z "$changes" ]] || fail "production repository has local changes"
    [[ "$(trusted_git -C "$APP_DIR" rev-parse HEAD)" == "$TARGET_COMMIT" ]] || \
        fail "application checkout is not the prepared target commit"
}

validate_arguments() {
    case "$MODE" in
        --plan) (( $# == 3 )) || fail "usage: --plan TARGET PLAN_PATH (no approval)" ;;
        --apply) (( $# == 4 )) || fail "usage: --apply TARGET PLAN_PATH COMMIT:SHA256" ;;
        *) fail "expected --plan or --apply" ;;
    esac
    [[ "$TARGET_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "target commit must be a full 40-character SHA"
    [[ "$PLAN_PATH" == /* ]] || fail "PLAN_PATH must be absolute and outside the application checkout"
    if [[ "$MODE" == --apply ]]; then
        [[ "$REVIEWED_MIGRATION_APPROVAL" =~ ^[0-9a-f]{40}:[0-9a-f]{64}$ ]] || \
            fail "reviewed migration approval must be COMMIT:SHA256 in lowercase hex"
        [[ "${REVIEWED_MIGRATION_APPROVAL%%:*}" == "$TARGET_COMMIT" ]] || \
            fail "reviewed migration approval does not match the target commit"
        [[ -f "$PLAN_PATH" && ! -L "$PLAN_PATH" ]] || fail "approved plan must be a regular, non-symlink file"
    else
        [[ ! -e "$PLAN_PATH" && ! -L "$PLAN_PATH" ]] || fail "plan output already exists; never overwrite a reviewed plan"
    fi
}

validate_arguments "$@"
[[ "$APP_DIR" == /* ]] || fail "POLYPBASE_MIGRATION_APP_DIR must explicitly select an absolute root-installed snapshot"
CURRENT_STEP="trusted filesystem preflight"
# No application interpreter, imports, site hooks, credentials or inherited Python
# environment are involved in this check. The runner itself is delivered/authenticated
# out of band; the entire installed control bundle must also pass this boundary.
/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -I -S -B - \
    "$APP_DIR" "$CONTROL_DIR" "$LIVE_APP_DIR" "${BASH_SOURCE[0]}" <<'PREFLIGHT'
import grp
import os
from pathlib import Path
import pwd
import stat
import sys


class TrustError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise TrustError(message)


def check_identity():
    dedicated = pwd.getpwnam("polypbase_migrator")
    dedicated_group = grp.getgrnam("polypbase_migrator")
    uid, gid = os.getuid(), os.getgid()
    require(uid != 0 and os.geteuid() == uid == dedicated.pw_uid,
            "run as the dedicated nonroot polypbase_migrator OS user")
    require(gid != 0 and os.getegid() == gid == dedicated_group.gr_gid == dedicated.pw_gid,
            "use the dedicated polypbase_migrator primary OS group")
    groups = set(os.getgroups()) | {gid}
    require(0 not in groups, "dedicated migrator must not belong to the root group")
    # The normal web account must exist; reject both UID and group sharing.
    web = pwd.getpwnam("polypbase")
    runtimes = [web]
    try:
        runtimes.append(pwd.getpwnam("polypbase_cca_eob"))
    except KeyError:
        pass
    for runtime in runtimes:
        require(uid != runtime.pw_uid and runtime.pw_gid not in groups,
                "migrator must not share a web runtime UID or group")
        require(not groups.intersection(os.getgrouplist(runtime.pw_name, runtime.pw_gid)),
                "migrator and web runtime must not share supplementary groups")


def check_node(path):
    info = path.lstat()
    require(info.st_uid == 0, "snapshot/control path is not root-owned")
    # POSIX symlink mode is normally 0777; its parent and resolved target govern writes.
    if not stat.S_ISLNK(info.st_mode):
        require(not info.st_mode & 0o022, "snapshot/control path is group/other writable")
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode),
                "special files are forbidden in executable trees")
    return info


def controlled_path(path, resolving=None):
    """Check every lexical ancestor and every symlink hop before resolving it."""
    require(path.is_absolute(), "trusted paths must be absolute")
    resolving = set() if resolving is None else resolving
    current = Path(path.anchor)
    check_node(current)
    for part in path.parts[1:]:
        if part == "..":
            require(current.is_dir(), "non-directory before parent traversal")
            current = current.parent
            check_node(current)
            continue
        current = current / part
        info = check_node(current)
        if stat.S_ISLNK(info.st_mode):
            require(current not in resolving, "symlink cycle in executable path")
            target = Path(os.readlink(current))
            if not target.is_absolute():
                # Preserve '..': resolve preceding links before parent traversal.
                target = current.parent / target
            current = controlled_path(target, resolving | {current})
    return current


def check_tree(path, seen=None, forbidden=None):
    """Include ignored bytecode, .pth, .git, .venv and external linked directories."""
    seen = set() if seen is None else seen
    resolved = controlled_path(path)
    require(forbidden is None or not resolved.is_relative_to(forbidden),
            "executable tree must not link into the live web checkout")
    info = check_node(resolved)
    key = (info.st_dev, info.st_ino)
    if key in seen:
        return resolved
    seen.add(key)
    if stat.S_ISDIR(info.st_mode):
        for child in resolved.iterdir():
            check_tree(child, seen, forbidden)
    return resolved


def preflight(snapshot, control, live, runner):
    check_identity()
    live = live.resolve()
    snapshot, control = check_tree(snapshot, forbidden=live), check_tree(control, forbidden=live)
    require(controlled_path(runner.absolute()).is_relative_to(control),
            "runner must be installed in the root-controlled control bundle")
    require(not snapshot.is_relative_to(live) and not live.is_relative_to(snapshot),
            "migration snapshot must be distinct from the live web checkout")
    require((snapshot / ".git").is_dir(), "snapshot must contain its Git repository")
    require((snapshot / ".venv/bin/python").is_file(), "root-installed snapshot venv is required")
    require(os.access(snapshot / ".venv/bin/python", os.X_OK), "snapshot Python is not executable")
    require((snapshot / "backend").is_dir(), "snapshot backend is missing")
    require((control / "backend/config/database_backup.py").is_file(), "control backup helper is missing")


if __name__ == "__main__":
    try:
        preflight(*(Path(value) for value in sys.argv[1:]))
    except Exception:
        print("MIGRATION_ERROR: trusted snapshot/control or dedicated OS identity preflight failed", file=sys.stderr)
        sys.exit(1)
PREFLIGHT
CURRENT_STEP="release identity"
for command in git flock cmp sha256sum; do
    command -v "$command" >/dev/null 2>&1 || fail "required command is unavailable: $command"
done
[[ -d "$APP_DIR/.git" ]] || fail "application repository not found at $APP_DIR"
[[ -x "$APP_DIR/.venv/bin/python" ]] || fail "Python virtual environment is missing"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "another deployment is already running"
assert_checkout

CURRENT_STEP="dedicated connection"
[[ "${POSTGRES_USER:-}" == "$EXPECTED_MIGRATOR" ]] || \
    fail "POSTGRES_USER must be explicitly set to $EXPECTED_MIGRATOR"
[[ -n "${POSTGRES_HOST:-}" && "$POSTGRES_HOST" != /* ]] || \
    fail "POSTGRES_HOST must select an independently authenticated TCP/TLS endpoint"
[[ "${PGOPTIONS:-}" == "-c role=$EXPECTED_OWNER" ]] || \
    fail "PGOPTIONS must explicitly request SET ROLE $EXPECTED_OWNER"

cd "$APP_DIR/backend"
# -I excludes cwd, user site and PYTHON* injection. Root-controlled venv .pth files
# are permitted only after the full tree check above; do not create ignored pyc.
export DJANGO_SETTINGS_MODULE=config.settings
# One process and one physical Django connection for identity, planning and execution.
"$APP_DIR/.venv/bin/python" -I -B - "$MODE" "$TARGET_COMMIT" "$PLAN_PATH" \
    "$REVIEWED_MIGRATION_APPROVAL" "$APP_DIR" "$BACKUP_DIR" "$CONTROL_DIR" <<'PY'
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class GuardError(RuntimeError):
    """Only fixed, non-sensitive operational diagnostics may use this exception."""


def require(condition, message):
    if not condition:
        raise GuardError(message)


def assert_checkout(app_dir, target):
    def git(*args):
        return subprocess.check_output(
            ["/usr/bin/git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
             "-c", f"safe.directory={app_dir}", "-C", str(app_dir), *args],
            env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
            stdin=subprocess.DEVNULL, timeout=30,
        ).decode("utf-8").strip()
    require(git("symbolic-ref", "--short", "HEAD") == "main", "checkout is not on main")
    require(not git("status", "--porcelain", "--untracked-files=all"), "checkout is not clean")
    require(git("rev-parse", "HEAD") == target, "checkout commit changed")


def assert_identity(connection, physical=None):
    require(connection.vendor == "postgresql", "migration database must be PostgreSQL")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT session_user::text, current_user::text, "
            "current_setting('session_replication_role'), pg_backend_pid()"
        )
        session, current, replication, pid = cursor.fetchone()
    require((session, current, replication) ==
            ("box1902_migrator", "box1902_owner", "origin"), "database identity mismatch")
    if physical is not None:
        require(connection.connection is physical[0] and pid == physical[1],
                "migration connection changed")
    return connection.connection, pid


def capture_plan(connection, target):
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    require(not executor.loader.detect_conflicts(), "conflicting migration leaves")
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)
    require(not any(backwards for _, backwards in plan), "backwards migration refused")
    document = {
        "format": "polypbase-dedicated-migrations-v1", "commit": target,
        "migrations": [
            {"app": migration.app_label, "name": migration.name,
             "operations": [operation.describe() for operation in migration.operations]}
            for migration, _ in plan
        ],
    }
    data = (json.dumps(document, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return executor, targets, plan, data


def verify_plan(data, approved_file, approval, target):
    # cmp checks exact supplied bytes, not reconstructed text or command output.
    with tempfile.NamedTemporaryFile() as current:
        current.write(data)
        current.flush()
        result = subprocess.run(
            ["cmp", "--silent", str(approved_file), current.name],
            stdin=subprocess.DEVNULL, timeout=30,
        )
        require(result.returncode == 0, "current plan differs from approved plan bytes")
    digest = hashlib.sha256(data).hexdigest()
    require(approval == f"{target}:{digest}", "approval does not match commit and plan SHA256")
    require(hashlib.sha256(approved_file.read_bytes()).hexdigest() == digest,
            "approved plan file changed")
    return digest


def backup(connection, backup_dir, control_dir, target):
    # Use the authenticated control bundle, not backup code from the app HEAD.
    helper_path = control_dir / "backend/config/database_backup.py"
    spec = importlib.util.spec_from_file_location("dedicated_database_backup", helper_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = copy.deepcopy(connection.settings_dict)
    user = os.environ.get("POLYPBASE_BACKUP_USER", "")
    require(bool(user.strip()) and user not in
            (database["USER"], "box1902_owner"), "separate backup login is required")
    database["USER"] = user
    database["PASSWORD"] = os.environ.get("POLYPBASE_BACKUP_PASSWORD", "")
    # Never reuse the migrator's role, service, passfile, or routing overrides.
    database["OPTIONS"] = {"sslmode": database.get("OPTIONS", {}).get("sslmode", "prefer"),
                           "passfile": os.devnull}
    ambient = {key: value for key, value in os.environ.items() if key.upper().startswith("PG")}
    try:
        for key in ambient:
            del os.environ[key]
        archive = module.create_complete_archive(
            database, backup_dir, f"pre-migrations-{target[:8]}", client_major=18,
        )
        module.verify_archive(archive, client_major=18)
    finally:
        os.environ.update(ambient)
    print(f"MIGRATION_BACKUP_OK file={Path(archive['path']).name} sha256={archive['sha256']}", flush=True)


def execute(connection, target, approved_file, approval, app_dir, physical):
    from django.core.management.sql import emit_post_migrate_signal, emit_pre_migrate_signal
    # Rebuild the graph and re-read applied history after the backup.
    executor, targets, plan, data = capture_plan(connection, target)
    require(bool(plan), "no pending migrations; stale approval refused")
    verify_plan(data, approved_file, approval, target)
    state = executor._create_project_state(with_applied_migrations=True)
    emit_pre_migrate_signal(1, False, connection.alias, stdout=sys.stdout, apps=state.apps, plan=plan)
    # Signals may run application code. Recheck everything at the execution boundary.
    executor, targets, plan, data = capture_plan(connection, target)
    require(bool(plan), "no pending migrations; stale approval refused")
    digest = verify_plan(data, approved_file, approval, target)
    assert_checkout(app_dir, target)
    assert_identity(connection, physical)
    print(f"MIGRATION_REVIEW_APPROVED commit={target} plan_sha256={digest}", flush=True)
    state = executor.migrate(targets, plan=plan, state=state.clone(), fake=False, fake_initial=False)
    assert_identity(connection, physical)
    require(not capture_plan(connection, target)[2], "pending migrations remain after execution")
    # Match Django migrate's post-migration model rendering and permission hooks.
    state.clear_delayed_apps_cache()
    state.apps
    emit_post_migrate_signal(1, False, connection.alias, stdout=sys.stdout, apps=state.apps, plan=plan)
    assert_identity(connection, physical)
    assert_checkout(app_dir, target)
    print(f"MIGRATION_COMPLETE commit={target}", flush=True)


def main():
    mode, target, plan_path, approval, app_dir, backup_dir, control_dir = sys.argv[1:]
    app_dir, approved_file = Path(app_dir).resolve(), Path(plan_path).resolve()
    require(not approved_file.is_relative_to(app_dir), "plan must be outside the application checkout")
    # Isolated Python deliberately omits cwd; only the verified snapshot is added.
    sys.path.insert(0, str(app_dir / "backend"))
    import django
    django.setup()
    from django.apps import apps
    from django.core.management.commands.migrate import Command
    from django.db import connection
    from django.utils.module_loading import module_has_submodule
    from importlib import import_module
    # Retain migrate's database system checks and management signal registration.
    physical = assert_identity(connection)
    Command().check(databases=[connection.alias])
    for app in apps.get_app_configs():
        if module_has_submodule(app.module, "management"):
            import_module(".management", app.name)
    _, _, plan, data = capture_plan(connection, target)
    assert_checkout(app_dir, target)
    assert_identity(connection, physical)
    if mode == "--plan":
        with approved_file.open("xb") as output:
            output.write(data)
        print(f"MIGRATION_PLAN commit={target} sha256={hashlib.sha256(data).hexdigest()} file={approved_file}")
        if not plan:
            print("MIGRATION_NO_PENDING (no approval applicable)")
        return
    require(mode == "--apply", "unknown mode")
    require(bool(plan), "no pending migrations; stale approval refused")
    verify_plan(data, approved_file, approval, target)
    backup(connection, Path(backup_dir), Path(control_dir), target)
    execute(connection, target, approved_file, approval, app_dir, physical)


if __name__ == "__main__":
    try:
        main()
    except GuardError as error:
        print(f"MIGRATION_ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Database/driver exceptions may contain secrets or scientific values.
        print("MIGRATION_ERROR: dedicated migration failed; stop and diagnose securely", file=sys.stderr)
        sys.exit(1)
PY
