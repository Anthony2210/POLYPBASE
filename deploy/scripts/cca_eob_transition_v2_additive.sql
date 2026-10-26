
DO $target$ BEGIN
    IF current_database()<>'polypbase' OR session_user<>'postgres' OR current_user<>'postgres'
        OR current_setting('server_version_num')::integer NOT BETWEEN 180000 AND 189999
        OR inet_server_addr() IS NOT NULL OR current_setting('port') <> '5433'
        OR pg_is_in_recovery() THEN
        RAISE EXCEPTION 'STOP: expected local primary PostgreSQL 18 polypbase:5433 as postgres'; END IF;
END $target$;

BEGIN;
SET LOCAL lock_timeout = '10s'; SET LOCAL statement_timeout = '30s'; SET LOCAL standard_conforming_strings = on;
DO $transition$
DECLARE original_metadata jsonb; final_metadata jsonb;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename='polypbase_cca_eob') THEN
        RAISE EXCEPTION 'STOP: correction login has live sessions'; END IF;
    EXECUTE 'DO $verify$

DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''polypbase_cca_eob'';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''pg_authid'') IS DISTINCT FROM ''Polypbase one-off CCA-EOB V3 correction executor v1'' THEN
        RAISE EXCEPTION ''STOP: unexpected correction role identity/attributes''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''STOP: correction role must have no memberships in either direction''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''pg_authid''::regclass
        AND refobjid=r.oid AND deptype=''o'') THEN
        RAISE EXCEPTION ''STOP: correction role owns an object''; END IF;
    IF has_database_privilege(r.oid,current_database(),''CREATE'')
        OR has_schema_privilege(r.oid,''public'',''CREATE'') THEN
        RAISE EXCEPTION ''STOP: correction role can CREATE''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_''
            AND c.relkind IN (''r'',''p'',''v'',''m'',''f'') LOOP
        IF has_table_privilege(r.oid,relation.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'') THEN
            RAISE EXCEPTION ''STOP: broad table privilege on %'', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''public'' AND relation.relname=''django_migrations'' AND attribute.attname IN (''app'',''name'');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''SELECT'') <> expected THEN
                RAISE EXCEPTION ''STOP: wrong SELECT privilege on %.%'',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''UPDATE,INSERT,REFERENCES'') THEN
                RAISE EXCEPTION ''STOP: direct write privilege on %.%'',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_'' AND c.relkind=''S'' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''USAGE,SELECT,UPDATE'') THEN
            RAISE EXCEPTION ''STOP: sequence privilege on %'',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''STOP: unexpected default privileges''; END IF;
    FOR finding IN 
SELECT ''role-level setting'' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''large-object privilege'' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''SELECT,UPDATE'')
UNION ALL
SELECT ''large-object compatibility privileges'' WHERE current_setting(''lo_compat_privileges'') <> ''off''
UNION ALL
SELECT ''schema authority: '' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''information_schema'' AND left(n.nspname, 3) <> ''pg_''
      AND (has_schema_privilege(r.oid, n.oid, ''CREATE'')
           OR (n.nspname <> ''public'' AND has_schema_privilege(r.oid, n.oid, ''USAGE'')))
UNION ALL
SELECT ''security definer: '' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''pg_catalog'', ''information_schema'')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),
          to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''EXECUTE'')
 LOOP
        RAISE EXCEPTION ''STOP: unexpected authority: %'', finding.label; END LOOP;
END;
$verify$;

DO $helpers$
DECLARE p pg_proc%ROWTYPE;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc candidate JOIN pg_namespace n ON n.oid=candidate.pronamespace
        WHERE n.nspname=''public'' AND starts_with(candidate.proname,''cca_eob_'')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=candidate.oid)) THEN
        RAISE EXCEPTION ''STOP: unrecognized CCA-EOB helper or overload''; END IF;
    
    SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(''public.cca_eob_lock_v1(bigint)'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing helper public.cca_eob_lock_v1(bigint)'';
    ELSE
        IF p.prosrc IS DISTINCT FROM $body$
BEGIN
    IF session_user::text <> ''polypbase_cca_eob'' OR actor_id IS NULL OR actor_id <= 0 THEN
        RAISE EXCEPTION ''Dedicated CCA-EOB login and explicit actor required'' USING ERRCODE = ''42501'';
    END IF;
    IF current_setting(''transaction_isolation'') <> ''read committed'' THEN
        RAISE EXCEPTION ''CCA-EOB apply requires READ COMMITTED'' USING ERRCODE = ''25001'';
    END IF;
    PERFORM id FROM public.cultures_box
        WHERE organization_id = 1 AND id IN (1835,1836,1837) ORDER BY id FOR UPDATE;
    IF (SELECT count(*) FROM public.cultures_box WHERE organization_id = 1 AND
        (id, global_code) IN ((1835,''CCA-EOB-2.004''),(1836,''CCA-EOB-2.005''),(1837,''CCA-EOB-2.006''))) <> 3 THEN
        RAISE EXCEPTION ''CCA-EOB Box identities changed'' USING ERRCODE = ''23514'';
    END IF;
    PERFORM id FROM public.organizations_organization WHERE id = 1 FOR NO KEY UPDATE;
    PERFORM id FROM public.accounts_organizationmembership
        WHERE organization_id = 1 AND user_id = actor_id ORDER BY id FOR UPDATE;
    PERFORM id FROM public.auth_user WHERE id = actor_id FOR NO KEY UPDATE;
    IF NOT EXISTS (SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1 AND m.is_active AND m.role = ''admin'') THEN
        RAISE EXCEPTION ''Active organization 1 admin required'' USING ERRCODE = ''42501'';
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
$body$
            OR p.proowner <> ''polypbase''::regrole OR NOT p.prosecdef
            OR p.proconfig IS DISTINCT FROM ARRAY[''search_path=pg_catalog, pg_temp'']::text[]
            OR p.prolang <> (SELECT oid FROM pg_language WHERE lanname=''plpgsql'')
            OR p.provolatile <> ''v'' OR p.proparallel <> ''u'' OR p.prokind <> ''f''
            OR p.proisstrict OR p.proleakproof OR p.proretset
            OR p.prorettype <> ''void''::regtype OR p.pronargs <> 1
            OR p.proargtypes::text <> ''20'' OR p.pronargdefaults <> 0
            OR p.proargnames IS DISTINCT FROM ARRAY[''actor_id'']::text[]
            OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
            OR p.proargdefaults IS NOT NULL OR p.probin IS NOT NULL OR p.prosqlbody IS NOT NULL
            OR p.prosupport <> 0 OR p.provariadic <> 0 OR p.protrftypes IS NOT NULL
            OR p.procost <> 100 OR p.prorows <> 0 THEN
            RAISE EXCEPTION ''STOP: unexpected helper definition/metadata: public.cca_eob_lock_v1(bigint)''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))) x
            WHERE x.privilege_type <> ''EXECUTE'' OR x.is_grantable
                OR x.grantor <> p.proowner OR x.grantee NOT IN (p.proowner,''polypbase_cca_eob''::regrole))
            OR NOT has_function_privilege(''polypbase_cca_eob'',''public.cca_eob_lock_v1(bigint)'',''EXECUTE'')
            OR (SELECT count(*) FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))))<>2 THEN
            RAISE EXCEPTION ''STOP: unexpected helper ACL: public.cca_eob_lock_v1(bigint)''; END IF;
    END IF;

    SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(''public.cca_eob_read_v1(bigint)'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing helper public.cca_eob_read_v1(bigint)'';
    ELSE
        IF p.prosrc IS DISTINCT FROM $body$
DECLARE result jsonb;
BEGIN
    IF session_user::text <> ''polypbase_cca_eob'' OR actor_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1
            AND m.is_active AND m.role = ''admin''
    ) THEN
        RAISE EXCEPTION ''Dedicated CCA-EOB login and active organization 1 admin required'' USING ERRCODE = ''42501'';
    END IF;
    
DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''polypbase_cca_eob'';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''pg_authid'') IS DISTINCT FROM ''Polypbase one-off CCA-EOB V3 correction executor v1'' THEN
        RAISE EXCEPTION ''STOP: unexpected correction role identity/attributes''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''STOP: correction role must have no memberships in either direction''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''pg_authid''::regclass
        AND refobjid=r.oid AND deptype=''o'') THEN
        RAISE EXCEPTION ''STOP: correction role owns an object''; END IF;
    IF has_database_privilege(r.oid,current_database(),''CREATE'')
        OR has_schema_privilege(r.oid,''public'',''CREATE'') THEN
        RAISE EXCEPTION ''STOP: correction role can CREATE''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_''
            AND c.relkind IN (''r'',''p'',''v'',''m'',''f'') LOOP
        IF has_table_privilege(r.oid,relation.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'') THEN
            RAISE EXCEPTION ''STOP: broad table privilege on %'', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''public'' AND relation.relname=''django_migrations'' AND attribute.attname IN (''app'',''name'');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''SELECT'') <> expected THEN
                RAISE EXCEPTION ''STOP: wrong SELECT privilege on %.%'',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''UPDATE,INSERT,REFERENCES'') THEN
                RAISE EXCEPTION ''STOP: direct write privilege on %.%'',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_'' AND c.relkind=''S'' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''USAGE,SELECT,UPDATE'') THEN
            RAISE EXCEPTION ''STOP: sequence privilege on %'',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''STOP: unexpected default privileges''; END IF;
    FOR finding IN 
SELECT ''role-level setting'' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''large-object privilege'' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''SELECT,UPDATE'')
UNION ALL
SELECT ''large-object compatibility privileges'' WHERE current_setting(''lo_compat_privileges'') <> ''off''
UNION ALL
SELECT ''schema authority: '' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''information_schema'' AND left(n.nspname, 3) <> ''pg_''
      AND (has_schema_privilege(r.oid, n.oid, ''CREATE'')
           OR (n.nspname <> ''public'' AND has_schema_privilege(r.oid, n.oid, ''USAGE'')))
UNION ALL
SELECT ''security definer: '' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''pg_catalog'', ''information_schema'')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),
          to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''EXECUTE'')
 LOOP
        RAISE EXCEPTION ''STOP: unexpected authority: %'', finding.label; END LOOP;
END;

    IF (SELECT count(*) FROM public.cultures_box WHERE organization_id = 1 AND
        (id, global_code) IN ((1835,''CCA-EOB-2.004''),(1836,''CCA-EOB-2.005''),(1837,''CCA-EOB-2.006''))) <> 3 THEN
        RAISE EXCEPTION ''CCA-EOB Box identities changed'' USING ERRCODE = ''23514'';
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
        ''boxes'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM boxes b),
        ''measurements'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM biology b),
        ''locations'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM locations b),
        ''movements'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM movements b),
        ''external_movement_links'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
            ''id'',m.id,''box_id'',m.box_id,''origin_location_id'',m.origin_location_id,
            ''destination_location_id'',m.destination_location_id) ORDER BY m.id),''[]''::jsonb)
            FROM public.cultures_boxmovement m WHERE m.box_id NOT IN (SELECT id FROM boxes)
            AND (m.origin_location_id IN (SELECT id FROM locations)
                OR m.destination_location_id IN (SELECT id FROM locations))),
        ''zones'', (SELECT coalesce(jsonb_agg(jsonb_build_object(''id'',z.id,
            ''organization_id'',z.organization_id) ORDER BY z.id),''[]''::jsonb)
            FROM public.cultures_thermalzone z WHERE z.id IN (SELECT id FROM zone_ids)),
        ''attribution_user_ids'', (SELECT coalesce(jsonb_agg(user_id ORDER BY user_id),''[]''::jsonb)
            FROM (SELECT DISTINCT user_id FROM public.accounts_organizationmembership
                WHERE organization_id = 1 AND user_id IN (
                    SELECT original_observer_id FROM biology UNION SELECT imported_by_id FROM biology)) members),
        ''histories'', jsonb_build_object(
            ''subculture_events'', EXISTS (SELECT 1 FROM public.cultures_subcultureevent WHERE parent_box_id IN (SELECT id FROM boxes)),
            ''subculture_allocations'', EXISTS (SELECT 1 FROM public.cultures_subcultureallocation WHERE child_box_id IN (SELECT id FROM boxes)),
            ''lineages'', EXISTS (SELECT 1 FROM public.cultures_boxlineage WHERE parent_box_id IN (SELECT id FROM boxes) OR child_box_id IN (SELECT id FROM boxes)),
            ''transfer_items'', EXISTS (SELECT 1 FROM public.cultures_transferitem WHERE source_box_id IN (SELECT id FROM boxes))),
        ''incoming_evidence'', EXISTS (SELECT 1 FROM public.measurements_biologicalmeasurement
            WHERE box_id NOT IN (SELECT id FROM boxes) AND
                (duplicate_of_id IN (SELECT id FROM biology) OR environment_source_box_id IN (SELECT id FROM boxes))),
        ''historical_readings_exist'', EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading),
        ''historical_linked_measurement_ids'', (SELECT coalesce(jsonb_agg(r.biological_measurement_id
            ORDER BY r.biological_measurement_id),''[]''::jsonb)
            FROM public.measurements_historicaltemperaturereading r
            WHERE r.biological_measurement_id IN (SELECT id FROM biology)),
        ''guard_state'', jsonb_build_object(
            ''triggers'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''binding'',to_jsonb(t),''table'',c.relname,''definition'',pg_get_triggerdef(t.oid),
                ''function_definition'',pg_get_functiondef(p.oid),''function'',to_jsonb(p),
                ''function_owner'',pg_get_userbyid(p.proowner)) ORDER BY c.relname,t.tgname),''[]''::jsonb)
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE n.nspname=''public'' AND NOT t.tgisinternal AND starts_with(t.tgname,''historical_'')),
            ''historical_functions'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''catalog'',to_jsonb(p),''definition'',pg_get_functiondef(p.oid),
                ''owner'',pg_get_userbyid(p.proowner)) ORDER BY p.oid),''[]''::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=''public'' AND starts_with(p.proname,''historical_temperature_'')),
            ''helpers'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''catalog'',to_jsonb(p),''definition'',pg_get_functiondef(p.oid),
                ''owner'',pg_get_userbyid(p.proowner)) ORDER BY p.oid),''[]''::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=''public'' AND starts_with(p.proname,''cca_eob_'')))
    ) INTO result;
    RETURN result;
END
$body$
            OR p.proowner <> ''polypbase''::regrole OR NOT p.prosecdef
            OR p.proconfig IS DISTINCT FROM ARRAY[''search_path=pg_catalog, pg_temp'',''TimeZone=UTC'',''DateStyle=ISO, YMD'']::text[]
            OR p.prolang <> (SELECT oid FROM pg_language WHERE lanname=''plpgsql'')
            OR p.provolatile <> ''v'' OR p.proparallel <> ''u'' OR p.prokind <> ''f''
            OR p.proisstrict OR p.proleakproof OR p.proretset
            OR p.prorettype <> ''jsonb''::regtype OR p.pronargs <> 1
            OR p.proargtypes::text <> ''20'' OR p.pronargdefaults <> 0
            OR p.proargnames IS DISTINCT FROM ARRAY[''actor_id'']::text[]
            OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
            OR p.proargdefaults IS NOT NULL OR p.probin IS NOT NULL OR p.prosqlbody IS NOT NULL
            OR p.prosupport <> 0 OR p.provariadic <> 0 OR p.protrftypes IS NOT NULL
            OR p.procost <> 100 OR p.prorows <> 0 THEN
            RAISE EXCEPTION ''STOP: unexpected helper definition/metadata: public.cca_eob_read_v1(bigint)''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))) x
            WHERE x.privilege_type <> ''EXECUTE'' OR x.is_grantable
                OR x.grantor <> p.proowner OR x.grantee NOT IN (p.proowner,''polypbase_cca_eob''::regrole))
            OR NOT has_function_privilege(''polypbase_cca_eob'',''public.cca_eob_read_v1(bigint)'',''EXECUTE'')
            OR (SELECT count(*) FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))))<>2 THEN
            RAISE EXCEPTION ''STOP: unexpected helper ACL: public.cca_eob_read_v1(bigint)''; END IF;
    END IF;

    SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(''public.cca_eob_apply_v1(text)'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing helper public.cca_eob_apply_v1(text)'';
    ELSE
        IF p.prosrc IS DISTINCT FROM $body$
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
    IF session_user::text <> ''polypbase_cca_eob'' OR reviewed_hash IS NULL
        OR reviewed_hash !~ ''^[0-9a-f]{64}$'' THEN
        RAISE EXCEPTION ''Dedicated CCA-EOB login and reviewed hash required'' USING ERRCODE = ''42501'';
    END IF;
    
DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''postgres''::regrole;
    approval_issuer oid := ''polypbase''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''public.cca_eob_approval_v1'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing trusted approval table'';
    ELSE
        IF obj_description(approval_table.oid,''pg_class'') IS DISTINCT FROM ''Polypbase CCA-EOB trusted approval v2''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''r''
            OR approval_table.relpersistence <> ''p'' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''STOP: unrecognized approval table identity''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''n'')
                IS DISTINCT FROM ARRAY[''CHECK (singleton)'',''PRIMARY KEY (singleton)'']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''STOP: approval table schema drift''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''r'',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''SELECT'')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''SELECT'')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''UPDATE'') <> (a.attname=''consumed_at'')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''INSERT,REFERENCES'')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''consumed_at''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''UPDATE'' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''consumed_at'',''UPDATE'') THEN
            RAISE EXCEPTION ''STOP: approval table ACL drift''; END IF;
    END IF;
END;

    
DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''polypbase_cca_eob'';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''pg_authid'') IS DISTINCT FROM ''Polypbase one-off CCA-EOB V3 correction executor v1'' THEN
        RAISE EXCEPTION ''STOP: unexpected correction role identity/attributes''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''STOP: correction role must have no memberships in either direction''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''pg_authid''::regclass
        AND refobjid=r.oid AND deptype=''o'') THEN
        RAISE EXCEPTION ''STOP: correction role owns an object''; END IF;
    IF has_database_privilege(r.oid,current_database(),''CREATE'')
        OR has_schema_privilege(r.oid,''public'',''CREATE'') THEN
        RAISE EXCEPTION ''STOP: correction role can CREATE''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_''
            AND c.relkind IN (''r'',''p'',''v'',''m'',''f'') LOOP
        IF has_table_privilege(r.oid,relation.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'') THEN
            RAISE EXCEPTION ''STOP: broad table privilege on %'', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''public'' AND relation.relname=''django_migrations'' AND attribute.attname IN (''app'',''name'');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''SELECT'') <> expected THEN
                RAISE EXCEPTION ''STOP: wrong SELECT privilege on %.%'',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''UPDATE,INSERT,REFERENCES'') THEN
                RAISE EXCEPTION ''STOP: direct write privilege on %.%'',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_'' AND c.relkind=''S'' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''USAGE,SELECT,UPDATE'') THEN
            RAISE EXCEPTION ''STOP: sequence privilege on %'',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''STOP: unexpected default privileges''; END IF;
    FOR finding IN 
SELECT ''role-level setting'' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''large-object privilege'' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''SELECT,UPDATE'')
UNION ALL
SELECT ''large-object compatibility privileges'' WHERE current_setting(''lo_compat_privileges'') <> ''off''
UNION ALL
SELECT ''schema authority: '' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''information_schema'' AND left(n.nspname, 3) <> ''pg_''
      AND (has_schema_privilege(r.oid, n.oid, ''CREATE'')
           OR (n.nspname <> ''public'' AND has_schema_privilege(r.oid, n.oid, ''USAGE'')))
