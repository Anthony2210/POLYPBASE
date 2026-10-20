"""Read-only, per-connection timeout preflight for historical snapshot backups.

A finite budget is an admission threshold, not a bound on backup duration: local
archive hashing and plan recomputation have no wall-clock limit. Callers must
check both live control sessions after archive verification and before publishing
evidence; a disconnected or aborted session must fail closed.
"""

from config import database_backup

IDLE_MARGIN_SECONDS = 300
MIN_TRANSACTION_SECONDS = 3600
_TIMEOUT_SQL = """
    SELECT name, setting::bigint, unit
    FROM pg_catalog.pg_settings
    WHERE name IN ('idle_in_transaction_session_timeout', 'transaction_timeout')
"""


def check_backup_timeouts(database):
    """Return validated effective session timeouts in milliseconds, without SET.

    Accept a psycopg connection or Django connection wrapper exposing cursor().
    Call for BOTH the snapshot exporter and backup control connection before
    export/dump. An autocommit preflight before BEGIN is suitable; within an
    importing transaction, SET TRANSACTION SNAPSHOT must precede this SELECT.
    No transaction is begun, committed, rolled back, or reconfigured here.

    Zero explicitly disables a timeout. Missing settings (including PostgreSQL
    versions without transaction_timeout), unexpected units and invalid values
    are refused. Statement and lock timeout policies are left to their callers.
    """
    idle_ms = (2 * database_backup.TIMEOUT_SECONDS + IDLE_MARGIN_SECONDS) * 1000
    minimums = {
        "idle_in_transaction_session_timeout": idle_ms,
        "transaction_timeout": max(MIN_TRANSACTION_SECONDS * 1000, idle_ms),
    }
    with database.cursor() as cursor:
        cursor.execute(_TIMEOUT_SQL)
        rows = cursor.fetchall()

    values = {}
    for name, value, unit in rows:
        if name not in minimums or name in values:
            raise ValueError("Unexpected or duplicate backup timeout setting.")
        if unit != "ms" or type(value) is not int or value < 0:
            raise ValueError(f"Invalid backup timeout setting: {name}; expected nonnegative milliseconds.")
        values[name] = value
    if values.keys() != minimums.keys():
        raise ValueError("Both idle and transaction backup timeout settings are required.")
    for name, minimum in minimums.items():
        value = values[name]
        if value != 0 and value < minimum:
            raise ValueError(
                f"Backup timeout too short: {name}={value}ms; "
                f"requires 0 (disabled) or at least {minimum}ms."
            )
    return values
