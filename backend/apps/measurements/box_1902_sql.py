"""SQL-only Box 1902 capability contract; importing this module opens no database.

The owner and migrator are trusted DDL authorities. Runtime, approver and executor
are independent, untrusted logins. Provisioning is a separately reviewed operation.

Approval authenticates exact plan bytes and the embedded SQL manifest, not the
structure of the full Django workflow. The trusted independent approver may approve
a SQL-only plan containing operation and manifest. Only the dedicated executor can
apply it; runtime has no public exception entrypoint. Plan SHA-256 is enforced by
the approval table constraint and rechecked by the writer.
"""

from importlib import import_module
import hashlib
import json
import re

OPERATION = "cla_box_1902_preservation_20261109_v2"
REASON = (
    "Anthony's explicit product-owner decision, based on laboratory confirmation "
    "reported orally to him; not an independently verified laboratory attestation. "
    "Correct Box 1902 identity only; preserve scientific history."
)
_SQL_REASON = "'" + REASON.replace("'", "''") + "'"
OWNER = "box1902_owner"
RUNTIME = "polypbase"
APPROVER = "box1902_approver"
EXECUTOR = "box1902_executor"
MIGRATOR = "box1902_migrator"
# Dedicated read-only backup login: complete pg_dump without runtime private access.
BACKUP = "box1902_backup"
ROLES = (OWNER, RUNTIME, APPROVER, EXECUTOR, MIGRATOR, BACKUP)
PRIVATE_TABLES = ("approval_v2", "context_v2", "correction_v2")
APPROVAL_COLUMNS = (
    "id", "operation", "actor_id", "plan_text", "plan_hash", "manifest", "expires_at",
)
GUARD_NAMES = (
    "historical_temperature_immutable", "historical_temperature_reading_immutable",
    "historical_temperature_decimal", "historical_temperature_reading_valid",
    "historical_temperature_evidence_valid", "historical_temperature_nonempty",
    "historical_temperature_biology_guard", "historical_temperature_box_guard",
)
CONFIG = "SET search_path = pg_catalog, pg_temp SET TimeZone = 'UTC' SET DateStyle = 'ISO, YMD'"


def canonical_plan(plan):
    """Exact UTF-8 approval bytes; the workflow must use this same serialization."""
    return json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def plan_digest(plan_text):
    return hashlib.sha256(plan_text.encode("utf-8")).hexdigest()


def _function(signature, body, *, language="plpgsql", stable=False):
    return (f"CREATE OR REPLACE FUNCTION {signature}\nLANGUAGE {language} "
            f"{'STABLE ' if stable else ''}SECURITY DEFINER {CONFIG} AS $$\n{body}\n$$;")


TABLE_SQL = f"""
CREATE SCHEMA box1902_private AUTHORIZATION box1902_owner;
REVOKE ALL ON SCHEMA box1902_private FROM PUBLIC;
CREATE TABLE box1902_private.approval_v2 (
    id integer PRIMARY KEY CHECK (id = 1),
    operation text NOT NULL CHECK (operation = '{OPERATION}'),
    actor_id bigint NOT NULL,
    plan_text text NOT NULL CHECK (jsonb_typeof(plan_text::jsonb) = 'object'),
    plan_hash text NOT NULL CONSTRAINT approval_v2_plan_hash_check CHECK (
        plan_hash = encode(sha256(convert_to(plan_text, 'UTF8')), 'hex')),
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz
);
CREATE TABLE box1902_private.context_v2 (
    approval_id integer PRIMARY KEY REFERENCES box1902_private.approval_v2(id),
    xid xid8 NOT NULL, backend_pid integer NOT NULL,
    executor name NOT NULL CHECK (executor = 'box1902_executor'),
    before_box jsonb NOT NULL, after_box jsonb NOT NULL,
    validated boolean NOT NULL DEFAULT false
);
CREATE TABLE box1902_private.correction_v2 (
    approval_id integer PRIMARY KEY REFERENCES box1902_private.approval_v2(id),
    operation text NOT NULL CHECK (operation = '{OPERATION}'),
    actor_id bigint NOT NULL, executor name NOT NULL,
    reason text NOT NULL,
    before_box jsonb NOT NULL, after_box jsonb NOT NULL,
    manifest jsonb NOT NULL, plan_text text NOT NULL, plan_hash text NOT NULL,
    consumed_at timestamptz NOT NULL
);
REVOKE ALL ON ALL TABLES IN SCHEMA box1902_private FROM PUBLIC,
    polypbase, box1902_approver, box1902_executor, box1902_migrator;
"""

MANIFEST_SQL = _function("public.box_1902_manifest_v2() RETURNS jsonb", f"""
SELECT jsonb_build_object(
    'operation', '{OPERATION}',
    'box', (SELECT to_jsonb(b) FROM public.cultures_box b WHERE b.id = 1902),
    'strains', (SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.id), '[]'::jsonb)
        FROM public.taxonomy_strain s WHERE s.id IN (213, 285)),
    'species', (SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.id), '[]'::jsonb)
        FROM public.taxonomy_species s WHERE s.id IN (131, 164)),
    'biology', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY b.id), '[]'::jsonb)
        FROM public.measurements_biologicalmeasurement b WHERE b.box_id = 1902),
    'readings', (SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY r.id), '[]'::jsonb)
        FROM public.measurements_historicaltemperaturereading r
        JOIN public.measurements_biologicalmeasurement b ON b.id = r.biological_measurement_id
        WHERE b.box_id = 1902),
    'evidence', (SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY e.id), '[]'::jsonb)
        FROM public.measurements_historicaltemperatureevidence e
        JOIN public.measurements_historicaltemperaturereading r ON r.id = e.reading_id
        JOIN public.measurements_biologicalmeasurement b ON b.id = r.biological_measurement_id
        WHERE b.box_id = 1902))
""", language="sql", stable=True)

LOCK_SQL = _function("public.box_1902_lock_v2(actor_id bigint) RETURNS void", """
DECLARE t record;
BEGIN
    IF session_user <> 'box1902_executor' OR current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Dedicated executor and READ COMMITTED required' USING ERRCODE = '42501';
    END IF;
    -- Include every public table, not just Django's model registry or scientific rows.
    FOR t IN SELECT n.nspname, c.relname FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('public', 'box1902_private') AND c.relkind IN ('r', 'p')
        ORDER BY n.nspname, c.relname
    LOOP
        EXECUTE format('LOCK TABLE %I.%I IN EXCLUSIVE MODE NOWAIT', t.nspname, t.relname);
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1
          AND m.is_active AND m.role = 'admin') THEN
        RAISE EXCEPTION 'Active institutional administrator required' USING ERRCODE = '42501';
    END IF;
END
""")

