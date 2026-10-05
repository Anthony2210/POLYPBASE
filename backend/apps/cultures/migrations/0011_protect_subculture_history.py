"""Protect quantitative snapshots from ORM bulk and SQL corrections."""

from django.db import migrations

EVENT_FIELDS = (
    "parent_box_id", "event_date", "reason", "notes", "occurred_at",
    "parent_state_sequence", "parent_polyp_count_before", "allocated_polyp_count",
    "parent_polyp_count_after", "parent_state_snapshot", "author_name",
)


def install_guards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        comparison = " OR ".join(f"OLD.{field} IS DISTINCT FROM NEW.{field}" for field in EVENT_FIELDS)
        schema_editor.execute(f"""
            CREATE FUNCTION protect_subculture_event() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.occurred_at IS NOT NULL THEN
                        RAISE EXCEPTION 'Quantitative subculture history is protected' USING ERRCODE = '23514';
                    END IF;
                    RETURN OLD;
                END IF;
                IF (OLD.occurred_at IS NOT NULL OR NEW.occurred_at IS NOT NULL) AND ({comparison}) THEN
                    RAISE EXCEPTION 'Quantitative subculture history is immutable' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER protect_subculture_event BEFORE UPDATE OR DELETE ON cultures_subcultureevent
            FOR EACH ROW EXECUTE FUNCTION protect_subculture_event();
            CREATE FUNCTION protect_subculture_allocation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Subculture allocations are immutable and protected' USING ERRCODE = '23514';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER protect_subculture_allocation BEFORE UPDATE OR DELETE ON cultures_subcultureallocation
            FOR EACH ROW EXECUTE FUNCTION protect_subculture_allocation();
        """)
    elif vendor == "sqlite":
        comparison = " OR ".join(f"OLD.{field} IS NOT NEW.{field}" for field in EVENT_FIELDS)
        schema_editor.execute(f"""
            CREATE TRIGGER protect_subculture_event BEFORE UPDATE ON cultures_subcultureevent
            WHEN (OLD.occurred_at IS NOT NULL OR NEW.occurred_at IS NOT NULL) AND ({comparison})
            BEGIN SELECT RAISE(ABORT, 'Quantitative subculture history is immutable'); END;
        """)
        # Django's SQLite flush uses DELETE rather than PostgreSQL TRUNCATE.
        # Deletion remains protected by the ORM; production also guards SQL.
        schema_editor.execute("""
            CREATE TRIGGER protect_subculture_allocation BEFORE UPDATE ON cultures_subcultureallocation
            BEGIN SELECT RAISE(ABORT, 'Subculture allocations are immutable'); END;
        """)


def remove_guards(apps, schema_editor):
    if schema_editor.connection.vendor in {"postgresql", "sqlite"}:
        suffix = " ON cultures_subcultureevent" if schema_editor.connection.vendor == "postgresql" else ""
        schema_editor.execute(f"DROP TRIGGER IF EXISTS protect_subculture_event{suffix}")
        suffix = " ON cultures_subcultureallocation" if schema_editor.connection.vendor == "postgresql" else ""
        schema_editor.execute(f"DROP TRIGGER IF EXISTS protect_subculture_allocation{suffix}")
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP FUNCTION IF EXISTS protect_subculture_event()")
        schema_editor.execute("DROP FUNCTION IF EXISTS protect_subculture_allocation()")


class Migration(migrations.Migration):
    dependencies = [("cultures", "0010_quantitative_subculture")]
    operations = [migrations.RunPython(install_guards, remove_guards)]
