"""Structural SQL guards. JSON meaning and authorization belong to the publisher.

SQLite DELETE guards deliberately require TestCase rollback, not flush-based
TransactionTestCase cleanup. PostgreSQL TRUNCATE is not guarded, preserving
Django TransactionTestCase flush even for empty tables, like the receipt guards.
Production protection against privileged TRUNCATE requires database permissions
in future infrastructure work. Row guards are not a tamper-proof audit boundary.
Derived source keys are application-computed canonical SHA256 digests; SQL checks
only digest shape and uniqueness, not correspondence to locator and evidence.
"""
from django.conf import settings
from django.db import migrations


TABLES = {
    "stream": "cultures_boxhistorystream",
    "revision": "cultures_boxhistoryrevision",
    "identity": "cultures_boxhistoryeventidentity",
    "revision_shape": "cultures_boxhistoryrevision",
}


def install_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor not in {"sqlite", "postgresql"}:
        raise RuntimeError("History revision guards require SQLite or PostgreSQL.")
    pg = vendor == "postgresql"
    different = "IS DISTINCT FROM" if pg else "IS NOT"
    user = apps.get_model(settings.AUTH_USER_MODEL)
    user_table = schema_editor.quote_name(user._meta.db_table)
    user_pk = schema_editor.quote_name(user._meta.pk.column)
    revision = TABLES["revision"]

    def guard(key, action, predicate):
        name = f"history_{key}_{action.lower()}"
        table = TABLES[key]
        if pg:
            schema_editor.execute(f"""
                CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF {predicate} THEN
                        RAISE EXCEPTION 'History storage integrity violation: {name}' USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END; $$;
                CREATE TRIGGER {name} BEFORE {action} ON {table}
                FOR EACH ROW EXECUTE FUNCTION {name}();
            """)
        else:
            schema_editor.execute(f"""
                CREATE TRIGGER {name} BEFORE {action} ON {table}
                WHEN {predicate}
                BEGIN SELECT RAISE(ABORT, 'History storage integrity violation: {name}'); END;
            """)

    scope = """NEW.original_box_id < 1 OR NEW.box_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM cultures_box b WHERE b.id = NEW.box_id
        AND b.id = NEW.original_box_id AND b.organization_id = NEW.organization_id)"""
    guard("stream", "INSERT", f"({scope}) OR NEW.head_id IS NOT NULL")
    guard("stream", "UPDATE", f"""
        NEW.id {different} OLD.id OR NEW.organization_id {different} OLD.organization_id
        OR NEW.original_box_id {different} OLD.original_box_id
        OR (NEW.box_id {different} OLD.box_id AND NEW.box_id IS NOT NULL)
        OR ((NEW.head_id {different} OLD.head_id) AND (
            NEW.head_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM {revision} r WHERE r.id = NEW.head_id
                AND r.stream_id = OLD.id

                AND ((OLD.head_id IS NULL AND r.sequence = 1 AND r.predecessor_id IS NULL)
                     OR (OLD.head_id IS NOT NULL AND r.predecessor_id = OLD.head_id)))))
    """)
    guard("stream", "DELETE", "TRUE")
    guard("revision", "INSERT", f"""
        NEW.sequence < 1 OR NEW.schema_version < 1 OR trim(NEW.reason) = ''
        OR NEW.author_id IS NULL OR NEW.author_identifier <> NEW.author_id
        OR NOT EXISTS (SELECT 1 FROM {user_table} u WHERE u.{user_pk} = NEW.author_id AND u.is_active)
        OR (NEW.sequence = 1 AND NEW.predecessor_id IS NOT NULL)
        OR (NEW.sequence > 1 AND (NEW.predecessor_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM {revision} p WHERE p.id = NEW.predecessor_id
            AND p.stream_id = NEW.stream_id AND p.sequence = NEW.sequence - 1)))
    """)
    revision_model = apps.get_model("cultures", "BoxHistoryRevision")
    comparisons = " OR ".join(
        f"NEW.{field.column} {different} OLD.{field.column}"
        for field in revision_model._meta.local_fields if field.name != "author"
    )
    guard("revision", "UPDATE", f"({comparisons}) OR (NEW.author_id {different} OLD.author_id AND NOT (OLD.author_id IS NOT NULL AND NEW.author_id IS NULL))")
    guard("revision", "DELETE", "TRUE")
    guard("identity", "UPDATE", "TRUE")
    guard("identity", "DELETE", "TRUE")
    if pg:
        json_type = lambda column: f"jsonb_typeof(NEW.{column})"
        digest_bad = lambda column: f"NEW.{column} !~ '^[0-9a-f]{{64}}$'"
        operations_length = "jsonb_array_length(NEW.operations)"
        justifications_length = "jsonb_array_length(NEW.justifications)"
    else:
        json_type = lambda column: f"json_type(NEW.{column})"
        digest_bad = lambda column: f"(length(NEW.{column}) <> 64 OR NEW.{column} GLOB '*[^0-9a-f]*')"
        operations_length = "json_array_length(NEW.operations)"
        justifications_length = "json_array_length(NEW.justifications)"
    guard("identity", "INSERT", f"""
        (NEW.kind = 'persisted' AND trim(NEW.source_key) = '')
        OR (NEW.kind = 'derived' AND (({digest_bad('source_fingerprint')}) OR ({digest_bad('derived_source_key')})))
        OR {json_type('locator')} <> 'object'
        OR {json_type('source_snapshot')} <> 'object'
    """)
    # A separate trigger keeps JSON shape/digest checks independent of chain checks.

    guard("revision_shape", "INSERT", f"""
        {json_type('operations')} <> 'array' OR {json_type('review_values')} <> 'object'
        OR {json_type('metadata')} <> 'object' OR {json_type('justifications')} <> 'array'
        OR {digest_bad('source_fingerprint')} OR {digest_bad('request_fingerprint')}
        OR (CASE WHEN {json_type('operations')} = 'array' THEN {operations_length} > 100 ELSE FALSE END)
        OR (CASE WHEN {json_type('justifications')} = 'array' THEN {justifications_length} > 100 ELSE FALSE END)
    """)



def remove_guards(apps, schema_editor):
    pg = schema_editor.connection.vendor == "postgresql"
    for key, actions in {
        "stream": ("insert", "update", "delete"),
        "revision": ("insert", "update", "delete"),
        "identity": ("insert", "update", "delete"),
        "revision_shape": ("insert",),
    }.items():
        table = TABLES["revision" if key == "revision_shape" else key]
        for action in actions:
            name = f"history_{key}_{action}"
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}" + (f" ON {table}" if pg else ""))
            if pg:
                schema_editor.execute(f"DROP FUNCTION IF EXISTS {name}()")



class Migration(migrations.Migration):
    dependencies = [("cultures", "0019_history_revision_storage")]
    operations = [migrations.RunPython(install_guards, remove_guards)]
