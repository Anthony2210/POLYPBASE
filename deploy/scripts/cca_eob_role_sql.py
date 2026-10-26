"""Emit reviewed CCA-only SQL; never connect or execute it.

Run with psql -X -v ON_ERROR_STOP=1 as the independently verified local postgres
administrator, after explicit approval. Provision never installs an approval.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from config import cca_eob_database_role as contract  # noqa: E402

ROLE = contract.ROLE
MARKER = contract.ROLE_MARKER


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def helper_checks(required=True, *, specs=None):
    """Check bodies and every execution-relevant metadata/ACL attribute before DDL."""
    specs = tuple(contract.helper_specs() if specs is None else specs)
    checks = []
    for signature, source, result, names in specs:
        config = "ARRAY['search_path=pg_catalog, pg_temp']::text[]"
        if signature != contract.FUNCTION:
            config = "ARRAY['search_path=pg_catalog, pg_temp','TimeZone=UTC','DateStyle=ISO, YMD']::text[]"
        argtype = "20" if signature != contract.WRITER else "25"
        checks.append(f"""
    SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure('{signature}');
    IF NOT FOUND THEN
        {"RAISE EXCEPTION 'STOP: missing helper " + signature + "';" if required else "NULL;"}
    ELSE
        IF p.prosrc IS DISTINCT FROM $body${source}$body$
            OR p.proowner <> 'polypbase'::regrole OR NOT p.prosecdef
            OR p.proconfig IS DISTINCT FROM {config}
            OR p.prolang <> (SELECT oid FROM pg_language WHERE lanname='plpgsql')
            OR p.provolatile <> 'v' OR p.proparallel <> 'u' OR p.prokind <> 'f'
            OR p.proisstrict OR p.proleakproof OR p.proretset
            OR p.prorettype <> '{result}'::regtype OR p.pronargs <> 1
            OR p.proargtypes::text <> '{argtype}' OR p.pronargdefaults <> 0
            OR p.proargnames IS DISTINCT FROM ARRAY[{','.join(literal(n) for n in names)}]::text[]
            OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
            OR p.proargdefaults IS NOT NULL OR p.probin IS NOT NULL OR p.prosqlbody IS NOT NULL
            OR p.prosupport <> 0 OR p.provariadic <> 0 OR p.protrftypes IS NOT NULL
            OR p.procost <> 100 OR p.prorows <> 0 THEN
            RAISE EXCEPTION 'STOP: unexpected helper definition/metadata: {signature}'; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
            WHERE x.privilege_type <> 'EXECUTE' OR x.is_grantable
                OR x.grantor <> p.proowner OR x.grantee NOT IN (p.proowner,'{ROLE}'::regrole))
            OR NOT has_function_privilege('{ROLE}','{signature}','EXECUTE')
            OR (SELECT count(*) FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))))<>2 THEN
            RAISE EXCEPTION 'STOP: unexpected helper ACL: {signature}'; END IF;
    END IF;
""")
    signatures = ",".join(f"to_regprocedure('{s}')" for s, *_ in specs)
    # IS DISTINCT FROM semantics are needed while some helpers are not yet installed.
    return f"""
DO $helpers$
DECLARE p pg_proc%ROWTYPE;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc candidate JOIN pg_namespace n ON n.oid=candidate.pronamespace
        WHERE n.nspname='public' AND starts_with(candidate.proname,'cca_eob_')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[{signatures}]) known(oid) WHERE known.oid=candidate.oid)) THEN
        RAISE EXCEPTION 'STOP: unrecognized CCA-EOB helper or overload'; END IF;
    {''.join(checks)}
