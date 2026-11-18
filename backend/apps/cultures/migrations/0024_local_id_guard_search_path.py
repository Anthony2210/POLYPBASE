"""Make the 0023 Local ID trigger functions independent of the caller's search_path.

Trigger functions run with the search_path of the statement that fires them.
SECURITY DEFINER callers such as public.box_1902_apply_v2 pin
``search_path = pg_catalog, pg_temp``, so the unqualified ``cultures_box`` and
``cultures_boxlocalidalias`` references in the 0023 bodies failed with
``relation "cultures_box" does not exist`` (fail-closed, nothing written).

This migration replaces only the bodies of the six existing PostgreSQL
functions, in place, with ``CREATE OR REPLACE FUNCTION``:

- every application relation is schema-qualified (``public.``);
- each function pins ``search_path = pg_catalog, pg_temp``. ``public`` is
  deliberately not on the path, so no object created in a schema writable by
  another role can shadow a relation, function or operator. ``pg_temp`` is
  listed last explicitly; otherwise PostgreSQL would search it first.

The SQL logic is derived mechanically from the frozen 0023 builders (same
conditions, advisory lock, isolation refusal, SQLSTATE 23514 and messages).
``CREATE OR REPLACE`` keeps each function OID, owner, ACL and trigger
attachment; no trigger or table is dropped or recreated, and functions stay
SECURITY INVOKER. It must run as the functions' owner, like every migration.

SQLite triggers have no search_path and are unchanged.

Reverse is a deliberate no-op: restoring the 0023 bodies would reintroduce the
defect. The hardened bodies are behaviorally equivalent, and unapplying 0023
afterwards still drops these functions by name.
"""

from importlib import import_module
import re

from django.db import migrations


FUNCTION_CONFIG = "SET search_path = pg_catalog, pg_temp"
_RELATION = re.compile(r"(?<![\w.])(cultures_boxlocalidalias|cultures_box)\b")


def _qualify(sql):
    return _RELATION.sub(r"public.\1", sql)


def _function(name, body):
    return (f"CREATE OR REPLACE FUNCTION public.{name}() RETURNS trigger "
            f"LANGUAGE plpgsql {FUNCTION_CONFIG} AS $$\n{body}\n$$;")


def hardened_definitions():
    """Exact replacements for the six 0023 PostgreSQL trigger functions."""
    original = import_module("apps.cultures.migrations.0023_box_local_id_alias")
    definitions = {
        "lock_box_local_ids": """
            BEGIN
                PERFORM pg_catalog.pg_advisory_xact_lock(192837, 19);
                IF pg_catalog.current_setting('transaction_isolation') = 'repeatable read' THEN
                    RAISE EXCEPTION 'Local ID writes require READ COMMITTED or SERIALIZABLE isolation'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END;""",
        "immutable_box_local_id_aliases": """
            BEGIN
                RAISE EXCEPTION 'Box Local ID aliases are immutable' USING ERRCODE = '23514';
            END;""",
    }
    for name, table, event, query in (
        ("validate_box_local_id_alias", "cultures_boxlocalidalias", "INSERT", original._alias_invalid()),
        ("validate_box_local_id_insert", "cultures_box", "INSERT", original._box_invalid(insert=True)),
        ("validate_box_local_id_update", "cultures_box", "UPDATE", original._box_invalid(insert=False)),
    ):
        needs_check = ("TRUE" if table == "cultures_boxlocalidalias"
                       else original._box_needs_check(insert=event == "INSERT"))
        definitions[name] = f"""
            BEGIN
                IF {_qualify(needs_check)} THEN
                    IF EXISTS ({_qualify(query)}) THEN
                        RAISE EXCEPTION 'Local ID is reserved or its source identity is unresolved'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END;"""
    definitions["protect_box_alias_organization"] = f"""
            BEGIN
                IF NEW.organization_id IS DISTINCT FROM OLD.organization_id AND EXISTS (
                    {_qualify(original.INCOMING_ALIAS)}
                ) THEN
                    RAISE EXCEPTION 'An alias source organization is permanent' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;"""
    for name, body in definitions.items():
        unqualified = _RELATION.search(body.replace("public.cultures_box", ""))
        if unqualified:
            raise RuntimeError(f"Unqualified relation remains in {name}: {unqualified.group(0)}")
    return definitions


def harden_local_id_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    definitions = hardened_definitions()
    with schema_editor.connection.cursor() as cursor:
        # Replace only existing functions: never create an unattached substitute.
        cursor.execute(
            "SELECT p.proname FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = ANY(%s) AND p.pronargs = 0 "
            "AND p.prorettype = 'pg_catalog.trigger'::pg_catalog.regtype AND NOT p.prosecdef",
            [sorted(definitions)],
        )
        if {row[0] for row in cursor.fetchall()} != set(definitions):
            raise RuntimeError("The 0023 Local ID trigger functions are not installed as expected.")
    for name in sorted(definitions):
        schema_editor.execute(_function(name, definitions[name]), params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("cultures", "0023_box_local_id_alias")]

    operations = [
        migrations.RunPython(harden_local_id_guards, migrations.RunPython.noop),
    ]
