"""PostgreSQL scientific guards. No data import; nonempty rollback fails closed.

SQLite intentionally has no SQL-equivalent protections. The applicator requires
PostgreSQL and this migration. Owners/superusers can disable triggers: deploy with
an application role that cannot change schema or session_replication_role.

Relationship writes require READ COMMITTED so trigger queries see links committed
while waiting for locks. Inserts lock Box, biology, then reading. Arbitrary SQL
writers can still invert that ordering (especially across multiple rows); callers
must roll back on deadlock/lock timeout, never continue a partially failed import.
"""

from django.db import migrations

SQL = r"""
CREATE FUNCTION historical_temperature_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'Historical temperature provenance is append-only' USING ERRCODE = '23514';
END $$;
CREATE FUNCTION historical_temperature_reading_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.association_status = 'confirmed' AND NEW.association_status = 'pending_review'
           AND (NEW.id, NEW.biological_measurement_id, NEW.iso_year, NEW.iso_week, NEW.temperature_c, NEW.created_at)
               IS NOT DISTINCT FROM
               (OLD.id, OLD.biological_measurement_id, OLD.iso_year, OLD.iso_week, OLD.temperature_c, OLD.created_at) THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'Historical reading only permits irreversible association invalidation' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER historical_reading_immutable BEFORE UPDATE OR DELETE
ON measurements_historicaltemperaturereading FOR EACH ROW EXECUTE FUNCTION historical_temperature_reading_immutable();
CREATE TRIGGER historical_evidence_immutable BEFORE UPDATE OR DELETE
ON measurements_historicaltemperatureevidence FOR EACH ROW EXECUTE FUNCTION historical_temperature_immutable();
CREATE TRIGGER historical_reading_no_truncate BEFORE TRUNCATE
ON measurements_historicaltemperaturereading FOR EACH STATEMENT EXECUTE FUNCTION historical_temperature_immutable();
CREATE TRIGGER historical_evidence_no_truncate BEFORE TRUNCATE
ON measurements_historicaltemperatureevidence FOR EACH STATEMENT EXECUTE FUNCTION historical_temperature_immutable();

CREATE FUNCTION historical_temperature_decimal(value text) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF btrim(value) !~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$' THEN
        RAISE EXCEPTION 'Invalid historical scientific decimal' USING ERRCODE = '23514';
    END IF;
    RETURN btrim(value)::numeric;
END $$;

CREATE FUNCTION historical_temperature_reading_valid() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE b measurements_biologicalmeasurement; target_box bigint;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Historical relationship writes require READ COMMITTED' USING ERRCODE = '23514';
    END IF;
    SELECT box_id INTO STRICT target_box FROM measurements_biologicalmeasurement WHERE id = NEW.biological_measurement_id;
    -- Match application ordering: Box first, then biological measurement.
    PERFORM id FROM cultures_box WHERE id = target_box FOR SHARE;
    SELECT * INTO STRICT b FROM measurements_biologicalmeasurement WHERE id = NEW.biological_measurement_id FOR SHARE;
    IF b.box_id <> target_box OR extract(isoyear FROM b.week_start) <> NEW.iso_year
       OR extract(week FROM b.week_start) <> NEW.iso_week OR extract(isodow FROM b.week_start) <> 1
       OR extract(isoyear FROM b.measured_on) <> NEW.iso_year
       OR extract(week FROM b.measured_on) <> NEW.iso_week
       OR NEW.association_status IS DISTINCT FROM 'confirmed' OR NEW.temperature_c IS NULL THEN
        RAISE EXCEPTION 'Historical reading biological week mismatch' USING ERRCODE = '23514';
    END IF;
    PERFORM historical_temperature_decimal(NEW.temperature_c);
    RETURN NEW;
END $$;
CREATE TRIGGER historical_reading_valid BEFORE INSERT ON measurements_historicaltemperaturereading
FOR EACH ROW EXECUTE FUNCTION historical_temperature_reading_valid();

CREATE FUNCTION historical_temperature_evidence_valid() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE r measurements_historicaltemperaturereading; b measurements_biologicalmeasurement;
        target_biology bigint; target_box bigint;
        code text; label_parts text[]; p text[]; e text[]; w text[]; m text[]; first_row integer; last_row integer;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Historical relationship writes require READ COMMITTED' USING ERRCODE = '23514';
    END IF;
    SELECT biological_measurement_id INTO STRICT target_biology
        FROM measurements_historicaltemperaturereading WHERE id = NEW.reading_id;
    SELECT box_id INTO STRICT target_box FROM measurements_biologicalmeasurement WHERE id = target_biology;
    -- Unlocked discovery is not validation. Re-read after locking Box, biology,
    -- then reading, so direct SQL corrections cannot leave stale evidence valid.
    SELECT global_code INTO STRICT code FROM cultures_box WHERE id = target_box FOR SHARE;
    SELECT * INTO STRICT b FROM measurements_biologicalmeasurement WHERE id = target_biology FOR SHARE;
    SELECT * INTO STRICT r FROM measurements_historicaltemperaturereading WHERE id = NEW.reading_id FOR SHARE;
    label_parts := regexp_match(NEW.source_box, '^([A-Z]{3}-[A-Z]{3}-[0-9]+)[.]([0-9]{1,3})$');
    IF r.association_status IS DISTINCT FROM 'confirmed'
       OR r.biological_measurement_id <> target_biology OR b.box_id <> target_box
       OR extract(isoyear FROM b.week_start) <> r.iso_year OR extract(week FROM b.week_start) <> r.iso_week
       OR extract(isodow FROM b.week_start) <> 1
       OR extract(isoyear FROM b.measured_on) <> r.iso_year OR extract(week FROM b.measured_on) <> r.iso_week
       OR label_parts IS NULL OR label_parts[1] || '.' || lpad((label_parts[2]::integer)::text, 3, '0') <> code
       OR NEW.source_iso_year <> r.iso_year OR NEW.source_iso_week <> r.iso_week
       OR historical_temperature_decimal(NEW.source_value) <> historical_temperature_decimal(r.temperature_c)
       OR historical_temperature_decimal(NEW.polyp_value) <> b.polyp_count
       OR historical_temperature_decimal(NEW.ephyrae_value) <> b.ephyrae_count
       OR historical_temperature_decimal(NEW.week_value) <> r.iso_week
       OR NEW.source_sha256 !~ '^[0-9a-f]{64}$' OR btrim(NEW.source_file) = '' OR btrim(NEW.sheet) = '' THEN
        RAISE EXCEPTION 'Historical evidence scientific relationship mismatch' USING ERRCODE = '23514';
    END IF;
    p := regexp_match(NEW.polyp_cell, '^([A-Z]+)([1-9][0-9]*)$');
    e := regexp_match(NEW.ephyrae_cell, '^([A-Z]+)([1-9][0-9]*)$');
    w := regexp_match(NEW.week_cell, '^([A-Z]+)([1-9][0-9]*)$');
    IF NEW.temperature_cell !~ '^C[1-9][0-9]*$' OR p IS NULL OR e IS NULL OR w IS NULL THEN
        RAISE EXCEPTION 'Invalid historical evidence coordinates' USING ERRCODE = '23514';
    END IF;
    first_row := substring(NEW.temperature_cell FROM 2)::integer;
    IF NEW.temperature_merged_range = '' THEN
        last_row := NULL;
    ELSE
        m := regexp_match(NEW.temperature_merged_range, '^(C[1-9][0-9]*):C([1-9][0-9]*)$');
        IF m IS NULL OR m[1] <> NEW.temperature_cell OR m[2]::integer <= first_row THEN
            RAISE EXCEPTION 'Invalid historical evidence merge' USING ERRCODE = '23514';
        END IF;
        last_row := m[2]::integer;
    END IF;
    IF p[1] <> e[1] OR p[1] <> w[1] OR e[2]::integer <> p[2]::integer + 1
       OR (last_row IS NULL AND p[2]::integer <> first_row)
       OR (last_row IS NOT NULL AND (p[2]::integer < first_row OR e[2]::integer > last_row)) THEN
        RAISE EXCEPTION 'Historical evidence outside temperature block' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER historical_evidence_valid BEFORE INSERT ON measurements_historicaltemperatureevidence
FOR EACH ROW EXECUTE FUNCTION historical_temperature_evidence_valid();

CREATE FUNCTION historical_temperature_nonempty() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM measurements_historicaltemperatureevidence WHERE reading_id = NEW.id) THEN
        RAISE EXCEPTION 'Historical reading requires source evidence in the same transaction' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER historical_reading_nonempty AFTER INSERT ON measurements_historicaltemperaturereading
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION historical_temperature_nonempty();

CREATE FUNCTION historical_temperature_biology_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    -- A repeatable-read snapshot could hide a newly committed historical link,
    -- even after waiting for its row lock. Refuse protected-field changes there.
    IF (NEW.box_id, NEW.week_start, NEW.polyp_count, NEW.ephyrae_count,
        extract(isoyear FROM NEW.measured_on), extract(week FROM NEW.measured_on)) IS DISTINCT FROM
       (OLD.box_id, OLD.week_start, OLD.polyp_count, OLD.ephyrae_count,
        extract(isoyear FROM OLD.measured_on), extract(week FROM OLD.measured_on)) THEN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'Historical relationship writes require READ COMMITTED' USING ERRCODE = '23514';
        END IF;
        IF NEW.box_id IS DISTINCT FROM OLD.box_id AND EXISTS (
            SELECT 1 FROM measurements_historicaltemperaturereading WHERE biological_measurement_id = OLD.id
        ) THEN
            RAISE EXCEPTION 'Historical temperature biological Box cannot be reassigned' USING ERRCODE = '23514';
        END IF;
        -- Include measured_on independently: SQL can change it without deriving
        -- week_start. Same-week date corrections do not invalidate associations.
        UPDATE measurements_historicaltemperaturereading SET association_status = 'pending_review'
            WHERE biological_measurement_id = OLD.id AND association_status = 'confirmed';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER historical_biology_guard BEFORE UPDATE ON measurements_biologicalmeasurement
FOR EACH ROW EXECUTE FUNCTION historical_temperature_biology_guard();
CREATE FUNCTION historical_temperature_box_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF (NEW.organization_id, NEW.global_code, NEW.strain_id, NEW.box_number) IS DISTINCT FROM
       (OLD.organization_id, OLD.global_code, OLD.strain_id, OLD.box_number) THEN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'Historical relationship writes require READ COMMITTED' USING ERRCODE = '23514';
        END IF;
        IF NEW.organization_id IS DISTINCT FROM OLD.organization_id AND EXISTS (
            SELECT 1 FROM measurements_biologicalmeasurement b
            JOIN measurements_historicaltemperaturereading r ON r.biological_measurement_id = b.id WHERE b.box_id = OLD.id
        ) THEN
            RAISE EXCEPTION 'Historical temperature institution cannot be reassigned' USING ERRCODE = '23514';
        END IF;
        UPDATE measurements_historicaltemperaturereading r SET association_status = 'pending_review'
            FROM measurements_biologicalmeasurement b
            WHERE r.biological_measurement_id = b.id AND b.box_id = OLD.id AND r.association_status = 'confirmed';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER historical_box_guard BEFORE UPDATE ON cultures_box
FOR EACH ROW EXECUTE FUNCTION historical_temperature_box_guard();
"""

