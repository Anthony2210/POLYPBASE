"""Versioned, fixed-scope CCA-EOB SQL capabilities. No connections.

helper_specs() returns (signature, body, result_type, argument_names) tuples.
The approval document is imported and fingerprint-checked by the administrator,
not supplied by the runtime. PostgreSQL never reserializes it to compute a hash.
"""

import hashlib
import json
import re

ROLE = "polypbase_cca_eob"
SETTINGS = "config.cca_eob_production_settings"
FUNCTION = "public.cca_eob_lock_v1(bigint)"
READER = "public.cca_eob_read_v1(bigint)"
WRITER = "public.cca_eob_apply_v1(text)"
APPROVAL = "public.cca_eob_approval_v1"
APPROVAL_MARKER = "Polypbase CCA-EOB trusted approval v2"
FINAL_AUTHORIZATION_MAX_SECONDS = 300
RELEASE_KEYS = {"schema", "target", "plan_hash", "actor_id", "archive", "restoration_verified",
                "restore_report", "review_ref"}
ARCHIVE_KEYS = {"path", "sha256", "size_bytes"}
RELEASE_WHITESPACE = "".join(chr(code) for code in (
    *range(9, 14), *range(28, 33), 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B),
    0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
))
ROLE_MARKER = "Polypbase one-off CCA-EOB V3 correction executor v1"
READ_TABLES = ()
READ_COLUMNS = {"django_migrations": ("app", "name")}
V2_SHA256 = "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160"
AUDIT_COLUMNS = (
    "organization_id", "user_id", "action", "object_type", "object_id", "description",
    "metadata", "created_at", "edited_at", "edited_by_id", "original_values",
)

# This capability cannot select arbitrary tables/organizations or change data.
# All relations are qualified; no caller identifiers, SQL or search_path are used.
LOCK_BODY = """
BEGIN
    IF session_user::text <> '__CCA_ROLE__' OR actor_id IS NULL OR actor_id <= 0 THEN
        RAISE EXCEPTION 'Dedicated CCA-EOB login and explicit actor required' USING ERRCODE = '42501';
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'CCA-EOB apply requires READ COMMITTED' USING ERRCODE = '25001';
    END IF;
    PERFORM id FROM public.cultures_box
        WHERE organization_id = 1 AND id IN (1835,1836,1837) ORDER BY id FOR UPDATE;
    IF (SELECT count(*) FROM public.cultures_box WHERE organization_id = 1 AND
        (id, global_code) IN ((1835,'CCA-EOB-2.004'),(1836,'CCA-EOB-2.005'),(1837,'CCA-EOB-2.006'))) <> 3 THEN
        RAISE EXCEPTION 'CCA-EOB Box identities changed' USING ERRCODE = '23514';
    END IF;
    PERFORM id FROM public.organizations_organization WHERE id = 1 FOR NO KEY UPDATE;
    PERFORM id FROM public.accounts_organizationmembership
        WHERE organization_id = 1 AND user_id = actor_id ORDER BY id FOR UPDATE;
    PERFORM id FROM public.auth_user WHERE id = actor_id FOR NO KEY UPDATE;
    IF NOT EXISTS (SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1 AND m.is_active AND m.role = 'admin') THEN
        RAISE EXCEPTION 'Active organization 1 admin required' USING ERRCODE = '42501';
    END IF;
    PERFORM m.id FROM public.cultures_boxmovement m
        LEFT JOIN public.cultures_boxlocation origin ON origin.id = m.origin_location_id
        LEFT JOIN public.cultures_boxlocation destination ON destination.id = m.destination_location_id
        WHERE m.box_id IN (1835,1836,1837) OR origin.box_id IN (1835,1836,1837)
            OR destination.box_id IN (1835,1836,1837) ORDER BY m.id FOR UPDATE OF m;
    PERFORM id FROM public.cultures_boxlocation
        WHERE box_id IN (1835,1836,1837) ORDER BY id FOR UPDATE;
    PERFORM z.id FROM public.cultures_thermalzone z WHERE z.id IN (
        SELECT thermal_zone_id FROM public.cultures_box WHERE id IN (1835,1836,1837)
        UNION SELECT thermal_zone_id FROM public.cultures_boxlocation WHERE box_id IN (1835,1836,1837)
        UNION SELECT from_thermal_zone_id FROM public.cultures_boxmovement WHERE box_id IN (1835,1836,1837)
        UNION SELECT to_thermal_zone_id FROM public.cultures_boxmovement WHERE box_id IN (1835,1836,1837)
    ) ORDER BY z.id FOR NO KEY UPDATE;
    PERFORM id FROM public.measurements_biologicalmeasurement
        WHERE box_id IN (1835,1836,1837) ORDER BY id FOR UPDATE;
    LOCK TABLE public.measurements_biologicalmeasurement IN SHARE ROW EXCLUSIVE MODE;
    LOCK TABLE public.measurements_historicaltemperaturereading IN SHARE MODE;
END
"""


def authority_sql(role_oid):
    """Rows naming dangerous capabilities outside the reviewed table/column grants.

    role_oid is a trusted SQL expression yielding the role oid. Each row is a failure.
    """
    return f"""
SELECT 'role-level setting' AS label FROM pg_db_role_setting WHERE setrole = {role_oid}
UNION ALL
SELECT 'large-object privilege' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege({role_oid}, m.oid, 'SELECT,UPDATE')
UNION ALL
SELECT 'large-object compatibility privileges' WHERE current_setting('lo_compat_privileges') <> 'off'
UNION ALL
SELECT 'schema authority: ' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> 'information_schema' AND left(n.nspname, 3) <> 'pg_'
      AND (has_schema_privilege({role_oid}, n.oid, 'CREATE')
           OR (n.nspname <> 'public' AND has_schema_privilege({role_oid}, n.oid, 'USAGE')))
UNION ALL
SELECT 'security definer: ' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure('{FUNCTION}'),
          to_regprocedure('{READER}'),to_regprocedure('{WRITER}')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege({role_oid}, p.oid, 'EXECUTE')
"""