EVIDENCE_SQL = _function("public.box_1902_evidence_valid_v2(evidence_id bigint) RETURNS boolean", f"""
SELECT EXISTS (
    SELECT 1 FROM box1902_private.correction_v2 c
    JOIN box1902_private.approval_v2 a ON a.id = c.approval_id
    JOIN public.cultures_box box ON box.id = 1902
    JOIN public.measurements_biologicalmeasurement b ON b.box_id = box.id
    JOIN public.measurements_historicaltemperaturereading r ON r.biological_measurement_id = b.id
    JOIN public.measurements_historicaltemperatureevidence e ON e.reading_id = r.id
    WHERE e.id = evidence_id AND c.operation = '{OPERATION}'
      AND a.consumed_at = c.consumed_at AND c.actor_id = a.actor_id
      AND c.plan_text = a.plan_text AND c.plan_hash = a.plan_hash AND c.manifest = a.manifest
      AND jsonb_build_object(
          'id', box.id, 'organization_id', box.organization_id,
          'global_code', box.global_code, 'strain_id', box.strain_id,
          'local_code', box.local_code, 'status', box.status, 'box_number', box.box_number,
          'merged_into_id', box.merged_into_id, 'previous_global_code', box.previous_global_code
      ) = (SELECT jsonb_object_agg(field, c.after_box->field)
          FROM unnest(ARRAY['id', 'organization_id', 'global_code', 'strain_id', 'local_code',
              'status', 'box_number', 'merged_into_id', 'previous_global_code']) AS fields(field))
      AND EXISTS (SELECT 1 FROM public.taxonomy_strain s
          JOIN public.taxonomy_species p ON p.id = s.species_id
          WHERE s.id = box.strain_id AND s.id = 285 AND s.organization_id = 1
            AND s.code = 'CLC-JKA-1' AND p.id = 164 AND p.scientific_name = 'Chrysaora lactea')
      AND c.after_box = c.before_box || jsonb_build_object('global_code', 'CLC-JKA-1.009', 'strain_id', 285)
      AND c.manifest->'box' = c.before_box
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(c.manifest->'biology') x WHERE x = to_jsonb(b))
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(c.manifest->'readings') x WHERE x = to_jsonb(r))
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(c.manifest->'evidence') x WHERE x = to_jsonb(e))
)
""", language="sql", stable=True)

IMMUTABLE_SQL = _function("box1902_private.immutable_v2() RETURNS trigger", """
BEGIN
    RAISE EXCEPTION 'Box 1902 provenance is immutable' USING ERRCODE = '23514';
END
""")
APPROVAL_GUARD_SQL = _function("box1902_private.approval_guard_v2() RETURNS trigger", f"""
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF session_user <> 'box1902_approver' OR NEW.consumed_at IS NOT NULL
           OR NEW.expires_at <= clock_timestamp()
           OR NEW.expires_at > clock_timestamp() + interval '1 hour'
           OR NEW.manifest IS DISTINCT FROM public.box_1902_manifest_v2()
           OR NEW.plan_text::jsonb->'manifest' IS DISTINCT FROM NEW.manifest
           OR NOT (NEW.plan_text::jsonb @> '{{"operation":"{OPERATION}","classification":"READY",
               "organization_id":1,"box_id":1902,"source_strain_id":213,"source_species_id":131,
               "target_strain_id":285,"proposed":{{"global_code":"CLC-JKA-1.009","strain_id":285}},
               "conflicts":[]}}'::jsonb)
           OR NOT (NEW.plan_text::jsonb ?& ARRAY['before','execution','schema_hash','dependencies',
               'boxes','audits','namespaces','strains','species','organization','preservation_contract'])
           OR NEW.plan_text::jsonb->>'preservation_contract' IS DISTINCT FROM '{OPERATION}'
           OR coalesce(NEW.plan_text::jsonb->>'schema_hash','') !~ '^[0-9a-f]{{64}}$'
           OR coalesce(NEW.plan_text::jsonb->'execution'->>'target_hash','') !~ '^[0-9a-f]{{64}}$'
           OR coalesce(NEW.plan_text::jsonb->'execution'->>'mode','') NOT IN ('production','disposable')
           OR NEW.plan_text::jsonb->'conflicts' IS DISTINCT FROM '[]'::jsonb THEN
            RAISE EXCEPTION 'Independent exact live approval required' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF session_user = 'box1902_executor' AND current_user = 'box1902_owner'
           AND OLD.consumed_at IS NULL AND NEW.consumed_at IS NOT NULL
           AND (to_jsonb(OLD) - 'consumed_at') = (to_jsonb(NEW) - 'consumed_at')
           AND EXISTS (SELECT 1 FROM box1902_private.context_v2 c
               WHERE c.approval_id = OLD.id AND c.validated AND c.xid = pg_current_xact_id()
                 AND c.backend_pid = pg_backend_pid() AND c.executor = session_user) THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'Approval cannot be rewritten or removed' USING ERRCODE = '23514';
END
""")
CONTEXT_GUARD_SQL = _function("box1902_private.context_guard_v2() RETURNS trigger", """
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF session_user = 'box1902_executor' AND current_user = 'box1902_owner'
           AND NEW.xid = pg_current_xact_id() AND NEW.backend_pid = pg_backend_pid()
           AND NEW.executor = session_user AND NOT NEW.validated THEN
            RETURN NEW;
        END IF;
    ELSIF TG_OP = 'UPDATE' THEN
        IF session_user = 'box1902_executor' AND current_user = 'box1902_owner'
           AND OLD.xid = pg_current_xact_id() AND OLD.backend_pid = pg_backend_pid()
           AND OLD.executor = session_user AND NOT OLD.validated AND NEW.validated
           AND (to_jsonb(OLD) - 'validated') = (to_jsonb(NEW) - 'validated') THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'Context is durable and only allows acknowledgement' USING ERRCODE = '23514';
END
""")

_EXCEPTION_CHECK = f"""
        IF session_user = 'box1902_executor' AND EXISTS (
            SELECT 1 FROM box1902_private.context_v2 c
            JOIN box1902_private.approval_v2 a ON a.id = c.approval_id
            WHERE c.xid = pg_current_xact_id() AND c.backend_pid = pg_backend_pid()
              AND c.executor = session_user AND NOT c.validated
              AND a.consumed_at IS NULL AND a.expires_at > clock_timestamp()
              AND a.operation = '{OPERATION}'
              AND OLD.id = 1902 AND OLD.organization_id = 1
              AND c.before_box = to_jsonb(OLD) AND c.after_box = to_jsonb(NEW)
              AND c.after_box = c.before_box || jsonb_build_object('global_code', 'CLC-JKA-1.009', 'strain_id', 285)
        ) THEN
            UPDATE box1902_private.context_v2 SET validated = true
                WHERE xid = pg_current_xact_id() AND backend_pid = pg_backend_pid()
                  AND executor = session_user AND NOT validated;
            RETURN NEW;
        END IF;
        -- Never fall through to ordinary invalidation for a stale capability context.
        IF EXISTS (SELECT 1 FROM box1902_private.context_v2 c
            WHERE c.xid = pg_current_xact_id() AND c.backend_pid = pg_backend_pid()
              AND c.executor = session_user AND OLD.id = 1902) THEN
            RAISE EXCEPTION 'Box 1902 context was not accepted' USING ERRCODE = '42501';
        END IF;
"""


