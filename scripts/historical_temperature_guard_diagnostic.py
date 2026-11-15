"""Read-only guard evidence for the authorized disposable launcher's child.

The caller supplies Django's actual default connection, after provisioning and
again after migration 0018. Connection credentials and roles remain its concern.
Importing this module does not import Django or application settings.
"""

import json
from pathlib import Path
import traceback

from historical_temperature_cluster_lease import require_launch


_STAGES = ("before_0018", "after_0018")
_LEGACY_SEARCH_PATH = "search_path=pg_catalog, public, pg_temp"


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def record_guard_diagnostic(connection, stage):
    """Record catalog metadata and the real UI guard check, without DB writes."""
    launch = require_launch()
    if not isinstance(stage, str) or stage not in _STAGES:
        raise ValueError("stage must be exactly before_0018 or after_0018")

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT p.oid, n.nspname AS schema, p.proname AS name,
                   pg_catalog.pg_get_function_identity_arguments(p.oid) AS arguments,
                   pg_catalog.pg_get_userbyid(p.proowner) AS owner,
                   p.prosecdef, p.proconfig, p.provolatile, p.proparallel,
                   p.proisstrict, p.proleakproof, p.prosrc,
                   pg_catalog.pg_get_functiondef(p.oid) AS definition,
                   p.proacl::text AS acl
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname ~ '^historical_temperature_'
            ORDER BY n.nspname, p.proname, p.oid
        """)
        functions = _rows(cursor)
        cursor.execute("""
            SELECT t.oid, t.tgname AS name, t.tgenabled, t.tgisinternal,
                   n.nspname AS table_schema, c.relname AS table_name,
                   t.tgfoid AS function_oid, fn.nspname AS function_schema,
                   p.proname AS function_name,
                   pg_catalog.pg_get_function_identity_arguments(p.oid) AS function_arguments,
                   pg_catalog.pg_get_triggerdef(t.oid) AS definition
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
            JOIN pg_catalog.pg_namespace fn ON fn.oid = p.pronamespace
            WHERE t.tgname ~ '^historical_' OR p.proname ~ '^historical_temperature_'
            ORDER BY n.nspname, c.relname, t.tgname, t.oid
        """)
        triggers = _rows(cursor)
        # Migration history only: never inspect scientific table rows.
        cursor.execute("""
            SELECT app, name, applied
            FROM public.django_migrations
            WHERE app = %s
            ORDER BY name, applied
        """, ["measurements"])
        migrations = _rows(cursor)

    for migration in migrations:
        migration["applied"] = migration["applied"].isoformat()
    for function in functions:
        reasons = []
        if function["prosecdef"]:
            reasons.append("security_definer")
        if _LEGACY_SEARCH_PATH not in (function["proconfig"] or []):
            reasons.append("missing_legacy_search_path")
        function["legacy_predicate"] = {"fails": bool(reasons), "reasons": reasons}

    if stage == "after_0018":
        from apps.measurements import box_1902_sql as contract

        for function in functions:
            name = function["schema"] + "." + function["name"]
            expected = contract.EXPECTED_DEFINITIONS.get(name)
            # The contract contains function bodies, not pg_get_functiondef output.
            function["contract_definition_matches"] = (
                function["prosrc"] == expected if expected is not None else None
            )

    try:
        from apps.measurements.historical_temperature_ui import check_guards

        check_guards()
    except Exception as error:
        check = {
            "outcome": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }
    else:
        check = {"outcome": "passed", "error_type": None, "error": None, "traceback": None}

    result = {
        "stage": stage,
        "system_identifier": launch.system_identifier,
        "functions": functions,
        "triggers": triggers,
        "measurements_migrations": migrations,
        "check_guards": check,
    }
    path = (Path(__file__).resolve().parents[1] / ".local-docs"
            / f"historical-temperature-guards-{stage}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Historical temperature guards: {stage} {check['outcome']} -> {path}", flush=True)
    return result
