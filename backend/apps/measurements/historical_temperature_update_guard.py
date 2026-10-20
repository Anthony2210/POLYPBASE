"""Read-only attestation of the infrastructure-only import UPDATE denial."""

ROLE = "polypbase_hist_import"
FUNCTION_NAME = "polypbase_hist_import_deny_update"
TRIGGER_NAME = "polypbase_hist_import_no_update"
TABLES = (
    "organizations_organization",
    "auth_user",
    "accounts_organizationmembership",
    "cultures_box",
    "measurements_biologicalmeasurement",
    "measurements_historicaltemperaturereading",
)
FUNCTION_BODY = """
BEGIN
    IF session_user = 'polypbase_hist_import' OR current_user = 'polypbase_hist_import' THEN
        RAISE EXCEPTION 'Historical temperature import cannot UPDATE'
            USING ERRCODE = '42501';
    END IF;
    RETURN NULL;
END;
"""

IDENTITY_SQL = """
SELECT session_user, current_user,
       pg_catalog.has_parameter_privilege(current_user, 'session_replication_role', 'SET'),
       EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
               JOIN pg_catalog.pg_roles r ON r.oid = m.member
               WHERE r.rolname = current_user)
"""
FUNCTION_SQL = """
SELECT p.oid, n.nspname, p.proname, l.lanname, p.prorettype = 'pg_catalog.trigger'::pg_catalog.regtype,
       p.prokind, p.pronargs, p.proargtypes::pg_catalog.text, p.proallargtypes,
       p.proargmodes, p.proargnames, p.pronargdefaults, p.provariadic,
       p.proretset, p.prosecdef, p.proleakproof, p.proisstrict, p.provolatile, p.proparallel,
       p.prosrc, p.proconfig, p.proowner,
       pg_catalog.pg_has_role(current_user, p.proowner, 'MEMBER')
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid = p.prolang
WHERE n.nspname = 'public' AND p.proname = %s
"""
TRIGGERS_SQL = """
SELECT n.nspname, c.relname, c.relkind, c.relispartition,
       EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i
               WHERE i.inhrelid = c.oid OR i.inhparent = c.oid),
       t.tgname, t.tgfoid, t.tgtype, t.tgenabled, t.tgattr::pg_catalog.text,
       t.tgqual IS NULL, t.tgnargs, t.tgargs, t.tgisinternal, t.tgconstraint,
       t.tgconstrrelid, t.tgconstrindid, t.tgdeferrable, t.tginitdeferred, t.tgparentid
FROM pg_catalog.pg_trigger t
JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE t.tgname = %s OR t.tgfoid = %s
"""


def check_update_guards(database, role, owner_oid):
    """Raise ValueError unless the fixed policy is intact on this native connection.

    The caller owns target identity, table-owner checks and lock acquisition, and
    must repeat this attestation after taking the import's table locks.
    """
    if role != ROLE:
        raise ValueError("UPDATE guards require the fixed polypbase_hist_import role.")
    if type(owner_oid) is not int or owner_oid <= 0:
        raise ValueError("UPDATE guards require an attested backup owner OID.")
    with database.cursor() as cursor:
        cursor.execute(IDENTITY_SQL)
        if cursor.fetchone() != (ROLE, ROLE, False, False):
            raise ValueError("Unsafe UPDATE guard session identity, parameter privilege or membership.")
        cursor.execute(FUNCTION_SQL, (FUNCTION_NAME,))
        rows = cursor.fetchall()
        expected = (
            "public", FUNCTION_NAME, "plpgsql", True, "f", 0, "", None, None, None,
            0, 0, False, False, False, False, "v", "u", FUNCTION_BODY,
            ["search_path=pg_catalog, pg_temp"], owner_oid, False,
        )
        if len(rows) != 1 or tuple(rows[0][1:]) != expected:
            raise ValueError("Missing or altered historical import UPDATE guard function.")
        function_oid = rows[0][0]
        if type(function_oid) is not int or function_oid <= 0:
            raise ValueError("Invalid UPDATE guard function OID.")
        cursor.execute(TRIGGERS_SQL, (TRIGGER_NAME, function_oid))
        rows = cursor.fetchall()
        expected = {
            ("public", table, "r", False, False, TRIGGER_NAME, function_oid, 18, "A", "",
             True, 0, b"", False, 0, 0, 0, False, False, 0)
            for table in TABLES
        }
        # psycopg may expose bytea as either bytes or memoryview.
        actual = [tuple(row[:12]) + (bytes(row[12]),) + tuple(row[13:]) for row in rows]
        if len(actual) != len(TABLES) or set(actual) != expected:
            raise ValueError("Missing or altered historical import UPDATE guard triggers.")