UNION ALL
SELECT ''security definer: '' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''pg_catalog'', ''information_schema'')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),
          to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''EXECUTE'')
 LOOP
        RAISE EXCEPTION ''STOP: unexpected authority: %'', finding.label; END LOOP;
END;

    IF current_setting(''session_replication_role'') <> ''origin''
        OR current_setting(''transaction_isolation'') <> ''read committed'' THEN
        RAISE EXCEPTION ''CCA-EOB requires origin and READ COMMITTED'' USING ERRCODE = ''25001'';
    END IF;
    SELECT * INTO STRICT approval FROM public.cca_eob_approval_v1
        WHERE singleton AND plan_hash=reviewed_hash AND consumed_at IS NULL AND expires_at>clock_timestamp();
    
BEGIN
    IF (approval.final_authorization=jsonb_build_object(
            ''schema'',''cca_eob_final_apply_v1'',''plan_hash'',reviewed_hash,
            ''document'',approval.document,''release'',approval.release)
        AND approval.final_authorized_at<=clock_timestamp()
        AND approval.final_expires_at>clock_timestamp()
        AND approval.final_expires_at>approval.final_authorized_at
        AND approval.final_expires_at<=approval.expires_at
        AND approval.final_expires_at<=approval.final_authorized_at+interval ''300 seconds'')
        IS NOT TRUE THEN
        RAISE EXCEPTION ''Separate final CCA-EOB apply authorization missing, mismatched or expired'' USING ERRCODE=''42501'';
    END IF;
END;

    actor := (approval.document->>''actor_id'')::bigint;
    PERFORM public.cca_eob_lock_v1(actor);
    SELECT * INTO STRICT locked_approval FROM public.cca_eob_approval_v1 WHERE singleton FOR UPDATE;
    IF to_jsonb(approval) IS DISTINCT FROM to_jsonb(locked_approval)
        OR locked_approval.consumed_at IS NOT NULL OR locked_approval.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION ''CCA-EOB approval changed, expired or consumed'';
    END IF;
    
BEGIN
    IF (approval.final_authorization=jsonb_build_object(
            ''schema'',''cca_eob_final_apply_v1'',''plan_hash'',reviewed_hash,
            ''document'',approval.document,''release'',approval.release)
        AND approval.final_authorized_at<=clock_timestamp()
        AND approval.final_expires_at>clock_timestamp()
        AND approval.final_expires_at>approval.final_authorized_at
        AND approval.final_expires_at<=approval.expires_at
        AND approval.final_expires_at<=approval.final_authorized_at+interval ''300 seconds'')
        IS NOT TRUE THEN
        RAISE EXCEPTION ''Separate final CCA-EOB apply authorization missing, mismatched or expired'' USING ERRCODE=''42501'';
    END IF;
END;

    doc := approval.document;
    
DECLARE evidence jsonb := approval.release; archive jsonb := approval.release->''archive'';
BEGIN
    IF (jsonb_typeof(evidence)=''object''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(evidence) key)=ARRAY[
            ''actor_id'',''archive'',''plan_hash'',''restoration_verified'',''restore_report'',''review_ref'',''schema'',''target'']::text[]
        AND evidence->>''schema''=''cca_eob_release_v1''
        AND evidence->''target''=doc->''target'' AND jsonb_typeof(doc->''target'')=''object''
        AND evidence->>''plan_hash''=reviewed_hash
        AND evidence->''actor_id''=doc->''actor_id''
        AND jsonb_typeof(evidence->''actor_id'')=''number'' AND evidence->>''actor_id'' ~ ''^[1-9][0-9]*$''
        AND evidence->''restoration_verified''=''true''::jsonb
        AND jsonb_typeof(evidence->''restore_report'')=''string''
        AND length(btrim(evidence->>''restore_report'', chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)))>0
        AND jsonb_typeof(evidence->''review_ref'')=''string''
        AND length(btrim(evidence->>''review_ref'', chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)))>0
        AND jsonb_typeof(archive)=''object''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(archive) key)=ARRAY[
            ''path'',''sha256'',''size_bytes'']::text[]
        AND jsonb_typeof(archive->''path'')=''string'' AND left(archive->>''path'',1)=''/''
        AND length(archive->>''path'')>1 AND right(archive->>''path'',1)<>''/''
        AND position(''//'' in archive->>''path'')=0 AND position(chr(92) in archive->>''path'')=0
        AND archive->>''path'' !~ ''[[:cntrl:]]'' AND archive->>''path'' !~ ''(^|/)[.]{1,2}(/|$)''
        AND jsonb_typeof(archive->''sha256'')=''string'' AND archive->>''sha256'' ~ ''^[0-9a-f]{64}$''
        AND jsonb_typeof(archive->''size_bytes'')=''number'' AND archive->>''size_bytes'' ~ ''^[1-9][0-9]*$''
        AND (archive->>''size_bytes'')::numeric<=9223372036854775807) IS NOT TRUE THEN
        RAISE EXCEPTION ''Incomplete, malformed or unbound CCA-EOB archive/release evidence'';
    END IF;
END;

    correction := doc#>''{plan,correction}'';
    recipe := doc#>''{plan,write_recipe}'';
    IF (doc->>''schema''=''cca_eob_production_plan_v2''
        AND correction->>''operation''=''cca_eob_v2_20261016_v3''
        AND correction->''organization_id''=''1''::jsonb
        AND correction->>''v2_sha256''=''0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160''
        AND correction->''ready''=''true''::jsonb AND correction->''blockers''=''[]''::jsonb
        AND approval.release->>''schema''=''cca_eob_release_v1''
        AND approval.release->''target''=doc->''target''
        AND jsonb_typeof(doc->''target'')=''object''
        AND approval.release->>''plan_hash''=reviewed_hash
        AND approval.release->''actor_id''=doc->''actor_id''
        AND approval.release->''restoration_verified''=''true''::jsonb
        AND jsonb_typeof(approval.release->''review_ref'')=''string''
        AND length(btrim(approval.release->>''review_ref''))>0
        AND jsonb_typeof(approval.release->''restore_report'')=''string''
        AND length(btrim(approval.release->>''restore_report''))>0
        AND jsonb_typeof(recipe)=''object'') IS NOT TRUE THEN
        RAISE EXCEPTION ''Incomplete or unbound trusted CCA-EOB approval'';
    END IF;
    
DECLARE target jsonb := doc->''target''; live_identity jsonb;
BEGIN
    SELECT jsonb_build_object(''database'',current_database(),''database_oid'',d.oid::bigint,
        ''role'',session_user::text,''role_oid'',r.oid::bigint,
        ''server_address'',host(inet_server_addr()),''server_port'',inet_server_port(),
        ''postmaster_started_at'',to_char(pg_postmaster_start_time() AT TIME ZONE ''UTC'',
            ''YYYY-MM-DD"T"HH24:MI:SS.US"Z"''),
        ''server_version_num'',current_setting(''server_version_num'')::integer)
        INTO STRICT live_identity FROM pg_database d,pg_roles r
        WHERE d.datname=current_database() AND r.rolname=session_user;
    IF (jsonb_typeof(target)=''object''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(target) key)=ARRAY[
            ''configured_host'',''configured_port'',''database'',''database_oid'',''machine_id'',
            ''postmaster_started_at'',''role'',''role_oid'',''schema'',''server_address'',''server_port'',
            ''server_version_num'',''socket_gid'',''socket_uid'']::text[]
        AND target->>''schema''=''cca_eob_target_v2''
        AND jsonb_typeof(target->''machine_id'')=''string'' AND target->>''machine_id'' ~ ''^[0-9a-f]{32}$''
        AND target-ARRAY[''schema'',''machine_id'',''configured_host'',''configured_port'',''socket_uid'',''socket_gid'']=live_identity
        AND target->''role''=to_jsonb(''polypbase_cca_eob''::text) AND session_user::text=''polypbase_cca_eob''
        AND target->''configured_port''=to_jsonb(current_setting(''port''))
        AND current_setting(''server_version_num'')::integer BETWEEN 180000 AND 189999
        AND NOT pg_is_in_recovery()
        
        AND target->''configured_host''=to_jsonb(''/var/run/postgresql''::text)
        AND target->''configured_port''=to_jsonb(''5433''::text)
        AND current_database()=''polypbase'' AND current_setting(''port'')=''5433''
        AND inet_server_addr() IS NULL AND inet_server_port() IS NULL
        AND jsonb_typeof(target->''socket_uid'')=''number'' AND (target->>''socket_uid'')::bigint>0
        AND jsonb_typeof(target->''socket_gid'')=''number'' AND (target->>''socket_gid'')::bigint>0
    ) IS NOT TRUE THEN
        RAISE EXCEPTION ''STOP: live CCA-EOB target differs from trusted approval'';
    END IF;
END;

    
DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''postgres''::regrole;
    approval_issuer oid := ''polypbase''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''public.cca_eob_approval_v1'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing trusted approval table'';
    ELSE
        IF obj_description(approval_table.oid,''pg_class'') IS DISTINCT FROM ''Polypbase CCA-EOB trusted approval v2''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''r''
            OR approval_table.relpersistence <> ''p'' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''STOP: unrecognized approval table identity''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''n'')
                IS DISTINCT FROM ARRAY[''CHECK (singleton)'',''PRIMARY KEY (singleton)'']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''STOP: approval table schema drift''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''r'',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''SELECT'')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''SELECT'')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''UPDATE'') <> (a.attname=''consumed_at'')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''INSERT,REFERENCES'')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''consumed_at''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''UPDATE'' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''consumed_at'',''UPDATE'') THEN
            RAISE EXCEPTION ''STOP: approval table ACL drift''; END IF;
    END IF;
END;

    state := public.cca_eob_read_v1(actor);
    IF state IS DISTINCT FROM doc#>''{plan,database_snapshot}''
        OR state->''incoming_evidence'' <> ''false''::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_each(state->''histories'') h WHERE h.value <> ''false''::jsonb)
        OR state->''external_movement_links'' <> ''[]''::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->''boxes'') b
            WHERE b->''merged_into_id'' IS DISTINCT FROM ''null''::jsonb
                OR b->>''previous_global_code'' IS DISTINCT FROM '''')
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->''measurements'') b
            WHERE b->''duplicate_of_id'' IS DISTINCT FROM ''null''::jsonb
                OR b->''environment_source_box_id'' IS DISTINCT FROM ''null''::jsonb) THEN
        RAISE EXCEPTION ''Stale or blocked CCA-EOB snapshot'';
    END IF;
    IF (jsonb_typeof(correction->''entries'')=''array''
        AND jsonb_typeof(correction->''move_order'')=''array''
        AND jsonb_typeof(correction->''revision_changes'')=''array''
        AND jsonb_typeof(recipe->''moves'')=''array''
        AND jsonb_typeof(recipe->''revisions'')=''array''
        AND jsonb_typeof(recipe->''audits'')=''array'') IS NOT TRUE THEN
        RAISE EXCEPTION ''Malformed trusted write recipe'';
    END IF;
    move_count := jsonb_array_length(correction->''move_order'');
    IF move_count=0 OR jsonb_array_length(recipe->''moves'')<>move_count
        OR jsonb_array_length(recipe->''audits'')<>move_count+1
        OR recipe->''revisions'' IS DISTINCT FROM correction->''revision_changes''
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->''entries'') e
            WHERE coalesce(e->>''classification'','''') NOT IN (''REASSIGN_REQUIRED'',''ALREADY_MATCHES'',''OUT_OF_SOURCE'')
                OR e->>''measurement_pk'' IS NULL)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->''entries'') e
            GROUP BY (e->>''measurement_pk'')::bigint HAVING count(*)<>1)
        OR (SELECT count(*) FROM jsonb_array_elements(correction->''entries'') e
            WHERE e->>''classification''=''REASSIGN_REQUIRED'')<>move_count THEN
        RAISE EXCEPTION ''CCA-EOB recipe cardinalities differ'';
    END IF;
    -- The administrator authenticates the complete historical document''s Python
    -- fingerprint offline; SQL never reserializes JSONB as that historical identity.
    -- SQL independently binds the immutable original evidence and exact 72 moves.
    IF state->''historical_readings_exist'' IS DISTINCT FROM ''false''::jsonb THEN
        historical_plan := doc#>''{plan,thermal_exception,original_document,plan,correction}'';
        IF historical_plan#>''{baseline,histories}'' =
            ''{"subculture_events":[],"subculture_allocations":[],"lineages":[],"transfer_items":[]}''::jsonb THEN
            historical_plan := jsonb_set(historical_plan,''{baseline,histories}'',state->''histories'');
        END IF;
        IF (move_count=72
            AND doc#>>''{plan,thermal_exception,schema}''=''cca_eob_unlinked_72_v2''
            AND doc#>>''{plan,thermal_exception,original_plan_hash}'' ~ ''^[0-9a-f]{64}$''
            AND jsonb_typeof(doc#>''{plan,thermal_exception,original_document}'')=''object''
            AND doc#>>''{plan,thermal_exception,original_document,schema}''=''cca_eob_production_plan_v2''
            AND jsonb_typeof(doc#>''{plan,thermal_exception,original_document,plan}'')=''object''
            AND jsonb_typeof(historical_plan)=''object''
            AND historical_plan=correction) IS NOT TRUE THEN
            RAISE EXCEPTION ''Authenticated original 72-move thermal exception required'';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading r
        JOIN jsonb_array_elements(recipe->''moves'') m ON r.biological_measurement_id=(m->>''id'')::bigint) THEN
        RAISE EXCEPTION ''A planned measurement has a historical temperature association'';
    END IF;
    -- The lock helper holds SHARE on readings until commit: no phantom link can appear.
    -- Validate every raw baseline before the first update. JSON null and zero differ.
    FOR i IN 0..move_count-1 LOOP
        pk := (correction->''move_order''->>i)::bigint;
        IF pk IS NULL OR pk=ANY(seen) THEN RAISE EXCEPTION ''Duplicate or missing move ID''; END IF;
        seen := array_append(seen,pk);
        SELECT e INTO STRICT entry FROM jsonb_array_elements(correction->''entries'') e
            WHERE (e->>''measurement_pk'')::bigint=pk AND e->>''classification''=''REASSIGN_REQUIRED'';
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''measurements'') b WHERE (b->>''id'')::bigint=pk;
        SELECT b INTO STRICT reviewed_raw FROM jsonb_array_elements(doc#>''{plan,database_snapshot,measurements}'') b
            WHERE (b->>''id'')::bigint=pk;
        before_source := entry->''before'';
        move := recipe->''moves''->i;
        -- Raw equality protects every column. Python''s normalized before uses
        -- different timestamp/decimal encodings, so compare its source keys by type.
        IF (raw=reviewed_raw
            AND before_source ?& ARRAY[''id'',''box_id'',''measured_on'',''week_start'',
                ''polyp_count'',''ephyrae_count'',''strobila_count'']
            AND (before_source->>''id'')::bigint=pk
            AND (before_source->>''box_id'')::bigint=(raw->>''box_id'')::bigint
            AND (before_source->>''measured_on'')::date=(raw->>''measured_on'')::date
            AND (before_source->>''week_start'')::date=(raw->>''week_start'')::date
            AND (before_source->>''polyp_count'')::bigint IS NOT DISTINCT FROM (raw->>''polyp_count'')::bigint
            AND (before_source->>''ephyrae_count'')::bigint IS NOT DISTINCT FROM (raw->>''ephyrae_count'')::bigint
            AND (before_source->>''strobila_count'')::bigint IS NOT DISTINCT FROM (raw->>''strobila_count'')::bigint
            AND (raw->>''box_id'')::bigint IN (1835,1836,1837)
            AND (entry->>''target_box_pk'')::bigint IN (1835,1836)
            AND entry->>''target_box''=CASE (entry->>''target_box_pk'')::bigint
                WHEN 1835 THEN ''CCA-EOB-2.004'' WHEN 1836 THEN ''CCA-EOB-2.005'' END
            AND entry->''target_code''=entry->''target_box''
            AND entry->''target_box_pk''<>raw->''box_id''
            AND raw->''duplicate_of_id''=''null''::jsonb
            AND raw->''environment_source_box_id''=''null''::jsonb
            AND extract(isoyear FROM (raw->>''measured_on'')::date)=2026
            AND date_trunc(''week'',(raw->>''measured_on'')::date)::date=(raw->>''week_start'')::date
            AND (entry->>''iso_year'')::integer=2026
            AND (entry->>''iso_week'')::integer=extract(week FROM (raw->>''measured_on'')::date)
            AND entry->''source_value''=jsonb_build_array(raw->''polyp_count'',raw->''ephyrae_count'')
            AND move=jsonb_build_object(''id'',pk,''before_box_id'',raw->''box_id'',
                ''target_box_id'',entry->''target_box_pk'',''week_start'',raw->''week_start'')) IS NOT TRUE THEN
            RAISE EXCEPTION ''CCA-EOB move does not match approved raw baseline'';
        END IF;
    END LOOP;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->''revisions'') LOOP
        box_pk := (revision->>''box_id'')::bigint;
        IF box_pk IS NULL OR box_pk NOT IN (1835,1836,1837) OR box_pk=ANY(revised) THEN
            RAISE EXCEPTION ''Invalid or duplicate revision box''; END IF;
        revised := array_append(revised,box_pk);
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''boxes'') b WHERE (b->>''id'')::bigint=box_pk;
        SELECT count(*) INTO touched FROM jsonb_array_elements(recipe->''moves'') m
            WHERE (m->>''before_box_id'')::bigint=box_pk OR (m->>''target_box_id'')::bigint=box_pk;
        SELECT greatest((raw->>''polyp_state_revision'')::bigint+touched,
            coalesce(max((b->>''polyp_state_sequence'')::bigint),0)) INTO expected_after
            FROM jsonb_array_elements(state->''measurements'') b
            WHERE coalesce((SELECT (m->>''target_box_id'')::bigint FROM jsonb_array_elements(recipe->''moves'') m
                WHERE m->''id''=b->''id''),(b->>''box_id'')::bigint)=box_pk;
        IF (touched>0 AND revision->''before''=raw->''polyp_state_revision''
            AND (revision->>''after'')::bigint=expected_after) IS NOT TRUE THEN
            RAISE EXCEPTION ''CCA-EOB revision differs from touched count and final sequences''; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(recipe->''moves'') m
        WHERE NOT ((m->>''before_box_id'')::bigint=ANY(revised))
            OR NOT ((m->>''target_box_id'')::bigint=ANY(revised))) THEN
        RAISE EXCEPTION ''Missing touched revision''; END IF;
    FOR i IN 0..move_count LOOP
        audit := recipe->''audits''->i;
        IF (audit->''organization_id''=''1''::jsonb AND audit->''user_id''=doc->''actor_id''
            AND jsonb_typeof(audit->''metadata'')=''object''
            AND NOT (audit->''metadata'' ? ''execution_evidence'')
            AND jsonb_typeof(audit->''description'')=''string''
            AND audit#>>''{metadata,operation}''=''cca_eob_v2_20261016_v3'') IS NOT TRUE THEN
            RAISE EXCEPTION ''Invalid trusted audit identity or metadata''; END IF;
        IF i<move_count THEN
            move := recipe->''moves''->i;
            SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''boxes'') b
                WHERE b->''id''=move->''target_box_id'';
            IF (audit->>''action''=''update'' AND audit->>''object_type''=''box''
                AND audit->''object_id''=raw->''global_code''
                AND audit#>''{metadata,measurement_id}''=move->''id'') IS NOT TRUE THEN
                RAISE EXCEPTION ''Invalid measurement audit target''; END IF;
        ELSIF (audit->>''action''=''import'' AND audit->>''object_type''=''historical_correction''
            AND audit->>''object_id''=''cca_eob_v2_20261016_v3'') IS NOT TRUE THEN
            RAISE EXCEPTION ''Invalid correction summary audit'';
        END IF;
    END LOOP;
    FOR move IN SELECT value FROM jsonb_array_elements(recipe->''moves'') LOOP
        UPDATE public.measurements_biologicalmeasurement SET box_id=(move->>''target_box_id'')::bigint
            WHERE id=(move->>''id'')::bigint AND box_id=(move->>''before_box_id'')::bigint
                AND week_start=(move->>''week_start'')::date;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION ''CCA-EOB measurement update cardinality''; END IF;
    END LOOP;
    -- Recheck all scoped biology columns, not just the moved Box IDs.
    FOR raw IN SELECT value FROM jsonb_array_elements(state->''measurements'') LOOP
        before_source := raw;
        SELECT m INTO move FROM jsonb_array_elements(recipe->''moves'') m WHERE m->''id''=raw->''id'';
        IF FOUND THEN before_source := jsonb_set(raw,''{box_id}'',move->''target_box_id''); END IF;
        IF (SELECT to_jsonb(b) FROM public.measurements_biologicalmeasurement b
            WHERE b.id=(raw->>''id'')::bigint) IS DISTINCT FROM before_source THEN
            RAISE EXCEPTION ''CCA-EOB post-write biological integrity failure'';
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading WHERE biological_measurement_id=ANY(seen)) THEN
        RAISE EXCEPTION ''CCA-EOB post-write thermal integrity failure'';
    END IF;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->''revisions'') LOOP
        UPDATE public.cultures_box SET polyp_state_revision=(revision->>''after'')::bigint
            WHERE id=(revision->>''box_id'')::bigint AND organization_id=1
                AND polyp_state_revision=(revision->>''before'')::bigint;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION ''CCA-EOB revision update cardinality''; END IF;
    END LOOP;
    FOR audit IN SELECT value FROM jsonb_array_elements(recipe->''audits'') LOOP
        -- historical_plan_hash retains the complete historical document''s identity.
        -- Match the ORM default for a new audit with no edit history.
        INSERT INTO public.audit_auditlog (organization_id,user_id,action,object_type,object_id,
            description,metadata,created_at,edited_at,edited_by_id,original_values)
        VALUES (1,actor,audit->>''action'',audit->>''object_type'',audit->>''object_id'',audit->>''description'',
            (audit->''metadata'') || jsonb_build_object(''execution_evidence'',jsonb_build_object(
                ''target'',doc->''target'',''production_plan_hash'',reviewed_hash,
                ''authorization'',''APPLY_CCA_EOB_ORGANIZATION_1:''||reviewed_hash,''release'',approval.release,
                ''final_authorized_at'',approval.final_authorized_at,''final_expires_at'',approval.final_expires_at,
                ''historical_plan_hash'',doc#>''{plan,thermal_exception,original_plan_hash}'')),
            clock_timestamp(),NULL,NULL,''{}''::jsonb);
    END LOOP;
    UPDATE public.cca_eob_approval_v1 SET consumed_at=clock_timestamp()
        WHERE singleton AND consumed_at IS NULL AND expires_at>clock_timestamp()
            AND final_expires_at>clock_timestamp();
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION ''CCA-EOB approval expired during apply (plan or final authorization)''; END IF;
END
$body$
            OR p.proowner <> ''polypbase''::regrole OR NOT p.prosecdef
            OR p.proconfig IS DISTINCT FROM ARRAY[''search_path=pg_catalog, pg_temp'',''TimeZone=UTC'',''DateStyle=ISO, YMD'']::text[]
            OR p.prolang <> (SELECT oid FROM pg_language WHERE lanname=''plpgsql'')
            OR p.provolatile <> ''v'' OR p.proparallel <> ''u'' OR p.prokind <> ''f''
            OR p.proisstrict OR p.proleakproof OR p.proretset
            OR p.prorettype <> ''void''::regtype OR p.pronargs <> 1
            OR p.proargtypes::text <> ''25'' OR p.pronargdefaults <> 0
            OR p.proargnames IS DISTINCT FROM ARRAY[''reviewed_hash'']::text[]
            OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
            OR p.proargdefaults IS NOT NULL OR p.probin IS NOT NULL OR p.prosqlbody IS NOT NULL
            OR p.prosupport <> 0 OR p.provariadic <> 0 OR p.protrftypes IS NOT NULL
            OR p.procost <> 100 OR p.prorows <> 0 THEN
            RAISE EXCEPTION ''STOP: unexpected helper definition/metadata: public.cca_eob_apply_v1(text)''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))) x
            WHERE x.privilege_type <> ''EXECUTE'' OR x.is_grantable
                OR x.grantor <> p.proowner OR x.grantee NOT IN (p.proowner,''polypbase_cca_eob''::regrole))
            OR NOT has_function_privilege(''polypbase_cca_eob'',''public.cca_eob_apply_v1(text)'',''EXECUTE'')
            OR (SELECT count(*) FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))))<>2 THEN
            RAISE EXCEPTION ''STOP: unexpected helper ACL: public.cca_eob_apply_v1(text)''; END IF;
    END IF;