END $helpers$;
"""


def approval_checks(required=True):
    """The exact same approval guard used inline by the production writer."""
    return "DO $approval$\n" + contract.approval_guard_sql(required=required) + "$approval$;\n"


def verification(*, specs=None):
    return ("DO $verify$\n" + contract.runtime_capability_sql() + "$verify$;\n"
            + helper_checks(specs=specs) + approval_checks())


PREFLIGHT = """
DO $target$ BEGIN
    IF current_database()<>'polypbase' OR session_user<>'postgres' OR current_user<>'postgres'
        OR current_setting('server_version_num')::integer NOT BETWEEN 180000 AND 189999
        OR inet_server_addr() IS NOT NULL OR current_setting('port') <> '5433'
        OR pg_is_in_recovery() THEN
        RAISE EXCEPTION 'STOP: expected local primary PostgreSQL 18 polypbase:5433 as postgres'; END IF;
END $target$;
"""
# Waiting DDL can block later readers. A timeout rolls the transaction back.
TIMEOUTS = ("SET LOCAL lock_timeout = '10s'; SET LOCAL statement_timeout = '30s'; "
            "SET LOCAL standard_conforming_strings = on;")


def revoke_direct_grants():
    """Revoke only the recognized runtime role's explicit table/column/sequence ACLs."""
    return f"""
DO $revoke$
DECLARE t record; a record;
BEGIN
    FOR t IN SELECT c.oid,n.nspname,c.relname,c.relkind FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>'information_schema' AND left(n.nspname,3)<>'pg_'
            AND c.relkind IN ('r','p','v','m','f','S') LOOP
        IF t.relkind='S' THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE %I.%I FROM {ROLE}',t.nspname,t.relname);
        ELSE
            EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM {ROLE}',t.nspname,t.relname);
            FOR a IN SELECT attname FROM pg_attribute WHERE attrelid=t.oid AND attnum>0 AND NOT attisdropped
                AND attacl IS NOT NULL LOOP
                EXECUTE format('REVOKE SELECT (%I), INSERT (%I), UPDATE (%I), REFERENCES (%I) ON %I.%I FROM {ROLE}',
                    a.attname,a.attname,a.attname,a.attname,t.nspname,t.relname);
            END LOOP;
        END IF;
    END LOOP;
END $revoke$;
"""


