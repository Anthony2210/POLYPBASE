#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

APP_DIR="${POLYPBASE_APP_DIR:-/srv/polypbase/app}"
BACKUP_DIR="${POLYPBASE_BACKUP_DIR:-/srv/polypbase/backups}"
DEPLOY_DIR="${POLYPBASE_DEPLOY_DIR:-/srv/polypbase/deployments}"
LOCK_FILE="${POLYPBASE_DEPLOY_LOCK:-/srv/polypbase/deploy.lock}"
TARGET_COMMIT="${1:-}"
EXPECTED_USER="${POLYPBASE_SERVICE_USER:-polypbase}"
CURRENT_STEP="initialization"

fail() {
    printf 'DEPLOY_ERROR step=%s message=%s\n' "$CURRENT_STEP" "$*" >&2
    exit 1
}

on_error() {
    local line="$1"
    local status="$2"
    printf 'DEPLOY_FAILED step=%s line=%s exit=%s\n' \
        "$CURRENT_STEP" "$line" "$status" >&2
    exit "$status"
}

trap 'on_error "$LINENO" "$?"' ERR

(( $# == 1 )) || fail "expected exactly one target commit; migrations require the dedicated runner"
[[ "$TARGET_COMMIT" =~ ^[0-9a-f]{40}$ ]] || \
    fail "target commit must be a full 40-character SHA"
[[ "$(id -un)" == "$EXPECTED_USER" ]] || \
    fail "run this script as $EXPECTED_USER"
[[ -d "$APP_DIR/.git" ]] || fail "application repository not found at $APP_DIR"
[[ -x "$APP_DIR/.venv/bin/python" ]] || fail "Python virtual environment is missing"
[[ -f "$APP_DIR/frontend/package-lock.json" ]] || fail "frontend lock file is missing"

for command in git uv npm flock tee awk tail grep mv mkdir date df sha256sum; do
    command -v "$command" >/dev/null 2>&1 || fail "required command is unavailable: $command"
done

cd /
mkdir -p "$DEPLOY_DIR/releases"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "another deployment is already running"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SHORT_COMMIT="${TARGET_COMMIT:0:8}"
RELEASE_DIR="$DEPLOY_DIR/releases/${TIMESTAMP}-${SHORT_COMMIT}"
LOG_FILE="$DEPLOY_DIR/deploy-${TIMESTAMP}-${SHORT_COMMIT}.log"
mkdir -p "$RELEASE_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

printf 'DEPLOY_START commit=%s timestamp=%s\n' "$TARGET_COMMIT" "$TIMESTAMP"

CURRENT_STEP="repository preflight"
CURRENT_BRANCH="$(git -C "$APP_DIR" symbolic-ref --short HEAD)"
[[ "$CURRENT_BRANCH" == "main" ]] || fail "production repository is not on main"

WORKTREE_CHANGES="$(git -C "$APP_DIR" status --porcelain)"
[[ -z "$WORKTREE_CHANGES" ]] || fail "production repository has local changes"

CURRENT_COMMIT="$(git -C "$APP_DIR" rev-parse HEAD)"
git -C "$APP_DIR" fetch --quiet origin main
REMOTE_COMMIT="$(git -C "$APP_DIR" rev-parse origin/main)"
[[ "$REMOTE_COMMIT" == "$TARGET_COMMIT" ]] || \
    fail "origin/main does not match the requested commit"
git -C "$APP_DIR" merge-base --is-ancestor "$CURRENT_COMMIT" "$TARGET_COMMIT" || \
    fail "requested commit is not a fast-forward from production"

AVAILABLE_KB="$(df -Pk "$APP_DIR" | awk 'NR == 2 {print $4}')"
[[ "$AVAILABLE_KB" =~ ^[0-9]+$ ]] || fail "could not determine free disk space"
(( AVAILABLE_KB >= 1048576 )) || fail "less than 1 GiB is available on the application disk"

CURRENT_STEP="database backup"
# Only the independently installed broker may access the dedicated backup login.
# It verifies root-approved code and archive completeness before attesting success.
# Do not pass environment overrides or run checkout/venv code as the backup user.
BACKUP_OUTPUT="$(
    /usr/bin/sudo -n -u polypbase_backup \
        /usr/local/libexec/polypbase-deployment-backup "$TARGET_COMMIT"
)"
printf '%s\n' "$BACKUP_OUTPUT"
[[ "$BACKUP_OUTPUT" =~ ^BACKUP_OK\ /srv/polypbase/deployment-backups/pre-${SHORT_COMMIT}-[0-9]{8}T[0-9]{6}Z\.dump$ ]] || \
    fail "trusted backup broker did not return the expected BACKUP_OK path"
BACKUP_PATH="${BACKUP_OUTPUT#BACKUP_OK }"
BACKUP_NAME="${BACKUP_PATH##*/}"
# The archive is private to the backup OS identity; never broaden its permissions
# merely to repeat the broker's verification from the web OS identity.

CURRENT_STEP="fast-forward update"
git -C "$APP_DIR" merge --ff-only "$TARGET_COMMIT"
[[ "$(git -C "$APP_DIR" rev-parse HEAD)" == "$TARGET_COMMIT" ]] || \
    fail "repository did not reach the requested commit"

CURRENT_STEP="locked dependencies"
cd "$APP_DIR"
uv sync --frozen
npm --prefix frontend ci --no-audit --no-fund

# A provisioned dedicated runtime requires an independently reviewed exact policy.
# Never repair access by broadening the checkout, venv, groups or default ACLs.
CURRENT_STEP="CCA-EOB runtime read permissions"
CCA_RUNTIME_MANIFEST="/etc/polypbase/cca-eob-runtime-permissions-v1.json"
if id -u polypbase_cca_eob >/dev/null 2>&1 || [[ -e "$CCA_RUNTIME_MANIFEST" || -L "$CCA_RUNTIME_MANIFEST" ]]; then
    /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null "$APP_DIR/deploy/scripts/cca_eob_runtime_permissions.py" \
        check --root "$APP_DIR" --manifest "$CCA_RUNTIME_MANIFEST"
fi

CURRENT_STEP="frontend validation"
npm --prefix frontend run check:css
npm --prefix frontend run typecheck
STAGED_DIST="$RELEASE_DIR/frontend-dist"
(
    cd "$APP_DIR/frontend"
    ./node_modules/.bin/vite build --outDir "$STAGED_DIST" --emptyOutDir
)
[[ -s "$STAGED_DIST/index.html" ]] || fail "staged frontend build has no index.html"
# The deployment umask protects logs and backups. Nginx still needs to traverse
# directories and read the public frontend files after the atomic move.
chmod -R o+rX "$STAGED_DIST"

CURRENT_STEP="Django validation"
PYTHON="$APP_DIR/.venv/bin/python"
"$PYTHON" backend/manage.py check
"$PYTHON" backend/manage.py check --deploy
MIGRATION_PLAN="$("$PYTHON" backend/manage.py migrate --plan)"
printf '%s\n' "$MIGRATION_PLAN" | tee "$RELEASE_DIR/migrate-plan.txt"

CURRENT_STEP="database migration readiness"
# Migrations are never executed by the runtime account. Run
# run_dedicated_migrations.sh separately through the independently provisioned
# migrator, then resume this release. --check is read-only and also supports
# future migrations without granting DDL authority to the application.
"$PYTHON" backend/manage.py migrate --check --noinput || \
    fail "pending migrations require the separately authorized dedicated migrator; resume this release after it reports MIGRATION_COMPLETE"

CURRENT_STEP="static files"
"$PYTHON" backend/manage.py collectstatic --noinput

CURRENT_STEP="frontend publication"
PREVIOUS_DIST="$RELEASE_DIR/frontend-dist-previous"
if [[ -d "$APP_DIR/frontend/dist" ]]; then
    mv "$APP_DIR/frontend/dist" "$PREVIOUS_DIST"
fi
if ! mv "$STAGED_DIST" "$APP_DIR/frontend/dist"; then
    if [[ -d "$PREVIOUS_DIST" && ! -e "$APP_DIR/frontend/dist" ]]; then
        mv "$PREVIOUS_DIST" "$APP_DIR/frontend/dist"
    fi
    fail "could not publish the staged frontend build"
fi

CURRENT_STEP="release manifest"
cat >"$RELEASE_DIR/release.env" <<EOF
POLYPBASE_COMMIT=$TARGET_COMMIT
POLYPBASE_PREVIOUS_COMMIT=$CURRENT_COMMIT
POLYPBASE_BACKUP=$BACKUP_PATH
POLYPBASE_LOG=$LOG_FILE
POLYPBASE_RELEASE_DIR=$RELEASE_DIR
EOF

printf 'DEPLOY_PREPARED commit=%s previous=%s backup=%s log=%s release=%s\n' \
    "$TARGET_COMMIT" "$CURRENT_COMMIT" "$BACKUP_NAME" "$LOG_FILE" "$RELEASE_DIR"
