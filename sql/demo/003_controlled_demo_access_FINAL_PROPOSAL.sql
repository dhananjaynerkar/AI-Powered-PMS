/*
CONTROLLED LOCAL DEMO ACCESS — FINAL REVIEW-ONLY PROPOSAL

This file has NOT been executed. Do not run it until a DBA has separately
reviewed the hardening section, the main proposal, and the current database.
It is one-time-only: preconditions fail rather than changing existing demo
objects or roles. No password is present in this file.

Accepted local-demo exceptions:
- PUBLIC USAGE on schema public may remain in place.
- PUBLIC TEMPORARY on database postgres may remain in place. The API accepts
  no raw SQL and runs only fixed, bounded SELECT templates using server-side
  credentials. This exception is not approved for production.
*/

/*
DBA_HARDENING_SECTION_BEGIN

Run this section first, in a separate DBA-reviewed session, only after the
impact review confirms no external client relies on implicit CREATE in public.
It intentionally does NOT revoke PUBLIC USAGE on public.

BEGIN;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
COMMIT;

EMERGENCY_PUBLIC_CREATE_ROLLBACK_BEGIN
This restores the current broad shared-database privilege. Use only to reverse
the preceding hardening decision after DBA review; it is not part of demo
rollback.

BEGIN;
GRANT CREATE ON SCHEMA public TO PUBLIC;
COMMIT;
EMERGENCY_PUBLIC_CREATE_ROLLBACK_END
DBA_HARDENING_SECTION_END
*/

/* MAIN_PROPOSAL_BEGIN */
BEGIN;

DO $preconditions$
DECLARE
    required_table_count integer;
    required_column_count integer;
BEGIN
    IF current_database() <> 'postgres' THEN
        RAISE EXCEPTION 'expected database postgres, found %', current_database();
    END IF;
    IF current_user IN ('pms_app_runtime', 'pms_demo_runtime', 'pms_demo_view_owner') THEN
        RAISE EXCEPTION 'the applying identity is not an approved DDL identity';
    END IF;
    IF NOT COALESCE((SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user), false)
       OR NOT has_database_privilege(current_user, current_database(), 'CREATE') THEN
        RAISE EXCEPTION 'the applying identity cannot create the reviewed roles and schema';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('pms_demo_runtime', 'pms_demo_view_owner')) THEN
        RAISE EXCEPTION 'a proposed demo role already exists; proposal is one-time-only';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'pms_demo_access') THEN
        RAISE EXCEPTION 'schema pms_demo_access already exists';
    END IF;
    IF has_schema_privilege('public', 'public', 'CREATE') THEN
        RAISE EXCEPTION 'PUBLIC CREATE on schema public remains enabled; apply the separate DBA hardening section first';
    END IF;
    IF has_schema_privilege('public', 'pms_extract_2010_2023', 'USAGE') THEN
        RAISE EXCEPTION 'PUBLIC has extraction-schema USAGE';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'pms_extract_2010_2023'
          AND relation.relkind IN ('r', 'p')
          AND has_table_privilege('public', relation.oid, 'SELECT')
    ) THEN
        RAISE EXCEPTION 'PUBLIC has SELECT on an extraction base table';
    END IF;
    SELECT count(*) INTO required_table_count
    FROM pg_class AS relation
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'pms_extract_2010_2023'
      AND relation.relkind IN ('r', 'p')
      AND relation.relname IN (
          'extract_config', 'dim_division', 'dim_estate', 'dim_unit',
          'dim_plot', 'dim_property_lease', 'fact_monthly_bills'
      );
    IF required_table_count <> 7 THEN
        RAISE EXCEPTION 'required extraction source objects differ from the reviewed set';
    END IF;
    WITH expected(table_name, column_name, data_type) AS (
        VALUES
            ('extract_config', 'created_at', 'timestamp with time zone'),
            ('dim_division', 'div_code', 'character varying'),
            ('dim_division', 'div_name', 'character varying'),
            ('dim_division', 'status', 'character varying'),
            ('dim_estate', 'estate_code', 'character varying'),
            ('dim_estate', 'estate_name', 'character varying'),
            ('dim_estate', 'status', 'character varying'),
            ('dim_unit', 'unit_code', 'character varying'),
            ('dim_unit', 'unit_desc', 'character varying'),
            ('dim_unit', 'status', 'character varying'),
            ('dim_plot', 'plot_code', 'character varying'),
            ('dim_plot', 'area', 'numeric'),
            ('dim_plot', 'status', 'character varying'),
            ('dim_plot', 'is_vacant', 'boolean'),
            ('dim_plot', 'zone_id', 'integer'),
            ('dim_property_lease', 'tenancy_type', 'character varying'),
            ('dim_property_lease', 'lease_type_id', 'integer'),
            ('dim_property_lease', 'bill_periodicity', 'character varying'),
            ('dim_property_lease', 'duration_from', 'character varying'),
            ('dim_property_lease', 'duration_to', 'character varying'),
            ('dim_property_lease', 'renewal_date', 'character varying'),
            ('dim_property_lease', 'is_renewable', 'boolean'),
            ('dim_property_lease', 'status', 'character varying'),
            ('fact_monthly_bills', 'bill_date', 'date'),
            ('fact_monthly_bills', 'due_date', 'date'),
            ('fact_monthly_bills', 'bill_status', 'character')
    )
    SELECT count(*) INTO required_column_count
    FROM expected
    JOIN information_schema.columns AS actual
      ON actual.table_schema = 'pms_extract_2010_2023'
     AND actual.table_name = expected.table_name
     AND actual.column_name = expected.column_name
     AND actual.data_type = expected.data_type;
    IF required_column_count <> 26 THEN
        RAISE EXCEPTION 'required source columns are absent or have unexpected data types';
    END IF;