def _guards():
    """Derive only from the deployed ordinary guards, preserving their bindings."""
    original = import_module("apps.measurements.migrations.0014_measurement_canonical_evidence")
    box = original._box
    marker = "        UPDATE measurements_historicaltemperaturereading r SET association_status"
    if box.count(marker) != 1:
        raise RuntimeError("Review changed ordinary Box guard before installation")
    box = box.replace(marker, _EXCEPTION_CHECK + marker)
    evidence = original._evidence
    marker = "AND code = 'PCA-JKA-1.005'))"
    if evidence.count(marker) != 1:
        raise RuntimeError("Review changed ordinary evidence guard before installation")
    evidence = evidence.replace(marker, "AND code = 'PCA-JKA-1.005') "
                                "OR public.box_1902_evidence_valid_v2(NEW.id))")
    result = []
    for sql in (box, evidence):
        sql = sql.replace("FUNCTION historical_temperature_", "FUNCTION public.historical_temperature_", 1)
        sql = sql.replace("LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp",
                          f"LANGUAGE plpgsql SECURITY DEFINER {CONFIG}")
        for table in ("cultures_box", "measurements_biologicalmeasurement",
                      "measurements_historicaltemperaturereading", "measurements_historicaltemperatureevidence"):
            sql = re.sub(r"(?<![.\w])" + table + r"\b", "public." + table, sql)
        sql = re.sub(r"(?<![.\w])historical_temperature_decimal\(",
                     "public.historical_temperature_decimal(", sql)
        result.append(sql)
    return "\n".join(result)


GUARD_SQL = _guards()

APPLY_SQL = _function("public.box_1902_apply_v2(actor_id bigint, plan_hash text) RETURNS void", f"""
DECLARE
    approved box1902_private.approval_v2%ROWTYPE;
    fresh jsonb; v_before_box jsonb; v_after_box jsonb; observed jsonb;
    changed integer; consumed timestamptz; ns text; suffix bigint;
BEGIN
    PERFORM public.box_1902_lock_v2(actor_id);
    SELECT * INTO STRICT approved FROM box1902_private.approval_v2 a WHERE a.id = 1 FOR UPDATE;
    IF approved.operation <> '{OPERATION}' OR approved.actor_id <> actor_id
       OR approved.plan_hash IS DISTINCT FROM plan_hash
       OR approved.plan_hash <> encode(sha256(convert_to(approved.plan_text, 'UTF8')), 'hex')
       OR approved.consumed_at IS NOT NULL OR approved.expires_at <= clock_timestamp()
       OR approved.expires_at > clock_timestamp() + interval '1 hour' THEN
        RAISE EXCEPTION 'No exact independent live approval' USING ERRCODE = '42501';
    END IF;
    fresh := public.box_1902_manifest_v2();
    IF fresh IS DISTINCT FROM approved.manifest THEN
        RAISE EXCEPTION 'Approved manifest changed' USING ERRCODE = '23514';
    END IF;
    SELECT to_jsonb(b) INTO STRICT v_before_box FROM public.cultures_box b
        WHERE b.id = 1902 AND b.organization_id = 1 AND b.strain_id = 213
          AND b.global_code = 'CLA-JKA-1.009' AND b.local_code = 'CLA-JKA-1.09'
          AND b.status = 'inactive' AND b.box_number = '09'
          AND b.merged_into_id IS NULL AND b.previous_global_code = '';
    IF NOT EXISTS (SELECT 1 FROM public.taxonomy_strain s
        JOIN public.taxonomy_species p ON p.id = s.species_id
        WHERE s.id = 285 AND s.organization_id = 1 AND s.code = 'CLC-JKA-1'
          AND p.id = 164 AND p.scientific_name = 'Chrysaora lactea')
       OR NOT EXISTS (SELECT 1 FROM public.taxonomy_strain s
        JOIN public.taxonomy_species p ON p.id = s.species_id
        WHERE s.id = 213 AND (s.organization_id IS NULL OR s.organization_id = 1)
          AND s.code = 'CLA-JKA-1'
          AND p.id = 131 AND p.scientific_name = 'Cyanea lamarckii') THEN
        RAISE EXCEPTION 'Exact source and target taxonomy required' USING ERRCODE = '23514';
    END IF;
    IF (SELECT array_agg((r->>'id')::bigint ORDER BY (r->>'id')::bigint)
        FROM jsonb_array_elements(fresh->'readings') r)
        IS DISTINCT FROM ARRAY(SELECT generate_series(12173::bigint, 12213::bigint))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(fresh->'readings') r
          WHERE r->>'association_status' IS DISTINCT FROM 'confirmed'
             OR (r->>'iso_year')::int IS DISTINCT FROM 2025
             OR (r->>'iso_week')::int IS DISTINCT FROM (r->>'id')::int - 12161
             OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(fresh->'evidence') e
                 WHERE e->'reading_id' = r->'id'))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(fresh->'evidence') e
          WHERE e->>'source_sha256' IS DISTINCT FROM '0776396ad12b6479e53755496b0e6c51ec3a2e1ff581c983f84f82fe943b84fa'
             OR e->>'source_file' IS DISTINCT FROM 'Suivi_2025.xlsx'
             OR e->>'sheet' IS DISTINCT FROM 'Semaestomeae'
             OR e->>'source_box' IS DISTINCT FROM 'CLA-JKA-1.09'
             OR (e->>'source_iso_year')::int IS DISTINCT FROM 2025
             OR (e->>'source_iso_week')::int IS DISTINCT FROM (e->>'reading_id')::int - 12161) THEN
        RAISE EXCEPTION 'Exact 41 confirmed readings and original source evidence required' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.cultures_box WHERE id <> 1902
        AND global_code IN ('CLA-JKA-1.009', 'CLC-JKA-1.009')) THEN
        RAISE EXCEPTION 'Historical code collision' USING ERRCODE = '23514';
    END IF;
    v_after_box := v_before_box || jsonb_build_object('global_code', 'CLC-JKA-1.009', 'strain_id', 285);
    INSERT INTO box1902_private.context_v2
        (approval_id, xid, backend_pid, executor, before_box, after_box)
        VALUES (approved.id, pg_current_xact_id(), pg_backend_pid(), session_user, v_before_box, v_after_box);
    -- Same explicit registration as register_box_code: never lower a high-water mark.
    FOREACH ns IN ARRAY ARRAY['CLA-JKA-1', 'CLC-JKA-1'] LOOP
        suffix := 9;
        INSERT INTO public.cultures_boxcodenamespace (namespace, high_water) VALUES (ns, suffix)
        ON CONFLICT (namespace) DO UPDATE SET high_water = greatest(
            public.cultures_boxcodenamespace.high_water, EXCLUDED.high_water);
    END LOOP;
    UPDATE public.cultures_box SET global_code = 'CLC-JKA-1.009', strain_id = 285
        WHERE id = 1902 AND organization_id = 1;
    GET DIAGNOSTICS changed = ROW_COUNT;
    observed := public.box_1902_manifest_v2();
    IF changed <> 1 OR observed->'box' IS DISTINCT FROM v_after_box
       OR (observed - 'box') IS DISTINCT FROM (fresh - 'box')
       OR NOT EXISTS (SELECT 1 FROM box1902_private.context_v2 c WHERE c.approval_id = approved.id
           AND c.validated AND c.xid = pg_current_xact_id() AND c.backend_pid = pg_backend_pid()
           AND c.executor = session_user AND c.before_box = v_before_box AND c.after_box = v_after_box)
       OR approved.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'Scientific preservation or trigger acknowledgement failed' USING ERRCODE = '23514';
    END IF;
    consumed := clock_timestamp();
    UPDATE box1902_private.approval_v2 SET consumed_at = consumed WHERE id = approved.id;
    INSERT INTO box1902_private.correction_v2
        (approval_id, operation, actor_id, executor, reason, before_box, after_box, manifest, plan_text, plan_hash, consumed_at)
        VALUES (approved.id, approved.operation, actor_id, session_user, {_SQL_REASON}, v_before_box, v_after_box,
            fresh, approved.plan_text, approved.plan_hash, consumed);
    INSERT INTO public.audit_auditlog
        (organization_id, user_id, action, object_type, object_id, description, metadata, original_values, created_at)
    SELECT 1, actor_id, 'update', event.object_type, event.object_id,
        {_SQL_REASON},
        jsonb_build_object('operation', approved.operation, 'before', v_before_box, 'after', v_after_box,
            'plan_hash', approved.plan_hash, 'reviewed_plan', approved.plan_text::jsonb,
            'reason', {_SQL_REASON}, 'scientific_basis', {_SQL_REASON},
            'source_species_id', 131, 'actor_id', actor_id, 'approval_id', approved.id),
        v_before_box, consumed
    FROM (VALUES ('box', 'CLC-JKA-1.009'), ('cla_box_1902_correction', '1902')) event(object_type, object_id);
END
""")

