-- Future operator procedure only. Not executed against any database by the agent.
-- Run with psql -X -v ON_ERROR_STOP=1; disconnect after the last SELECT.
-- Fixed scope: organization 1. No prefix selection of operational Box rows.
-- See docs/cla-sme-postgresql-operations.md for interpretation and release gates.
BEGIN READ ONLY;

-- Q1: Independent live identity; OIDs and postmaster time must never be guessed.
SELECT current_database() AS database, d.oid::bigint AS database_oid,
       session_user::text, current_user::text AS role, r.oid::bigint AS role_oid,
       host(inet_server_addr()) AS server_address, inet_server_port() AS server_port,
       to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS postmaster_started_at,
       current_setting('server_version_num')::integer AS server_version_num,
       current_setting('port')::integer AS configured_port,
       current_setting('session_replication_role') AS replication_role,
       current_schemas(false) AS schemas, pg_is_in_recovery() AS in_recovery,
       current_setting('transaction_read_only') AS transaction_read_only
FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_roles r
WHERE d.datname = current_database() AND r.rolname = current_user;

-- Q2: Schema presence and migration metadata, not permission to migrate.
SELECT app, count(*) AS applied_count, max(name) AS last_name
FROM public.django_migrations
WHERE app = ANY (ARRAY['cultures','taxonomy','measurements','audit','organizations'])
GROUP BY app ORDER BY app;
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = ANY (ARRAY[
 'cultures_box','taxonomy_species','taxonomy_strain','taxonomy_organizationspeciescode',
 'taxonomy_localstrainidentity','measurements_biologicalmeasurement','cultures_boxlocation',
 'cultures_boxmovement','cultures_boxlineage','cultures_subcultureevent',
 'cultures_subcultureallocation','cultures_identificationtag','audit_auditlog'])
ORDER BY table_name, ordinal_position;

-- Q3: Exact Species and eligible Strain references. No foreign reference rows.
SELECT id, scientific_name FROM public.taxonomy_species
WHERE scientific_name = ANY (ARRAY['Cyanea lamarckii','Chrysaora lactea',
 'Stomolophus meleagris','Stomolophus sp.2']) ORDER BY id;
SELECT s.id, s.organization_id, s.code, s.species_id, p.scientific_name,
       s.number, s.origin_code, s.origin_id, s.global_identity_id,
       EXISTS (SELECT 1 FROM public.taxonomy_localstrainidentity l
               WHERE l.strain_id = s.id) AS has_local_identity,
       (s.organization_id = 1 OR (s.organization_id IS NULL AND EXISTS (
         SELECT 1 FROM public.cultures_box b WHERE b.organization_id = 1 AND b.strain_id = s.id
       ))) AS eligible,
       (SELECT count(*) FROM public.cultures_box b
        WHERE b.organization_id = 1 AND b.strain_id = s.id) AS own_witness_count,
       md5(to_jsonb(s)::text) AS reference_digest
FROM public.taxonomy_strain s JOIN public.taxonomy_species p ON p.id = s.species_id
WHERE (s.id = ANY (ARRAY[213,245,247]) OR s.code = ANY (ARRAY['CLC-JKA-1','SSP-JKA-1']))
  AND (s.organization_id = 1 OR (s.organization_id IS NULL AND EXISTS (
    SELECT 1 FROM public.cultures_box b WHERE b.organization_id = 1 AND b.strain_id = s.id)))
ORDER BY s.id;
SELECT a.id, a.organization_id, a.code, a.species_id, s.scientific_name
FROM public.taxonomy_organizationspeciescode a JOIN public.taxonomy_species s ON s.id = a.species_id
WHERE a.organization_id = 1 AND (upper(a.code) = ANY (ARRAY['CLA','CLC','SME','SSP'])
 OR s.scientific_name = ANY (ARRAY['Cyanea lamarckii','Chrysaora lactea',
 'Stomolophus meleagris','Stomolophus sp.2'])) ORDER BY a.id;
SELECT l.id, l.strain_id, l.species_code_assignment_id, l.provenance_code_assignment_id,
       s.origin_id, s.global_identity_id, md5(to_jsonb(l)::text) AS local_identity_digest
