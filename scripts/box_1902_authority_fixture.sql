-- TEST FIXTURE ONLY. Loaded solely into fresh databases allocated by the PG18 harness.
-- Not a Django migration, production provisioner, manifest or correction workflow.
CREATE SCHEMA box1902_private;
CREATE TABLE public.taxonomy_species (id bigint PRIMARY KEY, scientific_name text NOT NULL);
CREATE TABLE public.taxonomy_strain (
    id bigint PRIMARY KEY, organization_id bigint NOT NULL, code text NOT NULL,
    species_id bigint NOT NULL REFERENCES public.taxonomy_species
);
CREATE TABLE public.cultures_box (
    id bigint PRIMARY KEY, organization_id bigint NOT NULL, global_code text NOT NULL,
    strain_id bigint NOT NULL REFERENCES public.taxonomy_strain,
    local_code text NOT NULL, status text NOT NULL, box_number text NOT NULL,
    merged_into_id bigint, previous_global_code text NOT NULL DEFAULT '', notes text NOT NULL DEFAULT ''
);
CREATE TABLE public.measurements_biologicalmeasurement (
    id bigint PRIMARY KEY, box_id bigint NOT NULL REFERENCES public.cultures_box,
    polyp_count integer, ephyrae_count integer
);
CREATE TABLE public.measurements_historicaltemperaturereading (
    id bigint PRIMARY KEY, biological_measurement_id bigint NOT NULL
        REFERENCES public.measurements_biologicalmeasurement,
    iso_year integer NOT NULL, iso_week integer NOT NULL, temperature_c numeric,
    association_status text NOT NULL CHECK (association_status IN ('confirmed', 'pending_review'))
);
CREATE TABLE public.measurements_historicaltemperatureevidence (
    id bigint PRIMARY KEY, reading_id bigint NOT NULL
        REFERENCES public.measurements_historicaltemperaturereading,
    source_box text NOT NULL, immutable_content text NOT NULL
);
CREATE TABLE box1902_private.approval (
    id integer PRIMARY KEY CHECK (id = 1),
    operation text NOT NULL CHECK (operation = 'box1902_authority_proof_v1'),
    executor name NOT NULL CHECK (executor = 'box1902_executor'),
    organization_id bigint NOT NULL CHECK (organization_id = 1),
    box_id bigint NOT NULL CHECK (box_id = 1902),
    old_code text NOT NULL CHECK (old_code = 'CLA-JKA-1.009'),
    new_code text NOT NULL CHECK (new_code = 'CLC-JKA-1.009'),
    old_strain bigint NOT NULL CHECK (old_strain = 213),
    new_strain bigint NOT NULL CHECK (new_strain = 285),
    review_ref text NOT NULL CHECK (review_ref = 'synthetic-authority-only-not-a-manifest'),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz
);
CREATE TABLE box1902_private.context (
    approval_id integer PRIMARY KEY REFERENCES box1902_private.approval,
    xid xid8 NOT NULL, backend_pid integer NOT NULL, executor name NOT NULL,
    before_box jsonb NOT NULL, after_box jsonb NOT NULL,
    validated boolean NOT NULL DEFAULT false
);
CREATE TABLE box1902_private.outcome (
    approval_id integer PRIMARY KEY REFERENCES box1902_private.approval,
    operation text NOT NULL, executor name NOT NULL, review_ref text NOT NULL,
    before_box jsonb NOT NULL, after_box jsonb NOT NULL,
    outcome text NOT NULL CHECK (outcome = 'applied')
);