TRIGGER_SQL = """
CREATE TRIGGER approval_v2_guard BEFORE INSERT OR UPDATE OR DELETE ON box1902_private.approval_v2
FOR EACH ROW EXECUTE FUNCTION box1902_private.approval_guard_v2();
CREATE TRIGGER approval_v2_no_truncate BEFORE TRUNCATE ON box1902_private.approval_v2
FOR EACH STATEMENT EXECUTE FUNCTION box1902_private.immutable_v2();
CREATE TRIGGER context_v2_guard BEFORE INSERT OR UPDATE OR DELETE ON box1902_private.context_v2
FOR EACH ROW EXECUTE FUNCTION box1902_private.context_guard_v2();
CREATE TRIGGER context_v2_no_truncate BEFORE TRUNCATE ON box1902_private.context_v2
FOR EACH STATEMENT EXECUTE FUNCTION box1902_private.immutable_v2();
CREATE TRIGGER correction_v2_guard BEFORE UPDATE OR DELETE ON box1902_private.correction_v2
FOR EACH ROW EXECUTE FUNCTION box1902_private.immutable_v2();
CREATE TRIGGER correction_v2_no_truncate BEFORE TRUNCATE ON box1902_private.correction_v2
FOR EACH STATEMENT EXECUTE FUNCTION box1902_private.immutable_v2();
"""

FUNCTION_SQL = "\n".join((MANIFEST_SQL, LOCK_SQL, EVIDENCE_SQL, IMMUTABLE_SQL,
                          APPROVAL_GUARD_SQL, CONTEXT_GUARD_SQL, GUARD_SQL, APPLY_SQL))
# Exact source bodies exported for pg_proc.prosrc attestation (not whitespace-normalized).
EXPECTED_DEFINITIONS = {
    name: body for name, body in re.findall(
        r"CREATE OR REPLACE FUNCTION ([\w.]+)\([^;]*?AS \$\$(.*?)\$\$;",
        FUNCTION_SQL, re.S,
    )
}
FUNCTION_SIGNATURES = {
    "public.box_1902_manifest_v2": "()",
    "public.box_1902_lock_v2": "(bigint)",
    "public.box_1902_apply_v2": "(bigint,text)",
    "public.box_1902_evidence_valid_v2": "(bigint)",
    "public.historical_temperature_box_guard": "()",
    "public.historical_temperature_evidence_valid": "()",
    "box1902_private.immutable_v2": "()",
    "box1902_private.approval_guard_v2": "()",
    "box1902_private.context_guard_v2": "()",
}
EXCEPTION_FUNCTIONS = ("public.box_1902_lock_v2", "public.box_1902_apply_v2")

ACL_SQL = "\n".join(
    f"REVOKE ALL ON FUNCTION {name}{sig} FROM PUBLIC, polypbase, box1902_approver, "
    "box1902_executor, box1902_migrator;"
    for name, sig in FUNCTION_SIGNATURES.items()
) + """
GRANT USAGE ON SCHEMA box1902_private TO box1902_approver;
GRANT INSERT (id, operation, actor_id, plan_text, plan_hash, manifest, expires_at)
    ON box1902_private.approval_v2 TO box1902_approver;
-- Dedicated backup login reads protected provenance for a complete archive only.
GRANT USAGE ON SCHEMA box1902_private TO box1902_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA box1902_private TO box1902_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE box1902_owner IN SCHEMA box1902_private
    GRANT SELECT ON TABLES TO box1902_backup;
GRANT EXECUTE ON FUNCTION public.box_1902_manifest_v2()
    TO polypbase, box1902_approver, box1902_executor;
GRANT EXECUTE ON FUNCTION public.box_1902_evidence_valid_v2(bigint) TO PUBLIC;
GRANT EXECUTE ON FUNCTION public.box_1902_lock_v2(bigint), public.box_1902_apply_v2(bigint,text)
    TO box1902_executor;
"""
SQL = "\n".join((TABLE_SQL, FUNCTION_SQL, TRIGGER_SQL, ACL_SQL))


class ContractError(RuntimeError):
    """Installed SQL authority does not match the reviewed contract."""


