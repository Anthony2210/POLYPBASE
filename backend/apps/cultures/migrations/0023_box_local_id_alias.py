"""Confirmed Local IDs only; no biological inference and no Box rewrites.

Rollback is intentionally refused when even one alias cannot be reconstructed
from current/previous fields on the same explicit, same-institution merge chain.
Export/review such aliases and retain this migration instead of dropping history.
PostgreSQL uses a transaction advisory lock shared with allocation. REPEATABLE
READ is refused (a snapshot predating a competing claim cannot validate it);
READ COMMITTED and SERIALIZABLE are supported. SQLite serializes writers.
Rollback requires an atomic READ COMMITTED transaction and an idle database:
Box then alias ACCESS EXCLUSIVE NOWAIT locks fence the snapshot through DROP.
Busy tables cause a safe refusal, never a partially acquired waiting lock set.
"""

import warnings

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


# UNION (not UNION ALL) terminates even for legacy cycles. Foreign edges are
# deliberately not traversed; only actual same-organization terminal rows count.
GRAPH = """
WITH RECURSIVE ancestry(root, id) AS (
    SELECT id, id FROM cultures_box
    UNION
    SELECT a.root, b.merged_into_id FROM ancestry a JOIN cultures_box b ON b.id = a.id
    WHERE b.merged_into_id IS NOT NULL
), paths(root, id, organization_id) AS (
    SELECT id, id, organization_id FROM cultures_box
    UNION
    SELECT p.root, b.merged_into_id, p.organization_id
    FROM paths p JOIN cultures_box b ON b.id = p.id
    JOIN cultures_box target ON target.id = b.merged_into_id
    WHERE target.organization_id = p.organization_id
), canonical(root, terminal) AS (
    SELECT p.root, p.id FROM paths p JOIN cultures_box b ON b.id = p.id
    WHERE b.merged_into_id IS NULL
), claims(code, box_id) AS (
    SELECT global_code, id FROM cultures_box
    UNION SELECT previous_global_code, id FROM cultures_box WHERE previous_global_code <> ''
    UNION SELECT code, box_id FROM cultures_boxlocalidalias
)
"""


def _state(apps, alias):
    Box = apps.get_model("cultures", "Box")
    rows = {b.pk: b for b in Box.objects.using(alias).all()}

    def canonical(pk):
        seen = set()
        organization_id = rows[pk].organization_id
        while pk in rows and pk not in seen:
            seen.add(pk)
            box = rows[pk]
            if box.organization_id != organization_id:
                return None
            if box.merged_into_id is None:
                return (organization_id, pk)
            pk = box.merged_into_id
        return None

    return rows, canonical


def backfill_verified_previous_codes(apps, schema_editor):
    alias = schema_editor.connection.alias
    Alias = apps.get_model("cultures", "BoxLocalIdAlias")
    rows, canonical = _state(apps, alias)
    claims = {}
    for box in rows.values():
        for code in (box.global_code, box.previous_global_code):
            if code:
                claims.setdefault(code, set()).add(box.pk)
    skipped = 0
    for code, owners in sorted(claims.items()):
        sources = sorted(pk for pk in owners if rows[pk].previous_global_code == code)
        if not sources:
            continue
        identities = {canonical(pk) for pk in owners}
        if (len(identities) != 1 or None in identities or not code.strip()
                or code != code.strip() or len(code) > 100):
            skipped += 1
            continue
        # Prefer the actual terminal source if it has this previous field;
        # otherwise keep a deterministic original donor, never invent a link.
        terminal = next(iter(identities))[1]
        source = terminal if terminal in sources else sources[0]
        Alias.objects.using(alias).create(code=code, box_id=source)
    if skipped:
        warnings.warn(f"Local ID alias backfill skipped {skipped} conflicting or unresolved historical codes; "
                      "existing Box fields were preserved for review.", RuntimeWarning)