FROM public.taxonomy_localstrainidentity l
JOIN public.taxonomy_strain s ON s.id = l.strain_id
JOIN public.taxonomy_organizationspeciescode a ON a.id = l.species_code_assignment_id
WHERE s.organization_id = 1 AND a.organization_id = 1
 AND (s.id = ANY (ARRAY[213,245,247]) OR s.code = ANY (ARRAY['CLC-JKA-1','SSP-JKA-1']))
 AND (l.provenance_code_assignment_id IS NULL OR EXISTS (
  SELECT 1 FROM public.taxonomy_organizationprovenancecode p
  WHERE p.id = l.provenance_code_assignment_id AND p.organization_id = 1)) ORDER BY l.id;

-- Q4: Hidden references/collisions are booleans only, never foreign row contents.
SELECT EXISTS (
 SELECT 1 FROM public.taxonomy_strain s
 WHERE upper(s.code) = ANY (ARRAY['CLC-JKA-1','SSP-JKA-1'])
 AND s.organization_id IS DISTINCT FROM 1
 AND NOT (s.organization_id IS NULL AND EXISTS (
   SELECT 1 FROM public.cultures_box b WHERE b.organization_id = 1 AND b.strain_id = s.id))
) AS hidden_target_reference;
SELECT EXISTS (
 SELECT 1 FROM public.cultures_box b WHERE b.organization_id <> 1
 AND (b.global_code = ANY (c.codes) OR b.previous_global_code = ANY (c.codes))
) AS foreign_code_collision_or_reservation
FROM (SELECT ARRAY[
 'CLA-JKA-1.001','CLA-JKA-1.002','CLA-JKA-1.003','CLA-JKA-1.004','CLA-JKA-1.005',
 'CLA-JKA-1.006','CLA-JKA-1.007','CLA-JKA-1.008','CLA-JKA-1.009','CLA-JKA-1.010',
 'CLC-JKA-1.009','CLC-JKA-1.010',
 'SME-JKA-1.001','SME-JKA-1.004','SME-JKA-1.005','SME-JKA-1.006',
 'SME-JKA-1.007','SME-JKA-1.008','SME-JKA-1.009','SME-JKA-1.010',
 'SSP-JKA-1.001','SSP-JKA-1.004','SSP-JKA-1.005','SSP-JKA-1.006',
 'SSP-JKA-1.007','SSP-JKA-1.008','SSP-JKA-1.009','SSP-JKA-1.010']::text[] AS codes) c;

-- Q5: Closed identity inventory plus exact own witnesses of shared target 247.
-- preserved_box_digest excludes ONLY the two permitted mutation fields.
SELECT b.id AS box_pk, b.organization_id, b.global_code, b.local_code,
       b.strain_id, s.species_id, p.scientific_name, b.merged_into_id, b.previous_global_code,
       md5((to_jsonb(b) - 'global_code' - 'strain_id')::text) AS preserved_box_digest
FROM public.cultures_box b JOIN public.taxonomy_strain s ON s.id = b.strain_id
JOIN public.taxonomy_species p ON p.id = s.species_id
WHERE b.organization_id = 1 AND (b.strain_id = 247 OR b.global_code = ANY (ARRAY[
 'CLA-JKA-1.001','CLA-JKA-1.002','CLA-JKA-1.003','CLA-JKA-1.004','CLA-JKA-1.005',
 'CLA-JKA-1.006','CLA-JKA-1.007','CLA-JKA-1.008','CLA-JKA-1.009','CLA-JKA-1.010',
 'CLC-JKA-1.009','CLC-JKA-1.010',
 'SME-JKA-1.001','SME-JKA-1.004','SME-JKA-1.005','SME-JKA-1.006',
 'SME-JKA-1.007','SME-JKA-1.008','SME-JKA-1.009','SME-JKA-1.010',
 'SSP-JKA-1.001','SSP-JKA-1.004','SSP-JKA-1.005','SSP-JKA-1.006',
 'SSP-JKA-1.007','SSP-JKA-1.008','SSP-JKA-1.009','SSP-JKA-1.010'])) ORDER BY b.id;