def assert_topology(connection, installation=False):
    """Read-only, fail-closed check. Installation requires explicit trusted SET ROLE."""
    if connection.vendor != "postgresql":
        raise ContractError("Box 1902 requires PostgreSQL")
    with connection.cursor() as c:
        c.execute("SELECT current_user, session_user, current_setting('server_version_num')::int")
        current, session, version = c.fetchone()
        if version < 160000:
            raise ContractError("PostgreSQL 16+ membership options required")
        if installation:
            c.execute("SELECT to_regnamespace('box1902_private')")
            if c.fetchone()[0] is not None:
                raise ContractError("Private capability schema already exists; refuse replacement")
        if installation and (current != OWNER or session != MIGRATOR):
            raise ContractError("Install as trusted box1902_migrator with explicit SET ROLE box1902_owner")
        c.execute("""SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolinherit,
            rolreplication, rolbypassrls, rolcanlogin FROM pg_catalog.pg_roles WHERE rolname = ANY(%s)""", [list(ROLES)])
        roles = {row[0]: row[1:] for row in c.fetchall()}
        if set(roles) != set(ROLES):
            raise ContractError("Missing independent Box 1902 roles; provision separately")
        for role, flags in roles.items():
            if (any(flags[:6]) or (role == OWNER and flags[6])
                    or (role in (RUNTIME, APPROVER, MIGRATOR, BACKUP) and not flags[6])):
                raise ContractError(f"Unsafe role attributes: {role}")
        c.execute("""SELECT parent.rolname, member.rolname, m.admin_option, m.inherit_option, m.set_option
            FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles parent ON parent.oid = m.roleid
            JOIN pg_catalog.pg_roles member ON member.oid = m.member
            WHERE parent.rolname = ANY(%s) OR member.rolname = ANY(%s)""", [list(ROLES), list(ROLES)])
        if c.fetchall() != [(OWNER, MIGRATOR, False, False, True)]:
            raise ContractError("Membership must be exactly migrator SET-only owner")
        c.execute("""SELECT pg_get_userbyid(datdba) FROM pg_catalog.pg_database
            WHERE datname = current_database()""")
        if c.fetchone()[0] != OWNER:
            raise ContractError("Database must be owned by protected owner")
        c.execute("""SELECT nspname, pg_get_userbyid(nspowner) FROM pg_catalog.pg_namespace
            WHERE nspname IN ('public', 'box1902_private')""")
        schemas = dict(c.fetchall())
        if schemas.get("public") != OWNER or ("box1902_private" in schemas and schemas["box1902_private"] != OWNER):
            raise ContractError("Unsafe schema ownership")
        c.execute("""SELECT n.nspname, cl.relname FROM pg_catalog.pg_class cl
            JOIN pg_catalog.pg_namespace n ON n.oid = cl.relnamespace
            WHERE n.nspname IN ('public', 'box1902_private')
              AND cl.relkind IN ('r','p','S','v','m','f') AND cl.relowner <> %s::regrole""", [OWNER])
        if c.fetchall():
            raise ContractError("All public/private relations must have protected ownership")
        c.execute("""SELECT p.oid FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname IN ('public','box1902_private') AND p.proowner <> %s::regrole""", [OWNER])
        if c.fetchall():
            raise ContractError("All public/private routines must have protected ownership")
        c.execute("""SELECT 1 FROM pg_catalog.pg_db_role_setting s
            WHERE (s.setrole=0 OR s.setrole IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)))
              AND (s.setdatabase=0 OR s.setdatabase=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()))
              AND EXISTS (SELECT 1 FROM unnest(s.setconfig) setting
                  WHERE setting LIKE 'session_replication_role=%%' AND setting <> 'session_replication_role=origin')""",
                  [list(ROLES)])
        if c.fetchall():
            raise ContractError("Unsafe default session_replication_role")
        c.execute("SELECT current_setting('session_replication_role')")
        if c.fetchone()[0] != 'origin':
            raise ContractError("Triggers must run in origin mode")
        for role in (RUNTIME, APPROVER, EXECUTOR, MIGRATOR, BACKUP):
            c.execute("SELECT has_parameter_privilege(%s, 'session_replication_role', 'SET,ALTER SYSTEM')", [role])
            if c.fetchone()[0]:
                raise ContractError(f"Trigger-bypass parameter privilege: {role}")
            c.execute("""SELECT has_database_privilege(%s, current_database(), 'CREATE'),
                has_database_privilege(%s, current_database(), 'TEMP'),
                EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname !~ '^pg_'
                    AND nspname <> 'information_schema' AND has_schema_privilege(%s, oid, 'CREATE'))""",
                      [role, role, role])
            if any(c.fetchone()):
                raise ContractError(f"Untrusted database/schema creation privilege: {role}")
        c.execute("""SELECT n.nspname, p.proname FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND p.prosecdef
              AND has_function_privilege(%s,p.oid,'EXECUTE')
              AND p.oid NOT IN (coalesce(to_regprocedure('public.box_1902_manifest_v2()')::oid,0),
                               coalesce(to_regprocedure('public.box_1902_evidence_valid_v2(bigint)')::oid,0))""", [RUNTIME])
        if c.fetchall():
            raise ContractError("Runtime has an unreviewed SECURITY DEFINER capability")
        for role in (APPROVER, EXECUTOR, MIGRATOR, BACKUP):
            c.execute("""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class t
                JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m','f')
                AND CASE WHEN t.relkind IN ('r','p','v','m','f') THEN
                    has_table_privilege(%s,t.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(%s,t.oid,'INSERT,UPDATE,REFERENCES') ELSE false END)""", [role, role])
            if c.fetchone()[0]:
                raise ContractError(f"Untrusted public write privilege: {role}")
            c.execute("""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class t
                JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public'
                AND CASE t.relkind WHEN 'S' THEN
                                    has_sequence_privilege(%s,t.oid,'USAGE,UPDATE') ELSE false END)""", [role])
            if c.fetchone()[0]:
                raise ContractError(f"Untrusted public sequence write privilege: {role}")
        for role in (APPROVER, EXECUTOR, MIGRATOR, BACKUP):
            allowed = ['box_1902_evidence_valid_v2']
            if role in (APPROVER, EXECUTOR):
                allowed.append('box_1902_manifest_v2')
            if role == EXECUTOR:
                allowed += ['box_1902_lock_v2', 'box_1902_apply_v2']
            c.execute("""SELECT p.proname FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.proname <> ALL(%s)
                  AND has_function_privilege(%s,p.oid,'EXECUTE')""", [allowed, role])
            if c.fetchall():
                raise ContractError(f"Unexpected public routine access: {role}")
        # The backup login must read every public relation and sequence, never more.
        c.execute("SELECT has_schema_privilege(%s, 'public', 'USAGE')", [BACKUP])
        if not c.fetchone()[0]:
            raise ContractError("Backup role lacks public schema USAGE")
        c.execute("""SELECT count(*) FROM pg_catalog.pg_class t
            JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname='public' AND t.relkind IN ('r','p')
              AND NOT has_table_privilege(%s,t.oid,'SELECT')""", [BACKUP])
        if c.fetchone()[0]:
            raise ContractError("Backup role lacks SELECT on public tables")
        c.execute("""SELECT count(*) FROM pg_catalog.pg_class t
            JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname='public' AND t.relkind='S'
              AND NOT has_sequence_privilege(%s,t.oid,'SELECT')""", [BACKUP])
        if c.fetchone()[0]:
            raise ContractError("Backup role lacks SELECT on public sequences")
        for name in ('public.box_1902_lock_v2(bigint)', 'public.box_1902_apply_v2(bigint,text)'):
            c.execute("SELECT to_regprocedure(%s)::oid", [name])
            oid = c.fetchone()[0]
            if oid:
                c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')", [RUNTIME, oid])
                if c.fetchone()[0]:
                    raise ContractError("Runtime has exceptional EXECUTE")
    return True


