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

[[ "$TARGET_COMMIT" =~ ^[0-9a-f]{40}$ ]] || \
    fail "target commit must be a full 40-character SHA"
[[ "$(id -un)" == "$EXPECTED_USER" ]] || \
    fail "run this script as $EXPECTED_USER"
[[ -d "$APP_DIR/.git" ]] || fail "application repository not found at $APP_DIR"
[[ -x "$APP_DIR/.venv/bin/python" ]] || fail "Python virtual environment is missing"
[[ -f "$APP_DIR/frontend/package-lock.json" ]] || fail "frontend lock file is missing"
[[ -x /usr/bin/pg_restore ]] || fail "pg_restore is unavailable"

for command in git uv npm flock tee awk tail grep mv mkdir date df; do
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
cd "$APP_DIR/backend"
BACKUP_OUTPUT="$(
    "$APP_DIR/.venv/bin/python" \
        "$APP_DIR/deploy/scripts/backup_database.py" \
        --output-dir "$BACKUP_DIR" \
        --label "pre-${SHORT_COMMIT}"
)"
printf '%s\n' "$BACKUP_OUTPUT"
BACKUP_NAME="$(printf '%s\n' "$BACKUP_OUTPUT" | awk '/^BACKUP_OK / {print $2}' | tail -n 1)"
[[ -n "$BACKUP_NAME" ]] || fail "backup script did not return BACKUP_OK"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"
[[ -s "$BACKUP_PATH" ]] || fail "backup file is missing or empty"
/usr/bin/pg_restore --list "$BACKUP_PATH" >/dev/null

CURRENT_STEP="fast-forward update"
git -C "$APP_DIR" merge --ff-only "$TARGET_COMMIT"
[[ "$(git -C "$APP_DIR" rev-parse HEAD)" == "$TARGET_COMMIT" ]] || \
    fail "repository did not reach the requested commit"

CURRENT_STEP="locked dependencies"
cd "$APP_DIR"
uv sync --frozen
npm --prefix frontend ci --no-audit --no-fund

CURRENT_STEP="frontend validation"
npm --prefix frontend run check:css
npm --prefix frontend run typecheck
STAGED_DIST="$RELEASE_DIR/frontend-dist"
(
    cd "$APP_DIR/frontend"
    ./node_modules/.bin/vite build --outDir "$STAGED_DIST" --emptyOutDir
)
[[ -s "$STAGED_DIST/index.html" ]] || fail "staged frontend build has no index.html"

CURRENT_STEP="Django validation"
PYTHON="$APP_DIR/.venv/bin/python"
"$PYTHON" backend/manage.py check
"$PYTHON" backend/manage.py check --deploy
MIGRATION_PLAN="$("$PYTHON" backend/manage.py migrate --plan)"
printf '%s\n' "$MIGRATION_PLAN" | tee "$RELEASE_DIR/migrate-plan.txt"
if printf '%s\n' "$MIGRATION_PLAN" | grep -Eiq \
    'remove field|delete model|rename field|rename model|raw python operation|raw SQL operation'; then
    fail "potentially destructive migrations require a manual deployment review"
fi

CURRENT_STEP="database migrations"
"$PYTHON" backend/manage.py migrate --noinput

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