-- Q6: Measurements, without individual values or author identifiers.
-- count(m.id)=0 is absence; a scientific zero is never a missing measurement.
SELECT b.id AS box_pk, b.global_code, count(m.id) AS measurement_count,
       min(m.measured_on) AS first_measured_on, max(m.measured_on) AS last_measured_on,
       count(m.id) FILTER (WHERE m.duplicate_of_id IS NULL) AS canonical_count,
       count(m.id) FILTER (WHERE m.polyp_count = 0) AS polyp_zero_count,
       count(m.id) FILTER (WHERE m.polyp_count IS NULL) AS polyp_null_count,
       count(m.id) FILTER (WHERE m.ephyrae_count = 0) AS ephyrae_zero_count,
       count(m.id) FILTER (WHERE m.ephyrae_count IS NULL) AS ephyrae_null_count,
       count(m.id) FILTER (WHERE m.strobila_count = 0) AS strobila_zero_count,
       count(m.id) FILTER (WHERE m.strobila_count IS NULL) AS strobila_null_count,
       md5(string_agg(to_jsonb(m)::text, ',' ORDER BY m.id)
           FILTER (WHERE m.id IS NOT NULL)) AS measurements_digest
FROM public.cultures_box b LEFT JOIN public.measurements_biologicalmeasurement m ON m.box_id = b.id
WHERE b.organization_id = 1 AND (b.strain_id = 247 OR b.global_code = ANY (ARRAY[
 'CLA-JKA-1.001','CLA-JKA-1.002','CLA-JKA-1.003','CLA-JKA-1.004','CLA-JKA-1.005',
 'CLA-JKA-1.006','CLA-JKA-1.007','CLA-JKA-1.008','CLA-JKA-1.009','CLA-JKA-1.010',
 'CLC-JKA-1.009','CLC-JKA-1.010',
 'SME-JKA-1.001','SME-JKA-1.004','SME-JKA-1.005','SME-JKA-1.006',
 'SME-JKA-1.007','SME-JKA-1.008','SME-JKA-1.009','SME-JKA-1.010',
 'SSP-JKA-1.001','SSP-JKA-1.004','SSP-JKA-1.005','SSP-JKA-1.006',
 'SSP-JKA-1.007','SSP-JKA-1.008','SSP-JKA-1.009','SSP-JKA-1.010']))
GROUP BY b.id, b.global_code ORDER BY b.id;

-- Q7: Same-suffix SME/SSP temporal overlap is observation, NOT continuity proof.
SELECT old.id AS sme_box_pk, newer.id AS ssp_box_pk,
       EXISTS (SELECT 1 FROM public.measurements_biologicalmeasurement a
        JOIN public.measurements_biologicalmeasurement z ON z.week_start = a.week_start
        WHERE a.box_id = old.id AND z.box_id = newer.id) AS shared_stored_week,
       (SELECT min(measured_on) FROM public.measurements_biologicalmeasurement
        WHERE box_id = newer.id) AS ssp_first_date
FROM public.cultures_box old JOIN public.cultures_box newer
 ON newer.global_code = replace(old.global_code, 'SME-', 'SSP-')
WHERE old.organization_id = 1 AND newer.organization_id = 1
 AND old.global_code = ANY (ARRAY['SME-JKA-1.001','SME-JKA-1.004','SME-JKA-1.005',
 'SME-JKA-1.006','SME-JKA-1.007','SME-JKA-1.008','SME-JKA-1.009','SME-JKA-1.010']) ORDER BY old.id;