def attest(connection, allow_retired=True):
    """Attest exact live or retired capability; never accept a partially retired writer."""
    assert_topology(connection)
    with connection.cursor() as c:
        c.execute("SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname = %s", [EXECUTOR])
        executor_login = c.fetchone()[0]
        c.execute("""SELECT n.nspname || '.' || p.proname, p.prosrc, p.prosecdef,
            p.proconfig, p.oid, pg_catalog.oidvectortypes(p.proargtypes), p.provolatile,
                        format_type(p.prorettype,NULL), p.proretset, p.proisstrict, p.proleakproof,
                        p.proparallel, p.prokind, p.pronargdefaults, p.proargnames,
                        (SELECT lanname FROM pg_catalog.pg_language WHERE oid=p.prolang)
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE (n.nspname='public' AND (p.proname = ANY(%s) OR p.proname ~ '^box_1902_'))
                            OR n.nspname='box1902_private'""",
                  [[name.split('.')[1] for name in EXPECTED_DEFINITIONS if name.startswith('public.')]])
        rows = c.fetchall()
        installed = {r[0]: r for r in rows}
        if len(rows) != len(installed):
            raise ContractError("Capability overloads are forbidden")
        retired = all(name not in installed for name in EXCEPTION_FUNCTIONS)
        expected = set(EXPECTED_DEFINITIONS) - (set(EXCEPTION_FUNCTIONS) if retired else set())
        if set(installed) != expected or (retired and (not allow_retired or executor_login)) or (not retired and not executor_login):
            raise ContractError("Unknown live/retired capability state")
        for name, row in installed.items():
            (_, body, definer, config, oid, arguments, volatility, result, returns_set,
             strict, leakproof, parallel, kind, defaults, argnames, language) = row
            if name.endswith('box_1902_manifest_v2'):
                expected_result, expected_args, expected_language = 'jsonb', None, 'sql'
            elif name.endswith('box_1902_evidence_valid_v2'):
                expected_result, expected_args, expected_language = 'boolean', ['evidence_id'], 'sql'
            elif name.endswith('box_1902_apply_v2'):
                expected_result, expected_args, expected_language = 'void', ['actor_id', 'plan_hash'], 'plpgsql'
            elif name.endswith('box_1902_lock_v2'):
                expected_result, expected_args, expected_language = 'void', ['actor_id'], 'plpgsql'
            else:
                expected_result, expected_args, expected_language = 'trigger', None, 'plpgsql'
            if (result != expected_result or argnames != expected_args or language != expected_language
                    or returns_set or strict or leakproof or parallel != 'u' or kind != 'f' or defaults):
                raise ContractError(f"Function API drift: {name}")
            expected_volatility = 's' if name in ('public.box_1902_manifest_v2', 'public.box_1902_evidence_valid_v2') else 'v'
            if volatility != expected_volatility:
                raise ContractError(f"Function volatility drift: {name}")
            if (body != EXPECTED_DEFINITIONS[name] or not definer
                    or set(config or ()) != {'search_path=pg_catalog, pg_temp', 'TimeZone=UTC', 'DateStyle=ISO, YMD'}):
                raise ContractError(f"Function definition drift: {name}")
            # Catalog-only lookup works even for runtime without private schema USAGE.
            if '(' + arguments.replace(' ', '') + ')' != FUNCTION_SIGNATURES[name]:
                raise ContractError(f"Function signature drift: {name}")
            readers = {OWNER}
            if name.endswith('box_1902_manifest_v2'):
                readers |= {RUNTIME, APPROVER, EXECUTOR}
            elif name in EXCEPTION_FUNCTIONS:
                readers.add(EXECUTOR)
            elif name.endswith('box_1902_evidence_valid_v2'):
                readers.add('PUBLIC')
            c.execute("""SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
                x.privilege_type, x.is_grantable FROM pg_catalog.pg_proc p,
                LATERAL aclexplode(coalesce(p.proacl, acldefault('f',p.proowner))) x WHERE p.oid=%s""", [oid])
            acl = c.fetchall()
            if {r[0] for r in acl} != readers or any(r[1:] != ('EXECUTE', False) for r in acl):
                raise ContractError(f"Function ACL drift: {name}")
        c.execute("""SELECT cl.relname FROM pg_catalog.pg_class cl
            JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace
            WHERE n.nspname='box1902_private' AND cl.relkind IN ('r','p','v','m','S','f')""")
        if {r[0] for r in c.fetchall()} != set(PRIVATE_TABLES):
            raise ContractError("Private relation inventory drift")
        _attest_triggers(c)
        _attest_private_acl(c)
        _attest_private_tables(c)
    return {"operation": OPERATION, "retired": retired}


def _attest_triggers(c):
    expected = {
        ('public', 'cultures_box', 'historical_box_guard'): ('public.historical_temperature_box_guard', 19),
        ('public', 'measurements_historicaltemperatureevidence', 'historical_evidence_valid'):
            ('public.historical_temperature_evidence_valid', 7),
    }
    for table, guard in (("approval_v2", "approval_guard_v2"), ("context_v2", "context_guard_v2"),
                         ("correction_v2", "immutable_v2")):
        expected[('box1902_private', table, table + '_guard')] = (
            'box1902_private.' + guard, 31 if table != 'correction_v2' else 27)
        expected[('box1902_private', table, table + '_no_truncate')] = ('box1902_private.immutable_v2', 34)
    c.execute("""SELECT n.nspname, cl.relname, t.tgname, pn.nspname || '.' || p.proname,
        t.tgtype, t.tgenabled, t.tgqual IS NULL, t.tgnargs, t.tgdeferrable, t.tginitdeferred
        FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class cl ON cl.oid=t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace
        JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
        JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace WHERE NOT t.tgisinternal
        AND (n.nspname='box1902_private' OR t.tgname IN ('historical_box_guard','historical_evidence_valid'))""")
    rows = c.fetchall()
    if len(rows) != len(expected):
        raise ContractError("Guard trigger inventory drift")
    for schema, table, trigger, function, kind, enabled, unqualified, nargs, deferred, initially in rows:
        if (expected.get((schema, table, trigger)) != (function, kind)
                or enabled != 'O' or not unqualified or nargs or deferred or initially):
            raise ContractError(f"Guard binding drift: {schema}.{trigger}")