CREATE FUNCTION box1902_private.apply(approval_id integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    approved box1902_private.approval%ROWTYPE;
    before_box jsonb;
    after_box jsonb;
    changed integer;
BEGIN
    IF session_user <> 'box1902_executor' THEN
        RAISE EXCEPTION 'Dedicated executor login required' USING ERRCODE = '42501';
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'READ COMMITTED required' USING ERRCODE = '25001';
    END IF;
    SELECT * INTO approved FROM box1902_private.approval a
        WHERE a.id = approval_id FOR UPDATE;
    IF NOT FOUND OR approved.consumed_at IS NOT NULL
       OR approved.expires_at <= clock_timestamp()
       OR approved.expires_at > clock_timestamp() + interval '1 hour'
       OR approved.executor <> session_user THEN
        RAISE EXCEPTION 'No independent live approval' USING ERRCODE = '42501';
    END IF;
    -- Coarse maintenance locks suffice for this small technical fixture, not a full manifest.
    LOCK TABLE public.cultures_box, public.taxonomy_strain, public.taxonomy_species,
        public.measurements_biologicalmeasurement,
        public.measurements_historicaltemperaturereading,
        public.measurements_historicaltemperatureevidence IN EXCLUSIVE MODE;
    IF approved.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'Approval expired while waiting for maintenance locks' USING ERRCODE = '42501';
    END IF;
    SELECT to_jsonb(b) INTO before_box FROM public.cultures_box b
        WHERE b.id = approved.box_id AND b.organization_id = approved.organization_id
          AND b.global_code = approved.old_code AND b.strain_id = approved.old_strain
          AND b.local_code = 'CLA-JKA-1.09' AND b.status = 'inactive'
          AND b.box_number = '09' AND b.merged_into_id IS NULL AND b.previous_global_code = ''
        FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM public.taxonomy_strain s JOIN public.taxonomy_species p ON p.id = s.species_id
        WHERE s.id = 285 AND s.organization_id = 1 AND s.code = 'CLC-JKA-1'
          AND p.id = 164 AND p.scientific_name = 'Chrysaora lactea'
    ) THEN
        RAISE EXCEPTION 'Exact technical source and target required' USING ERRCODE = '23514';
    END IF;
    after_box := before_box || jsonb_build_object('global_code', approved.new_code, 'strain_id', approved.new_strain);
    INSERT INTO box1902_private.context (
        approval_id, xid, backend_pid, executor, before_box, after_box
    ) VALUES (
        approved.id, pg_current_xact_id(), pg_backend_pid(), session_user, before_box, after_box
    );
    UPDATE public.cultures_box SET global_code = approved.new_code, strain_id = approved.new_strain
        WHERE id = approved.box_id AND organization_id = approved.organization_id;
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 OR NOT EXISTS (
        SELECT 1 FROM public.cultures_box b WHERE b.id = approved.box_id AND to_jsonb(b) = after_box
    ) OR NOT EXISTS (
        SELECT 1 FROM box1902_private.context c WHERE c.approval_id = approved.id AND c.validated
    ) THEN
        RAISE EXCEPTION 'Box or protected trigger postcondition failed' USING ERRCODE = '23514';
    END IF;
    INSERT INTO box1902_private.outcome VALUES (
        approved.id, approved.operation, session_user, approved.review_ref, before_box, after_box, 'applied'
    );
    UPDATE box1902_private.approval SET consumed_at = clock_timestamp() WHERE id = approved.id;
    DELETE FROM box1902_private.context WHERE context.approval_id = approved.id;
END $$;
REVOKE ALL ON FUNCTION box1902_private.apply(integer) FROM PUBLIC;

INSERT INTO public.taxonomy_species VALUES (131, 'Cyanea lamarckii'), (164, 'Chrysaora lactea');
INSERT INTO public.taxonomy_strain VALUES (213, 1, 'CLA-JKA-1', 131), (285, 1, 'CLC-JKA-1', 164);
INSERT INTO public.cultures_box (id, organization_id, global_code, strain_id, local_code, status, box_number)
VALUES (1902, 1, 'CLA-JKA-1.009', 213, 'CLA-JKA-1.09', 'inactive', '09'),
       (1903, 1, 'CLA-JKA-1.010', 213, 'CLA-JKA-1.10', 'inactive', '10'),
       (2902, 2, 'CLA-JKA-1.099', 213, 'CLA-JKA-1.99', 'inactive', '99');
INSERT INTO public.measurements_biologicalmeasurement
SELECT n, 1902, 0, NULL FROM generate_series(12173, 12213) n;
INSERT INTO public.measurements_biologicalmeasurement VALUES (19030, 1903, 0, NULL), (29020, 2902, 0, NULL);
INSERT INTO public.measurements_historicaltemperaturereading
SELECT n, n, 2025, n - 12161, 0, 'confirmed' FROM generate_series(12173, 12213) n;
INSERT INTO public.measurements_historicaltemperaturereading
VALUES (19030, 19030, 2025, 12, NULL, 'confirmed'), (29020, 29020, 2025, 12, 0, 'confirmed');
INSERT INTO public.measurements_historicaltemperatureevidence
SELECT id, id, 'CLA-JKA-1.09', 'synthetic immutable evidence ' || id
FROM public.measurements_historicaltemperaturereading;
REVOKE ALL ON SCHEMA box1902_private FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA box1902_private FROM PUBLIC;