def refuse_lossy_rollback(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        if not connection.in_atomic_block:
            raise IrreversibleError("Local ID rollback requires an atomic migration.")
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] != "read committed":
                raise IrreversibleError("Local ID rollback requires READ COMMITTED isolation.")
            # Writers/readers may already own implicit relation locks before a
            # trigger runs. Do not wait while holding only one of these tables:
            # fail closed if either table is busy. On success these locks outlive
            # this preflight and remain until the migration commits its DROP.
            cursor.execute("LOCK TABLE cultures_box, cultures_boxlocalidalias "
                           "IN ACCESS EXCLUSIVE MODE NOWAIT")
    alias = connection.alias
    rows, canonical = _state(apps, alias)
    reconstructible = {}
    for box in rows.values():
        identity = canonical(box.pk)
        if identity is not None:
            for code in (box.global_code, box.previous_global_code):
                if code:
                    reconstructible.setdefault(code, set()).add(identity)
    for item in apps.get_model("cultures", "BoxLocalIdAlias").objects.using(alias).all():
        identity = canonical(item.box_id)
        if identity is None or reconstructible.get(item.code) != {identity}:
            raise IrreversibleError("Rollback would discard confirmed Local ID aliases. Retain migration 0023 "
                                    "and review/export the registry before planning a lossless downgrade.")


def _alias_invalid():
    return GRAPH + """
    SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM canonical WHERE root = NEW.box_id)
       OR trim(NEW.code) = '' OR trim(NEW.code) <> NEW.code OR length(NEW.code) > 100
       OR EXISTS (
        SELECT 1 FROM claims c
        LEFT JOIN canonical a ON a.root = NEW.box_id
        LEFT JOIN canonical b ON b.root = c.box_id
        JOIN cultures_box source ON source.id = NEW.box_id
        JOIN cultures_box other ON other.id = c.box_id
        WHERE c.code = NEW.code AND (a.terminal IS NULL OR b.terminal IS NULL
            OR a.terminal <> b.terminal OR source.organization_id <> other.organization_id)
       )
    """


def _box_invalid(*, insert):
    changed = "1 = 1" if insert else "NEW.global_code IS DISTINCT FROM OLD.global_code"
    previous_changed = "1 = 1" if insert else "NEW.previous_global_code IS DISTINCT FROM OLD.previous_global_code"
    graph_changed = "1 = 1" if insert else (
        "(NEW.merged_into_id IS DISTINCT FROM OLD.merged_into_id "
        "OR NEW.organization_id IS DISTINCT FROM OLD.organization_id)")
    return GRAPH + f"""
    SELECT 1 WHERE EXISTS (
            SELECT 1 FROM claims a JOIN claims b ON a.code = b.code AND a.box_id <> b.box_id
            LEFT JOIN canonical ca ON ca.root = a.box_id
            LEFT JOIN canonical cb ON cb.root = b.box_id
            JOIN cultures_box ba ON ba.id = a.box_id JOIN cultures_box bb ON bb.id = b.box_id
            WHERE (ca.terminal IS NULL OR cb.terminal IS NULL OR ca.terminal <> cb.terminal
                   OR ba.organization_id <> bb.organization_id)
            AND ((a.box_id = NEW.id AND (
                ({changed} AND a.code = NEW.global_code)
                OR ({previous_changed} AND a.code = NEW.previous_global_code)))
                OR ({graph_changed} AND EXISTS (
                    SELECT 1 FROM ancestry WHERE root = a.box_id AND id = NEW.id)))
        )
        OR ({graph_changed} AND EXISTS (
            SELECT 1 FROM cultures_boxlocalidalias a JOIN ancestry p ON p.root = a.box_id
            LEFT JOIN canonical c ON c.root = a.box_id
            WHERE p.id = NEW.id AND c.terminal IS NULL
        ))
    """