def _attest_private_tables(c):
    expected = {
        'approval_v2': [('id', 'integer'), ('operation', 'text'), ('actor_id', 'bigint'),
                        ('plan_text', 'text'), ('plan_hash', 'text'), ('manifest', 'jsonb'),
                        ('expires_at', 'timestamp with time zone'), ('consumed_at', 'timestamp with time zone')],
        'context_v2': [('approval_id', 'integer'), ('xid', 'xid8'), ('backend_pid', 'integer'),
                       ('executor', 'name'), ('before_box', 'jsonb'), ('after_box', 'jsonb'), ('validated', 'boolean')],
        'correction_v2': [('approval_id', 'integer'), ('operation', 'text'), ('actor_id', 'bigint'),
                          ('executor', 'name'), ('reason', 'text'), ('before_box', 'jsonb'), ('after_box', 'jsonb'),
                          ('manifest', 'jsonb'), ('plan_text', 'text'), ('plan_hash', 'text'),
                          ('consumed_at', 'timestamp with time zone')],
    }
    for table, columns in expected.items():
        c.execute("""SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid), a.attidentity, a.attgenerated
            FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
            ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=(SELECT cl.oid FROM pg_catalog.pg_class cl
                JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace
                WHERE n.nspname='box1902_private' AND cl.relname=%s)
                AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", [table])
        wanted = [(col, typ, not (table == 'approval_v2' and col == 'consumed_at'),
                   'false' if col == 'validated' else None, '', '') for col, typ in columns]
        if c.fetchall() != wanted:
            raise ContractError(f"Private table shape drift: {table}")
        c.execute("""SELECT relrowsecurity, relforcerowsecurity, relpersistence, relispartition
            FROM pg_catalog.pg_class cl JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace
                        WHERE n.nspname='box1902_private' AND cl.relname=%s""", [table])
        if c.fetchone() != (False, False, 'p', False):
            raise ContractError(f"Private table storage/policy drift: {table}")
        c.execute("""SELECT count(*) FROM pg_catalog.pg_rewrite r
            JOIN pg_catalog.pg_class cl ON cl.oid=r.ev_class
            JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace
            WHERE n.nspname='box1902_private' AND cl.relname=%s""", [table])
        if c.fetchone()[0]:
            raise ContractError(f"Unexpected private table rule: {table}")
        constraints = {table + '_pkey': 'PRIMARY KEY (' + ('id' if table == 'approval_v2' else 'approval_id') + ')'}
        if table == 'approval_v2':
            constraints.update({
                'approval_v2_id_check': 'CHECK ((id = 1))',
                'approval_v2_operation_check': f"CHECK ((operation = '{OPERATION}'::text))",
                'approval_v2_plan_text_check': "CHECK ((jsonb_typeof((plan_text)::jsonb) = 'object'::text))",
                'approval_v2_plan_hash_check': "CHECK ((plan_hash = encode(sha256(convert_to(plan_text, 'UTF8'::name)), 'hex'::text)))",
                'approval_v2_manifest_check': "CHECK ((jsonb_typeof(manifest) = 'object'::text))",
            })
        else:
            constraints[table + '_approval_id_fkey'] = 'FOREIGN KEY (approval_id) REFERENCES box1902_private.approval_v2(id)'
            if table == 'context_v2':
                constraints['context_v2_executor_check'] = "CHECK ((executor = 'box1902_executor'::name))"
            else:
                constraints['correction_v2_operation_check'] = f"CHECK ((operation = '{OPERATION}'::text))"

        # PostgreSQL 18 catalogs NOT NULL constraints too; attnotnull is checked above.
        c.execute("""SELECT conname, pg_get_constraintdef(oid), convalidated, condeferrable, condeferred
            FROM pg_catalog.pg_constraint WHERE contype <> 'n' AND conrelid=(SELECT cl.oid FROM pg_catalog.pg_class cl
                            JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace
                            WHERE n.nspname='box1902_private' AND cl.relname=%s)""", [table])
        actual = c.fetchall()
        if (len(actual) != len(constraints) or any(
            name not in constraints or definition != constraints[name]
            or not validated or deferred or initially
            for name, definition, validated, deferred, initially in actual
        )):
            raise ContractError(f"Private constraint drift: {table}: {actual!r}")


def _attest_private_acl(c):
    c.execute("""SELECT cl.relname, CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
        x.privilege_type, x.is_grantable FROM pg_catalog.pg_class cl
        JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace,
        LATERAL aclexplode(coalesce(cl.relacl, acldefault('r',cl.relowner))) x
        WHERE n.nspname='box1902_private' AND cl.relkind='r'""")
    grants = c.fetchall()
    if any(role != OWNER and (role != BACKUP or privilege != 'SELECT' or grantable)
           for _, role, privilege, grantable in grants):
        raise ContractError("Private table grants are forbidden")
    for table in PRIVATE_TABLES:
        if not any(name == table and role == BACKUP and privilege == 'SELECT' and not grantable
                   for name, role, privilege, grantable in grants):
            raise ContractError(f"Backup role lacks SELECT on private table: {table}")
    c.execute("""SELECT cl.relname, a.attname, pg_get_userbyid(x.grantee), x.privilege_type, x.is_grantable
        FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class cl ON cl.oid=a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace,
        LATERAL aclexplode(a.attacl) x WHERE n.nspname='box1902_private' AND a.attnum>0""")
    if set(c.fetchall()) != {('approval_v2', col, APPROVER, 'INSERT', False) for col in APPROVAL_COLUMNS}:
        raise ContractError("Approval column grants drift")
    c.execute("""SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
        x.privilege_type, x.is_grantable FROM pg_catalog.pg_namespace n,
        LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) x
        WHERE n.nspname='box1902_private'""")
    if set(c.fetchall()) != {(OWNER, 'USAGE', False), (OWNER, 'CREATE', False),
                             (APPROVER, 'USAGE', False), (BACKUP, 'USAGE', False)}:
        raise ContractError("Private schema ACL drift")
    for role in (RUNTIME, EXECUTOR, MIGRATOR):
        c.execute("SELECT has_schema_privilege(%s,'box1902_private','USAGE')", [role])
        if c.fetchone()[0]:
            raise ContractError(f"Unexpected private schema access: {role}")


def provision_sql():
    """Emit a first-install transition, never adopt/reconfigure reserved roles.

    All technical roles must be absent, even when their attributes look correct.
    Reprovisioning or recovering precreated roles needs separate operator review.
    The existing runtime is accepted only as the unprivileged, membership-free
    owner of this database and its public application objects.
    """
    role_sql = "\n".join(
        f"CREATE ROLE {role} {'NOLOGIN' if role == OWNER else 'LOGIN'} NOSUPERUSER NOCREATEDB "
        "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;"
        for role in ROLES if role != RUNTIME
    )
    return """-- Run only as separately authorized cluster administrator, before migration 0018.
-- No password or authentication configuration is emitted.
BEGIN;
SET LOCAL search_path = pg_catalog;
DO $$ DECLARE runtime_oid oid; BEGIN
    -- No catalog mutation may precede these checks. Names/markers prove no identity.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname IN ('box1902_owner','box1902_approver','box1902_executor','box1902_migrator','box1902_backup')) THEN
        RAISE EXCEPTION 'Reserved Box 1902 role already exists; separate operator review required'
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='box1902_private') THEN
        RAISE EXCEPTION 'Provision only before capability installation; never reprovision a live/retired capability'
            USING ERRCODE = '42501';
    END IF;
    SELECT oid INTO runtime_oid FROM pg_catalog.pg_roles WHERE rolname='polypbase'
        AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolreplication AND NOT rolbypassrls;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Expected unprivileged existing application runtime required'
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members
        WHERE roleid=runtime_oid OR member=runtime_oid) THEN
        RAISE EXCEPTION 'Unexpected runtime membership; separate operator review required'
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database
        WHERE datname=current_database() AND datdba=runtime_oid)
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='public'
           AND nspowner IN (runtime_oid,'pg_database_owner'::regrole))
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
           AND c.relkind IN ('r','p','S','v','m','f') AND c.relowner<>runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
           JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proowner<>runtime_oid) THEN
        RAISE EXCEPTION 'Unexpected initial database/schema/object ownership; separate operator review required'
            USING ERRCODE = '42501';
    END IF;
    -- Only public is explicitly remediated below. Match the final topology's
    -- effective-privilege predicate, including PUBLIC grants and schema ownership.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_namespace
        WHERE nspname !~ '^pg_' AND nspname <> 'information_schema' AND nspname <> 'public'
          AND has_schema_privilege(runtime_oid,oid,'CREATE')) THEN
        RAISE EXCEPTION 'Unexpected runtime schema CREATE privilege; separate operator review required'
            USING ERRCODE = '42501';
    END IF;
    IF has_parameter_privilege(runtime_oid,'session_replication_role','SET,ALTER SYSTEM')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting s
           WHERE (s.setrole=0 OR s.setrole=runtime_oid)
             AND (s.setdatabase=0 OR s.setdatabase=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()))
             AND EXISTS (SELECT 1 FROM unnest(s.setconfig) setting
                 WHERE setting LIKE 'session_replication_role=%' AND setting<>'session_replication_role=origin')) THEN
        RAISE EXCEPTION 'Unexpected runtime trigger-bypass privilege or setting'
            USING ERRCODE = '42501';
    END IF;