END $helpers$;
DO $approval$

DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''postgres''::regrole;
    approval_issuer oid := ''polypbase''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''public.cca_eob_approval_v1'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing trusted approval table'';
    ELSE
        IF obj_description(approval_table.oid,''pg_class'') IS DISTINCT FROM ''Polypbase CCA-EOB trusted approval v2''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''r''
            OR approval_table.relpersistence <> ''p'' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''STOP: unrecognized approval table identity''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''n'')
                IS DISTINCT FROM ARRAY[''CHECK (singleton)'',''PRIMARY KEY (singleton)'']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''STOP: approval table schema drift''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''r'',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''SELECT'')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''SELECT'')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''UPDATE'') <> (a.attname=''consumed_at'')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''INSERT,REFERENCES'')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''consumed_at''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''UPDATE'' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''consumed_at'',''UPDATE'') THEN
            RAISE EXCEPTION ''STOP: approval table ACL drift''; END IF;
    END IF;
END;
$approval$;
';
    SELECT (SELECT jsonb_agg(to_jsonb(p) - 'prosrc' ORDER BY p.oid) FROM pg_proc p WHERE p.oid IN (SELECT to_regprocedure(s) FROM unnest(ARRAY['public.cca_eob_lock_v1(bigint)','public.cca_eob_read_v1(bigint)','public.cca_eob_apply_v1(text)']) s)) INTO original_metadata;
    EXECUTE 'CREATE OR REPLACE FUNCTION public.cca_eob_read_v1(actor_id bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET TimeZone = ''UTC'' SET DateStyle = ''ISO, YMD'' AS ''
DECLARE result jsonb;
BEGIN
    IF session_user::text <> ''''polypbase_cca_eob'''' OR actor_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1
            AND m.is_active AND m.role = ''''admin''''
    ) THEN
        RAISE EXCEPTION ''''Dedicated CCA-EOB login and active organization 1 admin required'''' USING ERRCODE = ''''42501'''';
    END IF;
    
DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''''polypbase_cca_eob'''';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''''pg_authid'''') IS DISTINCT FROM ''''Polypbase one-off CCA-EOB V3 correction executor v1'''' THEN
        RAISE EXCEPTION ''''STOP: unexpected correction role identity/attributes''''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''''STOP: correction role must have no memberships in either direction''''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''''pg_authid''''::regclass
        AND refobjid=r.oid AND deptype=''''o'''') THEN
        RAISE EXCEPTION ''''STOP: correction role owns an object''''; END IF;
    IF has_database_privilege(r.oid,current_database(),''''CREATE'''')
        OR has_schema_privilege(r.oid,''''public'''',''''CREATE'''') THEN
        RAISE EXCEPTION ''''STOP: correction role can CREATE''''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''''information_schema'''' AND left(n.nspname,3)<>''''pg_''''
            AND c.relkind IN (''''r'''',''''p'''',''''v'''',''''m'''',''''f'''') LOOP
        IF has_table_privilege(r.oid,relation.oid,''''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'''') THEN
            RAISE EXCEPTION ''''STOP: broad table privilege on %'''', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''''public'''' AND relation.relname=''''django_migrations'''' AND attribute.attname IN (''''app'''',''''name'''');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''''SELECT'''') <> expected THEN
                RAISE EXCEPTION ''''STOP: wrong SELECT privilege on %.%'''',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''''UPDATE,INSERT,REFERENCES'''') THEN
                RAISE EXCEPTION ''''STOP: direct write privilege on %.%'''',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''''information_schema'''' AND left(n.nspname,3)<>''''pg_'''' AND c.relkind=''''S'''' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''''USAGE,SELECT,UPDATE'''') THEN
            RAISE EXCEPTION ''''STOP: sequence privilege on %'''',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''''STOP: unexpected default privileges''''; END IF;
    FOR finding IN 
SELECT ''''role-level setting'''' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''''large-object privilege'''' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''''SELECT,UPDATE'''')
UNION ALL
SELECT ''''large-object compatibility privileges'''' WHERE current_setting(''''lo_compat_privileges'''') <> ''''off''''
UNION ALL
SELECT ''''schema authority: '''' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''''information_schema'''' AND left(n.nspname, 3) <> ''''pg_''''
      AND (has_schema_privilege(r.oid, n.oid, ''''CREATE'''')
           OR (n.nspname <> ''''public'''' AND has_schema_privilege(r.oid, n.oid, ''''USAGE'''')))
UNION ALL
SELECT ''''security definer: '''' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''''pg_catalog'''', ''''information_schema'''')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''''public.cca_eob_lock_v1(bigint)''''),
          to_regprocedure(''''public.cca_eob_read_v1(bigint)''''),to_regprocedure(''''public.cca_eob_apply_v1(text)'''')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''''EXECUTE'''')
 LOOP
        RAISE EXCEPTION ''''STOP: unexpected authority: %'''', finding.label; END LOOP;
END;

    IF (SELECT count(*) FROM public.cultures_box WHERE organization_id = 1 AND
        (id, global_code) IN ((1835,''''CCA-EOB-2.004''''),(1836,''''CCA-EOB-2.005''''),(1837,''''CCA-EOB-2.006''''))) <> 3 THEN
        RAISE EXCEPTION ''''CCA-EOB Box identities changed'''' USING ERRCODE = ''''23514'''';
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
        ''''boxes'''', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''''[]''''::jsonb) FROM boxes b),
        ''''measurements'''', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''''[]''''::jsonb) FROM biology b),
        ''''locations'''', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''''[]''''::jsonb) FROM locations b),
        ''''movements'''', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''''[]''''::jsonb) FROM movements b),
        ''''external_movement_links'''', (SELECT coalesce(jsonb_agg(jsonb_build_object(
            ''''id'''',m.id,''''box_id'''',m.box_id,''''origin_location_id'''',m.origin_location_id,
            ''''destination_location_id'''',m.destination_location_id) ORDER BY m.id),''''[]''''::jsonb)
            FROM public.cultures_boxmovement m WHERE m.box_id NOT IN (SELECT id FROM boxes)
            AND (m.origin_location_id IN (SELECT id FROM locations)
                OR m.destination_location_id IN (SELECT id FROM locations))),
        ''''zones'''', (SELECT coalesce(jsonb_agg(jsonb_build_object(''''id'''',z.id,
            ''''organization_id'''',z.organization_id) ORDER BY z.id),''''[]''''::jsonb)
            FROM public.cultures_thermalzone z WHERE z.id IN (SELECT id FROM zone_ids)),
        ''''attribution_user_ids'''', (SELECT coalesce(jsonb_agg(user_id ORDER BY user_id),''''[]''''::jsonb)
            FROM (SELECT DISTINCT user_id FROM public.accounts_organizationmembership
                WHERE organization_id = 1 AND user_id IN (
                    SELECT original_observer_id FROM biology UNION SELECT imported_by_id FROM biology)) members),
        ''''histories'''', jsonb_build_object(
            ''''subculture_events'''', EXISTS (SELECT 1 FROM public.cultures_subcultureevent WHERE parent_box_id IN (SELECT id FROM boxes)),
            ''''subculture_allocations'''', EXISTS (SELECT 1 FROM public.cultures_subcultureallocation WHERE child_box_id IN (SELECT id FROM boxes)),
            ''''lineages'''', EXISTS (SELECT 1 FROM public.cultures_boxlineage WHERE parent_box_id IN (SELECT id FROM boxes) OR child_box_id IN (SELECT id FROM boxes)),
            ''''transfer_items'''', EXISTS (SELECT 1 FROM public.cultures_transferitem WHERE source_box_id IN (SELECT id FROM boxes))),
        ''''incoming_evidence'''', EXISTS (SELECT 1 FROM public.measurements_biologicalmeasurement
            WHERE box_id NOT IN (SELECT id FROM boxes) AND
                (duplicate_of_id IN (SELECT id FROM biology) OR environment_source_box_id IN (SELECT id FROM boxes))),
        ''''addition_audits'''', (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.id),''''[]''''::jsonb)
            FROM public.audit_auditlog a WHERE a.metadata->>''''measurement_id'''' IN (''''104688'''',''''104689'''')),
        ''''historical_readings_exist'''', EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading),
        ''''historical_linked_measurement_ids'''', (SELECT coalesce(jsonb_agg(r.biological_measurement_id
            ORDER BY r.biological_measurement_id),''''[]''''::jsonb)
            FROM public.measurements_historicaltemperaturereading r
            WHERE r.biological_measurement_id IN (SELECT id FROM biology)),
        ''''guard_state'''', jsonb_build_object(
            ''''triggers'''', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''''binding'''',to_jsonb(t),''''table'''',c.relname,''''definition'''',pg_get_triggerdef(t.oid),
                ''''function_definition'''',pg_get_functiondef(p.oid),''''function'''',to_jsonb(p),
                ''''function_owner'''',pg_get_userbyid(p.proowner)) ORDER BY c.relname,t.tgname),''''[]''''::jsonb)
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE n.nspname=''''public'''' AND NOT t.tgisinternal AND starts_with(t.tgname,''''historical_'''')),
            ''''historical_functions'''', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''''catalog'''',to_jsonb(p),''''definition'''',pg_get_functiondef(p.oid),
                ''''owner'''',pg_get_userbyid(p.proowner)) ORDER BY p.oid),''''[]''''::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=''''public'''' AND starts_with(p.proname,''''historical_temperature_'''')),
            ''''helpers'''', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''''catalog'''',to_jsonb(p),''''definition'''',pg_get_functiondef(p.oid),
                ''''owner'''',pg_get_userbyid(p.proowner)) ORDER BY p.oid),''''[]''''::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=''''public'''' AND starts_with(p.proname,''''cca_eob_'''')))
    ) INTO result;
    RETURN result;
END
'''; EXECUTE 'CREATE OR REPLACE FUNCTION public.cca_eob_apply_v1(reviewed_hash text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp SET TimeZone = ''UTC'' SET DateStyle = ''ISO, YMD'' AS ''
DECLARE
    approval public.cca_eob_approval_v1%ROWTYPE;
    locked_approval public.cca_eob_approval_v1%ROWTYPE;
    doc jsonb; correction jsonb; recipe jsonb; state jsonb; entry jsonb; move jsonb;
    revision jsonb; audit jsonb; raw jsonb; reviewed_raw jsonb; before_source jsonb;
    historical_plan jsonb; projected jsonb; addition_time timestamptz;
    actor bigint; pk bigint; box_pk bigint;
    seen bigint[] := ARRAY[]::bigint[]; revised bigint[] := ARRAY[]::bigint[];
    touched bigint; expected_after bigint; changed bigint; move_count integer; i integer;
BEGIN
    IF session_user::text <> ''''polypbase_cca_eob'''' OR reviewed_hash IS NULL
        OR reviewed_hash !~ ''''^[0-9a-f]{64}$'''' THEN
        RAISE EXCEPTION ''''Dedicated CCA-EOB login and reviewed hash required'''' USING ERRCODE = ''''42501'''';
    END IF;
    
DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''''postgres''''::regrole;
    approval_issuer oid := ''''polypbase''''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''''public.cca_eob_approval_v1'''');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''''STOP: missing trusted approval table'''';
    ELSE
        IF obj_description(approval_table.oid,''''pg_class'''') IS DISTINCT FROM ''''Polypbase CCA-EOB trusted approval v2''''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''''r''''
            OR approval_table.relpersistence <> ''''p'''' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''''STOP: unrecognized approval table identity''''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''''n'''')
                IS DISTINCT FROM ARRAY[''''CHECK (singleton)'''',''''PRIMARY KEY (singleton)'''']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''''STOP: approval table schema drift''''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''''r'''',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''''SELECT'''')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''''SELECT'''')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'''')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''''UPDATE'''') <> (a.attname=''''consumed_at'''')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''''INSERT,REFERENCES'''')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''''consumed_at''''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''''UPDATE'''' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''''consumed_at'''',''''UPDATE'''') THEN
            RAISE EXCEPTION ''''STOP: approval table ACL drift''''; END IF;
    END IF;
END;

    
DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''''polypbase_cca_eob'''';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''''pg_authid'''') IS DISTINCT FROM ''''Polypbase one-off CCA-EOB V3 correction executor v1'''' THEN
        RAISE EXCEPTION ''''STOP: unexpected correction role identity/attributes''''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''''STOP: correction role must have no memberships in either direction''''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''''pg_authid''''::regclass
        AND refobjid=r.oid AND deptype=''''o'''') THEN
        RAISE EXCEPTION ''''STOP: correction role owns an object''''; END IF;
    IF has_database_privilege(r.oid,current_database(),''''CREATE'''')
        OR has_schema_privilege(r.oid,''''public'''',''''CREATE'''') THEN
        RAISE EXCEPTION ''''STOP: correction role can CREATE''''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''''information_schema'''' AND left(n.nspname,3)<>''''pg_''''
            AND c.relkind IN (''''r'''',''''p'''',''''v'''',''''m'''',''''f'''') LOOP
        IF has_table_privilege(r.oid,relation.oid,''''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'''') THEN
            RAISE EXCEPTION ''''STOP: broad table privilege on %'''', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''''public'''' AND relation.relname=''''django_migrations'''' AND attribute.attname IN (''''app'''',''''name'''');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''''SELECT'''') <> expected THEN
                RAISE EXCEPTION ''''STOP: wrong SELECT privilege on %.%'''',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''''UPDATE,INSERT,REFERENCES'''') THEN
                RAISE EXCEPTION ''''STOP: direct write privilege on %.%'''',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''''information_schema'''' AND left(n.nspname,3)<>''''pg_'''' AND c.relkind=''''S'''' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''''USAGE,SELECT,UPDATE'''') THEN
            RAISE EXCEPTION ''''STOP: sequence privilege on %'''',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''''STOP: unexpected default privileges''''; END IF;
    FOR finding IN 
SELECT ''''role-level setting'''' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''''large-object privilege'''' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''''SELECT,UPDATE'''')
UNION ALL
SELECT ''''large-object compatibility privileges'''' WHERE current_setting(''''lo_compat_privileges'''') <> ''''off''''
UNION ALL
SELECT ''''schema authority: '''' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''''information_schema'''' AND left(n.nspname, 3) <> ''''pg_''''
      AND (has_schema_privilege(r.oid, n.oid, ''''CREATE'''')
           OR (n.nspname <> ''''public'''' AND has_schema_privilege(r.oid, n.oid, ''''USAGE'''')))
UNION ALL
SELECT ''''security definer: '''' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''''pg_catalog'''', ''''information_schema'''')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''''public.cca_eob_lock_v1(bigint)''''),
          to_regprocedure(''''public.cca_eob_read_v1(bigint)''''),to_regprocedure(''''public.cca_eob_apply_v1(text)'''')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''''EXECUTE'''')
 LOOP
        RAISE EXCEPTION ''''STOP: unexpected authority: %'''', finding.label; END LOOP;
END;

    IF current_setting(''''session_replication_role'''') <> ''''origin''''
        OR current_setting(''''transaction_isolation'''') <> ''''read committed'''' THEN
        RAISE EXCEPTION ''''CCA-EOB requires origin and READ COMMITTED'''' USING ERRCODE = ''''25001'''';
    END IF;
    SELECT * INTO STRICT approval FROM public.cca_eob_approval_v1
        WHERE singleton AND plan_hash=reviewed_hash AND consumed_at IS NULL AND expires_at>clock_timestamp();
    
BEGIN
    IF (approval.final_authorization=jsonb_build_object(
            ''''schema'''',''''cca_eob_final_apply_v1'''',''''plan_hash'''',reviewed_hash,
            ''''document'''',approval.document,''''release'''',approval.release)
        AND approval.final_authorized_at<=clock_timestamp()
        AND approval.final_expires_at>clock_timestamp()
        AND approval.final_expires_at>approval.final_authorized_at
        AND approval.final_expires_at<=approval.expires_at
        AND approval.final_expires_at<=approval.final_authorized_at+interval ''''300 seconds'''')
        IS NOT TRUE THEN
        RAISE EXCEPTION ''''Separate final CCA-EOB apply authorization missing, mismatched or expired'''' USING ERRCODE=''''42501'''';
    END IF;
END;

    actor := (approval.document->>''''actor_id'''')::bigint;
    PERFORM public.cca_eob_lock_v1(actor);
    SELECT * INTO STRICT locked_approval FROM public.cca_eob_approval_v1 WHERE singleton FOR UPDATE;
    IF to_jsonb(approval) IS DISTINCT FROM to_jsonb(locked_approval)
        OR locked_approval.consumed_at IS NOT NULL OR locked_approval.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION ''''CCA-EOB approval changed, expired or consumed'''';
    END IF;
    
BEGIN
    IF (approval.final_authorization=jsonb_build_object(
            ''''schema'''',''''cca_eob_final_apply_v1'''',''''plan_hash'''',reviewed_hash,
            ''''document'''',approval.document,''''release'''',approval.release)
        AND approval.final_authorized_at<=clock_timestamp()
        AND approval.final_expires_at>clock_timestamp()
        AND approval.final_expires_at>approval.final_authorized_at
        AND approval.final_expires_at<=approval.expires_at
        AND approval.final_expires_at<=approval.final_authorized_at+interval ''''300 seconds'''')
        IS NOT TRUE THEN
        RAISE EXCEPTION ''''Separate final CCA-EOB apply authorization missing, mismatched or expired'''' USING ERRCODE=''''42501'''';
    END IF;
END;

    doc := approval.document;
    
DECLARE evidence jsonb := approval.release; archive jsonb := approval.release->''''archive'''';
BEGIN
    IF (jsonb_typeof(evidence)=''''object''''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(evidence) key)=ARRAY[
            ''''actor_id'''',''''archive'''',''''plan_hash'''',''''restoration_verified'''',''''restore_report'''',''''review_ref'''',''''schema'''',''''target'''']::text[]
        AND evidence->>''''schema''''=''''cca_eob_release_v1''''
        AND evidence->''''target''''=doc->''''target'''' AND jsonb_typeof(doc->''''target'''')=''''object''''
        AND evidence->>''''plan_hash''''=reviewed_hash
        AND evidence->''''actor_id''''=doc->''''actor_id''''
        AND jsonb_typeof(evidence->''''actor_id'''')=''''number'''' AND evidence->>''''actor_id'''' ~ ''''^[1-9][0-9]*$''''
        AND evidence->''''restoration_verified''''=''''true''''::jsonb
        AND jsonb_typeof(evidence->''''restore_report'''')=''''string''''
        AND length(btrim(evidence->>''''restore_report'''', chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)))>0
        AND jsonb_typeof(evidence->''''review_ref'''')=''''string''''
        AND length(btrim(evidence->>''''review_ref'''', chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)))>0
        AND jsonb_typeof(archive)=''''object''''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(archive) key)=ARRAY[
            ''''path'''',''''sha256'''',''''size_bytes'''']::text[]
        AND jsonb_typeof(archive->''''path'''')=''''string'''' AND left(archive->>''''path'''',1)=''''/''''
        AND length(archive->>''''path'''')>1 AND right(archive->>''''path'''',1)<>''''/''''
        AND position(''''//'''' in archive->>''''path'''')=0 AND position(chr(92) in archive->>''''path'''')=0
        AND archive->>''''path'''' !~ ''''[[:cntrl:]]'''' AND archive->>''''path'''' !~ ''''(^|/)[.]{1,2}(/|$)''''
        AND jsonb_typeof(archive->''''sha256'''')=''''string'''' AND archive->>''''sha256'''' ~ ''''^[0-9a-f]{64}$''''
        AND jsonb_typeof(archive->''''size_bytes'''')=''''number'''' AND archive->>''''size_bytes'''' ~ ''''^[1-9][0-9]*$''''
        AND (archive->>''''size_bytes'''')::numeric<=9223372036854775807) IS NOT TRUE THEN
        RAISE EXCEPTION ''''Incomplete, malformed or unbound CCA-EOB archive/release evidence'''';
    END IF;
END;

    correction := doc#>''''{plan,correction}'''';
    recipe := doc#>''''{plan,write_recipe}'''';
    IF (doc->>''''schema''''=''''cca_eob_production_plan_v2''''
        AND correction->>''''operation''''=''''cca_eob_v2_20261016_v3''''
        AND correction->''''organization_id''''=''''1''''::jsonb
        AND correction->>''''v2_sha256''''=''''0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160''''
        AND correction->''''ready''''=''''true''''::jsonb AND correction->''''blockers''''=''''[]''''::jsonb
        AND approval.release->>''''schema''''=''''cca_eob_release_v1''''
        AND approval.release->''''target''''=doc->''''target''''
        AND jsonb_typeof(doc->''''target'''')=''''object''''
        AND approval.release->>''''plan_hash''''=reviewed_hash
        AND approval.release->''''actor_id''''=doc->''''actor_id''''
        AND approval.release->''''restoration_verified''''=''''true''''::jsonb
        AND jsonb_typeof(approval.release->''''review_ref'''')=''''string''''
        AND length(btrim(approval.release->>''''review_ref''''))>0
        AND jsonb_typeof(approval.release->''''restore_report'''')=''''string''''
        AND length(btrim(approval.release->>''''restore_report''''))>0
        AND jsonb_typeof(recipe)=''''object'''') IS NOT TRUE THEN
        RAISE EXCEPTION ''''Incomplete or unbound trusted CCA-EOB approval'''';
    END IF;
    
DECLARE target jsonb := doc->''''target''''; live_identity jsonb;
BEGIN
    SELECT jsonb_build_object(''''database'''',current_database(),''''database_oid'''',d.oid::bigint,
        ''''role'''',session_user::text,''''role_oid'''',r.oid::bigint,
        ''''server_address'''',host(inet_server_addr()),''''server_port'''',inet_server_port(),
        ''''postmaster_started_at'''',to_char(pg_postmaster_start_time() AT TIME ZONE ''''UTC'''',
            ''''YYYY-MM-DD"T"HH24:MI:SS.US"Z"''''),
        ''''server_version_num'''',current_setting(''''server_version_num'''')::integer)
        INTO STRICT live_identity FROM pg_database d,pg_roles r
        WHERE d.datname=current_database() AND r.rolname=session_user;
    IF (jsonb_typeof(target)=''''object''''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(target) key)=ARRAY[
            ''''configured_host'''',''''configured_port'''',''''database'''',''''database_oid'''',''''machine_id'''',
            ''''postmaster_started_at'''',''''role'''',''''role_oid'''',''''schema'''',''''server_address'''',''''server_port'''',
            ''''server_version_num'''',''''socket_gid'''',''''socket_uid'''']::text[]
        AND target->>''''schema''''=''''cca_eob_target_v2''''
        AND jsonb_typeof(target->''''machine_id'''')=''''string'''' AND target->>''''machine_id'''' ~ ''''^[0-9a-f]{32}$''''
        AND target-ARRAY[''''schema'''',''''machine_id'''',''''configured_host'''',''''configured_port'''',''''socket_uid'''',''''socket_gid'''']=live_identity
        AND target->''''role''''=to_jsonb(''''polypbase_cca_eob''''::text) AND session_user::text=''''polypbase_cca_eob''''
        AND target->''''configured_port''''=to_jsonb(current_setting(''''port''''))
        AND current_setting(''''server_version_num'''')::integer BETWEEN 180000 AND 189999
        AND NOT pg_is_in_recovery()
        
        AND target->''''configured_host''''=to_jsonb(''''/var/run/postgresql''''::text)
        AND target->''''configured_port''''=to_jsonb(''''5433''''::text)
        AND current_database()=''''polypbase'''' AND current_setting(''''port'''')=''''5433''''
        AND inet_server_addr() IS NULL AND inet_server_port() IS NULL
        AND jsonb_typeof(target->''''socket_uid'''')=''''number'''' AND (target->>''''socket_uid'''')::bigint>0
        AND jsonb_typeof(target->''''socket_gid'''')=''''number'''' AND (target->>''''socket_gid'''')::bigint>0
    ) IS NOT TRUE THEN
        RAISE EXCEPTION ''''STOP: live CCA-EOB target differs from trusted approval'''';
    END IF;
END;

    
DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''''postgres''''::regrole;
    approval_issuer oid := ''''polypbase''''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''''public.cca_eob_approval_v1'''');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''''STOP: missing trusted approval table'''';
    ELSE
        IF obj_description(approval_table.oid,''''pg_class'''') IS DISTINCT FROM ''''Polypbase CCA-EOB trusted approval v2''''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''''r''''
            OR approval_table.relpersistence <> ''''p'''' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''''STOP: unrecognized approval table identity''''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''''n'''')
                IS DISTINCT FROM ARRAY[''''CHECK (singleton)'''',''''PRIMARY KEY (singleton)'''']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''''STOP: approval table schema drift''''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''''r'''',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''''SELECT'''')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''''SELECT'''')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'''')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''''UPDATE'''') <> (a.attname=''''consumed_at'''')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''''INSERT,REFERENCES'''')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''''consumed_at''''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''''UPDATE'''' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''''consumed_at'''',''''UPDATE'''') THEN
            RAISE EXCEPTION ''''STOP: approval table ACL drift''''; END IF;
    END IF;
END;

    LOCK TABLE public.audit_auditlog IN SHARE MODE;
    state := public.cca_eob_read_v1(actor);
    IF state IS DISTINCT FROM doc#>''''{plan,database_snapshot}''''
        OR state->''''incoming_evidence'''' <> ''''false''''::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_each(state->''''histories'''') h WHERE h.value <> ''''false''''::jsonb)
        OR state->''''external_movement_links'''' <> ''''[]''''::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->''''boxes'''') b
            WHERE b->''''merged_into_id'''' IS DISTINCT FROM ''''null''''::jsonb
                OR b->>''''previous_global_code'''' IS DISTINCT FROM '''''''')
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->''''measurements'''') b
            WHERE b->''''duplicate_of_id'''' IS DISTINCT FROM ''''null''''::jsonb
                OR b->''''environment_source_box_id'''' IS DISTINCT FROM ''''null''''::jsonb) THEN
        RAISE EXCEPTION ''''Stale or blocked CCA-EOB snapshot'''';
    END IF;
    IF (jsonb_typeof(correction->''''entries'''')=''''array''''
        AND jsonb_typeof(correction->''''move_order'''')=''''array''''
        AND jsonb_typeof(correction->''''revision_changes'''')=''''array''''
        AND jsonb_typeof(recipe->''''moves'''')=''''array''''
        AND jsonb_typeof(recipe->''''revisions'''')=''''array''''
        AND jsonb_typeof(recipe->''''audits'''')=''''array'''') IS NOT TRUE THEN
        RAISE EXCEPTION ''''Malformed trusted write recipe'''';
    END IF;
    move_count := jsonb_array_length(correction->''''move_order'''');
    IF move_count=0 OR jsonb_array_length(recipe->''''moves'''')<>move_count
        OR jsonb_array_length(recipe->''''audits'''')<>move_count+1
        OR recipe->''''revisions'''' IS DISTINCT FROM correction->''''revision_changes''''
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->''''entries'''') e
            WHERE coalesce(e->>''''classification'''','''''''') NOT IN (''''REASSIGN_REQUIRED'''',''''ALREADY_MATCHES'''',''''OUT_OF_SOURCE'''')
                OR e->>''''measurement_pk'''' IS NULL)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->''''entries'''') e
            GROUP BY (e->>''''measurement_pk'''')::bigint HAVING count(*)<>1)
        OR (SELECT count(*) FROM jsonb_array_elements(correction->''''entries'''') e
            WHERE e->>''''classification''''=''''REASSIGN_REQUIRED'''')<>move_count THEN
        RAISE EXCEPTION ''''CCA-EOB recipe cardinalities differ'''';
    END IF;
    -- The administrator authenticates the complete historical document''''s Python
    -- fingerprint offline; SQL never reserializes JSONB as that historical identity.
    -- SQL independently binds the immutable original evidence and exact 72 moves.
    IF state->''''historical_readings_exist'''' IS DISTINCT FROM ''''false''''::jsonb THEN
        IF (doc#>>''''{plan,thermal_exception,original_plan_hash}''''=''''7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8''''
            AND encode(sha256(convert_to(doc#>>''''{plan,historical_document_text}'''',''''UTF8'''')),''''hex'''')=''''7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8''''
            AND (doc#>>''''{plan,historical_document_text}'''')::jsonb=doc#>''''{plan,thermal_exception,original_document}'''') IS NOT TRUE THEN
            RAISE EXCEPTION ''''Complete pinned historical document authentication failed'''';
        END IF;
        historical_plan := doc#>''''{plan,thermal_exception,original_document,plan,correction}'''';
        IF historical_plan#>''''{baseline,histories}'''' =
            ''''{"subculture_events":[],"subculture_allocations":[],"lineages":[],"transfer_items":[]}''''::jsonb THEN
            historical_plan := jsonb_set(historical_plan,''''{baseline,histories}'''',state->''''histories'''');
        END IF;
        
        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.measurements_biologicalmeasurement,b)) ORDER BY ord),''''[]''''::jsonb)
            FROM jsonb_array_elements(correction#>''''{baseline,measurements}'''') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''''measurements''''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''''{baseline,measurements}'''') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.measurements_biologicalmeasurement,b))) k)) THEN
            RAISE EXCEPTION ''''Complete CCA-EOB measurements baseline differs from locked raw snapshot'''';
        END IF;

        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.cultures_box,b)) ORDER BY ord),''''[]''''::jsonb)
            FROM jsonb_array_elements(correction#>''''{baseline,boxes}'''') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''''boxes''''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''''{baseline,boxes}'''') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.cultures_box,b))) k)) THEN
            RAISE EXCEPTION ''''Complete CCA-EOB boxes baseline differs from locked raw snapshot'''';
        END IF;

        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxlocation,b)) ORDER BY ord),''''[]''''::jsonb)
            FROM jsonb_array_elements(correction#>''''{baseline,location_context,locations}'''') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''''locations''''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''''{baseline,location_context,locations}'''') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxlocation,b))) k)) THEN
            RAISE EXCEPTION ''''Complete CCA-EOB locations baseline differs from locked raw snapshot'''';
        END IF;

        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxmovement,b)) ORDER BY ord),''''[]''''::jsonb)
            FROM jsonb_array_elements(correction#>''''{baseline,location_context,movements}'''') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''''movements''''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''''{baseline,location_context,movements}'''') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxmovement,b))) k)) THEN
            RAISE EXCEPTION ''''Complete CCA-EOB movements baseline differs from locked raw snapshot'''';
        END IF;

        IF correction#>''''{baseline,histories}'''' IS DISTINCT FROM state->''''histories''''
            OR correction#>''''{baseline,location_context,zones}'''' IS DISTINCT FROM state->''''zones''''
            OR correction#>''''{baseline,location_context,external_movement_links}'''' IS DISTINCT FROM state->''''external_movement_links'''' THEN
            RAISE EXCEPTION ''''Complete CCA-EOB relationship baseline differs from locked raw snapshot'''';
        END IF;

        
        IF historical_plan IS DISTINCT FROM correction THEN
            IF (doc#>>''''{plan,thermal_exception,original_plan_hash}''''=''''7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8''''
                AND historical_plan->''''counts''''=''''{"REASSIGN_REQUIRED":72,"ALREADY_MATCHES":8,"OUT_OF_SOURCE":4}''''::jsonb
                AND correction->''''counts''''=''''{"REASSIGN_REQUIRED":72,"ALREADY_MATCHES":8,"OUT_OF_SOURCE":6}''''::jsonb
                AND jsonb_array_length(state->''''addition_audits'''')=2
                AND NOT EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading
                    WHERE biological_measurement_id IN (104688,104689))) IS NOT TRUE THEN
                RAISE EXCEPTION ''''Exact authenticated CCA-EOB additive delta required'''';
            END IF;
            FOR pk, box_pk, addition_time IN VALUES
                (104688::bigint,1836::bigint,''''2026-10-21T09:37:07.809036Z''''::timestamptz),
                (104689::bigint,1837::bigint,''''2026-10-21T09:44:03.511877Z''''::timestamptz) LOOP
                SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''''measurements'''') b WHERE (b->>''''id'''')::bigint=pk;
                SELECT b INTO STRICT reviewed_raw FROM jsonb_array_elements(correction#>''''{baseline,measurements}'''') b
                    WHERE (b->>''''id'''')::bigint=pk;
                SELECT a INTO STRICT audit FROM jsonb_array_elements(state->''''addition_audits'''') a
                    WHERE a#>''''{metadata,measurement_id}''''=to_jsonb(pk);
                SELECT e INTO STRICT entry FROM jsonb_array_elements(correction->''''entries'''') e
                    WHERE (e->>''''measurement_pk'''')::bigint=pk;
                IF (raw->''''box_id''''=to_jsonb(box_pk) AND raw->>''''week_start''''=''''2026-10-19''''
                                    AND date_trunc(''''week'''',(raw->>''''measured_on'''')::date)::date=(raw->>''''week_start'''')::date
                    AND (raw->>''''created_at'''')::timestamptz=addition_time
                    AND raw->>''''origin''''=''''direct_entry'''' AND raw->>''''temporal_precision''''=''''exact_date''''
                    AND raw->''''polyp_state_sequence''''=''''25''''::jsonb AND raw->>''''observer_status''''=''''known''''
                    AND raw->''''user_id''''<>''''null''''::jsonb AND raw->''''original_observer_id''''=raw->''''user_id''''
                    AND raw->''''imported_by_id''''=''''null''''::jsonb AND raw->>''''import_format''''=''''''''
                    AND raw->''''duplicate_of_id''''=''''null''''::jsonb AND raw->''''environment_source_box_id''''=''''null''''::jsonb
                    AND entry->>''''classification''''=''''OUT_OF_SOURCE'''' AND entry->''''before''''=reviewed_raw
                    AND raw-''''created_at''''-''''salinity_psu''''=reviewed_raw-''''created_at''''-''''salinity_psu''''
                    AND (reviewed_raw->>''''created_at'''')::timestamptz=addition_time
                    AND (raw->>''''salinity_psu'''')::numeric IS NOT DISTINCT FROM (reviewed_raw->>''''salinity_psu'''')::numeric
                    AND EXISTS (SELECT 1 FROM public.accounts_organizationmembership
                        WHERE organization_id=1 AND user_id=(raw->>''''user_id'''')::bigint)
                    AND audit->''''organization_id''''=''''1''''::jsonb AND audit->''''user_id''''=raw->''''user_id''''
                    AND audit->>''''action''''=''''entry'''' AND audit->>''''object_type''''=''''box''''
                    AND audit->>''''object_id''''=CASE box_pk WHEN 1836 THEN ''''CCA-EOB-2.005'''' ELSE ''''CCA-EOB-2.006'''' END
                    AND audit->''''edited_at''''=''''null''''::jsonb AND audit->''''edited_by_id''''=''''null''''::jsonb
                    AND audit->''''original_values''''=''''{}''''::jsonb
                    AND (audit->>''''created_at'''')::timestamptz >= (raw->>''''created_at'''')::timestamptz
                    AND audit->''''metadata''''=jsonb_build_object(''''measurement_id'''',pk,''''valeurs'''',jsonb_build_object(
                        ''''date'''',raw->''''measured_on'''',''''polypes'''',raw->''''polyp_count'''',''''ephyrules'''',raw->''''ephyrae_count'''',
                        ''''strobiles'''',raw->''''strobila_count'''',''''salinite_psu'''',CASE WHEN raw->''''salinity_psu''''=''''null''''::jsonb THEN NULL
                            ELSE to_char((raw->>''''salinity_psu'''')::numeric,''''FM999990.00'''') END,
                        ''''statut_culture'''',raw->''''culture_status'''',''''a_verifier'''',raw->''''needs_attention'''',''''note'''',raw->''''notes''''))
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(historical_plan#>''''{baseline,measurements}'''') b
                        WHERE (b->>''''id'''')::bigint=pk)
                    AND (SELECT b->''''polyp_state_revision'''' FROM jsonb_array_elements(historical_plan#>''''{baseline,boxes}'''') b
                        WHERE (b->>''''id'''')::bigint=box_pk)=''''24''''::jsonb
                    AND (SELECT b->''''polyp_state_revision'''' FROM jsonb_array_elements(correction#>''''{baseline,boxes}'''') b
                        WHERE (b->>''''id'''')::bigint=box_pk)=''''25''''::jsonb) IS NOT TRUE THEN
                    RAISE EXCEPTION ''''CCA-EOB addition provenance, audit, thermal state or revision differs'''';
                END IF;
            END LOOP;
            -- Preserve array ordering and every unrelated field in this projection.
            projected := jsonb_set(correction,''''{baseline,measurements}'''',
                (SELECT jsonb_agg(b ORDER BY ord) FROM jsonb_array_elements(correction#>''''{baseline,measurements}'''') WITH ORDINALITY x(b,ord)
                    WHERE (b->>''''id'''')::bigint NOT IN (104688,104689)));
            projected := jsonb_set(projected,''''{entries}'''',
                (SELECT jsonb_agg(e ORDER BY ord) FROM jsonb_array_elements(correction->''''entries'''') WITH ORDINALITY x(e,ord)
                    WHERE (e->>''''measurement_pk'''')::bigint NOT IN (104688,104689)));
            projected := jsonb_set(projected,''''{counts,OUT_OF_SOURCE}'''',''''4''''::jsonb);
            projected := jsonb_set(projected,''''{baseline,boxes}'''',
                (SELECT jsonb_agg(CASE WHEN (b->>''''id'''')::bigint IN (1836,1837)
                    THEN jsonb_set(b,''''{polyp_state_revision}'''',''''24''''::jsonb) ELSE b END ORDER BY ord)
                 FROM jsonb_array_elements(projected#>''''{baseline,boxes}'''') WITH ORDINALITY x(b,ord)));
            IF (SELECT jsonb_agg(r->''''box_id'''' ORDER BY ord) FROM jsonb_array_elements(correction->''''revision_changes'''') WITH ORDINALITY x(r,ord))
                IS DISTINCT FROM (SELECT jsonb_agg(r->''''box_id'''' ORDER BY ord) FROM jsonb_array_elements(historical_plan->''''revision_changes'''') WITH ORDINALITY x(r,ord)) THEN
                RAISE EXCEPTION ''''CCA-EOB revision identities/order changed'''';
            END IF;
            -- The existing writer below recomputes every current revision against
            -- raw rows and touched counts before writing. Historical values stay authenticated.
            projected := jsonb_set(projected,''''{revision_changes}'''',historical_plan->''''revision_changes'''');
            IF projected IS DISTINCT FROM historical_plan THEN
                RAISE EXCEPTION ''''CCA-EOB historical scientific identity changed'''';
            END IF;
        END IF;

        IF (move_count=72
            AND doc#>>''''{plan,thermal_exception,schema}''''=''''cca_eob_unlinked_72_v2''''
            AND doc#>>''''{plan,thermal_exception,original_plan_hash}'''' ~ ''''^[0-9a-f]{64}$''''
            AND jsonb_typeof(doc#>''''{plan,thermal_exception,original_document}'''')=''''object''''
            AND doc#>>''''{plan,thermal_exception,original_document,schema}''''=''''cca_eob_production_plan_v2''''
            AND jsonb_typeof(doc#>''''{plan,thermal_exception,original_document,plan}'''')=''''object''''
            AND jsonb_typeof(historical_plan)=''''object''''
            AND (historical_plan=correction OR projected=historical_plan)) IS NOT TRUE THEN
            RAISE EXCEPTION ''''Authenticated original 72-move thermal exception required'''';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading r
        JOIN jsonb_array_elements(recipe->''''moves'''') m ON r.biological_measurement_id=(m->>''''id'''')::bigint) THEN
        RAISE EXCEPTION ''''A planned measurement has a historical temperature association'''';
    END IF;
    -- The lock helper holds SHARE on readings until commit: no phantom link can appear.
    -- Validate every raw baseline before the first update. JSON null and zero differ.
    FOR i IN 0..move_count-1 LOOP
        pk := (correction->''''move_order''''->>i)::bigint;
        IF pk IS NULL OR pk=ANY(seen) THEN RAISE EXCEPTION ''''Duplicate or missing move ID''''; END IF;
        seen := array_append(seen,pk);
        SELECT e INTO STRICT entry FROM jsonb_array_elements(correction->''''entries'''') e
            WHERE (e->>''''measurement_pk'''')::bigint=pk AND e->>''''classification''''=''''REASSIGN_REQUIRED'''';
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''''measurements'''') b WHERE (b->>''''id'''')::bigint=pk;
        SELECT b INTO STRICT reviewed_raw FROM jsonb_array_elements(doc#>''''{plan,database_snapshot,measurements}'''') b
            WHERE (b->>''''id'''')::bigint=pk;
        before_source := entry->''''before'''';
        move := recipe->''''moves''''->i;
        -- Raw equality protects every column. Python''''s normalized before uses
        -- different timestamp/decimal encodings, so compare its source keys by type.
        IF (raw=reviewed_raw
            AND before_source ?& ARRAY[''''id'''',''''box_id'''',''''measured_on'''',''''week_start'''',
                ''''polyp_count'''',''''ephyrae_count'''',''''strobila_count'''']
            AND (before_source->>''''id'''')::bigint=pk
            AND (before_source->>''''box_id'''')::bigint=(raw->>''''box_id'''')::bigint
            AND (before_source->>''''measured_on'''')::date=(raw->>''''measured_on'''')::date
            AND (before_source->>''''week_start'''')::date=(raw->>''''week_start'''')::date
            AND (before_source->>''''polyp_count'''')::bigint IS NOT DISTINCT FROM (raw->>''''polyp_count'''')::bigint
            AND (before_source->>''''ephyrae_count'''')::bigint IS NOT DISTINCT FROM (raw->>''''ephyrae_count'''')::bigint
            AND (before_source->>''''strobila_count'''')::bigint IS NOT DISTINCT FROM (raw->>''''strobila_count'''')::bigint
            AND (raw->>''''box_id'''')::bigint IN (1835,1836,1837)
            AND (entry->>''''target_box_pk'''')::bigint IN (1835,1836)
            AND entry->>''''target_box''''=CASE (entry->>''''target_box_pk'''')::bigint
                WHEN 1835 THEN ''''CCA-EOB-2.004'''' WHEN 1836 THEN ''''CCA-EOB-2.005'''' END
            AND entry->''''target_code''''=entry->''''target_box''''
            AND entry->''''target_box_pk''''<>raw->''''box_id''''
            AND raw->''''duplicate_of_id''''=''''null''''::jsonb
            AND raw->''''environment_source_box_id''''=''''null''''::jsonb
            AND extract(isoyear FROM (raw->>''''measured_on'''')::date)=2026
            AND date_trunc(''''week'''',(raw->>''''measured_on'''')::date)::date=(raw->>''''week_start'''')::date
            AND (entry->>''''iso_year'''')::integer=2026
            AND (entry->>''''iso_week'''')::integer=extract(week FROM (raw->>''''measured_on'''')::date)
            AND entry->''''source_value''''=jsonb_build_array(raw->''''polyp_count'''',raw->''''ephyrae_count'''')
            AND move=jsonb_build_object(''''id'''',pk,''''before_box_id'''',raw->''''box_id'''',
                ''''target_box_id'''',entry->''''target_box_pk'''',''''week_start'''',raw->''''week_start'''')) IS NOT TRUE THEN
            RAISE EXCEPTION ''''CCA-EOB move does not match approved raw baseline'''';
        END IF;
    END LOOP;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->''''revisions'''') LOOP
        box_pk := (revision->>''''box_id'''')::bigint;
        IF box_pk IS NULL OR box_pk NOT IN (1835,1836,1837) OR box_pk=ANY(revised) THEN
            RAISE EXCEPTION ''''Invalid or duplicate revision box''''; END IF;
        revised := array_append(revised,box_pk);
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''''boxes'''') b WHERE (b->>''''id'''')::bigint=box_pk;
        SELECT count(*) INTO touched FROM jsonb_array_elements(recipe->''''moves'''') m
            WHERE (m->>''''before_box_id'''')::bigint=box_pk OR (m->>''''target_box_id'''')::bigint=box_pk;
        SELECT greatest((raw->>''''polyp_state_revision'''')::bigint+touched,
            coalesce(max((b->>''''polyp_state_sequence'''')::bigint),0)) INTO expected_after
            FROM jsonb_array_elements(state->''''measurements'''') b
            WHERE coalesce((SELECT (m->>''''target_box_id'''')::bigint FROM jsonb_array_elements(recipe->''''moves'''') m
                WHERE m->''''id''''=b->''''id''''),(b->>''''box_id'''')::bigint)=box_pk;
        IF (touched>0 AND revision->''''before''''=raw->''''polyp_state_revision''''
            AND (revision->>''''after'''')::bigint=expected_after) IS NOT TRUE THEN
            RAISE EXCEPTION ''''CCA-EOB revision differs from touched count and final sequences''''; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(recipe->''''moves'''') m
        WHERE NOT ((m->>''''before_box_id'''')::bigint=ANY(revised))
            OR NOT ((m->>''''target_box_id'''')::bigint=ANY(revised))) THEN
        RAISE EXCEPTION ''''Missing touched revision''''; END IF;
    FOR i IN 0..move_count LOOP
        audit := recipe->''''audits''''->i;
        IF (audit->''''organization_id''''=''''1''''::jsonb AND audit->''''user_id''''=doc->''''actor_id''''
            AND jsonb_typeof(audit->''''metadata'''')=''''object''''
            AND NOT (audit->''''metadata'''' ? ''''execution_evidence'''')
            AND jsonb_typeof(audit->''''description'''')=''''string''''
            AND audit#>>''''{metadata,operation}''''=''''cca_eob_v2_20261016_v3'''') IS NOT TRUE THEN
            RAISE EXCEPTION ''''Invalid trusted audit identity or metadata''''; END IF;
        IF i<move_count THEN
            move := recipe->''''moves''''->i;
            SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''''boxes'''') b
                WHERE b->''''id''''=move->''''target_box_id'''';
            IF (audit->>''''action''''=''''update'''' AND audit->>''''object_type''''=''''box''''
                AND audit->''''object_id''''=raw->''''global_code''''
                AND audit#>''''{metadata,measurement_id}''''=move->''''id'''') IS NOT TRUE THEN
                RAISE EXCEPTION ''''Invalid measurement audit target''''; END IF;
        ELSIF (audit->>''''action''''=''''import'''' AND audit->>''''object_type''''=''''historical_correction''''
            AND audit->>''''object_id''''=''''cca_eob_v2_20261016_v3'''') IS NOT TRUE THEN
            RAISE EXCEPTION ''''Invalid correction summary audit'''';
        END IF;
    END LOOP;
    FOR move IN SELECT value FROM jsonb_array_elements(recipe->''''moves'''') LOOP
        UPDATE public.measurements_biologicalmeasurement SET box_id=(move->>''''target_box_id'''')::bigint
            WHERE id=(move->>''''id'''')::bigint AND box_id=(move->>''''before_box_id'''')::bigint
                AND week_start=(move->>''''week_start'''')::date;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION ''''CCA-EOB measurement update cardinality''''; END IF;
    END LOOP;
    -- Recheck all scoped biology columns, not just the moved Box IDs.
    FOR raw IN SELECT value FROM jsonb_array_elements(state->''''measurements'''') LOOP
        before_source := raw;
        SELECT m INTO move FROM jsonb_array_elements(recipe->''''moves'''') m WHERE m->''''id''''=raw->''''id'''';
        IF FOUND THEN before_source := jsonb_set(raw,''''{box_id}'''',move->''''target_box_id''''); END IF;
        IF (SELECT to_jsonb(b) FROM public.measurements_biologicalmeasurement b
            WHERE b.id=(raw->>''''id'''')::bigint) IS DISTINCT FROM before_source THEN
            RAISE EXCEPTION ''''CCA-EOB post-write biological integrity failure'''';
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading WHERE biological_measurement_id=ANY(seen)) THEN
        RAISE EXCEPTION ''''CCA-EOB post-write thermal integrity failure'''';
    END IF;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->''''revisions'''') LOOP
        UPDATE public.cultures_box SET polyp_state_revision=(revision->>''''after'''')::bigint
            WHERE id=(revision->>''''box_id'''')::bigint AND organization_id=1
                AND polyp_state_revision=(revision->>''''before'''')::bigint;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION ''''CCA-EOB revision update cardinality''''; END IF;
    END LOOP;
    FOR audit IN SELECT value FROM jsonb_array_elements(recipe->''''audits'''') LOOP
        -- historical_plan_hash retains the complete historical document''''s identity.
        -- Match the ORM default for a new audit with no edit history.
        INSERT INTO public.audit_auditlog (organization_id,user_id,action,object_type,object_id,
            description,metadata,created_at,edited_at,edited_by_id,original_values)
        VALUES (1,actor,audit->>''''action'''',audit->>''''object_type'''',audit->>''''object_id'''',audit->>''''description'''',
            (audit->''''metadata'''') || jsonb_build_object(''''execution_evidence'''',jsonb_build_object(
                ''''target'''',doc->''''target'''',''''production_plan_hash'''',reviewed_hash,
                ''''authorization'''',''''APPLY_CCA_EOB_ORGANIZATION_1:''''||reviewed_hash,''''release'''',approval.release,
                ''''final_authorized_at'''',approval.final_authorized_at,''''final_expires_at'''',approval.final_expires_at,
                ''''historical_plan_hash'''',doc#>''''{plan,thermal_exception,original_plan_hash}'''')),
            clock_timestamp(),NULL,NULL,''''{}''''::jsonb);
    END LOOP;
    UPDATE public.cca_eob_approval_v1 SET consumed_at=clock_timestamp()
        WHERE singleton AND consumed_at IS NULL AND expires_at>clock_timestamp()
            AND final_expires_at>clock_timestamp();
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION ''''CCA-EOB approval expired during apply (plan or final authorization)''''; END IF;
END
''';
    EXECUTE 'DO $verify$

DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''polypbase_cca_eob'';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''pg_authid'') IS DISTINCT FROM ''Polypbase one-off CCA-EOB V3 correction executor v1'' THEN
        RAISE EXCEPTION ''STOP: unexpected correction role identity/attributes''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''STOP: correction role must have no memberships in either direction''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''pg_authid''::regclass
        AND refobjid=r.oid AND deptype=''o'') THEN
        RAISE EXCEPTION ''STOP: correction role owns an object''; END IF;
    IF has_database_privilege(r.oid,current_database(),''CREATE'')
        OR has_schema_privilege(r.oid,''public'',''CREATE'') THEN
        RAISE EXCEPTION ''STOP: correction role can CREATE''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_''
            AND c.relkind IN (''r'',''p'',''v'',''m'',''f'') LOOP
        IF has_table_privilege(r.oid,relation.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'') THEN
            RAISE EXCEPTION ''STOP: broad table privilege on %'', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''public'' AND relation.relname=''django_migrations'' AND attribute.attname IN (''app'',''name'');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''SELECT'') <> expected THEN
                RAISE EXCEPTION ''STOP: wrong SELECT privilege on %.%'',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''UPDATE,INSERT,REFERENCES'') THEN
                RAISE EXCEPTION ''STOP: direct write privilege on %.%'',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_'' AND c.relkind=''S'' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''USAGE,SELECT,UPDATE'') THEN
            RAISE EXCEPTION ''STOP: sequence privilege on %'',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''STOP: unexpected default privileges''; END IF;
    FOR finding IN 
SELECT ''role-level setting'' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''large-object privilege'' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''SELECT,UPDATE'')
UNION ALL
SELECT ''large-object compatibility privileges'' WHERE current_setting(''lo_compat_privileges'') <> ''off''
UNION ALL
SELECT ''schema authority: '' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''information_schema'' AND left(n.nspname, 3) <> ''pg_''
      AND (has_schema_privilege(r.oid, n.oid, ''CREATE'')
           OR (n.nspname <> ''public'' AND has_schema_privilege(r.oid, n.oid, ''USAGE'')))
UNION ALL
SELECT ''security definer: '' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''pg_catalog'', ''information_schema'')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),
          to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''EXECUTE'')
 LOOP
        RAISE EXCEPTION ''STOP: unexpected authority: %'', finding.label; END LOOP;
END;
$verify$;

DO $helpers$
DECLARE p pg_proc%ROWTYPE;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc candidate JOIN pg_namespace n ON n.oid=candidate.pronamespace
        WHERE n.nspname=''public'' AND starts_with(candidate.proname,''cca_eob_'')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=candidate.oid)) THEN
        RAISE EXCEPTION ''STOP: unrecognized CCA-EOB helper or overload''; END IF;
    
    SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(''public.cca_eob_lock_v1(bigint)'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing helper public.cca_eob_lock_v1(bigint)'';
    ELSE
        IF p.prosrc IS DISTINCT FROM $body$
BEGIN
    IF session_user::text <> ''polypbase_cca_eob'' OR actor_id IS NULL OR actor_id <= 0 THEN
        RAISE EXCEPTION ''Dedicated CCA-EOB login and explicit actor required'' USING ERRCODE = ''42501'';
    END IF;
    IF current_setting(''transaction_isolation'') <> ''read committed'' THEN
        RAISE EXCEPTION ''CCA-EOB apply requires READ COMMITTED'' USING ERRCODE = ''25001'';
    END IF;
    PERFORM id FROM public.cultures_box
        WHERE organization_id = 1 AND id IN (1835,1836,1837) ORDER BY id FOR UPDATE;
    IF (SELECT count(*) FROM public.cultures_box WHERE organization_id = 1 AND
        (id, global_code) IN ((1835,''CCA-EOB-2.004''),(1836,''CCA-EOB-2.005''),(1837,''CCA-EOB-2.006''))) <> 3 THEN
        RAISE EXCEPTION ''CCA-EOB Box identities changed'' USING ERRCODE = ''23514'';
    END IF;
    PERFORM id FROM public.organizations_organization WHERE id = 1 FOR NO KEY UPDATE;
    PERFORM id FROM public.accounts_organizationmembership
        WHERE organization_id = 1 AND user_id = actor_id ORDER BY id FOR UPDATE;
    PERFORM id FROM public.auth_user WHERE id = actor_id FOR NO KEY UPDATE;
    IF NOT EXISTS (SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1 AND m.is_active AND m.role = ''admin'') THEN
        RAISE EXCEPTION ''Active organization 1 admin required'' USING ERRCODE = ''42501'';
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
$body$
            OR p.proowner <> ''polypbase''::regrole OR NOT p.prosecdef
            OR p.proconfig IS DISTINCT FROM ARRAY[''search_path=pg_catalog, pg_temp'']::text[]
            OR p.prolang <> (SELECT oid FROM pg_language WHERE lanname=''plpgsql'')
            OR p.provolatile <> ''v'' OR p.proparallel <> ''u'' OR p.prokind <> ''f''
            OR p.proisstrict OR p.proleakproof OR p.proretset
            OR p.prorettype <> ''void''::regtype OR p.pronargs <> 1
            OR p.proargtypes::text <> ''20'' OR p.pronargdefaults <> 0
            OR p.proargnames IS DISTINCT FROM ARRAY[''actor_id'']::text[]
            OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
            OR p.proargdefaults IS NOT NULL OR p.probin IS NOT NULL OR p.prosqlbody IS NOT NULL
            OR p.prosupport <> 0 OR p.provariadic <> 0 OR p.protrftypes IS NOT NULL
            OR p.procost <> 100 OR p.prorows <> 0 THEN
            RAISE EXCEPTION ''STOP: unexpected helper definition/metadata: public.cca_eob_lock_v1(bigint)''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))) x
            WHERE x.privilege_type <> ''EXECUTE'' OR x.is_grantable
                OR x.grantor <> p.proowner OR x.grantee NOT IN (p.proowner,''polypbase_cca_eob''::regrole))
            OR NOT has_function_privilege(''polypbase_cca_eob'',''public.cca_eob_lock_v1(bigint)'',''EXECUTE'')
            OR (SELECT count(*) FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))))<>2 THEN
            RAISE EXCEPTION ''STOP: unexpected helper ACL: public.cca_eob_lock_v1(bigint)''; END IF;
    END IF;

    SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(''public.cca_eob_read_v1(bigint)'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing helper public.cca_eob_read_v1(bigint)'';
    ELSE
        IF p.prosrc IS DISTINCT FROM $body$
DECLARE result jsonb;
BEGIN
    IF session_user::text <> ''polypbase_cca_eob'' OR actor_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.auth_user u
        JOIN public.accounts_organizationmembership m ON m.user_id = u.id
        WHERE u.id = actor_id AND u.is_active AND m.organization_id = 1
            AND m.is_active AND m.role = ''admin''
    ) THEN
        RAISE EXCEPTION ''Dedicated CCA-EOB login and active organization 1 admin required'' USING ERRCODE = ''42501'';
    END IF;
    
DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''polypbase_cca_eob'';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''pg_authid'') IS DISTINCT FROM ''Polypbase one-off CCA-EOB V3 correction executor v1'' THEN
        RAISE EXCEPTION ''STOP: unexpected correction role identity/attributes''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''STOP: correction role must have no memberships in either direction''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''pg_authid''::regclass
        AND refobjid=r.oid AND deptype=''o'') THEN
        RAISE EXCEPTION ''STOP: correction role owns an object''; END IF;
    IF has_database_privilege(r.oid,current_database(),''CREATE'')
        OR has_schema_privilege(r.oid,''public'',''CREATE'') THEN
        RAISE EXCEPTION ''STOP: correction role can CREATE''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_''
            AND c.relkind IN (''r'',''p'',''v'',''m'',''f'') LOOP
        IF has_table_privilege(r.oid,relation.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'') THEN
            RAISE EXCEPTION ''STOP: broad table privilege on %'', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''public'' AND relation.relname=''django_migrations'' AND attribute.attname IN (''app'',''name'');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''SELECT'') <> expected THEN
                RAISE EXCEPTION ''STOP: wrong SELECT privilege on %.%'',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''UPDATE,INSERT,REFERENCES'') THEN
                RAISE EXCEPTION ''STOP: direct write privilege on %.%'',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_'' AND c.relkind=''S'' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''USAGE,SELECT,UPDATE'') THEN
            RAISE EXCEPTION ''STOP: sequence privilege on %'',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''STOP: unexpected default privileges''; END IF;
    FOR finding IN 
SELECT ''role-level setting'' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''large-object privilege'' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''SELECT,UPDATE'')
UNION ALL
SELECT ''large-object compatibility privileges'' WHERE current_setting(''lo_compat_privileges'') <> ''off''
UNION ALL
SELECT ''schema authority: '' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''information_schema'' AND left(n.nspname, 3) <> ''pg_''
      AND (has_schema_privilege(r.oid, n.oid, ''CREATE'')
           OR (n.nspname <> ''public'' AND has_schema_privilege(r.oid, n.oid, ''USAGE'')))
UNION ALL
SELECT ''security definer: '' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''pg_catalog'', ''information_schema'')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),
          to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''EXECUTE'')
 LOOP
        RAISE EXCEPTION ''STOP: unexpected authority: %'', finding.label; END LOOP;
END;

    IF (SELECT count(*) FROM public.cultures_box WHERE organization_id = 1 AND
        (id, global_code) IN ((1835,''CCA-EOB-2.004''),(1836,''CCA-EOB-2.005''),(1837,''CCA-EOB-2.006''))) <> 3 THEN
        RAISE EXCEPTION ''CCA-EOB Box identities changed'' USING ERRCODE = ''23514'';
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
        ''boxes'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM boxes b),
        ''measurements'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM biology b),
        ''locations'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM locations b),
        ''movements'', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY id),''[]''::jsonb) FROM movements b),
        ''external_movement_links'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
            ''id'',m.id,''box_id'',m.box_id,''origin_location_id'',m.origin_location_id,
            ''destination_location_id'',m.destination_location_id) ORDER BY m.id),''[]''::jsonb)
            FROM public.cultures_boxmovement m WHERE m.box_id NOT IN (SELECT id FROM boxes)
            AND (m.origin_location_id IN (SELECT id FROM locations)
                OR m.destination_location_id IN (SELECT id FROM locations))),
        ''zones'', (SELECT coalesce(jsonb_agg(jsonb_build_object(''id'',z.id,
            ''organization_id'',z.organization_id) ORDER BY z.id),''[]''::jsonb)
            FROM public.cultures_thermalzone z WHERE z.id IN (SELECT id FROM zone_ids)),
        ''attribution_user_ids'', (SELECT coalesce(jsonb_agg(user_id ORDER BY user_id),''[]''::jsonb)
            FROM (SELECT DISTINCT user_id FROM public.accounts_organizationmembership
                WHERE organization_id = 1 AND user_id IN (
                    SELECT original_observer_id FROM biology UNION SELECT imported_by_id FROM biology)) members),
        ''histories'', jsonb_build_object(
            ''subculture_events'', EXISTS (SELECT 1 FROM public.cultures_subcultureevent WHERE parent_box_id IN (SELECT id FROM boxes)),
            ''subculture_allocations'', EXISTS (SELECT 1 FROM public.cultures_subcultureallocation WHERE child_box_id IN (SELECT id FROM boxes)),
            ''lineages'', EXISTS (SELECT 1 FROM public.cultures_boxlineage WHERE parent_box_id IN (SELECT id FROM boxes) OR child_box_id IN (SELECT id FROM boxes)),
            ''transfer_items'', EXISTS (SELECT 1 FROM public.cultures_transferitem WHERE source_box_id IN (SELECT id FROM boxes))),
        ''incoming_evidence'', EXISTS (SELECT 1 FROM public.measurements_biologicalmeasurement
            WHERE box_id NOT IN (SELECT id FROM boxes) AND
                (duplicate_of_id IN (SELECT id FROM biology) OR environment_source_box_id IN (SELECT id FROM boxes))),
        ''addition_audits'', (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.id),''[]''::jsonb)
            FROM public.audit_auditlog a WHERE a.metadata->>''measurement_id'' IN (''104688'',''104689'')),
        ''historical_readings_exist'', EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading),
        ''historical_linked_measurement_ids'', (SELECT coalesce(jsonb_agg(r.biological_measurement_id
            ORDER BY r.biological_measurement_id),''[]''::jsonb)
            FROM public.measurements_historicaltemperaturereading r
            WHERE r.biological_measurement_id IN (SELECT id FROM biology)),
        ''guard_state'', jsonb_build_object(
            ''triggers'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''binding'',to_jsonb(t),''table'',c.relname,''definition'',pg_get_triggerdef(t.oid),
                ''function_definition'',pg_get_functiondef(p.oid),''function'',to_jsonb(p),
                ''function_owner'',pg_get_userbyid(p.proowner)) ORDER BY c.relname,t.tgname),''[]''::jsonb)
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE n.nspname=''public'' AND NOT t.tgisinternal AND starts_with(t.tgname,''historical_'')),
            ''historical_functions'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''catalog'',to_jsonb(p),''definition'',pg_get_functiondef(p.oid),
                ''owner'',pg_get_userbyid(p.proowner)) ORDER BY p.oid),''[]''::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=''public'' AND starts_with(p.proname,''historical_temperature_'')),
            ''helpers'', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                ''catalog'',to_jsonb(p),''definition'',pg_get_functiondef(p.oid),
                ''owner'',pg_get_userbyid(p.proowner)) ORDER BY p.oid),''[]''::jsonb)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=''public'' AND starts_with(p.proname,''cca_eob_'')))
    ) INTO result;
    RETURN result;
END
$body$
            OR p.proowner <> ''polypbase''::regrole OR NOT p.prosecdef
            OR p.proconfig IS DISTINCT FROM ARRAY[''search_path=pg_catalog, pg_temp'',''TimeZone=UTC'',''DateStyle=ISO, YMD'']::text[]
            OR p.prolang <> (SELECT oid FROM pg_language WHERE lanname=''plpgsql'')
            OR p.provolatile <> ''v'' OR p.proparallel <> ''u'' OR p.prokind <> ''f''
            OR p.proisstrict OR p.proleakproof OR p.proretset
            OR p.prorettype <> ''jsonb''::regtype OR p.pronargs <> 1
            OR p.proargtypes::text <> ''20'' OR p.pronargdefaults <> 0
            OR p.proargnames IS DISTINCT FROM ARRAY[''actor_id'']::text[]
            OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
            OR p.proargdefaults IS NOT NULL OR p.probin IS NOT NULL OR p.prosqlbody IS NOT NULL
            OR p.prosupport <> 0 OR p.provariadic <> 0 OR p.protrftypes IS NOT NULL
            OR p.procost <> 100 OR p.prorows <> 0 THEN
            RAISE EXCEPTION ''STOP: unexpected helper definition/metadata: public.cca_eob_read_v1(bigint)''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))) x
            WHERE x.privilege_type <> ''EXECUTE'' OR x.is_grantable
                OR x.grantor <> p.proowner OR x.grantee NOT IN (p.proowner,''polypbase_cca_eob''::regrole))
            OR NOT has_function_privilege(''polypbase_cca_eob'',''public.cca_eob_read_v1(bigint)'',''EXECUTE'')
            OR (SELECT count(*) FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))))<>2 THEN
            RAISE EXCEPTION ''STOP: unexpected helper ACL: public.cca_eob_read_v1(bigint)''; END IF;
    END IF;

    SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(''public.cca_eob_apply_v1(text)'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing helper public.cca_eob_apply_v1(text)'';
    ELSE
        IF p.prosrc IS DISTINCT FROM $body$
DECLARE
    approval public.cca_eob_approval_v1%ROWTYPE;
    locked_approval public.cca_eob_approval_v1%ROWTYPE;
    doc jsonb; correction jsonb; recipe jsonb; state jsonb; entry jsonb; move jsonb;
    revision jsonb; audit jsonb; raw jsonb; reviewed_raw jsonb; before_source jsonb;
    historical_plan jsonb; projected jsonb; addition_time timestamptz;
    actor bigint; pk bigint; box_pk bigint;
    seen bigint[] := ARRAY[]::bigint[]; revised bigint[] := ARRAY[]::bigint[];
    touched bigint; expected_after bigint; changed bigint; move_count integer; i integer;
BEGIN
    IF session_user::text <> ''polypbase_cca_eob'' OR reviewed_hash IS NULL
        OR reviewed_hash !~ ''^[0-9a-f]{64}$'' THEN
        RAISE EXCEPTION ''Dedicated CCA-EOB login and reviewed hash required'' USING ERRCODE = ''42501'';
    END IF;
    
DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''postgres''::regrole;
    approval_issuer oid := ''polypbase''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''public.cca_eob_approval_v1'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing trusted approval table'';
    ELSE
        IF obj_description(approval_table.oid,''pg_class'') IS DISTINCT FROM ''Polypbase CCA-EOB trusted approval v2''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''r''
            OR approval_table.relpersistence <> ''p'' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''STOP: unrecognized approval table identity''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''n'')
                IS DISTINCT FROM ARRAY[''CHECK (singleton)'',''PRIMARY KEY (singleton)'']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''STOP: approval table schema drift''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''r'',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''SELECT'')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''SELECT'')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''UPDATE'') <> (a.attname=''consumed_at'')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''INSERT,REFERENCES'')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''consumed_at''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''UPDATE'' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''consumed_at'',''UPDATE'') THEN
            RAISE EXCEPTION ''STOP: approval table ACL drift''; END IF;
    END IF;
END;

    
DECLARE r record; relation record; attribute record; finding record; expected boolean;
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname=''polypbase_cca_eob'';
    IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR r.rolbypassrls OR r.rolinherit OR r.rolconnlimit<>1 OR shobj_description(r.oid,''pg_authid'') IS DISTINCT FROM ''Polypbase one-off CCA-EOB V3 correction executor v1'' THEN
        RAISE EXCEPTION ''STOP: unexpected correction role identity/attributes''; END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid OR roleid=r.oid) THEN
        RAISE EXCEPTION ''STOP: correction role must have no memberships in either direction''; END IF;
    IF EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid=''pg_authid''::regclass
        AND refobjid=r.oid AND deptype=''o'') THEN
        RAISE EXCEPTION ''STOP: correction role owns an object''; END IF;
    IF has_database_privilege(r.oid,current_database(),''CREATE'')
        OR has_schema_privilege(r.oid,''public'',''CREATE'') THEN
        RAISE EXCEPTION ''STOP: correction role can CREATE''; END IF;
    FOR relation IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_''
            AND c.relkind IN (''r'',''p'',''v'',''m'',''f'') LOOP
        IF has_table_privilege(r.oid,relation.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'') THEN
            RAISE EXCEPTION ''STOP: broad table privilege on %'', relation.relname; END IF;
        FOR attribute IN SELECT attnum,attname FROM pg_attribute
            WHERE attrelid=relation.oid AND attnum>0 AND NOT attisdropped LOOP
            expected := relation.nspname=''public'' AND relation.relname=''django_migrations'' AND attribute.attname IN (''app'',''name'');
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''SELECT'') <> expected THEN
                RAISE EXCEPTION ''STOP: wrong SELECT privilege on %.%'',relation.relname,attribute.attname; END IF;
            IF has_column_privilege(r.oid,relation.oid,attribute.attnum,''UPDATE,INSERT,REFERENCES'') THEN
                RAISE EXCEPTION ''STOP: direct write privilege on %.%'',relation.relname,attribute.attname; END IF;
        END LOOP;
    END LOOP;
    FOR relation IN SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname<>''information_schema'' AND left(n.nspname,3)<>''pg_'' AND c.relkind=''S'' LOOP
        IF has_sequence_privilege(r.oid,relation.oid,''USAGE,SELECT,UPDATE'') THEN
            RAISE EXCEPTION ''STOP: sequence privilege on %'',relation.relname; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) x WHERE x.grantee=r.oid) THEN
        RAISE EXCEPTION ''STOP: unexpected default privileges''; END IF;
    FOR finding IN 
SELECT ''role-level setting'' AS label FROM pg_db_role_setting WHERE setrole = r.oid
UNION ALL
SELECT ''large-object privilege'' FROM pg_largeobject_metadata m
    WHERE has_largeobject_privilege(r.oid, m.oid, ''SELECT,UPDATE'')
UNION ALL
SELECT ''large-object compatibility privileges'' WHERE current_setting(''lo_compat_privileges'') <> ''off''
UNION ALL
SELECT ''schema authority: '' || n.nspname FROM pg_namespace n
    WHERE n.nspname <> ''information_schema'' AND left(n.nspname, 3) <> ''pg_''
      AND (has_schema_privilege(r.oid, n.oid, ''CREATE'')
           OR (n.nspname <> ''public'' AND has_schema_privilege(r.oid, n.oid, ''USAGE'')))
UNION ALL
SELECT ''security definer: '' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND n.nspname NOT IN (''pg_catalog'', ''information_schema'')
      AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[to_regprocedure(''public.cca_eob_lock_v1(bigint)''),
          to_regprocedure(''public.cca_eob_read_v1(bigint)''),to_regprocedure(''public.cca_eob_apply_v1(text)'')]) known(oid) WHERE known.oid=p.oid)
      AND has_function_privilege(r.oid, p.oid, ''EXECUTE'')
 LOOP
        RAISE EXCEPTION ''STOP: unexpected authority: %'', finding.label; END LOOP;
END;

    IF current_setting(''session_replication_role'') <> ''origin''
        OR current_setting(''transaction_isolation'') <> ''read committed'' THEN
        RAISE EXCEPTION ''CCA-EOB requires origin and READ COMMITTED'' USING ERRCODE = ''25001'';
    END IF;
    SELECT * INTO STRICT approval FROM public.cca_eob_approval_v1
        WHERE singleton AND plan_hash=reviewed_hash AND consumed_at IS NULL AND expires_at>clock_timestamp();
    
BEGIN
    IF (approval.final_authorization=jsonb_build_object(
            ''schema'',''cca_eob_final_apply_v1'',''plan_hash'',reviewed_hash,
            ''document'',approval.document,''release'',approval.release)
        AND approval.final_authorized_at<=clock_timestamp()
        AND approval.final_expires_at>clock_timestamp()
        AND approval.final_expires_at>approval.final_authorized_at
        AND approval.final_expires_at<=approval.expires_at
        AND approval.final_expires_at<=approval.final_authorized_at+interval ''300 seconds'')
        IS NOT TRUE THEN
        RAISE EXCEPTION ''Separate final CCA-EOB apply authorization missing, mismatched or expired'' USING ERRCODE=''42501'';
    END IF;
END;

    actor := (approval.document->>''actor_id'')::bigint;
    PERFORM public.cca_eob_lock_v1(actor);
    SELECT * INTO STRICT locked_approval FROM public.cca_eob_approval_v1 WHERE singleton FOR UPDATE;
    IF to_jsonb(approval) IS DISTINCT FROM to_jsonb(locked_approval)
        OR locked_approval.consumed_at IS NOT NULL OR locked_approval.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION ''CCA-EOB approval changed, expired or consumed'';
    END IF;
    
BEGIN
    IF (approval.final_authorization=jsonb_build_object(
            ''schema'',''cca_eob_final_apply_v1'',''plan_hash'',reviewed_hash,
            ''document'',approval.document,''release'',approval.release)
        AND approval.final_authorized_at<=clock_timestamp()
        AND approval.final_expires_at>clock_timestamp()
        AND approval.final_expires_at>approval.final_authorized_at
        AND approval.final_expires_at<=approval.expires_at
        AND approval.final_expires_at<=approval.final_authorized_at+interval ''300 seconds'')
        IS NOT TRUE THEN
        RAISE EXCEPTION ''Separate final CCA-EOB apply authorization missing, mismatched or expired'' USING ERRCODE=''42501'';
    END IF;
END;

    doc := approval.document;
    
DECLARE evidence jsonb := approval.release; archive jsonb := approval.release->''archive'';
BEGIN
    IF (jsonb_typeof(evidence)=''object''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(evidence) key)=ARRAY[
            ''actor_id'',''archive'',''plan_hash'',''restoration_verified'',''restore_report'',''review_ref'',''schema'',''target'']::text[]
        AND evidence->>''schema''=''cca_eob_release_v1''
        AND evidence->''target''=doc->''target'' AND jsonb_typeof(doc->''target'')=''object''
        AND evidence->>''plan_hash''=reviewed_hash
        AND evidence->''actor_id''=doc->''actor_id''
        AND jsonb_typeof(evidence->''actor_id'')=''number'' AND evidence->>''actor_id'' ~ ''^[1-9][0-9]*$''
        AND evidence->''restoration_verified''=''true''::jsonb
        AND jsonb_typeof(evidence->''restore_report'')=''string''
        AND length(btrim(evidence->>''restore_report'', chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)))>0
        AND jsonb_typeof(evidence->''review_ref'')=''string''
        AND length(btrim(evidence->>''review_ref'', chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288)))>0
        AND jsonb_typeof(archive)=''object''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(archive) key)=ARRAY[
            ''path'',''sha256'',''size_bytes'']::text[]
        AND jsonb_typeof(archive->''path'')=''string'' AND left(archive->>''path'',1)=''/''
        AND length(archive->>''path'')>1 AND right(archive->>''path'',1)<>''/''
        AND position(''//'' in archive->>''path'')=0 AND position(chr(92) in archive->>''path'')=0
        AND archive->>''path'' !~ ''[[:cntrl:]]'' AND archive->>''path'' !~ ''(^|/)[.]{1,2}(/|$)''
        AND jsonb_typeof(archive->''sha256'')=''string'' AND archive->>''sha256'' ~ ''^[0-9a-f]{64}$''
        AND jsonb_typeof(archive->''size_bytes'')=''number'' AND archive->>''size_bytes'' ~ ''^[1-9][0-9]*$''
        AND (archive->>''size_bytes'')::numeric<=9223372036854775807) IS NOT TRUE THEN
        RAISE EXCEPTION ''Incomplete, malformed or unbound CCA-EOB archive/release evidence'';
    END IF;
END;

    correction := doc#>''{plan,correction}'';
    recipe := doc#>''{plan,write_recipe}'';
    IF (doc->>''schema''=''cca_eob_production_plan_v2''
        AND correction->>''operation''=''cca_eob_v2_20261016_v3''
        AND correction->''organization_id''=''1''::jsonb
        AND correction->>''v2_sha256''=''0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160''
        AND correction->''ready''=''true''::jsonb AND correction->''blockers''=''[]''::jsonb
        AND approval.release->>''schema''=''cca_eob_release_v1''
        AND approval.release->''target''=doc->''target''
        AND jsonb_typeof(doc->''target'')=''object''
        AND approval.release->>''plan_hash''=reviewed_hash
        AND approval.release->''actor_id''=doc->''actor_id''
        AND approval.release->''restoration_verified''=''true''::jsonb
        AND jsonb_typeof(approval.release->''review_ref'')=''string''
        AND length(btrim(approval.release->>''review_ref''))>0
        AND jsonb_typeof(approval.release->''restore_report'')=''string''
        AND length(btrim(approval.release->>''restore_report''))>0
        AND jsonb_typeof(recipe)=''object'') IS NOT TRUE THEN
        RAISE EXCEPTION ''Incomplete or unbound trusted CCA-EOB approval'';
    END IF;
    
DECLARE target jsonb := doc->''target''; live_identity jsonb;
BEGIN
    SELECT jsonb_build_object(''database'',current_database(),''database_oid'',d.oid::bigint,
        ''role'',session_user::text,''role_oid'',r.oid::bigint,
        ''server_address'',host(inet_server_addr()),''server_port'',inet_server_port(),
        ''postmaster_started_at'',to_char(pg_postmaster_start_time() AT TIME ZONE ''UTC'',
            ''YYYY-MM-DD"T"HH24:MI:SS.US"Z"''),
        ''server_version_num'',current_setting(''server_version_num'')::integer)
        INTO STRICT live_identity FROM pg_database d,pg_roles r
        WHERE d.datname=current_database() AND r.rolname=session_user;
    IF (jsonb_typeof(target)=''object''
        AND (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(target) key)=ARRAY[
            ''configured_host'',''configured_port'',''database'',''database_oid'',''machine_id'',
            ''postmaster_started_at'',''role'',''role_oid'',''schema'',''server_address'',''server_port'',
            ''server_version_num'',''socket_gid'',''socket_uid'']::text[]
        AND target->>''schema''=''cca_eob_target_v2''
        AND jsonb_typeof(target->''machine_id'')=''string'' AND target->>''machine_id'' ~ ''^[0-9a-f]{32}$''
        AND target-ARRAY[''schema'',''machine_id'',''configured_host'',''configured_port'',''socket_uid'',''socket_gid'']=live_identity
        AND target->''role''=to_jsonb(''polypbase_cca_eob''::text) AND session_user::text=''polypbase_cca_eob''
        AND target->''configured_port''=to_jsonb(current_setting(''port''))
        AND current_setting(''server_version_num'')::integer BETWEEN 180000 AND 189999
        AND NOT pg_is_in_recovery()
        
        AND target->''configured_host''=to_jsonb(''/var/run/postgresql''::text)
        AND target->''configured_port''=to_jsonb(''5433''::text)
        AND current_database()=''polypbase'' AND current_setting(''port'')=''5433''
        AND inet_server_addr() IS NULL AND inet_server_port() IS NULL
        AND jsonb_typeof(target->''socket_uid'')=''number'' AND (target->>''socket_uid'')::bigint>0
        AND jsonb_typeof(target->''socket_gid'')=''number'' AND (target->>''socket_gid'')::bigint>0
    ) IS NOT TRUE THEN
        RAISE EXCEPTION ''STOP: live CCA-EOB target differs from trusted approval'';
    END IF;
END;

    
DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''postgres''::regrole;
    approval_issuer oid := ''polypbase''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''public.cca_eob_approval_v1'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing trusted approval table'';
    ELSE
        IF obj_description(approval_table.oid,''pg_class'') IS DISTINCT FROM ''Polypbase CCA-EOB trusted approval v2''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''r''
            OR approval_table.relpersistence <> ''p'' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''STOP: unrecognized approval table identity''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''n'')
                IS DISTINCT FROM ARRAY[''CHECK (singleton)'',''PRIMARY KEY (singleton)'']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''STOP: approval table schema drift''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''r'',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''SELECT'')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''SELECT'')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''UPDATE'') <> (a.attname=''consumed_at'')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''INSERT,REFERENCES'')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''consumed_at''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''UPDATE'' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''consumed_at'',''UPDATE'') THEN
            RAISE EXCEPTION ''STOP: approval table ACL drift''; END IF;
    END IF;
END;

    LOCK TABLE public.audit_auditlog IN SHARE MODE;
    state := public.cca_eob_read_v1(actor);
    IF state IS DISTINCT FROM doc#>''{plan,database_snapshot}''
        OR state->''incoming_evidence'' <> ''false''::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_each(state->''histories'') h WHERE h.value <> ''false''::jsonb)
        OR state->''external_movement_links'' <> ''[]''::jsonb
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->''boxes'') b
            WHERE b->''merged_into_id'' IS DISTINCT FROM ''null''::jsonb
                OR b->>''previous_global_code'' IS DISTINCT FROM '''')
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(state->''measurements'') b
            WHERE b->''duplicate_of_id'' IS DISTINCT FROM ''null''::jsonb
                OR b->''environment_source_box_id'' IS DISTINCT FROM ''null''::jsonb) THEN
        RAISE EXCEPTION ''Stale or blocked CCA-EOB snapshot'';
    END IF;
    IF (jsonb_typeof(correction->''entries'')=''array''
        AND jsonb_typeof(correction->''move_order'')=''array''
        AND jsonb_typeof(correction->''revision_changes'')=''array''
        AND jsonb_typeof(recipe->''moves'')=''array''
        AND jsonb_typeof(recipe->''revisions'')=''array''
        AND jsonb_typeof(recipe->''audits'')=''array'') IS NOT TRUE THEN
        RAISE EXCEPTION ''Malformed trusted write recipe'';
    END IF;
    move_count := jsonb_array_length(correction->''move_order'');
    IF move_count=0 OR jsonb_array_length(recipe->''moves'')<>move_count
        OR jsonb_array_length(recipe->''audits'')<>move_count+1
        OR recipe->''revisions'' IS DISTINCT FROM correction->''revision_changes''
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->''entries'') e
            WHERE coalesce(e->>''classification'','''') NOT IN (''REASSIGN_REQUIRED'',''ALREADY_MATCHES'',''OUT_OF_SOURCE'')
                OR e->>''measurement_pk'' IS NULL)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction->''entries'') e
            GROUP BY (e->>''measurement_pk'')::bigint HAVING count(*)<>1)
        OR (SELECT count(*) FROM jsonb_array_elements(correction->''entries'') e
            WHERE e->>''classification''=''REASSIGN_REQUIRED'')<>move_count THEN
        RAISE EXCEPTION ''CCA-EOB recipe cardinalities differ'';
    END IF;
    -- The administrator authenticates the complete historical document''s Python
    -- fingerprint offline; SQL never reserializes JSONB as that historical identity.
    -- SQL independently binds the immutable original evidence and exact 72 moves.
    IF state->''historical_readings_exist'' IS DISTINCT FROM ''false''::jsonb THEN
        IF (doc#>>''{plan,thermal_exception,original_plan_hash}''=''7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8''
            AND encode(sha256(convert_to(doc#>>''{plan,historical_document_text}'',''UTF8'')),''hex'')=''7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8''
            AND (doc#>>''{plan,historical_document_text}'')::jsonb=doc#>''{plan,thermal_exception,original_document}'') IS NOT TRUE THEN
            RAISE EXCEPTION ''Complete pinned historical document authentication failed'';
        END IF;
        historical_plan := doc#>''{plan,thermal_exception,original_document,plan,correction}'';
        IF historical_plan#>''{baseline,histories}'' =
            ''{"subculture_events":[],"subculture_allocations":[],"lineages":[],"transfer_items":[]}''::jsonb THEN
            historical_plan := jsonb_set(historical_plan,''{baseline,histories}'',state->''histories'');
        END IF;
        
        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.measurements_biologicalmeasurement,b)) ORDER BY ord),''[]''::jsonb)
            FROM jsonb_array_elements(correction#>''{baseline,measurements}'') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''measurements''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''{baseline,measurements}'') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.measurements_biologicalmeasurement,b))) k)) THEN
            RAISE EXCEPTION ''Complete CCA-EOB measurements baseline differs from locked raw snapshot'';
        END IF;

        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.cultures_box,b)) ORDER BY ord),''[]''::jsonb)
            FROM jsonb_array_elements(correction#>''{baseline,boxes}'') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''boxes''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''{baseline,boxes}'') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.cultures_box,b))) k)) THEN
            RAISE EXCEPTION ''Complete CCA-EOB boxes baseline differs from locked raw snapshot'';
        END IF;

        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxlocation,b)) ORDER BY ord),''[]''::jsonb)
            FROM jsonb_array_elements(correction#>''{baseline,location_context,locations}'') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''locations''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''{baseline,location_context,locations}'') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxlocation,b))) k)) THEN
            RAISE EXCEPTION ''Complete CCA-EOB locations baseline differs from locked raw snapshot'';
        END IF;

        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxmovement,b)) ORDER BY ord),''[]''::jsonb)
            FROM jsonb_array_elements(correction#>''{baseline,location_context,movements}'') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->''movements''
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>''{baseline,location_context,movements}'') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.cultures_boxmovement,b))) k)) THEN
            RAISE EXCEPTION ''Complete CCA-EOB movements baseline differs from locked raw snapshot'';
        END IF;

        IF correction#>''{baseline,histories}'' IS DISTINCT FROM state->''histories''
            OR correction#>''{baseline,location_context,zones}'' IS DISTINCT FROM state->''zones''
            OR correction#>''{baseline,location_context,external_movement_links}'' IS DISTINCT FROM state->''external_movement_links'' THEN
            RAISE EXCEPTION ''Complete CCA-EOB relationship baseline differs from locked raw snapshot'';
        END IF;

        
        IF historical_plan IS DISTINCT FROM correction THEN
            IF (doc#>>''{plan,thermal_exception,original_plan_hash}''=''7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8''
                AND historical_plan->''counts''=''{"REASSIGN_REQUIRED":72,"ALREADY_MATCHES":8,"OUT_OF_SOURCE":4}''::jsonb
                AND correction->''counts''=''{"REASSIGN_REQUIRED":72,"ALREADY_MATCHES":8,"OUT_OF_SOURCE":6}''::jsonb
                AND jsonb_array_length(state->''addition_audits'')=2
                AND NOT EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading
                    WHERE biological_measurement_id IN (104688,104689))) IS NOT TRUE THEN
                RAISE EXCEPTION ''Exact authenticated CCA-EOB additive delta required'';
            END IF;
            FOR pk, box_pk, addition_time IN VALUES
                (104688::bigint,1836::bigint,''2026-10-21T09:37:07.809036Z''::timestamptz),
                (104689::bigint,1837::bigint,''2026-10-21T09:44:03.511877Z''::timestamptz) LOOP
                SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''measurements'') b WHERE (b->>''id'')::bigint=pk;
                SELECT b INTO STRICT reviewed_raw FROM jsonb_array_elements(correction#>''{baseline,measurements}'') b
                    WHERE (b->>''id'')::bigint=pk;
                SELECT a INTO STRICT audit FROM jsonb_array_elements(state->''addition_audits'') a
                    WHERE a#>''{metadata,measurement_id}''=to_jsonb(pk);
                SELECT e INTO STRICT entry FROM jsonb_array_elements(correction->''entries'') e
                    WHERE (e->>''measurement_pk'')::bigint=pk;
                IF (raw->''box_id''=to_jsonb(box_pk) AND raw->>''week_start''=''2026-10-19''
                                    AND date_trunc(''week'',(raw->>''measured_on'')::date)::date=(raw->>''week_start'')::date
                    AND (raw->>''created_at'')::timestamptz=addition_time
                    AND raw->>''origin''=''direct_entry'' AND raw->>''temporal_precision''=''exact_date''
                    AND raw->''polyp_state_sequence''=''25''::jsonb AND raw->>''observer_status''=''known''
                    AND raw->''user_id''<>''null''::jsonb AND raw->''original_observer_id''=raw->''user_id''
                    AND raw->''imported_by_id''=''null''::jsonb AND raw->>''import_format''=''''
                    AND raw->''duplicate_of_id''=''null''::jsonb AND raw->''environment_source_box_id''=''null''::jsonb
                    AND entry->>''classification''=''OUT_OF_SOURCE'' AND entry->''before''=reviewed_raw
                    AND raw-''created_at''-''salinity_psu''=reviewed_raw-''created_at''-''salinity_psu''
                    AND (reviewed_raw->>''created_at'')::timestamptz=addition_time
                    AND (raw->>''salinity_psu'')::numeric IS NOT DISTINCT FROM (reviewed_raw->>''salinity_psu'')::numeric
                    AND EXISTS (SELECT 1 FROM public.accounts_organizationmembership
                        WHERE organization_id=1 AND user_id=(raw->>''user_id'')::bigint)
                    AND audit->''organization_id''=''1''::jsonb AND audit->''user_id''=raw->''user_id''
                    AND audit->>''action''=''entry'' AND audit->>''object_type''=''box''
                    AND audit->>''object_id''=CASE box_pk WHEN 1836 THEN ''CCA-EOB-2.005'' ELSE ''CCA-EOB-2.006'' END
                    AND audit->''edited_at''=''null''::jsonb AND audit->''edited_by_id''=''null''::jsonb
                    AND audit->''original_values''=''{}''::jsonb
                    AND (audit->>''created_at'')::timestamptz >= (raw->>''created_at'')::timestamptz
                    AND audit->''metadata''=jsonb_build_object(''measurement_id'',pk,''valeurs'',jsonb_build_object(
                        ''date'',raw->''measured_on'',''polypes'',raw->''polyp_count'',''ephyrules'',raw->''ephyrae_count'',
                        ''strobiles'',raw->''strobila_count'',''salinite_psu'',CASE WHEN raw->''salinity_psu''=''null''::jsonb THEN NULL
                            ELSE to_char((raw->>''salinity_psu'')::numeric,''FM999990.00'') END,
                        ''statut_culture'',raw->''culture_status'',''a_verifier'',raw->''needs_attention'',''note'',raw->''notes''))
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(historical_plan#>''{baseline,measurements}'') b
                        WHERE (b->>''id'')::bigint=pk)
                    AND (SELECT b->''polyp_state_revision'' FROM jsonb_array_elements(historical_plan#>''{baseline,boxes}'') b
                        WHERE (b->>''id'')::bigint=box_pk)=''24''::jsonb
                    AND (SELECT b->''polyp_state_revision'' FROM jsonb_array_elements(correction#>''{baseline,boxes}'') b
                        WHERE (b->>''id'')::bigint=box_pk)=''25''::jsonb) IS NOT TRUE THEN
                    RAISE EXCEPTION ''CCA-EOB addition provenance, audit, thermal state or revision differs'';
                END IF;
            END LOOP;
            -- Preserve array ordering and every unrelated field in this projection.
            projected := jsonb_set(correction,''{baseline,measurements}'',
                (SELECT jsonb_agg(b ORDER BY ord) FROM jsonb_array_elements(correction#>''{baseline,measurements}'') WITH ORDINALITY x(b,ord)
                    WHERE (b->>''id'')::bigint NOT IN (104688,104689)));
            projected := jsonb_set(projected,''{entries}'',
                (SELECT jsonb_agg(e ORDER BY ord) FROM jsonb_array_elements(correction->''entries'') WITH ORDINALITY x(e,ord)
                    WHERE (e->>''measurement_pk'')::bigint NOT IN (104688,104689)));
            projected := jsonb_set(projected,''{counts,OUT_OF_SOURCE}'',''4''::jsonb);
            projected := jsonb_set(projected,''{baseline,boxes}'',
                (SELECT jsonb_agg(CASE WHEN (b->>''id'')::bigint IN (1836,1837)
                    THEN jsonb_set(b,''{polyp_state_revision}'',''24''::jsonb) ELSE b END ORDER BY ord)
                 FROM jsonb_array_elements(projected#>''{baseline,boxes}'') WITH ORDINALITY x(b,ord)));
            IF (SELECT jsonb_agg(r->''box_id'' ORDER BY ord) FROM jsonb_array_elements(correction->''revision_changes'') WITH ORDINALITY x(r,ord))
                IS DISTINCT FROM (SELECT jsonb_agg(r->''box_id'' ORDER BY ord) FROM jsonb_array_elements(historical_plan->''revision_changes'') WITH ORDINALITY x(r,ord)) THEN
                RAISE EXCEPTION ''CCA-EOB revision identities/order changed'';
            END IF;
            -- The existing writer below recomputes every current revision against
            -- raw rows and touched counts before writing. Historical values stay authenticated.
            projected := jsonb_set(projected,''{revision_changes}'',historical_plan->''revision_changes'');
            IF projected IS DISTINCT FROM historical_plan THEN
                RAISE EXCEPTION ''CCA-EOB historical scientific identity changed'';
            END IF;
        END IF;

        IF (move_count=72
            AND doc#>>''{plan,thermal_exception,schema}''=''cca_eob_unlinked_72_v2''
            AND doc#>>''{plan,thermal_exception,original_plan_hash}'' ~ ''^[0-9a-f]{64}$''
            AND jsonb_typeof(doc#>''{plan,thermal_exception,original_document}'')=''object''
            AND doc#>>''{plan,thermal_exception,original_document,schema}''=''cca_eob_production_plan_v2''
            AND jsonb_typeof(doc#>''{plan,thermal_exception,original_document,plan}'')=''object''
            AND jsonb_typeof(historical_plan)=''object''
            AND (historical_plan=correction OR projected=historical_plan)) IS NOT TRUE THEN
            RAISE EXCEPTION ''Authenticated original 72-move thermal exception required'';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading r
        JOIN jsonb_array_elements(recipe->''moves'') m ON r.biological_measurement_id=(m->>''id'')::bigint) THEN
        RAISE EXCEPTION ''A planned measurement has a historical temperature association'';
    END IF;
    -- The lock helper holds SHARE on readings until commit: no phantom link can appear.
    -- Validate every raw baseline before the first update. JSON null and zero differ.
    FOR i IN 0..move_count-1 LOOP
        pk := (correction->''move_order''->>i)::bigint;
        IF pk IS NULL OR pk=ANY(seen) THEN RAISE EXCEPTION ''Duplicate or missing move ID''; END IF;
        seen := array_append(seen,pk);
        SELECT e INTO STRICT entry FROM jsonb_array_elements(correction->''entries'') e
            WHERE (e->>''measurement_pk'')::bigint=pk AND e->>''classification''=''REASSIGN_REQUIRED'';
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''measurements'') b WHERE (b->>''id'')::bigint=pk;
        SELECT b INTO STRICT reviewed_raw FROM jsonb_array_elements(doc#>''{plan,database_snapshot,measurements}'') b
            WHERE (b->>''id'')::bigint=pk;
        before_source := entry->''before'';
        move := recipe->''moves''->i;
        -- Raw equality protects every column. Python''s normalized before uses
        -- different timestamp/decimal encodings, so compare its source keys by type.
        IF (raw=reviewed_raw
            AND before_source ?& ARRAY[''id'',''box_id'',''measured_on'',''week_start'',
                ''polyp_count'',''ephyrae_count'',''strobila_count'']
            AND (before_source->>''id'')::bigint=pk
            AND (before_source->>''box_id'')::bigint=(raw->>''box_id'')::bigint
            AND (before_source->>''measured_on'')::date=(raw->>''measured_on'')::date
            AND (before_source->>''week_start'')::date=(raw->>''week_start'')::date
            AND (before_source->>''polyp_count'')::bigint IS NOT DISTINCT FROM (raw->>''polyp_count'')::bigint
            AND (before_source->>''ephyrae_count'')::bigint IS NOT DISTINCT FROM (raw->>''ephyrae_count'')::bigint
            AND (before_source->>''strobila_count'')::bigint IS NOT DISTINCT FROM (raw->>''strobila_count'')::bigint
            AND (raw->>''box_id'')::bigint IN (1835,1836,1837)
            AND (entry->>''target_box_pk'')::bigint IN (1835,1836)
            AND entry->>''target_box''=CASE (entry->>''target_box_pk'')::bigint
                WHEN 1835 THEN ''CCA-EOB-2.004'' WHEN 1836 THEN ''CCA-EOB-2.005'' END
            AND entry->''target_code''=entry->''target_box''
            AND entry->''target_box_pk''<>raw->''box_id''
            AND raw->''duplicate_of_id''=''null''::jsonb
            AND raw->''environment_source_box_id''=''null''::jsonb
            AND extract(isoyear FROM (raw->>''measured_on'')::date)=2026
            AND date_trunc(''week'',(raw->>''measured_on'')::date)::date=(raw->>''week_start'')::date
            AND (entry->>''iso_year'')::integer=2026
            AND (entry->>''iso_week'')::integer=extract(week FROM (raw->>''measured_on'')::date)
            AND entry->''source_value''=jsonb_build_array(raw->''polyp_count'',raw->''ephyrae_count'')
            AND move=jsonb_build_object(''id'',pk,''before_box_id'',raw->''box_id'',
                ''target_box_id'',entry->''target_box_pk'',''week_start'',raw->''week_start'')) IS NOT TRUE THEN
            RAISE EXCEPTION ''CCA-EOB move does not match approved raw baseline'';
        END IF;
    END LOOP;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->''revisions'') LOOP
        box_pk := (revision->>''box_id'')::bigint;
        IF box_pk IS NULL OR box_pk NOT IN (1835,1836,1837) OR box_pk=ANY(revised) THEN
            RAISE EXCEPTION ''Invalid or duplicate revision box''; END IF;
        revised := array_append(revised,box_pk);
        SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''boxes'') b WHERE (b->>''id'')::bigint=box_pk;
        SELECT count(*) INTO touched FROM jsonb_array_elements(recipe->''moves'') m
            WHERE (m->>''before_box_id'')::bigint=box_pk OR (m->>''target_box_id'')::bigint=box_pk;
        SELECT greatest((raw->>''polyp_state_revision'')::bigint+touched,
            coalesce(max((b->>''polyp_state_sequence'')::bigint),0)) INTO expected_after
            FROM jsonb_array_elements(state->''measurements'') b
            WHERE coalesce((SELECT (m->>''target_box_id'')::bigint FROM jsonb_array_elements(recipe->''moves'') m
                WHERE m->''id''=b->''id''),(b->>''box_id'')::bigint)=box_pk;
        IF (touched>0 AND revision->''before''=raw->''polyp_state_revision''
            AND (revision->>''after'')::bigint=expected_after) IS NOT TRUE THEN
            RAISE EXCEPTION ''CCA-EOB revision differs from touched count and final sequences''; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(recipe->''moves'') m
        WHERE NOT ((m->>''before_box_id'')::bigint=ANY(revised))
            OR NOT ((m->>''target_box_id'')::bigint=ANY(revised))) THEN
        RAISE EXCEPTION ''Missing touched revision''; END IF;
    FOR i IN 0..move_count LOOP
        audit := recipe->''audits''->i;
        IF (audit->''organization_id''=''1''::jsonb AND audit->''user_id''=doc->''actor_id''
            AND jsonb_typeof(audit->''metadata'')=''object''
            AND NOT (audit->''metadata'' ? ''execution_evidence'')
            AND jsonb_typeof(audit->''description'')=''string''
            AND audit#>>''{metadata,operation}''=''cca_eob_v2_20261016_v3'') IS NOT TRUE THEN
            RAISE EXCEPTION ''Invalid trusted audit identity or metadata''; END IF;
        IF i<move_count THEN
            move := recipe->''moves''->i;
            SELECT b INTO STRICT raw FROM jsonb_array_elements(state->''boxes'') b
                WHERE b->''id''=move->''target_box_id'';
            IF (audit->>''action''=''update'' AND audit->>''object_type''=''box''
                AND audit->''object_id''=raw->''global_code''
                AND audit#>''{metadata,measurement_id}''=move->''id'') IS NOT TRUE THEN
                RAISE EXCEPTION ''Invalid measurement audit target''; END IF;
        ELSIF (audit->>''action''=''import'' AND audit->>''object_type''=''historical_correction''
            AND audit->>''object_id''=''cca_eob_v2_20261016_v3'') IS NOT TRUE THEN
            RAISE EXCEPTION ''Invalid correction summary audit'';
        END IF;
    END LOOP;
    FOR move IN SELECT value FROM jsonb_array_elements(recipe->''moves'') LOOP
        UPDATE public.measurements_biologicalmeasurement SET box_id=(move->>''target_box_id'')::bigint
            WHERE id=(move->>''id'')::bigint AND box_id=(move->>''before_box_id'')::bigint
                AND week_start=(move->>''week_start'')::date;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION ''CCA-EOB measurement update cardinality''; END IF;
    END LOOP;
    -- Recheck all scoped biology columns, not just the moved Box IDs.
    FOR raw IN SELECT value FROM jsonb_array_elements(state->''measurements'') LOOP
        before_source := raw;
        SELECT m INTO move FROM jsonb_array_elements(recipe->''moves'') m WHERE m->''id''=raw->''id'';
        IF FOUND THEN before_source := jsonb_set(raw,''{box_id}'',move->''target_box_id''); END IF;
        IF (SELECT to_jsonb(b) FROM public.measurements_biologicalmeasurement b
            WHERE b.id=(raw->>''id'')::bigint) IS DISTINCT FROM before_source THEN
            RAISE EXCEPTION ''CCA-EOB post-write biological integrity failure'';
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading WHERE biological_measurement_id=ANY(seen)) THEN
        RAISE EXCEPTION ''CCA-EOB post-write thermal integrity failure'';
    END IF;
    FOR revision IN SELECT value FROM jsonb_array_elements(recipe->''revisions'') LOOP
        UPDATE public.cultures_box SET polyp_state_revision=(revision->>''after'')::bigint
            WHERE id=(revision->>''box_id'')::bigint AND organization_id=1
                AND polyp_state_revision=(revision->>''before'')::bigint;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed<>1 THEN RAISE EXCEPTION ''CCA-EOB revision update cardinality''; END IF;
    END LOOP;
    FOR audit IN SELECT value FROM jsonb_array_elements(recipe->''audits'') LOOP
        -- historical_plan_hash retains the complete historical document''s identity.
        -- Match the ORM default for a new audit with no edit history.
        INSERT INTO public.audit_auditlog (organization_id,user_id,action,object_type,object_id,
            description,metadata,created_at,edited_at,edited_by_id,original_values)
        VALUES (1,actor,audit->>''action'',audit->>''object_type'',audit->>''object_id'',audit->>''description'',
            (audit->''metadata'') || jsonb_build_object(''execution_evidence'',jsonb_build_object(
                ''target'',doc->''target'',''production_plan_hash'',reviewed_hash,
                ''authorization'',''APPLY_CCA_EOB_ORGANIZATION_1:''||reviewed_hash,''release'',approval.release,
                ''final_authorized_at'',approval.final_authorized_at,''final_expires_at'',approval.final_expires_at,
                ''historical_plan_hash'',doc#>''{plan,thermal_exception,original_plan_hash}'')),
            clock_timestamp(),NULL,NULL,''{}''::jsonb);
    END LOOP;
    UPDATE public.cca_eob_approval_v1 SET consumed_at=clock_timestamp()
        WHERE singleton AND consumed_at IS NULL AND expires_at>clock_timestamp()
            AND final_expires_at>clock_timestamp();
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION ''CCA-EOB approval expired during apply (plan or final authorization)''; END IF;
END
$body$
            OR p.proowner <> ''polypbase''::regrole OR NOT p.prosecdef
            OR p.proconfig IS DISTINCT FROM ARRAY[''search_path=pg_catalog, pg_temp'',''TimeZone=UTC'',''DateStyle=ISO, YMD'']::text[]
            OR p.prolang <> (SELECT oid FROM pg_language WHERE lanname=''plpgsql'')
            OR p.provolatile <> ''v'' OR p.proparallel <> ''u'' OR p.prokind <> ''f''
            OR p.proisstrict OR p.proleakproof OR p.proretset
            OR p.prorettype <> ''void''::regtype OR p.pronargs <> 1
            OR p.proargtypes::text <> ''25'' OR p.pronargdefaults <> 0
            OR p.proargnames IS DISTINCT FROM ARRAY[''reviewed_hash'']::text[]
            OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
            OR p.proargdefaults IS NOT NULL OR p.probin IS NOT NULL OR p.prosqlbody IS NOT NULL
            OR p.prosupport <> 0 OR p.provariadic <> 0 OR p.protrftypes IS NOT NULL
            OR p.procost <> 100 OR p.prorows <> 0 THEN
            RAISE EXCEPTION ''STOP: unexpected helper definition/metadata: public.cca_eob_apply_v1(text)''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))) x
            WHERE x.privilege_type <> ''EXECUTE'' OR x.is_grantable
                OR x.grantor <> p.proowner OR x.grantee NOT IN (p.proowner,''polypbase_cca_eob''::regrole))
            OR NOT has_function_privilege(''polypbase_cca_eob'',''public.cca_eob_apply_v1(text)'',''EXECUTE'')
            OR (SELECT count(*) FROM aclexplode(coalesce(p.proacl,acldefault(''f'',p.proowner))))<>2 THEN
            RAISE EXCEPTION ''STOP: unexpected helper ACL: public.cca_eob_apply_v1(text)''; END IF;
    END IF;

END $helpers$;
DO $approval$

DECLARE
    approval_table pg_class%ROWTYPE;
    approval_owner oid := ''postgres''::regrole;
    approval_issuer oid := ''polypbase''::regrole;
BEGIN
    SELECT * INTO approval_table FROM pg_class WHERE oid=to_regclass(''public.cca_eob_approval_v1'');
    IF NOT FOUND THEN
        RAISE EXCEPTION ''STOP: missing trusted approval table'';
    ELSE
        IF obj_description(approval_table.oid,''pg_class'') IS DISTINCT FROM ''Polypbase CCA-EOB trusted approval v2''
            OR approval_table.relowner <> approval_owner OR approval_table.relkind <> ''r''
            OR approval_table.relpersistence <> ''p'' OR approval_table.relrowsecurity
            OR approval_table.relforcerowsecurity OR approval_table.relispartition
            OR approval_table.reloptions IS NOT NULL OR approval_table.reloftype <> 0 OR approval_table.relhasrules
            OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid=approval_table.oid OR inhparent=approval_table.oid)
            OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=approval_table.oid AND NOT tgisinternal)
            OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=approval_table.oid) THEN
            RAISE EXCEPTION ''STOP: unrecognized approval table identity''; END IF;
        IF (SELECT jsonb_agg(jsonb_build_array(attname,format_type(atttypid,atttypmod),
                attnotnull,attidentity,attgenerated,atthasdef) ORDER BY attnum)
            FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ''[
                ["singleton","boolean",true,"","",false],
                ["plan_hash","text",true,"","",false],
                ["document","jsonb",true,"","",false],
                ["release","jsonb",true,"","",false],
                ["expires_at","timestamp with time zone",true,"","",false],
                ["consumed_at","timestamp with time zone",false,"","",false],
                ["final_authorization","jsonb",false,"","",false],
                ["final_authorized_at","timestamp with time zone",false,"","",false],
                ["final_expires_at","timestamp with time zone",false,"","",false]]''::jsonb
            OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=approval_table.oid AND attnum>0 AND attisdropped)
            -- PG18 NOT NULL constraints are verified through attnotnull above.
            OR (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY contype) FROM pg_constraint
                WHERE conrelid=approval_table.oid AND contype<>''n'')
                IS DISTINCT FROM ARRAY[''CHECK (singleton)'',''PRIMARY KEY (singleton)'']::text[]
            OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=approval_table.oid AND
                (NOT convalidated OR condeferrable OR condeferred))
            OR (SELECT count(*) FROM pg_index WHERE indrelid=approval_table.oid)<>1 THEN
            RAISE EXCEPTION ''STOP: approval table schema drift''; END IF;
        IF EXISTS (SELECT 1 FROM aclexplode(coalesce(approval_table.relacl,
                acldefault(''r'',approval_table.relowner))) x
            WHERE x.grantor<>approval_table.relowner OR x.is_grantable OR
                NOT (x.grantee=approval_table.relowner OR
                    (x.grantee=approval_issuer AND x.privilege_type=''SELECT'')))
            OR NOT has_table_privilege(approval_issuer,approval_table.oid,''SELECT'')
            OR (approval_issuer<>approval_table.relowner AND (
                has_table_privilege(approval_issuer,approval_table.oid,''UPDATE,INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN'')
                OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=approval_table.oid AND a.attnum>0 AND NOT a.attisdropped
                    AND (has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''UPDATE'') <> (a.attname=''consumed_at'')
                        OR has_column_privilege(approval_issuer,approval_table.oid,a.attnum,''INSERT,REFERENCES'')))))
            OR EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) x
                WHERE a.attrelid=approval_table.oid AND (a.attname<>''consumed_at''
                    OR x.grantee<>approval_issuer OR x.grantor<>approval_table.relowner
                    OR x.privilege_type<>''UPDATE'' OR x.is_grantable))
            OR NOT has_column_privilege(approval_issuer,approval_table.oid,''consumed_at'',''UPDATE'') THEN
            RAISE EXCEPTION ''STOP: approval table ACL drift''; END IF;
    END IF;
END;
$approval$;
';
    SELECT (SELECT jsonb_agg(to_jsonb(p) - 'prosrc' ORDER BY p.oid) FROM pg_proc p WHERE p.oid IN (SELECT to_regprocedure(s) FROM unnest(ARRAY['public.cca_eob_lock_v1(bigint)','public.cca_eob_read_v1(bigint)','public.cca_eob_apply_v1(text)']) s)) INTO final_metadata;
    IF final_metadata IS DISTINCT FROM original_metadata THEN
        RAISE EXCEPTION 'STOP: helper metadata or ACL changed during transition'; END IF;
END $transition$;
COMMIT;