def _box_needs_check(*, insert):
    # Ordinary inserts/saves should not traverse every merge chain. The current
    # code has its own unique index; only historical claims require graph work.
    current = "1 = 1" if insert else "NEW.global_code IS DISTINCT FROM OLD.global_code"
    previous = "1 = 1" if insert else "NEW.previous_global_code IS DISTINCT FROM OLD.previous_global_code"
    graph = "0 = 1" if insert else (
        "(NEW.merged_into_id IS DISTINCT FROM OLD.merged_into_id "
        "OR NEW.organization_id IS DISTINCT FROM OLD.organization_id)")
    return f"""(({current}) AND (
        EXISTS (SELECT 1 FROM cultures_box WHERE previous_global_code = NEW.global_code AND id <> NEW.id)
        OR EXISTS (SELECT 1 FROM cultures_boxlocalidalias WHERE code = NEW.global_code)
    )) OR (({previous}) AND NEW.previous_global_code <> '') OR ({graph})"""


# Walk incoming edges without institution filtering. In a BEFORE guard this
# includes the old valid path; it also terminates on inspectable legacy cycles.
INCOMING_ALIAS = """
    WITH RECURSIVE incoming(id) AS (
        SELECT OLD.id
        UNION
        SELECT b.id FROM cultures_box b JOIN incoming i ON b.merged_into_id = i.id
    )
    SELECT 1 FROM cultures_boxlocalidalias a JOIN incoming i ON i.id = a.box_id
"""


def install_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute("""
            CREATE FUNCTION lock_box_local_ids() RETURNS trigger AS $$
            BEGIN
                PERFORM pg_advisory_xact_lock(192837, 19);
                IF current_setting('transaction_isolation') = 'repeatable read' THEN
                    RAISE EXCEPTION 'Local ID writes require READ COMMITTED or SERIALIZABLE isolation'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER lock_box_local_ids BEFORE INSERT OR UPDATE OF global_code,
                previous_global_code, merged_into_id, organization_id ON cultures_box
                FOR EACH STATEMENT EXECUTE FUNCTION lock_box_local_ids();
            CREATE TRIGGER lock_box_local_id_aliases BEFORE INSERT ON cultures_boxlocalidalias
                FOR EACH STATEMENT EXECUTE FUNCTION lock_box_local_ids();
            CREATE FUNCTION immutable_box_local_id_aliases() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Box Local ID aliases are immutable' USING ERRCODE = '23514';
            END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER immutable_box_local_id_aliases BEFORE UPDATE OR DELETE ON cultures_boxlocalidalias
                FOR EACH ROW EXECUTE FUNCTION immutable_box_local_id_aliases();
            CREATE TRIGGER no_truncate_box_local_id_aliases BEFORE TRUNCATE ON cultures_boxlocalidalias
                FOR EACH STATEMENT EXECUTE FUNCTION immutable_box_local_id_aliases();
        """)
        for name, table, event, query in (
            ("validate_box_local_id_alias", "cultures_boxlocalidalias", "INSERT", _alias_invalid()),
            ("validate_box_local_id_insert", "cultures_box", "INSERT", _box_invalid(insert=True)),
            ("validate_box_local_id_update", "cultures_box", "UPDATE OF global_code, previous_global_code, merged_into_id, organization_id", _box_invalid(insert=False)),
        ):
            needs_check = "TRUE" if table == "cultures_boxlocalidalias" else _box_needs_check(insert=event == "INSERT")
            schema_editor.execute(f"""
                CREATE FUNCTION {name}() RETURNS trigger AS $$
                BEGIN
                    IF {needs_check} THEN
                        IF EXISTS ({query}) THEN
                            RAISE EXCEPTION 'Local ID is reserved or its source identity is unresolved'
                                USING ERRCODE = '23514';
                        END IF;
                    END IF;
                    RETURN NEW;
                END; $$ LANGUAGE plpgsql;
                CREATE TRIGGER {name} AFTER {event} ON {table}
                    FOR EACH ROW EXECUTE FUNCTION {name}();
            """)
    elif vendor == "sqlite":
        for event in ("UPDATE", "DELETE"):
            schema_editor.execute(f"""CREATE TRIGGER immutable_box_local_id_alias_{event.lower()}
                BEFORE {event} ON cultures_boxlocalidalias
                BEGIN SELECT RAISE(ABORT, 'Box Local ID aliases are immutable'); END;""")
        for name, table, event, query in (
            ("validate_box_local_id_alias", "cultures_boxlocalidalias", "INSERT", _alias_invalid()),
            ("validate_box_local_id_insert", "cultures_box", "INSERT", _box_invalid(insert=True)),
            ("validate_box_local_id_update", "cultures_box", "UPDATE OF global_code, previous_global_code, merged_into_id, organization_id", _box_invalid(insert=False)),
        ):
            needs_check = "1 = 1" if table == "cultures_boxlocalidalias" else _box_needs_check(insert=event == "INSERT")
            schema_editor.execute(f"""CREATE TRIGGER {name} AFTER {event} ON {table}
                WHEN CASE WHEN {needs_check} THEN EXISTS ({query}) ELSE 0 END
                BEGIN SELECT RAISE(ABORT, 'Local ID is reserved or its source identity is unresolved'); END;""")
        schema_editor.execute(f"""CREATE TRIGGER protect_box_alias_organization BEFORE UPDATE OF organization_id
            ON cultures_box WHEN NEW.organization_id IS NOT OLD.organization_id
            AND EXISTS ({INCOMING_ALIAS})
            BEGIN SELECT RAISE(ABORT, 'An alias source organization is permanent'); END;""")
    else:
        raise RuntimeError("Box Local ID aliases require PostgreSQL or SQLite guards.")
    if vendor == "postgresql":
        schema_editor.execute(f"""CREATE FUNCTION protect_box_alias_organization() RETURNS trigger AS $$
            BEGIN
                IF NEW.organization_id IS DISTINCT FROM OLD.organization_id AND EXISTS (
                    {INCOMING_ALIAS}
                ) THEN
                    RAISE EXCEPTION 'An alias source organization is permanent' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER protect_box_alias_organization BEFORE UPDATE OF organization_id ON cultures_box
                FOR EACH ROW EXECUTE FUNCTION protect_box_alias_organization();""")