END $$;
""" + role_sql + """
-- CREATE ROLE deliberately has no IF NOT EXISTS: a concurrent collision must fail,
-- never turn into adoption between the preflight and creation.
ALTER ROLE polypbase NOINHERIT;
DO $$ DECLARE obj record; BEGIN
    EXECUTE format('ALTER DATABASE %I OWNER TO box1902_owner',current_database());
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC, polypbase, box1902_approver, box1902_executor, box1902_migrator',current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO polypbase, box1902_approver, box1902_executor, box1902_migrator, box1902_backup',current_database());
    ALTER SCHEMA public OWNER TO box1902_owner;
    REVOKE ALL ON SCHEMA public FROM PUBLIC, polypbase, box1902_approver, box1902_executor, box1902_migrator, box1902_backup;
    GRANT USAGE ON SCHEMA public TO polypbase, box1902_approver, box1902_executor, box1902_migrator, box1902_backup;
    FOR obj IN SELECT n.nspname, cl.relname, cl.relkind FROM pg_catalog.pg_class cl
        JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='public'
        AND cl.relkind IN ('r','p','S','v','m','f') ORDER BY (cl.relkind='S'), cl.relname
    LOOP
        EXECUTE format('ALTER %s %I.%I OWNER TO box1902_owner',
            CASE obj.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
            WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END,
            obj.nspname,obj.relname);
    END LOOP;
    FOR obj IN SELECT p.oid::regprocedure AS signature,
        EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
            WHERE x.grantee=0 AND x.privilege_type='EXECUTE') AS public_execute
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
    LOOP
        EXECUTE format('ALTER ROUTINE %s OWNER TO box1902_owner',obj.signature);
        IF obj.public_execute THEN
            EXECUTE format('GRANT EXECUTE ON ROUTINE %s TO polypbase',obj.signature);
        END IF;
        EXECUTE format('REVOKE ALL ON ROUTINE %s FROM PUBLIC, box1902_approver, box1902_executor, box1902_migrator',obj.signature);
    END LOOP;
    -- Table revokes do not remove column-level grants.
    FOR obj IN SELECT cl.oid::regclass AS relation, a.attname FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class cl ON cl.oid=a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace
        WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
    LOOP
        EXECUTE format('REVOKE ALL (%I) ON TABLE %s FROM PUBLIC, box1902_approver, box1902_executor, box1902_migrator, box1902_backup',obj.attname,obj.relation);
    END LOOP;
END $$;
GRANT box1902_owner TO box1902_migrator WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
-- Preserve ordinary runtime DML after removing implicit ownership. No exceptional grant.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO polypbase;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO polypbase;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, box1902_approver, box1902_executor, box1902_migrator, box1902_backup;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, box1902_approver, box1902_executor, box1902_migrator, box1902_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO box1902_executor;
-- Complete read-only backup authority: tables and sequences, never write or DDL.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO box1902_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO box1902_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE box1902_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE box1902_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO polypbase;
ALTER DEFAULT PRIVILEGES FOR ROLE box1902_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO polypbase;
ALTER DEFAULT PRIVILEGES FOR ROLE box1902_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO box1902_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE box1902_owner IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO box1902_backup;
DO $$ BEGIN
    -- The public exception is preflight-only. Ownership transfer must also remove
    -- implicit pg_database_owner authority, not just explicit runtime ACL entries.
    IF has_database_privilege('polypbase',current_database(),'CREATE')
       OR has_database_privilege('polypbase',current_database(),'TEMP')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace
           WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
             AND has_schema_privilege('polypbase',oid,'CREATE')) THEN
        RAISE EXCEPTION 'Runtime database/schema creation privilege remains after provisioning'
            USING ERRCODE = '42501';
    END IF;
END $$;
COMMIT;
"""


def approval_sql(plan_text, actor_id, manifest, expires_at):
    """Emit literal approval SQL for execution through the independent approver login.

    The reviewed JSON is supplied verbatim, not rebuilt from the live database.
    Expiry is an explicit ISO timestamp. No database or settings are imported.
    """
    if type(actor_id) is not int or actor_id <= 0:
        raise ValueError("Positive integer actor_id required")
    plan = json.loads(plan_text)
    if not isinstance(plan, dict) or canonical_plan(plan) != plan_text or plan.get('operation') != OPERATION:
        raise ValueError("Exact canonical reviewed v2 plan required")
    required = {'before', 'execution', 'schema_hash', 'dependencies', 'boxes', 'audits',
                'namespaces', 'strains', 'species', 'organization', 'preservation_contract'}
    scope = {'classification': 'READY', 'organization_id': 1, 'box_id': 1902,
             'source_strain_id': 213, 'source_species_id': 131, 'target_strain_id': 285,
             'preservation_contract': OPERATION, 'conflicts': [],
             'proposed': {'global_code': 'CLC-JKA-1.009', 'strain_id': 285}}
    if not required.issubset(plan) or any(plan.get(key) != value for key, value in scope.items()):
        raise ValueError("Complete reviewed READY v2 workflow plan required; proof-only documents are forbidden")
    if not isinstance(manifest, dict) or manifest.get('operation') != OPERATION:
        raise ValueError("Reviewed SQL manifest required")
    if plan.get('manifest') != manifest:
        raise ValueError("Reviewed plan must contain the exact approved SQL manifest")
    def literal(value):
        return "'" + value.replace("'", "''") + "'"
    return ("BEGIN;\nSET LOCAL standard_conforming_strings = on;\n"
            "INSERT INTO box1902_private.approval_v2 (" + ", ".join(APPROVAL_COLUMNS) + ") VALUES (1, "
            + literal(OPERATION) + f", {actor_id}, " + literal(plan_text) + ", "
            + literal(plan_digest(plan_text)) + ", " + literal(canonical_plan(manifest)) + "::jsonb, "
            + literal(expires_at) + "::timestamptz);\nCOMMIT;\n")


def retirement_sql():
    """Retain every provenance row and read helper; retire only one-off entrypoints."""
    return """-- Separately authorized trusted DDL session, after successful receipt verification.
BEGIN;
SET LOCAL ROLE box1902_owner;
REVOKE EXECUTE ON FUNCTION public.box_1902_apply_v2(bigint,text), public.box_1902_lock_v2(bigint)
    FROM box1902_executor;
DROP FUNCTION public.box_1902_apply_v2(bigint,text);
DROP FUNCTION public.box_1902_lock_v2(bigint);
RESET ROLE;
ALTER ROLE box1902_executor NOLOGIN;
COMMIT;
"""
