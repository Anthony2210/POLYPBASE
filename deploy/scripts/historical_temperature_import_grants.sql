-- Infrastructure preparation only, NEVER run by deployment or the import command.
-- Requires separate approval and independent target/role/ACL verification.
-- The operator creates the fresh NOLOGIN role first. Existing owners stay unchanged.
-- No backup-wide SELECT grants: the separately attested application login dumps.
BEGIN;
GRANT CONNECT ON DATABASE polypbase TO polypbase_hist_import;
GRANT USAGE ON SCHEMA public TO polypbase_hist_import;
GRANT SELECT ON TABLE
    public.organizations_organization,
    public.auth_user,
    public.accounts_organizationmembership,
    public.cultures_box,
    public.measurements_biologicalmeasurement,
    public.measurements_historicaltemperaturereading,
    public.measurements_historicaltemperatureevidence,
    public.measurements_historicaltemperatureimportreceipt,
    public.audit_auditlog,
    public.django_migrations
TO polypbase_hist_import;
GRANT INSERT (
    biological_measurement_id, association_status, iso_year, iso_week, temperature_c, created_at
) ON public.measurements_historicaltemperaturereading TO polypbase_hist_import;
GRANT INSERT (
    reading_id, source_file, source_sha256, sheet, temperature_cell,
    temperature_merged_range, source_value, source_box, polyp_cell, polyp_value,
    ephyrae_cell, ephyrae_value, week_cell, week_value, source_iso_year, source_iso_week, created_at
) ON public.measurements_historicaltemperatureevidence TO polypbase_hist_import;
GRANT INSERT (
    organization_id, actor_id, plan_fingerprint, snapshot_sha256,
    accepted_plan, source_archive, results, created_at
) ON public.measurements_historicaltemperatureimportreceipt TO polypbase_hist_import;
GRANT INSERT (
    organization_id, user_id, action, object_type, object_id, description, metadata,
    created_at, edited_at, edited_by_id, original_values
) ON public.audit_auditlog TO polypbase_hist_import;
-- Additive statement guards must be installed before granting column UPDATE.
-- CREATE (not REPLACE) deliberately refuses pre-existing infrastructure objects.
-- The fresh importer remains NOLOGIN throughout this atomic preparation.
CREATE FUNCTION public.polypbase_hist_import_deny_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $guard$
BEGIN
    IF session_user = 'polypbase_hist_import' OR current_user = 'polypbase_hist_import' THEN
        RAISE EXCEPTION 'Historical temperature import cannot UPDATE'
            USING ERRCODE = '42501';
    END IF;
    RETURN NULL;
END;
$guard$;
ALTER FUNCTION public.polypbase_hist_import_deny_update() OWNER TO polypbase;
REVOKE ALL ON FUNCTION public.polypbase_hist_import_deny_update() FROM PUBLIC;

CREATE TRIGGER polypbase_hist_import_no_update
BEFORE UPDATE ON public.organizations_organization
FOR EACH STATEMENT EXECUTE FUNCTION public.polypbase_hist_import_deny_update();
ALTER TABLE public.organizations_organization ENABLE ALWAYS TRIGGER polypbase_hist_import_no_update;
CREATE TRIGGER polypbase_hist_import_no_update
BEFORE UPDATE ON public.auth_user
FOR EACH STATEMENT EXECUTE FUNCTION public.polypbase_hist_import_deny_update();
ALTER TABLE public.auth_user ENABLE ALWAYS TRIGGER polypbase_hist_import_no_update;
CREATE TRIGGER polypbase_hist_import_no_update
BEFORE UPDATE ON public.accounts_organizationmembership
FOR EACH STATEMENT EXECUTE FUNCTION public.polypbase_hist_import_deny_update();
ALTER TABLE public.accounts_organizationmembership ENABLE ALWAYS TRIGGER polypbase_hist_import_no_update;
CREATE TRIGGER polypbase_hist_import_no_update
BEFORE UPDATE ON public.cultures_box
FOR EACH STATEMENT EXECUTE FUNCTION public.polypbase_hist_import_deny_update();
ALTER TABLE public.cultures_box ENABLE ALWAYS TRIGGER polypbase_hist_import_no_update;
CREATE TRIGGER polypbase_hist_import_no_update
BEFORE UPDATE ON public.measurements_biologicalmeasurement
FOR EACH STATEMENT EXECUTE FUNCTION public.polypbase_hist_import_deny_update();
ALTER TABLE public.measurements_biologicalmeasurement ENABLE ALWAYS TRIGGER polypbase_hist_import_no_update;
CREATE TRIGGER polypbase_hist_import_no_update
BEFORE UPDATE ON public.measurements_historicaltemperaturereading
FOR EACH STATEMENT EXECUTE FUNCTION public.polypbase_hist_import_deny_update();
ALTER TABLE public.measurements_historicaltemperaturereading ENABLE ALWAYS TRIGGER polypbase_hist_import_no_update;

-- PostgreSQL requires UPDATE on a column for row locks, including trigger locks.
-- The statement guards deny actual UPDATE, including zero-row/no-op statements.
-- Locking SELECT does not fire these triggers. No importer DDL is granted.
GRANT UPDATE (notes) ON public.organizations_organization TO polypbase_hist_import;
GRANT UPDATE (last_login) ON public.auth_user TO polypbase_hist_import;
GRANT UPDATE (is_hidden_from_team) ON public.accounts_organizationmembership TO polypbase_hist_import;
GRANT UPDATE (notes) ON public.cultures_box TO polypbase_hist_import;
GRANT UPDATE (notes) ON public.measurements_biologicalmeasurement TO polypbase_hist_import;
GRANT UPDATE (iso_year) ON public.measurements_historicaltemperaturereading TO polypbase_hist_import;
-- MAINTAIN also permits maintenance operations; review this residual capability.
GRANT MAINTAIN ON TABLE
    public.cultures_box,
    public.measurements_biologicalmeasurement,
    public.measurements_historicaltemperaturereading,
    public.measurements_historicaltemperatureevidence,
    public.measurements_historicaltemperatureimportreceipt
TO polypbase_hist_import;
GRANT EXECUTE ON FUNCTION public.historical_temperature_decimal(text) TO polypbase_hist_import;
-- All four insert tables must have IDENTITY ids; no sequence privileges are needed.
COMMIT;