-- Q8: Incoming dependencies, hashes only for full related rows (including authors).
-- The application plan additionally traverses all incoming model relationships.
SELECT b.id AS box_pk,
 (SELECT count(*) FROM public.cultures_boxlocation x WHERE x.box_id=b.id) AS location_count,
 (SELECT md5(string_agg(to_jsonb(x)::text, ',' ORDER BY x.id))
  FROM public.cultures_boxlocation x WHERE x.box_id=b.id) AS location_digest,
 (SELECT count(*) FROM public.cultures_boxmovement x WHERE x.box_id=b.id) AS movement_count,
 (SELECT md5(string_agg(to_jsonb(x)::text, ',' ORDER BY x.id))
  FROM public.cultures_boxmovement x WHERE x.box_id=b.id) AS movement_digest,
 (SELECT count(*) FROM public.cultures_identificationtag x WHERE x.box_id=b.id) AS tag_count,
 (SELECT md5(string_agg(to_jsonb(x)::text, ',' ORDER BY x.id))
  FROM public.cultures_identificationtag x WHERE x.box_id=b.id) AS tag_digest,
 (SELECT count(*) FROM public.cultures_boxlineage x
  JOIN public.cultures_box p ON p.id=x.parent_box_id AND p.organization_id=1
  JOIN public.cultures_box c ON c.id=x.child_box_id AND c.organization_id=1
  WHERE x.parent_box_id=b.id OR x.child_box_id=b.id) AS own_lineage_count,
 (SELECT md5(string_agg(to_jsonb(x)::text, ',' ORDER BY x.id)) FROM public.cultures_boxlineage x
  JOIN public.cultures_box p ON p.id=x.parent_box_id AND p.organization_id=1
  JOIN public.cultures_box c ON c.id=x.child_box_id AND c.organization_id=1
  WHERE x.parent_box_id=b.id OR x.child_box_id=b.id) AS own_lineage_digest,
 EXISTS (SELECT 1 FROM public.cultures_boxlineage x
  JOIN public.cultures_box p ON p.id=x.parent_box_id
  JOIN public.cultures_box c ON c.id=x.child_box_id
  WHERE (x.parent_box_id=b.id OR x.child_box_id=b.id)
   AND (p.organization_id<>1 OR c.organization_id<>1)) AS foreign_lineage_blocked,
 (SELECT count(*) FROM public.cultures_subcultureevent x WHERE x.parent_box_id=b.id) AS parent_event_count,
 (SELECT md5(string_agg(to_jsonb(x)::text, ',' ORDER BY x.id))
  FROM public.cultures_subcultureevent x WHERE x.parent_box_id=b.id) AS parent_event_digest,
 (SELECT count(*) FROM public.cultures_subcultureallocation x
  JOIN public.cultures_subcultureevent e ON e.id=x.event_id
  JOIN public.cultures_box p ON p.id=e.parent_box_id AND p.organization_id=1
  WHERE x.child_box_id=b.id) AS own_child_allocation_count,
 (SELECT md5(string_agg(to_jsonb(x)::text, ',' ORDER BY x.id))
  FROM public.cultures_subcultureallocation x
  JOIN public.cultures_subcultureevent e ON e.id=x.event_id
  JOIN public.cultures_box p ON p.id=e.parent_box_id AND p.organization_id=1
  WHERE x.child_box_id=b.id) AS own_child_allocation_digest
FROM public.cultures_box b
WHERE b.organization_id=1 AND (b.strain_id=247 OR b.global_code = ANY (ARRAY[
 'CLA-JKA-1.001','CLA-JKA-1.002','CLA-JKA-1.003','CLA-JKA-1.004','CLA-JKA-1.005',
 'CLA-JKA-1.006','CLA-JKA-1.007','CLA-JKA-1.008','CLA-JKA-1.009','CLA-JKA-1.010',
 'CLC-JKA-1.009','CLC-JKA-1.010',
 'SME-JKA-1.001','SME-JKA-1.004','SME-JKA-1.005','SME-JKA-1.006',
 'SME-JKA-1.007','SME-JKA-1.008','SME-JKA-1.009','SME-JKA-1.010',
 'SSP-JKA-1.001','SSP-JKA-1.004','SSP-JKA-1.005','SSP-JKA-1.006',
 'SSP-JKA-1.007','SSP-JKA-1.008','SSP-JKA-1.009','SSP-JKA-1.010'])) ORDER BY b.id;

-- Q9: Audit receipts: selected metadata keys only. Never print metadata wholesale.
SELECT id, created_at, action, object_type, object_id,
       metadata->>'operation' AS operation, metadata->>'plan_hash' AS plan_hash,
       metadata->>'target_strain_id' AS prepared_target_strain_id,
       jsonb_array_length(COALESCE(metadata->'entries','[]'::jsonb)) AS receipt_entry_count
FROM public.audit_auditlog
WHERE organization_id=1
 AND metadata->>'operation' = ANY (ARRAY['cla_sme_identity_20261027_v2','cla_sme_references_20261027_v1'])
 AND object_type = ANY (ARRAY['identity_reconciliation','identity_reference_preparation'])
 AND object_id = ANY (ARRAY['cla','sme']) ORDER BY id;

-- No COMMIT or write statements: psql disconnection rolls back this read-only transaction.
