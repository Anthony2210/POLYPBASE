"""Pure privilege contract and SQL text for the local-UI temperature import.

No Django import and no connection. The text is printed for the database owner to
review and run; nothing here executes SQL.

Why these privileges (demonstrated on a disposable PostgreSQL 18 cluster):

* ``LOCK TABLE ... IN SHARE ROW EXCLUSIVE MODE`` needs table-level UPDATE, DELETE,
  TRUNCATE or MAINTAIN. A column-level UPDATE is not enough. MAINTAIN (PostgreSQL 17+)
  gives no way to change, delete or insert rows, so it is used instead of UPDATE.
* ``SELECT ... FOR UPDATE / FOR SHARE`` needs UPDATE on at least one column. Each table
  below gets that on a single inert column, never on an identity, role, active-state,
  authorization-date, count, week, Box or organization column.
* The guard triggers are not SECURITY DEFINER: they run as the invoker and lock Box,
  biology and the reading FOR SHARE, so those three need the same column privilege.
"""

import re
import sys

HISTORICAL_TABLES = (
    "measurements_historicaltemperaturereading",
    "measurements_historicaltemperatureevidence",
    "measurements_historicaltemperatureimportreceipt",
)
# Tables whose counts are compared before and after the import.
WATCHED_TABLES = (
    "cultures_box", "measurements_biologicalmeasurement", "organizations_organization",
    "accounts_organizationmembership", "auth_user", "audit_auditlog", "django_migrations",
    "cultures_thermalzone", "cultures_boxlocation", "cultures_boxmovement",
    "measurements_dailytemperature", "measurements_temperaturemeasurement",
    "measurements_salinitymeasurement",
) + HISTORICAL_TABLES
# The importer's single LOCK TABLE statement.
LOCKED_TABLES = ("cultures_box", "measurements_biologicalmeasurement") + HISTORICAL_TABLES
# table -> the one column carrying UPDATE, only so that row locks are allowed.
ROW_LOCK_COLUMNS = {
    "auth_user": "last_login",
    "accounts_organizationmembership": "is_hidden_from_team",
    "organizations_organization": "notes",
    "cultures_box": "notes",
    "measurements_biologicalmeasurement": "notes",
    "measurements_historicaltemperaturereading": "iso_year",  # immutable by trigger anyway
}
INSERT_TABLES = HISTORICAL_TABLES + ("audit_auditlog",)
# Privileges that must be absent everywhere (effective, including inherited and PUBLIC).
FORBIDDEN_PRIVILEGES = ("DELETE", "TRUNCATE", "TRIGGER")
_ROLE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


def check_role(role):
    if not _ROLE.fullmatch(role or "") or role == "postgres" or role.startswith("pg_"):
        raise ValueError("Use a dedicated lowercase role name, never postgres or pg_*.")
    return role


def contract(migrated=True):
    """Exact effective privileges of the restricted import role.

    Table-level SELECT, INSERT and MAINTAIN sets plus the single column-level UPDATE
    column per table. Anything else, missing or extra, is a difference.
    """
    keep = (lambda table: True) if migrated else (lambda table: table not in HISTORICAL_TABLES)
    return {
        "SELECT": {t for t in WATCHED_TABLES if keep(t)},
        "INSERT": {t for t in INSERT_TABLES if keep(t)},
        "MAINTAIN": {t for t in LOCKED_TABLES if keep(t)},
        "UPDATE_COLUMNS": {t: c for t, c in ROW_LOCK_COLUMNS.items() if keep(t)},
    }


def render_grants(role):
    role = check_role(role)
    expected = contract()
    lines = [
        "-- Review, then run as the owner on the independently verified disposable local target ONLY (PostgreSQL 18).",
        "-- Run after the integrated historical and canonical migrations. Create the role first (password set interactively):",
        f"--   CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;",
        "BEGIN;",
        f"GRANT USAGE ON SCHEMA public TO {role};",
    ]
    lines += [f"GRANT SELECT ON TABLE public.{t} TO {role};" for t in WATCHED_TABLES]
    lines += [f"GRANT INSERT ON TABLE public.{t} TO {role};" for t in INSERT_TABLES]
    lines += [f"GRANT MAINTAIN ON TABLE public.{t} TO {role};  -- LOCK TABLE only; no row access"
              for t in LOCKED_TABLES]
    lines += [f"GRANT UPDATE ({column}) ON TABLE public.{t} TO {role};  -- row locks only"
              for t, column in expected["UPDATE_COLUMNS"].items()]
    lines += [f"GRANT USAGE, SELECT ON SEQUENCE public.{t}_id_seq TO {role};" for t in INSERT_TABLES]
    lines.append(
        "DO $$ DECLARE f regprocedure; BEGIN FOR f IN SELECT p.oid::regprocedure FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
        "AND p.proname LIKE 'historical_temperature_%' LOOP "
        f"EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO {role}', f); END LOOP; END $$;"
    )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def render_revoke(role):
    role = check_role(role)
    return "\n".join([
        "-- Run as the table owner after stage D. Removes every temporary privilege.",
        "BEGIN;",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role};",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {role};",
        f"REVOKE ALL ON SCHEMA public FROM {role};",
        "COMMIT;",
        f"-- Then: ALTER ROLE {role} NOLOGIN;  (or DROP OWNED BY / DROP ROLE after review)",
    ]) + "\n"


def render_backend_grants(role):
    """Privileges the NORMAL UI backend role needs once migration 0012 is installed.

    The guard triggers run as the invoker. An ordinary biological or Box-identity
    correction therefore updates historical readings (to pending_review) and reads
    them, and the correction service reads readings. Without these grants those
    ordinary writes fail with permission denied even when no reading exists. If the
    backend connects as the role that owns the new tables, nothing is needed.
    """
    role = check_role(role)
    return "\n".join([
        "-- Run as the table owner right after stage B, BEFORE the UI backend handles writes again.",
        "-- Only for the role the normal UI backend uses; never for the one-time import role.",
        "BEGIN;",
        f"GRANT SELECT ON TABLE public.measurements_historicaltemperaturereading TO {role};",
        f"GRANT SELECT ON TABLE public.measurements_historicaltemperatureevidence TO {role};",
        f"GRANT UPDATE (association_status) ON TABLE public.measurements_historicaltemperaturereading TO {role};",
        "COMMIT;",
    ]) + "\n"


_RENDERERS = {"grants": render_grants, "revoke": render_revoke, "backend-grants": render_backend_grants}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] not in _RENDERERS:
        raise SystemExit("usage: python -m apps.measurements.historical_temperature_ui_sql grants|revoke|backend-grants ROLE")
    print(_RENDERERS[argv[0]](argv[1]), end="")


if __name__ == "__main__":
    main()