def validate_release(release, document, reviewed_hash):
    """Validate evidence structure/binding, never physical archive or restoration."""
    if (type(release) is not dict or set(release) != RELEASE_KEYS
            or release["schema"] != "cca_eob_release_v1"
            or type(document) is not dict or type(document.get("target")) is not dict
            or release["target"] != document["target"]
            or type(reviewed_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", reviewed_hash)
            or release["plan_hash"] != reviewed_hash
            or type(release["actor_id"]) is not int or release["actor_id"] <= 0
            or type(document.get("actor_id")) is not int or release["actor_id"] != document["actor_id"]
            or release["restoration_verified"] is not True
            or any(type(release[k]) is not str or not release[k].strip(RELEASE_WHITESPACE)
                   for k in ("restore_report", "review_ref"))):
        raise ValueError("Complete independently reviewed release bound to target, plan and actor required.")
    archive = release["archive"]
    if type(archive) is not dict or set(archive) != ARCHIVE_KEYS:
        raise ValueError("Archive path, SHA-256 and size metadata are required.")
    path = archive["path"]
    if (type(path) is not str or not path.startswith("/") or len(path) < 2
            or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(part in ("", ".", "..") for part in path[1:].split("/"))
            or type(archive["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])
            or type(archive["size_bytes"]) is not int or not 0 < archive["size_bytes"] <= 9223372036854775807):
        raise ValueError("Malformed archive path, SHA-256 or size metadata.")


def validate_thermal_exception(witness, correction):
    """Authenticate the complete historical document before extracting its V3 plan."""
    if (type(witness) is not dict
            or set(witness) != {"schema", "original_plan_hash", "original_document"}
            or witness["schema"] != "cca_eob_unlinked_72_v2"):
        raise ValueError("A protected authenticated original 72-move plan is required.")
    document = witness["original_document"]
    digest = witness["original_plan_hash"]
    if (type(document) is not dict
            or set(document) != {"schema", "target", "actor_id", "plan"}
            or document.get("schema") != "cca_eob_production_plan_v2"
            or type(document.get("target")) is not dict
            or type(document.get("actor_id")) is not int or document["actor_id"] <= 0
            or type(document.get("plan")) is not dict
            or type(document["plan"].get("correction")) is not dict
            or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest() != digest):
        raise ValueError("Historical document authentication or schema failed.")
    original = document["plan"]["correction"]
    # The historical local planner serializes empty histories as lists; the
    # production reader uses booleans. Normalize only this known representation.
    comparable = original
    if (original != correction and type(original) is dict and type(original.get("baseline")) is dict
            and original["baseline"].get("histories") == {
                "subculture_events": [], "subculture_allocations": [], "lineages": [], "transfer_items": []}):
        comparable = {**original, "baseline": {**original["baseline"], "histories": {
            "subculture_events": False, "subculture_allocations": False, "lineages": False, "transfer_items": False}}}
    if (comparable != correction
            or original.get("operation") != "cca_eob_v2_20261016_v3"
            or type(original.get("organization_id")) is not int or original["organization_id"] != 1
            or original.get("v2_sha256") != V2_SHA256
            or original.get("ready") is not True or original.get("blockers") != []):
        raise ValueError("Historical plan authentication or exact current identity failed.")
    order = original.get("move_order")
    entries = original.get("entries")
    if (type(order) is not list or len(order) != 72
            or any(type(pk) is not int or pk <= 0 for pk in order) or len(set(order)) != 72
            or type(entries) is not list
            or {e.get("measurement_pk") for e in entries if e.get("classification") == "REASSIGN_REQUIRED"} != set(order)
            or sum(e.get("classification") == "REASSIGN_REQUIRED" for e in entries) != 72):
        raise ValueError("Only the exact authenticated batch of 72 moves is eligible.")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError("Expected a simple PostgreSQL identifier.")
    return value


def lock_body(role=ROLE):
    return LOCK_BODY.replace("__CCA_ROLE__", identifier(role))


def lock_definition(role=ROLE):
    return (f"CREATE OR REPLACE FUNCTION {FUNCTION.replace('(bigint)', '(actor_id bigint)')} "
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER "
            "SET search_path = pg_catalog, pg_temp AS $cca$" + lock_body(role) + "$cca$;\n"
            f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC;\n"
            f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO {identifier(role)};\n")


def grants(role=ROLE):
    role = identifier(role)
    statements = [f"GRANT USAGE ON SCHEMA public TO {role};"]
    statements += [f"GRANT SELECT ON public.{table} TO {role};" for table in READ_TABLES]
    statements += [f"GRANT SELECT ({', '.join(columns)}) ON public.{table} TO {role};"
                   for table, columns in READ_COLUMNS.items()]

    return "\n".join(statements) + "\n"


def approval_guard_sql(role=ROLE, required=True):
    """Inline PL/pgSQL block shared by installer and writer; no SQL capability.

    Production pins postgres ownership and the polypbase function-owner grants.
    A generated custom-runtime body uses its definer as the synthetic table owner.
    This selection is made in Python, never from a caller GUC or SQL argument.
    """
    production = identifier(role) == ROLE
    owner = "'postgres'::regrole" if production else "current_user::regrole"
    issuer = "'polypbase'::regrole" if production else "current_user::regrole"
    absent = "RAISE EXCEPTION 'STOP: missing trusted approval table';" if required else "NULL;"
    return f"""
DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := {owner};
    approval_issuer oid := {issuer};
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass('{APPROVAL}');
    IF NOT FOUND THEN
        {absent}
    ELSE
        IF obj_description(approval_table.oid,'pg_class') IS DISTINCT FROM '{APPROVAL_MARKER}'
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> 'r'
            OR approval_table.relpersistence <> 'p' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION 'STOP: unrecognized approval table identity'; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM '[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]'::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>'n')
                IS DISTINCT FROM ARRAY['CHECK (singleton)','PRIMARY KEY (singleton)']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION 'STOP: approval table schema drift'; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault('r',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type='SELECT')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,'SELECT')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,'UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,'UPDATE') <> (a.attname='consumed_at')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,'INSERT,REFERENCES')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>'consumed_at'
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>'UPDATE' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,'consumed_at','UPDATE') THEN
            RAISE EXCEPTION 'STOP: approval table ACL drift'; END IF;
    END IF;
END;
"""


def runtime_capability_sql(role=ROLE):
    """Inline fixed-role PL/pgSQL authority audit, usable in a DO or function block.

    Checks the named runtime, not current_user (which is the capability owner).
    Does not grant, revoke, or install any database object.
    """
    role = identifier(role)
    production_attributes = (
        f"OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,'pg_authid') IS DISTINCT FROM '{ROLE_MARKER}'"
        if role == ROLE else ""
    )
    return f"""
DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname='{role}';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls {production_attributes} THEN
        RAISE EXCEPTION 'STOP: unexpected correction role identity/attributes'; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION 'STOP: correction role must have no memberships in either direction'; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
        AND refobjid=r.oid AND deptype='o') THEN
        RAISE EXCEPTION 'STOP: correction role owns an object'; END IF;
    IF has_database_privilege(r.oid,current_database(),'CREATE')
        OR has_schema_privilege(r.oid,'public','CREATE') THEN
        RAISE EXCEPTION 'STOP: correction role can CREATE'; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>'information_schema' AND left(n.nspname,3)<>'pg_'
            AND c.relkind IN ('r','p','v','m','f') LOOP
        IF has_table_privilege(r.oid,relation.oid,'UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN') THEN
            RAISE EXCEPTION 'STOP: broad table privilege on %', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname='public' AND relation.relname='django_migrations' AND attribute.attname IN ('app','name');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,'SELECT') <> expected THEN
                RAISE EXCEPTION 'STOP: wrong SELECT privilege on %.%',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,'UPDATE,INSERT,REFERENCES') THEN
                RAISE EXCEPTION 'STOP: direct write privilege on %.%',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>'information_schema' AND left(n.nspname,3)<>'pg_' AND c.relkind='S' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,'USAGE,SELECT,UPDATE') THEN
            RAISE EXCEPTION 'STOP: sequence privilege on %',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION 'STOP: unexpected default privileges'; END IF;
    FOR finding IN {authority_sql('r.oid')} LOOP
        RAISE EXCEPTION 'STOP: unexpected authority: %', finding.label; END LOOP;
END;
"""


def live_target_guard_sql(role=ROLE):
    """Inline writer guard; doc is the already fetched administrator document.

    SQL binds every DB-observable identity field. Machine ID and socket filesystem
    ownership remain independently root-attested by the production integration.
    """
    role = identifier(role)
    transport = """
        AND target->'configured_host'=to_jsonb('/var/run/postgresql'::text)
        AND target->'configured_port'=to_jsonb('5433'::text)
        AND current_database()='polypbase' AND current_setting('port')='5433'
        AND inet_server_addr() IS NULL AND inet_server_port() IS NULL
        AND jsonb_typeof(target->'socket_uid')='number' AND (target->>'socket_uid')::bigint>0
        AND jsonb_typeof(target->'socket_gid')='number' AND (target->>'socket_gid')::bigint>0
    """ if role == ROLE else """
        AND target->'configured_host'=to_jsonb(host(inet_server_addr()))
        AND target->'socket_uid'='null'::jsonb AND target->'socket_gid'='null'::jsonb
    """
    return f"""
DECLARE target jsonb := doc->'target'; live_identity jsonb;
BEGIN
    SELECT jsonb_build_object('database',current_database(),'database_oid',d.oid::bigint,
        'role',session_user::text,'role_oid',r.oid::bigint,
        'server_address',host(inet_server_addr()),'server_port',inet_server_port(),
        'postmaster_started_at',to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'server_version_num',current_setting('server_version_num')::integer)
        INTO STRICT live_identity FROM pg_database d,pg_roles r
        WHERE d.datname=current_database() AND r.rolname=session_user;
    IF (jsonb_typeof(target)='object'
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(target) key)=ARRAY[
            'configured_host','configured_port','database','database_oid','machine_id',
            'postmaster_started_at','role','role_oid','schema','server_address','server_port',
            'server_version_num','socket_gid','socket_uid']::text[]
        AND target->>'schema'='cca_eob_target_v2'
        AND jsonb_typeof(target->'machine_id')='string' AND target->>'machine_id' ~ '^[0-9a-f]{{32}}$'
        AND target-ARRAY['schema','machine_id','configured_host','configured_port','socket_uid','socket_gid']=live_identity
        AND target->'role'=to_jsonb('{role}'::text) AND session_user::text='{role}'
        AND target->'configured_port'=to_jsonb(current_setting('port'))
        AND current_setting('server_version_num')::integer BETWEEN 180000 AND 189999
        AND NOT pg_is_in_recovery()
        {transport}) IS NOT TRUE THEN
        RAISE EXCEPTION 'STOP: live CCA-EOB target differs from trusted approval';
    END IF;
END;
"""


def release_guard_sql():
    """Inline SQL evidence validation; does not inspect the physical backup."""
    return """
DECLARE evidence jsonb := approval.release; archive jsonb := approval.release->'archive';
BEGIN
    IF (jsonb_typeof(evidence)='object'
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(evidence) key)=ARRAY[
            'actor_id','archive','plan_hash','restoration_verified','restore_report','review_ref','schema','target']::text[]
        AND evidence->>'schema'='cca_eob_release_v1'
        AND evidence->'target'=doc->'target' AND jsonb_typeof(doc->'target')='object'
        AND evidence->>'plan_hash'=reviewed_hash
        AND evidence->'actor_id'=doc->'actor_id'
        AND jsonb_typeof(evidence->'actor_id')='number' AND evidence->>'actor_id' ~ '^[1-9][0-9]*$'
        AND evidence->'restoration_verified'='true'::jsonb
        AND jsonb_typeof(evidence->'restore_report')='string'
        AND length(btrim(evidence->>'restore_report', __CCA_RELEASE_WHITESPACE__))>0
        AND jsonb_typeof(evidence->'review_ref')='string'
        AND length(btrim(evidence->>'review_ref', __CCA_RELEASE_WHITESPACE__))>0
        AND jsonb_typeof(archive)='object'
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(archive) key)=ARRAY[
            'path','sha256','size_bytes']::text[]
        AND jsonb_typeof(archive->'path')='string' AND left(archive->>'path',1)='/'
        AND length(archive->>'path')>1 AND right(archive->>'path',1)<>'/'
        AND position('//' in archive->>'path')=0 AND position(chr(92) in archive->>'path')=0
        AND archive->>'path' !~ '[[:cntrl:]]' AND archive->>'path' !~ '(^|/)[.]{1,2}(/|$)'
        AND jsonb_typeof(archive->'sha256')='string' AND archive->>'sha256' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(archive->'size_bytes')='number' AND archive->>'size_bytes' ~ '^[1-9][0-9]*$'
        AND (archive->>'size_bytes')::numeric<=9223372036854775807) IS NOT TRUE THEN
        RAISE EXCEPTION 'Incomplete, malformed or unbound CCA-EOB archive/release evidence';
    END IF;
END;
""".replace("__CCA_RELEASE_WHITESPACE__", "||".join(f"chr({ord(char)})" for char in RELEASE_WHITESPACE))


def final_authorization_guard_sql():
    """The private administrator-installed second decision, not a caller token."""
    return f"""
BEGIN
    IF (approval.final_authorization=jsonb_build_object(
            'schema','cca_eob_final_apply_v1','plan_hash',reviewed_hash,
            'document',approval.document,'release',approval.release)
        AND approval.final_authorized_at<=clock_timestamp()
        AND approval.final_expires_at>clock_timestamp()
        AND approval.final_expires_at>approval.final_authorized_at
        AND approval.final_expires_at<=approval.expires_at
        AND approval.final_expires_at<=approval.final_authorized_at+interval '{FINAL_AUTHORIZATION_MAX_SECONDS} seconds')
        IS NOT TRUE THEN
        RAISE EXCEPTION 'Separate final CCA-EOB apply authorization missing, mismatched or expired' USING ERRCODE='42501';
    END IF;
END;
"""


READER_BODY = """
DECLARE result jsonb;
BEGIN
    IF session_user::text <> '__CCA_ROLE__' OR actor_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1
            AND m.is_active AND m.role = 'admin'
    ) THEN
        RAISE EXCEPTION 'Dedicated CCA-EOB login and active organization 1 admin required' USING ERRCODE = '42501';
    END IF;
    __CCA_RUNTIME_GUARD__
    IF (SELECT count(*) FROM public.cultures_box WHERE organization_id = 1 AND
        (id, global_code) IN ((1835,'CCA-EOB-2.004'),(1836,'CCA-EOB-2.005'),(1837,'CCA-EOB-2.006'))) <> 3 THEN
        RAISE EXCEPTION 'CCA-EOB Box identities changed' USING ERRCODE = '23514';
    END IF;
    WITH boxes AS (
        SELECT * FROM public.cultures_box WHERE organization_id = 1 AND id IN (1835,1836,1837)
    ), biology AS (
        SELECT * FROM public.measurements_biologicalmeasurement WHERE box_id IN (SELECT id FROM boxes)
    ), locations AS (
        SELECT * FROM public.cultures_boxlocation WHERE box_id IN (SELECT id FROM boxes)
    ), movements AS (
        SELECT * FROM public.cultures_boxmovement WHERE box_id IN (SELECT id FROM boxes)
    ), zone_ids AS (
        SELECT thermal_zone_id AS id FROM boxes
        UNION SELECT thermal_zone_id FROM locations
        UNION SELECT from_thermal_zone_id FROM movements
        UNION SELECT to_thermal_zone_id FROM movements
    )
    SELECT jsonb_build_object(
        'boxes', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),'[]'::jsonb) FROM boxes b),
        'measurements', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),'[]'::jsonb) FROM biology b),
        'locations', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),'[]'::jsonb) FROM locations b),
        'movements', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),'[]'::jsonb) FROM movements b),
        'external_movement_links', (SELECT coalesce(jsonb_agg(jsonb_build_object(
            'id',m.id,'box_id',m.box_id,'origin_location_id',m.origin_location_id,
            'destination_location_id',m.destination_location_id) ORDER BY m.id),'[]'::jsonb)
            FROM public.cultures_boxmovement m WHERE m.box_id NOT IN (SELECT id FROM boxes)
            AND (m.origin_location_id IN (SELECT id FROM locations)
                OR m.destination_location_id IN (SELECT id FROM locations))),
        'zones', (SELECT coalesce(jsonb_agg(jsonb_build_object('id',z.id,
            'organization_id',z.organization_id) ORDER BY z.id),'[]'::jsonb)
            FROM public.cultures_thermalzone z WHERE z.id IN (SELECT id FROM zone_ids)),
        'attribution_user_ids', (SELECT coalesce(jsonb_agg(user_id ORDER BY user_id),'[]'::jsonb)
            FROM (SELECT DISTINCT user_id FROM public.accounts_organizationmembership
                WHERE organization_id = 1 AND user_id IN (
                    SELECT original_observer_id FROM biology UNION SELECT imported_by_id FROM biology)) members),
        'histories', jsonb_build_object(
            'subculture_events', EXISTS (SELECT 1 FROM public.cultures_subcultureevent WHERE parent_box_id IN (SELECT id FROM boxes)),
            'subculture_allocations', EXISTS (SELECT 1 FROM public.cultures_subcultureallocation WHERE child_box_id IN (SELECT id FROM boxes)),
            'lineages', EXISTS (SELECT 1 FROM public.cultures_boxlineage WHERE parent_box_id IN (SELECT id FROM boxes) OR child_box_id IN (SELECT id FROM boxes)),
            'transfer_items', EXISTS (SELECT 1 FROM public.cultures_transferitem WHERE source_box_id IN (SELECT id FROM boxes))),
        'incoming_evidence', EXISTS (SELECT 1 FROM public.measurements_biologicalmeasurement
            WHERE box_id NOT IN (SELECT id FROM boxes) AND
                (duplicate_of_id IN (SELECT id FROM biology) OR environment_source_box_id IN (SELECT id FROM boxes))),
        'historical_readings_exist', EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading),
        'historical_linked_measurement_ids', (SELECT coalesce(jsonb_agg(r.biological_measurement_id
            ORDER BY r.biological_measurement_id),'[]'::jsonb)
            FROM public.measurements_historicaltemperaturereading r
            WHERE r.biological_measurement_id IN (SELECT id FROM biology)),
        'guard_state', jsonb_build_object(
            'triggers', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                'binding',to_jsonb(t),'table',c.relname,'definition',pg_get_triggerdef(t.oid),
                'function_definition',pg_get_functiondef(p.oid),'function',to_jsonb(p),
                'function_owner',pg_get_userbyid(p.proowner)) ORDER BY c.relname,t.tgname),'[]'::jsonb)
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE n.nspname='public' AND NOT t.tgisinternal AND starts_with(t.tgname,'historical_')),
            'historical_functions', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                'catalog',to_jsonb(p),'definition',pg_get_functiondef(p.oid),
                'owner',pg_get_userbyid(p.proowner)) ORDER BY p.oid),'[]'::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND starts_with(p.proname,'historical_temperature_')),
            'helpers', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                'catalog',to_jsonb(p),'definition',pg_get_functiondef(p.oid),
                'owner',pg_get_userbyid(p.proowner)) ORDER BY p.oid),'[]'::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND starts_with(p.proname,'cca_eob_')))
    ) INTO result;
    RETURN result;
END
"""


WRITER_BODY = """
DECLARE
    approval public.cca_eob_approval_v1%ROWTYPE;
    locked_approval public.cca_eob_approval_v1%ROWTYPE;
    doc jsonb; correction jsonb; recipe jsonb; state jsonb; entry jsonb; move jsonb;
    revision jsonb; audit jsonb; raw jsonb; reviewed_raw jsonb; before_source jsonb;
    historical_plan jsonb;
    actor bigint; pk bigint; box_pk bigint;
    seen bigint[] := ARRAY[]::bigint[]; revised bigint[] := ARRAY[]::bigint[];
    touched bigint; expected_after bigint; changed bigint; move_count integer; i integer;
BEGIN
    IF session_user::text <> '__CCA_ROLE__' OR reviewed_hash IS NULL
        OR reviewed_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Dedicated CCA-EOB login and reviewed hash required' USING ERRCODE = '42501';
    END IF;
    __CCA_APPROVAL_GUARD__
    __CCA_RUNTIME_GUARD__
    IF current_setting('session_replication_role') <> 'origin'
        OR current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'CCA-EOB requires origin and READ COMMITTED' USING ERRCODE = '25001';
    END IF;
    SELECT * INTO STRICT approval FROM public.cca_eob_approval_v1
        WHERE singleton AND plan_hash=reviewed_hash AND consumed_at IS NULL AND expires_at>clock_timestamp();
    __CCA_FINAL_AUTHORIZATION_GUARD__
    actor := (approval.document->>'actor_id')::bigint;
    PERFORM public.cca_eob_lock_v1(actor);
    SELECT * INTO STRICT locked_approval FROM public.cca_eob_approval_v1 WHERE singleton FOR UPDATE;
    IF to_jsonb(approval) IS DISTINCT FROM to_jsonb(locked_approval)
        OR locked_approval.consumed_at IS NOT NULL OR locked_approval.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'CCA-EOB approval changed, expired or consumed';
    END IF;
    __CCA_FINAL_AUTHORIZATION_GUARD__
    doc := approval.document;
    __CCA_RELEASE_GUARD__
    correction := doc#>'{plan,correction}';
    recipe := doc#>'{plan,write_recipe}';
    IF (doc->>'schema'='cca_eob_production_plan_v2'
        AND correction->>'operation'='cca_eob_v2_20261016_v3'
        AND correction->'organization_id'='1'::jsonb
        AND correction->>'v2_sha256'='__CCA_SHA__'
        AND correction->'ready'='true'::jsonb AND correction->'blockers'='[]'::jsonb
        AND approval.release->>'schema'='cca_eob_release_v1'
        AND approval.release->'target'=doc->'target'
        AND jsonb_typeof(doc->'target')='object'
        AND approval.release->>'plan_hash'=reviewed_hash
        AND approval.release->'actor_id'=doc->'actor_id'
        AND approval.release->'restoration_verified'='true'::jsonb
        AND jsonb_typeof(approval.release->'review_ref')='string'
        AND length(btrim(approval.release->>'review_ref'))>0
        AND jsonb_typeof(approval.release->'restore_report')='string'
        AND length(btrim(approval.release->>'restore_report'))>0
        AND jsonb_typeof(recipe)='object') IS NOT TRUE THEN
        RAISE EXCEPTION 'Incomplete or unbound trusted CCA-EOB approval';
    END IF;
    __CCA_TARGET_GUARD__
    __CCA_APPROVAL_GUARD__
    state := public.cca_eob_read_v1(actor);
    IF state IS DISTINCT FROM doc#>'{plan,database_snapshot}'
        OR state->'incoming_evidence' <> 'false'::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_each(state->'histories') h WHERE h.value <> 'false'::jsonb)
        OR state->'external_movement_links' <> '[]'::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->'boxes') b
            WHERE b->'merged_into_id' IS DISTINCT FROM 'null'::jsonb
                OR b->>'previous_global_code' IS DISTINCT FROM '')
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->'measurements') b
            WHERE b->'duplicate_of_id' IS DISTINCT FROM 'null'::jsonb
                OR b->'environment_source_box_id' IS DISTINCT FROM 'null'::jsonb) THEN
        RAISE EXCEPTION 'Stale or blocked CCA-EOB snapshot';
    END IF;
    IF (jsonb_typeof(correction->'entries')='array'
        AND jsonb_typeof(correction->'move_order')='array'
        AND jsonb_typeof(correction->'revision_changes')='array'
        AND jsonb_typeof(recipe->'moves')='array'
        AND jsonb_typeof(recipe->'revisions')='array'
        AND jsonb_typeof(recipe->'audits')='array') IS NOT TRUE THEN
        RAISE EXCEPTION 'Malformed trusted write recipe';
    END IF;
    move_count := jsonb_array_length(correction->'move_order');
    IF move_count=0 OR jsonb_array_length(recipe->'moves')<>move_count
        OR jsonb_array_length(recipe->'audits')<>move_count+1
        OR recipe->'revisions' IS DISTINCT FROM correction->'revision_changes'
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->'entries') e
            WHERE coalesce(e->>'classification','') NOT IN ('REASSIGN_REQUIRED','ALREADY_MATCHES','OUT_OF_SOURCE')
                OR e->>'measurement_pk' IS NULL)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->'entries') e
            GROUP BY (e->>'measurement_pk')::bigint HAVING count(*)<>1)
        OR (SELECT count(*) FROM jsonb_array_elements(correction->'entries') e
            WHERE e->>'classification'='REASSIGN_REQUIRED')<>move_count THEN
        RAISE EXCEPTION 'CCA-EOB recipe cardinalities differ';
    END IF;
    -- The administrator authenticates the complete historical document's Python
    -- fingerprint offline; SQL never reserializes JSONB as that historical identity.
    -- SQL independently binds the immutable original evidence and exact 72 moves.
    IF state->'historical_readings_exist' IS DISTINCT FROM 'false'::jsonb THEN
        historical_plan := doc#>'{plan,thermal_exception,original_document,plan,correction}';
        IF historical_plan#>'{baseline,histories}' =
            '{"subculture_events":[],"subculture_allocations":[],"lineages":[],"transfer_items":[]}'::jsonb THEN
            historical_plan := jsonb_set(historical_plan,'{baseline,histories}',state->'histories');
        END IF;
        IF (move_count=72
            AND doc#>>'{plan,thermal_exception,schema}'='cca_eob_unlinked_72_v2'
            AND doc#>>'{plan,thermal_exception,original_plan_hash}' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(doc#>'{plan,thermal_exception,original_document}')='object'
            AND doc#>>'{plan,thermal_exception,original_document,schema}'='cca_eob_production_plan_v2'
            AND jsonb_typeof(doc#>'{plan,thermal_exception,original_document,plan}')='object'
            AND jsonb_typeof(historical_plan)='object'
            AND historical_plan=correction) IS NOT TRUE THEN
            RAISE EXCEPTION 'Authenticated original 72-move thermal exception required';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading r
        JOIN jsonb_array_elements(recipe->'moves') m ON r.biological_measurement_id=(m->>'id')::bigint) THEN
        RAISE EXCEPTION 'A planned measurement has a historical temperature association';
    END IF;
    -- The lock helper holds SHARE on readings until commit: no phantom link can appear.
    -- Validate every raw baseline before the first update. JSON null and zero differ.
    FOR i IN 0..move_count-1 LOOP
        pk := (correction->'move_order'->>i)::bigint;
        IF pk IS NULL OR pk=ANY(seen) THEN RAISE EXCEPTION 'Duplicate or missing move ID'; END IF;
        seen := array_append(seen,pk);
        SELECT e INTO STRICT entry FROM jsonb_array_elements(correction->'entries') e
            WHERE (e->>'measurement_pk')::bigint=pk AND e->>'classification'='REASSIGN_REQUIRED';
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->'measurements') b WHERE (b->>'id')::bigint=pk;
        SELECT b INTO STRICT reviewed_raw FROM jsonb_array_elements(doc#>'{plan,database_snapshot,measurements}') b
            WHERE (b->>'id')::bigint=pk;
        before_source := entry->'before';
        move := recipe->'moves'->i;
        -- Raw equality protects every column. Python's normalized before uses
        -- different timestamp/decimal encodings, so compare its source keys by type.
        IF (raw=reviewed_raw
            AND before_source ?& ARRAY['id','box_id','measured_on','week_start',
                'polyp_count','ephyrae_count','strobila_count']
            AND (before_source->>'id')::bigint=pk
            AND (before_source->>'box_id')::bigint=(raw->>'box_id')::bigint
            AND (before_source->>'measured_on')::date=(raw->>'measured_on')::date
            AND (before_source->>'week_start')::date=(raw->>'week_start')::date
            AND (before_source->>'polyp_count')::bigint IS NOT DISTINCT FROM (raw->>'polyp_count')::bigint
            AND (before_source->>'ephyrae_count')::bigint IS NOT DISTINCT FROM (raw->>'ephyrae_count')::bigint
            AND (before_source->>'strobila_count')::bigint IS NOT DISTINCT FROM (raw->>'strobila_count')::bigint
            AND (raw->>'box_id')::bigint IN (1835,1836,1837)
            AND (entry->>'target_box_pk')::bigint IN (1835,1836)
            AND entry->>'target_box'=CASE (entry->>'target_box_pk')::bigint
                WHEN 1835 THEN 'CCA-EOB-2.004' WHEN 1836 THEN 'CCA-EOB-2.005' END
            AND entry->'target_code'=entry->'target_box'
            AND entry->'target_box_pk'<>raw->'box_id'
            AND raw->'duplicate_of_id'='null'::jsonb
            AND raw->'environment_source_box_id'='null'::jsonb
            AND extract(isoyear FROM (raw->>'measured_on')::date)=2026
            AND date_trunc('week',(raw->>'measured_on')::date)::date=(raw->>'week_start')::date
            AND (entry->>'iso_year')::integer=2026
            AND (entry->>'iso_week')::integer=extract(week FROM (raw->>'measured_on')::date)
            AND entry->'source_value'=jsonb_build_array(raw->'polyp_count',raw->'ephyrae_count')
            AND move=jsonb_build_object('id',pk,'before_box_id',raw->'box_id',
                'target_box_id',entry->'target_box_pk','week_start',raw->'week_start')) IS NOT TRUE THEN
            RAISE EXCEPTION 'CCA-EOB move does not match approved raw baseline';
        END IF;
    END LOOP;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->'revisions') LOOP
        box_pk := (revision->>'box_id')::bigint;
        IF box_pk IS NULL OR box_pk NOT IN (1835,1836,1837) OR box_pk=ANY(revised) THEN
            RAISE EXCEPTION 'Invalid or duplicate revision box'; END IF;
        revised := array_append(revised,box_pk);
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->'boxes') b WHERE (b->>'id')::bigint=box_pk;
        SELECT count(*) INTO touched FROM jsonb_array_elements(recipe->'moves') m
            WHERE (m->>'before_box_id')::bigint=box_pk OR (m->>'target_box_id')::bigint=box_pk;
        SELECT greatest((raw->>'polyp_state_revision')::bigint+touched,
            coalesce(max((b->>'polyp_state_sequence')::bigint),0)) INTO expected_after
            FROM jsonb_array_elements(state->'measurements') b
            WHERE coalesce((SELECT (m->>'target_box_id')::bigint FROM jsonb_array_elements(recipe->'moves') m
                WHERE m->'id'=b->'id'),(b->>'box_id')::bigint)=box_pk;
        IF (touched>0 AND revision->'before'=raw->'polyp_state_revision'
            AND (revision->>'after')::bigint=expected_after) IS NOT TRUE THEN
            RAISE EXCEPTION 'CCA-EOB revision differs from touched count and final sequences'; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(recipe->'moves') m
        WHERE NOT ((m->>'before_box_id')::bigint=ANY(revised))
            OR NOT ((m->>'target_box_id')::bigint=ANY(revised))) THEN
        RAISE EXCEPTION 'Missing touched revision'; END IF;
    FOR i IN 0..move_count LOOP
        audit := recipe->'audits'->i;
        IF (audit->'organization_id'='1'::jsonb AND audit->'user_id'=doc->'actor_id'
            AND jsonb_typeof(audit->'metadata')='object'
            AND NOT (audit->'metadata' ? 'execution_evidence')
            AND jsonb_typeof(audit->'description')='string'
            AND audit#>>'{metadata,operation}'='cca_eob_v2_20261016_v3') IS NOT TRUE THEN
            RAISE EXCEPTION 'Invalid trusted audit identity or metadata'; END IF;
        IF i<move_count THEN
            move := recipe->'moves'->i;
            SELECT b INTO STRICT raw FROM jsonb_array_elements(state->'boxes') b
                WHERE b->'id'=move->'target_box_id';
            IF (audit->>'action'='update' AND audit->>'object_type'='box'
                AND audit->'object_id'=raw->'global_code'
                AND audit#>'{metadata,measurement_id}'=move->'id') IS NOT TRUE THEN
                RAISE EXCEPTION 'Invalid measurement audit target'; END IF;
        ELSIF (audit->>'action'='import' AND audit->>'object_type'='historical_correction'
            AND audit->>'object_id'='cca_eob_v2_20261016_v3') IS NOT TRUE THEN
            RAISE EXCEPTION 'Invalid correction summary audit';
        END IF;
    END LOOP;
    FOR move IN SELECT value FROM jsonb_array_elements(recipe->'moves') LOOP
        UPDATE public.measurements_biologicalmeasurement SET box_id=(move->>'target_box_id')::bigint
            WHERE id=(move->>'id')::bigint AND box_id=(move->>'before_box_id')::bigint
                AND week_start=(move->>'week_start')::date;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION 'CCA-EOB measurement update cardinality'; END IF;
    END LOOP;
    -- Recheck all scoped biology columns, not just the moved Box IDs.
    FOR raw IN SELECT value FROM jsonb_array_elements(state->'measurements') LOOP
        before_source := raw;
        SELECT m INTO move FROM jsonb_array_elements(recipe->'moves') m WHERE m->'id'=raw->'id';
        IF FOUND THEN before_source := jsonb_set(raw,'{box_id}',move->'target_box_id'); END IF;
        IF (SELECT to_jsonb(b) FROM public.measurements_biologicalmeasurement b
            WHERE b.id=(raw->>'id')::bigint) IS DISTINCT FROM before_source THEN
            RAISE EXCEPTION 'CCA-EOB post-write biological integrity failure';
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading WHERE biological_measurement_id=ANY(seen)) THEN
        RAISE EXCEPTION 'CCA-EOB post-write thermal integrity failure';
    END IF;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->'revisions') LOOP
        UPDATE public.cultures_box SET polyp_state_revision=(revision->>'after')::bigint
            WHERE id=(revision->>'box_id')::bigint AND organization_id=1
                AND polyp_state_revision=(revision->>'before')::bigint;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION 'CCA-EOB revision update cardinality'; END IF;
    END LOOP;
    FOR audit IN SELECT value FROM jsonb_array_elements(recipe->'audits') LOOP
        -- historical_plan_hash retains the complete historical document's identity.
        -- Match the ORM default for a new audit with no edit history.
        INSERT INTO public.audit_auditlog (organization_id,user_id,action,object_type,object_id,
            description,metadata,created_at,edited_at,edited_by_id,original_values)
        VALUES (1,actor,audit->>'action',audit->>'object_type',audit->>'object_id',audit->>'description',
            (audit->'metadata') || jsonb_build_object('execution_evidence',jsonb_build_object(
                'target',doc->'target','production_plan_hash',reviewed_hash,
                'authorization','APPLY_CCA_EOB_ORGANIZATION_1:'||reviewed_hash,'release',approval.release,
                'final_authorized_at',approval.final_authorized_at,'final_expires_at',approval.final_expires_at,
                'historical_plan_hash',doc#>'{plan,thermal_exception,original_plan_hash}')),
            clock_timestamp(),NULL,NULL,'{}'::jsonb);
    END LOOP;
    UPDATE public.cca_eob_approval_v1 SET consumed_at=clock_timestamp()
        WHERE singleton AND consumed_at IS NULL AND expires_at>clock_timestamp()
            AND final_expires_at>clock_timestamp();
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION 'CCA-EOB approval expired during apply (plan or final authorization)'; END IF;
END
"""


def reader_body(role=ROLE):
    return (READER_BODY.replace("__CCA_ROLE__", identifier(role))
            .replace("__CCA_RUNTIME_GUARD__", runtime_capability_sql(role)))


def writer_body(role=ROLE):
    return (WRITER_BODY.replace("__CCA_ROLE__", identifier(role)).replace("__CCA_SHA__", V2_SHA256)
            .replace("__CCA_APPROVAL_GUARD__", approval_guard_sql(role=role))
            .replace("__CCA_RUNTIME_GUARD__", runtime_capability_sql(role))
            .replace("__CCA_TARGET_GUARD__", live_target_guard_sql(role))
                        .replace("__CCA_RELEASE_GUARD__", release_guard_sql())
                        .replace("__CCA_FINAL_AUTHORIZATION_GUARD__", final_authorization_guard_sql()))


def helper_specs(role=ROLE):
    """Stable tuples: SQL signature, exact prosrc, return SQL type, arg names."""
    return (
        (FUNCTION, lock_body(role), "void", ("actor_id",)),
        (READER, reader_body(role), "jsonb", ("actor_id",)),
        (WRITER, writer_body(role), "void", ("reviewed_hash",)),
    )


def _definition(signature, body, result, argument, role):
    types = signature.split("(", 1)[1][:-1]
    name = signature.split("(", 1)[0]
    return (f"CREATE OR REPLACE FUNCTION {name}({argument} {types}) RETURNS {result} "
            "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp "
            "SET TimeZone = 'UTC' SET DateStyle = 'ISO, YMD' AS $cca$" + body + "$cca$;\n"
            f"ALTER FUNCTION {signature} OWNER TO polypbase;\n"
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;\n"
            f"GRANT EXECUTE ON FUNCTION {signature} TO {identifier(role)};\n")


def reader_definition(role=ROLE):
    return _definition(READER, reader_body(role), "jsonb", "actor_id", role)


def writer_definition(role=ROLE):
    return _definition(WRITER, writer_body(role), "void", "reviewed_hash", role)


def boundary_definition(role=ROLE):
    return (lock_definition(role) + f"ALTER FUNCTION {FUNCTION} OWNER TO polypbase;\n"
            + reader_definition(role) + writer_definition(role))