END
$preconditions$;

CREATE ROLE pms_demo_view_owner
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE pms_demo_runtime
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE pms_demo_runtime SET default_transaction_read_only = on;
ALTER ROLE pms_demo_runtime SET statement_timeout = '5s';
ALTER ROLE pms_demo_runtime SET search_path = pms_demo_access, pg_catalog;

GRANT USAGE ON SCHEMA pms_extract_2010_2023 TO pms_demo_view_owner;
GRANT SELECT ON
    pms_extract_2010_2023.extract_config,
    pms_extract_2010_2023.dim_division,
    pms_extract_2010_2023.dim_estate,
    pms_extract_2010_2023.dim_unit,
    pms_extract_2010_2023.dim_plot,
    pms_extract_2010_2023.dim_property_lease,
    pms_extract_2010_2023.fact_monthly_bills
TO pms_demo_view_owner;

CREATE SCHEMA pms_demo_access AUTHORIZATION pms_demo_view_owner;
REVOKE ALL ON SCHEMA pms_demo_access FROM PUBLIC;
GRANT USAGE ON SCHEMA pms_demo_access TO pms_demo_runtime;

SET LOCAL ROLE pms_demo_view_owner;
CREATE VIEW pms_demo_access.division_reference (
    div_code, div_name, status, source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT division.div_code, division.div_name, division.status,
       (SELECT max(config.created_at) FROM pms_extract_2010_2023.extract_config AS config)
FROM pms_extract_2010_2023.dim_division AS division;
CREATE VIEW pms_demo_access.estate_reference (
    estate_code, estate_name, status, source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT estate.estate_code, estate.estate_name, estate.status,
       (SELECT max(config.created_at) FROM pms_extract_2010_2023.extract_config AS config)
FROM pms_extract_2010_2023.dim_estate AS estate;
CREATE VIEW pms_demo_access.unit_reference (
    unit_code, unit_desc, status, source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT unit.unit_code, unit.unit_desc, unit.status,
       (SELECT max(config.created_at) FROM pms_extract_2010_2023.extract_config AS config)
FROM pms_extract_2010_2023.dim_unit AS unit;
CREATE VIEW pms_demo_access.plot_summary (
    plot_code, area, status, is_vacant, zone_id, source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT plot.plot_code, plot.area, plot.status, plot.is_vacant, plot.zone_id,
       (SELECT max(config.created_at) FROM pms_extract_2010_2023.extract_config AS config)
FROM pms_extract_2010_2023.dim_plot AS plot;
CREATE VIEW pms_demo_access.approved_lease_summary (
    tenancy_type, lease_type_id, bill_periodicity, duration_from, duration_to,
    renewal_date, is_renewable, status, source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT lease.tenancy_type, lease.lease_type_id, lease.bill_periodicity,
       lease.duration_from, lease.duration_to, lease.renewal_date,
       lease.is_renewable, lease.status,
       (SELECT max(config.created_at) FROM pms_extract_2010_2023.extract_config AS config)
FROM pms_extract_2010_2023.dim_property_lease AS lease
WHERE lease.status = 'APPROVED';
CREATE VIEW pms_demo_access.recent_bill_summary (
    bill_date, due_date, bill_status, source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT bill.bill_date, bill.due_date, bill.bill_status,
       (SELECT max(config.created_at) FROM pms_extract_2010_2023.extract_config AS config)
FROM pms_extract_2010_2023.fact_monthly_bills AS bill
WHERE bill.bill_status = 'A';
RESET ROLE;

GRANT SELECT ON
    pms_demo_access.division_reference,
    pms_demo_access.estate_reference,
    pms_demo_access.unit_reference,
    pms_demo_access.plot_summary,
    pms_demo_access.approved_lease_summary,
    pms_demo_access.recent_bill_summary
TO pms_demo_runtime;

DO $postapply_validation$
DECLARE
    approved_view_count integer;
    approved_column_count integer;
BEGIN
    IF has_schema_privilege('public', 'public', 'CREATE') THEN
        RAISE EXCEPTION 'PUBLIC CREATE on schema public remains enabled';
    END IF;
    RAISE NOTICE 'accepted local-demo exception: PUBLIC USAGE on public = %, PUBLIC TEMP on database = %',
        has_schema_privilege('public', 'public', 'USAGE'),
        has_database_privilege('public', current_database(), 'TEMP');
    IF (SELECT count(*) FROM pg_roles WHERE rolname = 'pms_demo_view_owner'
          AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
          AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls) <> 1
       OR (SELECT count(*) FROM pg_roles WHERE rolname = 'pms_demo_runtime'
          AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
          AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls) <> 1 THEN
        RAISE EXCEPTION 'demo role attributes are not restricted as required';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members
               WHERE roleid IN (to_regrole('pms_demo_view_owner'), to_regrole('pms_demo_runtime'))
                  OR member IN (to_regrole('pms_demo_view_owner'), to_regrole('pms_demo_runtime'))) THEN
        RAISE EXCEPTION 'a demo role has an unapproved membership';
    END IF;
    IF (SELECT count(*) FROM pg_roles AS role, LATERAL unnest(role.rolconfig) AS config(setting)
        WHERE role.rolname = 'pms_demo_runtime' AND config.setting IN (
            'default_transaction_read_only=on', 'statement_timeout=5s',
            'search_path=pms_demo_access, pg_catalog')) <> 3 THEN
        RAISE EXCEPTION 'runtime configuration is not restricted as required';
    END IF;
    IF NOT has_schema_privilege('pms_demo_runtime', 'pms_demo_access', 'USAGE')
       OR has_schema_privilege('pms_demo_runtime', 'pms_demo_access', 'CREATE')
       OR has_schema_privilege('public', 'pms_demo_access', 'USAGE')
       OR has_schema_privilege('public', 'pms_demo_access', 'CREATE') THEN
        RAISE EXCEPTION 'demo-schema privileges are incorrect';
    END IF;
    IF has_schema_privilege('pms_demo_runtime', 'pms_extract_2010_2023', 'USAGE')
       OR has_schema_privilege('public', 'pms_extract_2010_2023', 'USAGE') THEN
        RAISE EXCEPTION 'runtime or PUBLIC has extraction-schema USAGE';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'pms_extract_2010_2023' AND relation.relkind IN ('r', 'p')
          AND (has_table_privilege('public', relation.oid, 'SELECT')
               OR has_table_privilege('pms_demo_runtime', relation.oid, 'SELECT'))
    ) THEN
        RAISE EXCEPTION 'PUBLIC or runtime can SELECT an extraction base table';
    END IF;
    IF NOT has_schema_privilege('pms_demo_view_owner', 'pms_extract_2010_2023', 'USAGE')
       OR (SELECT count(*) FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
           WHERE namespace.nspname = 'pms_extract_2010_2023' AND relation.relkind IN ('r','p')
             AND has_table_privilege('pms_demo_view_owner', relation.oid, 'SELECT')) <> 7
       OR EXISTS (
           SELECT 1 FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
           WHERE namespace.nspname = 'pms_extract_2010_2023' AND relation.relkind IN ('r','p')
             AND has_table_privilege('pms_demo_view_owner', relation.oid, 'SELECT')
             AND relation.relname NOT IN ('extract_config','dim_division','dim_estate','dim_unit','dim_plot','dim_property_lease','fact_monthly_bills')
       ) THEN
        RAISE EXCEPTION 'view owner source SELECT is not limited to the seven approved objects';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'pms_extract_2010_2023' AND relation.relkind IN ('r','p')
          AND has_table_privilege('pms_demo_view_owner', relation.oid,
              'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    ) THEN RAISE EXCEPTION 'view owner has a source write privilege'; END IF;
    SELECT count(*) INTO approved_view_count
    FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'pms_demo_access' AND relation.relkind = 'v'
      AND relation.relname IN ('division_reference','estate_reference','unit_reference','plot_summary','approved_lease_summary','recent_bill_summary')
      AND relation.relowner = to_regrole('pms_demo_view_owner')
      AND relation.reloptions @> ARRAY['security_barrier=true','security_invoker=false']::text[];
    IF approved_view_count <> 6 THEN RAISE EXCEPTION 'approved view ownership or security options are incorrect'; END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'pms_demo_access'
          AND (relation.relkind <> 'v' OR relation.relname NOT IN
             ('division_reference','estate_reference','unit_reference','plot_summary','approved_lease_summary','recent_bill_summary'))
    ) THEN RAISE EXCEPTION 'demo schema contains an unexpected object'; END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
          AND namespace.nspname NOT LIKE 'pg_toast%' AND relation.relkind IN ('r','p','v','m','S')
          AND has_table_privilege('pms_demo_runtime', relation.oid, 'SELECT')
          AND NOT (namespace.nspname = 'pms_demo_access' AND relation.relname IN
             ('division_reference','estate_reference','unit_reference','plot_summary','approved_lease_summary','recent_bill_summary'))
    ) THEN RAISE EXCEPTION 'runtime can SELECT an object outside the six approved views'; END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
          AND namespace.nspname NOT LIKE 'pg_toast%' AND relation.relkind IN ('r','p','v','m','S')
          AND has_table_privilege('pms_demo_runtime', relation.oid,
              'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    ) THEN RAISE EXCEPTION 'runtime has a write privilege'; END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = to_regrole('pms_demo_runtime'))
       OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = to_regrole('pms_demo_runtime'))
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = to_regrole('pms_demo_runtime'))
       OR EXISTS (SELECT 1 FROM pg_database WHERE datdba = to_regrole('pms_demo_runtime')) THEN
        RAISE EXCEPTION 'runtime owns a database object';
    END IF;
    IF (SELECT count(*) FROM pg_namespace WHERE nspname = 'pms_demo_access'
         AND nspowner = to_regrole('pms_demo_view_owner')) <> 1 THEN
        RAISE EXCEPTION 'view owner does not own the demo schema';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = to_regrole('postgres') AND nspname = 'pms_demo_access')
       OR EXISTS (SELECT 1 FROM pg_class AS relation JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                  WHERE namespace.nspname = 'pms_demo_access' AND relation.relowner = to_regrole('postgres')) THEN
        RAISE EXCEPTION 'postgres retains ownership of a demo object';
    END IF;
    WITH expected(table_name, column_name, ordinal_position) AS (
        VALUES
          ('approved_lease_summary','tenancy_type',1),('approved_lease_summary','lease_type_id',2),
          ('approved_lease_summary','bill_periodicity',3),('approved_lease_summary','duration_from',4),
          ('approved_lease_summary','duration_to',5),('approved_lease_summary','renewal_date',6),
          ('approved_lease_summary','is_renewable',7),('approved_lease_summary','status',8),('approved_lease_summary','source_refreshed_at',9),
          ('division_reference','div_code',1),('division_reference','div_name',2),('division_reference','status',3),('division_reference','source_refreshed_at',4),
          ('estate_reference','estate_code',1),('estate_reference','estate_name',2),('estate_reference','status',3),('estate_reference','source_refreshed_at',4),
          ('unit_reference','unit_code',1),('unit_reference','unit_desc',2),('unit_reference','status',3),('unit_reference','source_refreshed_at',4),
          ('plot_summary','plot_code',1),('plot_summary','area',2),('plot_summary','status',3),('plot_summary','is_vacant',4),('plot_summary','zone_id',5),('plot_summary','source_refreshed_at',6),
          ('recent_bill_summary','bill_date',1),('recent_bill_summary','due_date',2),('recent_bill_summary','bill_status',3),('recent_bill_summary','source_refreshed_at',4)
    ) SELECT count(*) INTO approved_column_count FROM expected
      JOIN information_schema.columns AS actual ON actual.table_schema = 'pms_demo_access'
       AND actual.table_name = expected.table_name AND actual.column_name = expected.column_name
       AND actual.ordinal_position = expected.ordinal_position;
    IF approved_column_count <> 31 THEN RAISE EXCEPTION 'approved view output columns differ from the reviewed contract'; END IF;
END
$postapply_validation$;
COMMIT;

/*
ROLLBACK_SQL_BEGIN
Copy only this section into a separate DBA-reviewed session. It reverses only
objects and grants created by the main proposal. It intentionally does not
restore PUBLIC CREATE; use the emergency hardening rollback above only if the
shared-database hardening change itself must be reversed.

BEGIN;
REVOKE SELECT ON
    pms_demo_access.division_reference, pms_demo_access.estate_reference,
    pms_demo_access.unit_reference, pms_demo_access.plot_summary,
    pms_demo_access.approved_lease_summary, pms_demo_access.recent_bill_summary
FROM pms_demo_runtime;
REVOKE USAGE ON SCHEMA pms_demo_access FROM pms_demo_runtime;
SET LOCAL ROLE pms_demo_view_owner;
DROP VIEW pms_demo_access.recent_bill_summary;
DROP VIEW pms_demo_access.approved_lease_summary;
DROP VIEW pms_demo_access.plot_summary;
DROP VIEW pms_demo_access.unit_reference;
DROP VIEW pms_demo_access.estate_reference;
DROP VIEW pms_demo_access.division_reference;
DROP SCHEMA pms_demo_access;
RESET ROLE;
REVOKE SELECT ON
    pms_extract_2010_2023.extract_config, pms_extract_2010_2023.dim_division,
    pms_extract_2010_2023.dim_estate, pms_extract_2010_2023.dim_unit,
    pms_extract_2010_2023.dim_plot, pms_extract_2010_2023.dim_property_lease,
    pms_extract_2010_2023.fact_monthly_bills
FROM pms_demo_view_owner;
REVOKE USAGE ON SCHEMA pms_extract_2010_2023 FROM pms_demo_view_owner;
DROP ROLE pms_demo_runtime;
DROP ROLE pms_demo_view_owner;
COMMIT;
ROLLBACK_SQL_END
*/