REVERSE_SQL = """
DROP TRIGGER historical_box_guard ON cultures_box;
DROP TRIGGER historical_biology_guard ON measurements_biologicalmeasurement;
DROP TRIGGER historical_reading_nonempty ON measurements_historicaltemperaturereading;
DROP TRIGGER historical_evidence_valid ON measurements_historicaltemperatureevidence;
DROP TRIGGER historical_reading_valid ON measurements_historicaltemperaturereading;
DROP TRIGGER historical_reading_immutable ON measurements_historicaltemperaturereading;
DROP TRIGGER historical_evidence_immutable ON measurements_historicaltemperatureevidence;
DROP TRIGGER historical_reading_no_truncate ON measurements_historicaltemperaturereading;
DROP TRIGGER historical_evidence_no_truncate ON measurements_historicaltemperatureevidence;
DROP FUNCTION historical_temperature_box_guard();
DROP FUNCTION historical_temperature_biology_guard();
DROP FUNCTION historical_temperature_nonempty();
DROP FUNCTION historical_temperature_evidence_valid();
DROP FUNCTION historical_temperature_reading_valid();
DROP FUNCTION historical_temperature_decimal(text);
DROP FUNCTION historical_temperature_reading_immutable();
DROP FUNCTION historical_temperature_immutable();
"""


def require_empty(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("LOCK TABLE cultures_box, measurements_biologicalmeasurement, measurements_historicaltemperaturereading, measurements_historicaltemperatureevidence IN ACCESS EXCLUSIVE MODE")
        cursor.execute("SELECT EXISTS (SELECT 1 FROM measurements_historicaltemperaturereading) OR EXISTS (SELECT 1 FROM measurements_historicaltemperatureevidence)")
        if cursor.fetchone()[0]:
            raise RuntimeError("Historical storage is not empty; reviewed data-preserving maintenance is required.")


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        require_empty(schema_editor)
        schema_editor.execute(SQL)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        require_empty(schema_editor)
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("measurements", "0011_historical_temperature_storage")]
    operations = [migrations.RunPython(forwards, backwards)]