def v2_snapshot():
    """Load the offline pre-additive contract, never the mutable backend module."""
    path = Path(__file__).with_name("cca_eob_v2_contract_snapshot.py")
    fingerprint = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if fingerprint != "0aae8b7b03160fabc83112ec77438572efa12a8f1e6c3f46b878e240375b6974":
        raise ValueError("The frozen v2 contract snapshot has changed.")
    spec = importlib.util.spec_from_file_location("cca_eob_v2_contract_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def transition_v2_additive():
    """Authenticate exact v2, replace changed bodies only, preserve all pg_proc metadata."""
    old = v2_snapshot()
    before, after = tuple(old.helper_specs()), tuple(contract.helper_specs())
    if (old.ROLE, old.FUNCTION, old.READER, old.WRITER, old.APPROVAL) != (
            ROLE, contract.FUNCTION, contract.READER, contract.WRITER, contract.APPROVAL):
        raise ValueError("Transition requires unchanged role, helper and approval identities.")
    if len(before) != 3 or len(after) != 3 or before[0] != after[0]:
        raise ValueError("Transition requires the exact unchanged v2 lock helper.")
    replacements = []
    for previous, target in zip(before[1:], after[1:]):
        signature, source, result, names = target
        if (signature, result, names) != (previous[0], previous[2], previous[3]):
            raise ValueError("Transition cannot change helper signatures, arguments or return types.")
        if source != previous[1]:
            name, types = signature.split("(", 1)
            definition = (
                f"CREATE OR REPLACE FUNCTION {name}({names[0]} {types[:-1]}) RETURNS {result} "
                "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp "
                "SET TimeZone = 'UTC' SET DateStyle = 'ISO, YMD' AS " + literal(source)
            )
            replacements.append("EXECUTE " + literal(definition) + ";")
    if not replacements:
        raise ValueError("Additive helpers are not ready: refusing a no-op v2 transition.")
    signatures = ",".join(literal(s) for s, *_ in before)
    # pg_proc includes the OID, raw ACL array, owner, settings, defaults and flags.
    # Only prosrc may differ. Do not ALTER OWNER, GRANT, REVOKE, DROP or recreate.
    catalog = (
        "SELECT jsonb_agg(to_jsonb(p) - 'prosrc' ORDER BY p.oid) FROM pg_proc p "
        f"WHERE p.oid IN (SELECT to_regprocedure(s) FROM unnest(ARRAY[{signatures}]) s)"
    )
    precheck = ("DO $verify$\n" + old.runtime_capability_sql() + "$verify$;\n"
                + helper_checks(specs=before)
                + "DO $approval$\n" + old.approval_guard_sql() + "$approval$;\n")
    return f"""
BEGIN;
{TIMEOUTS}
DO $transition$
DECLARE original_metadata jsonb; final_metadata jsonb;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename='{ROLE}') THEN
        RAISE EXCEPTION 'STOP: correction login has live sessions'; END IF;
    EXECUTE {literal(precheck)};
    SELECT ({catalog}) INTO original_metadata;
    {' '.join(replacements)}
    EXECUTE {literal(verification())};
    SELECT ({catalog}) INTO final_metadata;
    IF final_metadata IS DISTINCT FROM original_metadata THEN
        RAISE EXCEPTION 'STOP: helper metadata or ACL changed during transition'; END IF;
END $transition$;
COMMIT;
"""


def body(action):
    """Reviewed statements without target preflight, for isolated-cluster proofs."""
    if action == "transition-v2-additive":
        return transition_v2_additive()
    if action == "verify":
        return f"BEGIN READ ONLY;\n{TIMEOUTS}\n{verification()}\nROLLBACK;\n"
    if action == "rollback":
        # Commit NOLOGIN before checking sessions. Never terminate a live session.
        return f"""
BEGIN;
{TIMEOUTS}
DO $stop$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE}') THEN
        IF shobj_description((SELECT oid FROM pg_roles WHERE rolname='{ROLE}'),'pg_authid') IS DISTINCT FROM '{MARKER}' THEN
            RAISE EXCEPTION 'STOP: unexpected role identity'; END IF;
        IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
            AND refobjid='{ROLE}'::regrole AND deptype='o') THEN
            RAISE EXCEPTION 'STOP: role owns objects; do not use DROP OWNED'; END IF;
        ALTER ROLE {ROLE} NOLOGIN;
    END IF;
END $stop$;
COMMIT;
BEGIN;
{TIMEOUTS}
DO $sessions$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename='{ROLE}') THEN
        RAISE EXCEPTION 'STOP: correction login has live sessions; close them deliberately and re-run'; END IF;
END $sessions$;
{helper_checks(required=False)}
{approval_checks(required=False)}
DO $drop$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE}') THEN
        IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
            AND refobjid='{ROLE}'::regrole AND deptype='o') THEN
            RAISE EXCEPTION 'STOP: role owns objects'; END IF;
        DROP OWNED BY {ROLE} RESTRICT;
    END IF;
END $drop$;
{''.join('DROP FUNCTION IF EXISTS ' + s + ';' for s, *_ in reversed(contract.helper_specs()))}
DROP TABLE IF EXISTS {contract.APPROVAL} RESTRICT;
DROP ROLE IF EXISTS {ROLE};
COMMIT;
"""
    if action != "provision":
        raise ValueError("Expected provision, verify or rollback.")
    return f"""
BEGIN;
{TIMEOUTS}
DO $role$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename='{ROLE}') THEN
        RAISE EXCEPTION 'STOP: correction login has live sessions'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE}') THEN
        CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 1 PASSWORD NULL;
        COMMENT ON ROLE {ROLE} IS '{MARKER}';
    ELSIF shobj_description((SELECT oid FROM pg_roles WHERE rolname='{ROLE}'),'pg_authid') IS DISTINCT FROM '{MARKER}' THEN
        RAISE EXCEPTION 'STOP: preexisting unrecognized role; do not overwrite';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member='{ROLE}'::regrole OR roleid='{ROLE}'::regrole) THEN
        RAISE EXCEPTION 'STOP: unexpected memberships'; END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE}' AND
        (NOT rolcanlogin OR rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication
            OR rolbypassrls OR rolinherit OR rolconnlimit<>1)) THEN
        RAISE EXCEPTION 'STOP: unexpected role attributes; do not reenable role'; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
        AND refobjid='{ROLE}'::regrole AND deptype='o') THEN
        RAISE EXCEPTION 'STOP: runtime owns objects'; END IF;
END $role$;
{helper_checks(required=False)}
{approval_checks(required=False)}
DO $create$ BEGIN
    IF to_regclass('{contract.APPROVAL}') IS NULL THEN
        CREATE TABLE {contract.APPROVAL} (
            singleton boolean PRIMARY KEY CHECK (singleton),
            plan_hash text NOT NULL, document jsonb NOT NULL, release jsonb NOT NULL,
            expires_at timestamptz NOT NULL, consumed_at timestamptz,
            final_authorization jsonb, final_authorized_at timestamptz, final_expires_at timestamptz
        );
        ALTER TABLE {contract.APPROVAL} OWNER TO postgres;
        COMMENT ON TABLE {contract.APPROVAL} IS '{contract.APPROVAL_MARKER}';
        REVOKE ALL ON {contract.APPROVAL} FROM PUBLIC;
        GRANT SELECT ON {contract.APPROVAL} TO polypbase;
        GRANT UPDATE (consumed_at) ON {contract.APPROVAL} TO polypbase;
    END IF;
END $create$;
{revoke_direct_grants()}
GRANT CONNECT ON DATABASE polypbase TO {ROLE};
{contract.grants()}
{contract.boundary_definition()}
{verification()}
COMMIT;
"""


def emit(action):
    return PREFLIGHT + body(action)


def _validate_reviewed_evidence(document, reviewed_hash, release, historical_plan_hash=None):
    """Pure document/evidence checks shared by the two administrative decisions."""
    fingerprint = hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()
    if not isinstance(reviewed_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", reviewed_hash) or fingerprint != reviewed_hash:
        raise ValueError("The administrator document must match the complete reviewed Python fingerprint.")
    if not isinstance(document, dict) or document.get("schema") != "cca_eob_production_plan_v2":
        raise ValueError("Expected cca_eob_production_plan_v2.")
    actor = document.get("actor_id")
    plan = document.get("plan", {})
    correction = plan.get("correction", {})
    if (type(actor) is not int or actor <= 0 or not isinstance(document.get("target"), dict)
            or correction.get("operation") != "cca_eob_v2_20261016_v3"
            or type(correction.get("organization_id")) is not int or correction["organization_id"] != 1
            or correction.get("v2_sha256") != contract.V2_SHA256
            or correction.get("ready") is not True or correction.get("blockers") != []
            or not isinstance(plan.get("database_snapshot"), dict) or not isinstance(plan.get("write_recipe"), dict)):
        raise ValueError("Expected a complete ready fixed-scope plan and deterministic write recipe.")
    if plan["database_snapshot"].get("historical_readings_exist") is not False:
        # Both decisions bind the independent complete-document hash, not an inner plan hash.
        contract.validate_thermal_exception(plan.get("thermal_exception"), correction, snapshot=plan["database_snapshot"])
        if (type(historical_plan_hash) is not str
                or historical_plan_hash != plan["thermal_exception"]["original_plan_hash"]):
            raise ValueError("The independently authenticated fixed historical fingerprint is required.")
    elif historical_plan_hash is not None:
        raise ValueError("A historical fingerprint is only accepted for the thermal exception.")
    contract.validate_release(release, document, reviewed_hash)


def _expiry(expires_at):
    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError("An explicit future timezone-aware expiry is required.")
    return expiry


ADMINISTRATOR = """
DO $administrator$ BEGIN
    IF session_user<>'postgres' OR current_user<>'postgres' THEN
        RAISE EXCEPTION 'Independent postgres administrator required'; END IF;
END $administrator$;
"""


def approval_sql(document, reviewed_hash, release, expires_at, *, historical_plan_hash=None):
    """Approve the reviewed plan only; never arm the mutation capability."""
    _validate_reviewed_evidence(document, reviewed_hash, release, historical_plan_hash)
    expiry = _expiry(expires_at)
    doc_sql = literal(json.dumps(document, sort_keys=True, default=str, allow_nan=False))
    release_sql = literal(json.dumps(release, sort_keys=True, default=str, allow_nan=False))
    return PREFLIGHT + f"""
BEGIN;
{TIMEOUTS}
{ADMINISTRATOR}
{verification()}
INSERT INTO {contract.APPROVAL} (singleton,plan_hash,document,release,expires_at,consumed_at)
VALUES (true,{literal(reviewed_hash)},{doc_sql}::jsonb,{release_sql}::jsonb,{literal(expiry.isoformat())}::timestamptz,NULL);
COMMIT;
"""


def final_authorization_sql(document, reviewed_hash, release, expires_at, *, historical_plan_hash=None):
    """Second administrator decision; one installation, no renewal or caller token."""
    _validate_reviewed_evidence(document, reviewed_hash, release, historical_plan_hash)
    expiry = _expiry(expires_at)
    if (expiry-datetime.now(timezone.utc)).total_seconds() > contract.FINAL_AUTHORIZATION_MAX_SECONDS:
        raise ValueError("Final apply authorization must expire within five minutes.")
    document_sql = literal(json.dumps(document, sort_keys=True, default=str, allow_nan=False))
    release_sql = literal(json.dumps(release, sort_keys=True, default=str, allow_nan=False))
    authorization_body = f"""
DECLARE changed bigint; deadline timestamptz := {literal(expiry.isoformat())}::timestamptz;
BEGIN
    UPDATE {contract.APPROVAL}
        SET final_authorization=jsonb_build_object('schema','cca_eob_final_apply_v1',
                'plan_hash',plan_hash,'document',document,'release',release),
            final_authorized_at=clock_timestamp(), final_expires_at=deadline
        WHERE singleton AND plan_hash={literal(reviewed_hash)}
            AND document={document_sql}::jsonb AND release={release_sql}::jsonb
            AND consumed_at IS NULL AND expires_at>clock_timestamp()
            AND final_authorization IS NULL AND final_authorized_at IS NULL AND final_expires_at IS NULL
            AND deadline>clock_timestamp() AND deadline<=expires_at
            AND deadline<=clock_timestamp()+interval '{contract.FINAL_AUTHORIZATION_MAX_SECONDS} seconds';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed<>1 THEN
        RAISE EXCEPTION 'Final authorization requires the exact unconsumed plan, unchanged release and fresh bounded deadline; no renewal';
    END IF;
END;
"""
    delimiter = "$authorize_apply$"
    while delimiter in authorization_body:
        delimiter = delimiter[:-1] + "_$"
    return PREFLIGHT + f"""
BEGIN;
{TIMEOUTS}
{ADMINISTRATOR}
{verification()}
DO {delimiter}{authorization_body}{delimiter};
COMMIT;
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("provision", "verify", "rollback", "transition-v2-additive", "approve", "authorize-apply"))
    parser.add_argument("--document", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--reviewed-hash")
    parser.add_argument("--historical-plan-hash")
    parser.add_argument("--expires-at")
    args = parser.parse_args()
    if args.action in ("approve", "authorize-apply"):
        if not all((args.document, args.release, args.reviewed_hash, args.expires_at)):
            parser.error("approve/authorize-apply requires --document, --release, --reviewed-hash and --expires-at")
        generator = approval_sql if args.action == "approve" else final_authorization_sql
        sql = generator(json.loads(args.document.read_text(encoding="utf-8")), args.reviewed_hash,
                           json.loads(args.release.read_text(encoding="utf-8")), args.expires_at,
                           historical_plan_hash=args.historical_plan_hash)
    else:
        if any((args.document, args.release, args.reviewed_hash, args.expires_at, args.historical_plan_hash)):
            parser.error("approval arguments are only accepted with approve or authorize-apply")
        sql = emit(args.action)
    # Exact prosrc checks require LF-only bytes on every platform.
    sys.stdout.buffer.write(sql.encode("utf-8"))