def remove_guards(apps, schema_editor):
    triggers = {
        "cultures_box": ("lock_box_local_ids", "validate_box_local_id_insert", "validate_box_local_id_update",
                         "protect_box_alias_organization"),
        "cultures_boxlocalidalias": ("lock_box_local_id_aliases", "immutable_box_local_id_aliases",
                                    "no_truncate_box_local_id_aliases", "validate_box_local_id_alias",
                                    "immutable_box_local_id_alias_update", "immutable_box_local_id_alias_delete"),
    }
    for table, names in triggers.items():
        for name in names:
            suffix = f" ON {table}" if schema_editor.connection.vendor == "postgresql" else ""
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}{suffix}")
    if schema_editor.connection.vendor == "postgresql":
        for name in ("lock_box_local_ids", "immutable_box_local_id_aliases", "validate_box_local_id_alias",
                     "validate_box_local_id_insert", "validate_box_local_id_update", "protect_box_alias_organization"):
            schema_editor.execute(f"DROP FUNCTION IF EXISTS {name}()")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("cultures", "0022_box_global_identity")]

    operations = [
        migrations.CreateModel(
            name="BoxLocalIdAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=100, unique=True, editable=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("box", models.ForeignKey(to="cultures.box", on_delete=django.db.models.deletion.PROTECT,
                                          related_name="local_id_aliases", editable=False)),
            ],
            options={"constraints": [models.CheckConstraint(condition=~models.Q(code=""), name="box_local_id_alias_not_empty")]},
        ),
        migrations.RunPython(backfill_verified_previous_codes, migrations.RunPython.noop),
        migrations.RunPython(install_guards, remove_guards),
        # Reverse this first: a failed preflight leaves all guards installed.
        migrations.RunPython(migrations.RunPython.noop, refuse_lossy_rollback),
    ]
