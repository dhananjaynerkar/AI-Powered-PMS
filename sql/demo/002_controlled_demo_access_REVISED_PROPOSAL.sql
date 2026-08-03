/*
CONTROLLED LOCAL DEMO ACCESS — REVISED REVIEW-ONLY PROPOSAL

ONE-TIME-ONLY. This file has not been executed. Run only after an authorized
DBA reviews every statement and all fail-closed preconditions pass. The
pms_demo_runtime password is deliberately absent and must be assigned later,
interactively, by an authorized DBA.

Security model:
- pms_demo_view_owner is a restricted non-login owner with SELECT on exactly
  seven named extraction objects.
- pms_demo_runtime is a restricted login with SELECT on exactly six named
  security-barrier views.
- security_invoker is explicitly false. View queries therefore use the
  restricted view owner's source privileges; the runtime receives no source
  privilege or owner membership.
- The proposal does not alter existing source-schema, public-schema, database
  PUBLIC, or unrelated-object ACLs. Unsafe inherited privileges fail closed.
*/

BEGIN;

/* 1. Read-only precondition checks. */
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

    IF NOT COALESCE(
        (SELECT role.rolsuper OR role.rolcreaterole
           FROM pg_roles AS role
          WHERE role.rolname = current_user),
        false
    ) THEN
        RAISE EXCEPTION 'the applying identity cannot create the two restricted roles';
    END IF;

    IF NOT has_database_privilege(current_user, current_database(), 'CREATE') THEN
        RAISE EXCEPTION 'the applying identity cannot create the dedicated schema';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_roles AS role
         WHERE role.rolname IN ('pms_demo_view_owner', 'pms_demo_runtime')
    ) THEN
        RAISE EXCEPTION 'a proposed demo role already exists; this proposal is one-time-only';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_namespace AS namespace
         WHERE namespace.nspname = 'pms_demo_access'
    ) THEN
        RAISE EXCEPTION 'schema pms_demo_access already exists';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_demo_access'
           AND relation.relname IN (
               'division_reference',
               'estate_reference',
               'unit_reference',
               'plot_summary',
               'approved_lease_summary',
               'recent_bill_summary'
           )
    ) THEN
        RAISE EXCEPTION 'a proposed demo view already exists';
    END IF;

    IF has_schema_privilege('public', 'pms_extract_2010_2023', 'USAGE') THEN
        RAISE EXCEPTION 'PUBLIC has extraction-schema USAGE';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_extract_2010_2023'
           AND relation.relkind IN ('r', 'p')
           AND has_table_privilege('public', relation.oid, 'SELECT')
    ) THEN
        RAISE EXCEPTION 'PUBLIC has SELECT on an extraction base table';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
           AND namespace.nspname NOT LIKE 'pg_toast%'
           AND relation.relkind IN ('r', 'p', 'v', 'm', 'S')
           AND has_table_privilege('public', relation.oid, 'SELECT')
    ) THEN
        RAISE EXCEPTION 'PUBLIC relation SELECT would give the future runtime extra access';
    END IF;

    IF has_schema_privilege('public', 'public', 'USAGE')
       OR has_schema_privilege('public', 'public', 'CREATE') THEN
        RAISE EXCEPTION 'PUBLIC privileges on schema public would flow to the future runtime';
    END IF;

    IF has_database_privilege('public', current_database(), 'TEMP') THEN
        RAISE EXCEPTION 'PUBLIC TEMP would give the future runtime an unapproved privilege';
    END IF;

    IF NOT has_database_privilege('public', current_database(), 'CONNECT') THEN
        RAISE EXCEPTION 'PUBLIC CONNECT is absent; a separate named CONNECT review is required';
    END IF;

    SELECT count(*)
      INTO required_table_count
      FROM pg_class AS relation
      JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
     WHERE namespace.nspname = 'pms_extract_2010_2023'
       AND relation.relkind IN ('r', 'p')
       AND relation.relname IN (
           'extract_config',
           'dim_division',
           'dim_estate',
           'dim_unit',
           'dim_plot',
           'dim_property_lease',
           'fact_monthly_bills'
       );

    IF required_table_count <> 7 THEN
        RAISE EXCEPTION 'one or more required extraction base tables are absent';
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
    SELECT count(*)
      INTO required_column_count
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

/* 2. Restricted non-login view owner. */
CREATE ROLE pms_demo_view_owner
    NOLOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    NOREPLICATION
    NOBYPASSRLS;

/* 3. Restricted runtime login; its password is assigned later out of band. */
CREATE ROLE pms_demo_runtime
    LOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    NOREPLICATION
    NOBYPASSRLS;

ALTER ROLE pms_demo_runtime SET default_transaction_read_only = on;
ALTER ROLE pms_demo_runtime SET statement_timeout = '5s';
ALTER ROLE pms_demo_runtime SET search_path = pms_demo_access, pg_catalog;

/* 4. Exact source privileges for the restricted view owner. */
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

/* 5. New dedicated schema with controlled ownership. */
CREATE SCHEMA pms_demo_access AUTHORIZATION pms_demo_view_owner;
REVOKE ALL ON SCHEMA pms_demo_access FROM PUBLIC;
GRANT USAGE ON SCHEMA pms_demo_access TO pms_demo_runtime;

/* 6. Six explicit security-definer-style views owned by the non-login owner. */
SET LOCAL ROLE pms_demo_view_owner;

CREATE VIEW pms_demo_access.division_reference (
    div_code,
    div_name,
    status,
    source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT division.div_code,
       division.div_name,
       division.status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
  FROM pms_extract_2010_2023.dim_division AS division;

CREATE VIEW pms_demo_access.estate_reference (
    estate_code,
    estate_name,
    status,
    source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT estate.estate_code,
       estate.estate_name,
       estate.status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
  FROM pms_extract_2010_2023.dim_estate AS estate;

CREATE VIEW pms_demo_access.unit_reference (
    unit_code,
    unit_desc,
    status,
    source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT unit.unit_code,
       unit.unit_desc,
       unit.status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
  FROM pms_extract_2010_2023.dim_unit AS unit;

CREATE VIEW pms_demo_access.plot_summary (
    plot_code,
    area,
    status,
    is_vacant,
    zone_id,
    source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT plot.plot_code,
       plot.area,
       plot.status,
       plot.is_vacant,
       plot.zone_id,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
  FROM pms_extract_2010_2023.dim_plot AS plot;

CREATE VIEW pms_demo_access.approved_lease_summary (
    tenancy_type,
    lease_type_id,
    bill_periodicity,
    duration_from,
    duration_to,
    renewal_date,
    is_renewable,
    status,
    source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT lease.tenancy_type,
       lease.lease_type_id,
       lease.bill_periodicity,
       lease.duration_from,
       lease.duration_to,
       lease.renewal_date,
       lease.is_renewable,
       lease.status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
  FROM pms_extract_2010_2023.dim_property_lease AS lease
 WHERE lease.status = 'APPROVED';

CREATE VIEW pms_demo_access.recent_bill_summary (
    bill_date,
    due_date,
    bill_status,
    source_refreshed_at
) WITH (security_barrier = true, security_invoker = false) AS
SELECT bill.bill_date,
       bill.due_date,
       bill.bill_status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
  FROM pms_extract_2010_2023.fact_monthly_bills AS bill
 WHERE bill.bill_status = 'A';

RESET ROLE;

/* 7. Runtime privileges on six named views only. */
GRANT SELECT ON
    pms_demo_access.division_reference,
    pms_demo_access.estate_reference,
    pms_demo_access.unit_reference,
    pms_demo_access.plot_summary,
    pms_demo_access.approved_lease_summary,
    pms_demo_access.recent_bill_summary
TO pms_demo_runtime;

/* 8 and 9. Effective-privilege verification and fail-closed validation. */
DO $postapply_validation$
DECLARE
    approved_view_count integer;
    approved_column_count integer;
BEGIN
    IF (
        SELECT count(*)
          FROM pg_roles AS role
         WHERE role.rolname = 'pms_demo_view_owner'
           AND NOT role.rolcanlogin
           AND NOT role.rolsuper
           AND NOT role.rolcreatedb
           AND NOT role.rolcreaterole
           AND NOT role.rolinherit
           AND NOT role.rolreplication
           AND NOT role.rolbypassrls
    ) <> 1 THEN
        RAISE EXCEPTION 'view-owner attributes are not restricted as required';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_roles AS role
         WHERE role.rolname = 'pms_demo_runtime'
           AND role.rolcanlogin
           AND NOT role.rolsuper
           AND NOT role.rolcreatedb
           AND NOT role.rolcreaterole
           AND NOT role.rolinherit
           AND NOT role.rolreplication
           AND NOT role.rolbypassrls
    ) <> 1 THEN
        RAISE EXCEPTION 'runtime attributes are not restricted as required';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_auth_members AS membership
         WHERE membership.roleid IN (
                   to_regrole('pms_demo_view_owner'),
                   to_regrole('pms_demo_runtime')
               )
            OR membership.member IN (
                   to_regrole('pms_demo_view_owner'),
                   to_regrole('pms_demo_runtime')
               )
    ) THEN
        RAISE EXCEPTION 'a demo role has an unapproved membership relationship';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_roles AS role,
               LATERAL unnest(role.rolconfig) AS config(setting)
         WHERE role.rolname = 'pms_demo_runtime'
           AND config.setting = 'default_transaction_read_only=on'
    ) <> 1 THEN
        RAISE EXCEPTION 'runtime read-only default is absent';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_roles AS role,
               LATERAL unnest(role.rolconfig) AS config(setting)
         WHERE role.rolname = 'pms_demo_runtime'
           AND config.setting = 'statement_timeout=5s'
    ) <> 1 THEN
        RAISE EXCEPTION 'runtime statement timeout is not five seconds';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_roles AS role,
               LATERAL unnest(role.rolconfig) AS config(setting)
         WHERE role.rolname = 'pms_demo_runtime'
           AND config.setting = 'search_path=pms_demo_access, pg_catalog'
    ) <> 1 THEN
        RAISE EXCEPTION 'runtime search path is not restricted as required';
    END IF;

    IF NOT has_database_privilege('pms_demo_runtime', current_database(), 'CONNECT') THEN
        RAISE EXCEPTION 'runtime lacks database CONNECT';
    END IF;

    IF has_database_privilege('pms_demo_runtime', current_database(), 'TEMP') THEN
        RAISE EXCEPTION 'runtime has unapproved database TEMP';
    END IF;

    IF NOT has_schema_privilege('pms_demo_runtime', 'pms_demo_access', 'USAGE')
       OR has_schema_privilege('pms_demo_runtime', 'pms_demo_access', 'CREATE') THEN
        RAISE EXCEPTION 'runtime demo-schema privileges are incorrect';
    END IF;

    IF has_schema_privilege('pms_demo_runtime', 'pms_extract_2010_2023', 'USAGE') THEN
        RAISE EXCEPTION 'runtime has extraction-schema USAGE';
    END IF;

    IF has_schema_privilege('pms_demo_runtime', 'public', 'USAGE')
       OR has_schema_privilege('pms_demo_runtime', 'public', 'CREATE') THEN
        RAISE EXCEPTION 'runtime has an unapproved privilege on schema public';
    END IF;

    IF has_schema_privilege('public', 'pms_demo_access', 'USAGE')
       OR has_schema_privilege('public', 'pms_demo_access', 'CREATE') THEN
        RAISE EXCEPTION 'PUBLIC has access to the demo schema';
    END IF;

    IF NOT has_schema_privilege('pms_demo_view_owner', 'pms_extract_2010_2023', 'USAGE') THEN
        RAISE EXCEPTION 'view owner lacks extraction-schema USAGE';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_extract_2010_2023'
           AND relation.relkind IN ('r', 'p')
           AND relation.relname IN (
               'extract_config',
               'dim_division',
               'dim_estate',
               'dim_unit',
               'dim_plot',
               'dim_property_lease',
               'fact_monthly_bills'
           )
           AND NOT has_table_privilege('pms_demo_view_owner', relation.oid, 'SELECT')
    ) THEN
        RAISE EXCEPTION 'view owner lacks SELECT on an approved source object';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_extract_2010_2023'
           AND relation.relkind IN ('r', 'p')
           AND relation.relname NOT IN (
               'extract_config',
               'dim_division',
               'dim_estate',
               'dim_unit',
               'dim_plot',
               'dim_property_lease',
               'fact_monthly_bills'
           )
           AND has_table_privilege('pms_demo_view_owner', relation.oid, 'SELECT')
    ) THEN
        RAISE EXCEPTION 'view owner can SELECT an unapproved extraction table';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_extract_2010_2023'
           AND relation.relkind IN ('r', 'p')
           AND has_table_privilege(
                   'pms_demo_view_owner',
                   relation.oid,
                   'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
               )
    ) THEN
        RAISE EXCEPTION 'view owner has a write privilege on an extraction table';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_extract_2010_2023'
           AND relation.relkind IN ('r', 'p')
           AND has_table_privilege('pms_demo_runtime', relation.oid, 'SELECT')
    ) THEN
        RAISE EXCEPTION 'runtime can SELECT an extraction base table';
    END IF;

    SELECT count(*)
      INTO approved_view_count
      FROM pg_class AS relation
      JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
     WHERE namespace.nspname = 'pms_demo_access'
       AND relation.relkind = 'v'
       AND relation.relname IN (
           'division_reference',
           'estate_reference',
           'unit_reference',
           'plot_summary',
           'approved_lease_summary',
           'recent_bill_summary'
       )
       AND relation.relowner = to_regrole('pms_demo_view_owner')
       AND relation.reloptions @> ARRAY[
               'security_barrier=true',
               'security_invoker=false'
           ]::text[];

    IF approved_view_count <> 6 THEN
        RAISE EXCEPTION 'approved view ownership or security options are incorrect';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_demo_access'
           AND (
               relation.relkind <> 'v'
               OR relation.relname NOT IN (
                   'division_reference',
                   'estate_reference',
                   'unit_reference',
                   'plot_summary',
                   'approved_lease_summary',
                   'recent_bill_summary'
               )
           )
    ) THEN
        RAISE EXCEPTION 'the demo schema contains an unexpected object';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_demo_access'
           AND relation.relname IN (
               'division_reference',
               'estate_reference',
               'unit_reference',
               'plot_summary',
               'approved_lease_summary',
               'recent_bill_summary'
           )
           AND NOT has_table_privilege('pms_demo_runtime', relation.oid, 'SELECT')
    ) THEN
        RAISE EXCEPTION 'runtime lacks SELECT on an approved view';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
           AND namespace.nspname NOT LIKE 'pg_toast%'
           AND relation.relkind IN ('r', 'p', 'v', 'm', 'S')
           AND has_table_privilege('pms_demo_runtime', relation.oid, 'SELECT')
           AND NOT (
               namespace.nspname = 'pms_demo_access'
               AND relation.relname IN (
                   'division_reference',
                   'estate_reference',
                   'unit_reference',
                   'plot_summary',
                   'approved_lease_summary',
                   'recent_bill_summary'
               )
           )
    ) THEN
        RAISE EXCEPTION 'runtime can SELECT an object outside the six approved views';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
           AND namespace.nspname NOT LIKE 'pg_toast%'
           AND relation.relkind IN ('r', 'p', 'v', 'm', 'S')
           AND has_table_privilege(
                   'pms_demo_runtime',
                   relation.oid,
                   'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
               )
    ) THEN
        RAISE EXCEPTION 'runtime has a write privilege';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_namespace AS namespace
         WHERE namespace.nspowner = to_regrole('pms_demo_runtime')
    ) OR EXISTS (
        SELECT 1
          FROM pg_class AS relation
         WHERE relation.relowner = to_regrole('pms_demo_runtime')
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc AS routine
         WHERE routine.proowner = to_regrole('pms_demo_runtime')
    ) OR EXISTS (
        SELECT 1
          FROM pg_database AS database
         WHERE database.datdba = to_regrole('pms_demo_runtime')
    ) THEN
        RAISE EXCEPTION 'runtime owns a database object';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_namespace AS namespace
         WHERE namespace.nspname = 'pms_demo_access'
           AND namespace.nspowner = to_regrole('pms_demo_view_owner')
    ) <> 1 THEN
        RAISE EXCEPTION 'the approved owner does not own the demo schema';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_namespace AS namespace
         WHERE namespace.nspowner = to_regrole('pms_demo_view_owner')
           AND namespace.nspname <> 'pms_demo_access'
    ) OR EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE relation.relowner = to_regrole('pms_demo_view_owner')
           AND NOT (
               namespace.nspname = 'pms_demo_access'
               AND relation.relname IN (
                   'division_reference',
                   'estate_reference',
                   'unit_reference',
                   'plot_summary',
                   'approved_lease_summary',
                   'recent_bill_summary'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc AS routine
         WHERE routine.proowner = to_regrole('pms_demo_view_owner')
    ) OR EXISTS (
        SELECT 1
          FROM pg_database AS database
         WHERE database.datdba = to_regrole('pms_demo_view_owner')
    ) THEN
        RAISE EXCEPTION 'view owner owns an object outside its approved scope';
    END IF;

    WITH expected(table_name, column_name, ordinal_position) AS (
        VALUES
            ('approved_lease_summary', 'tenancy_type', 1),
            ('approved_lease_summary', 'lease_type_id', 2),
            ('approved_lease_summary', 'bill_periodicity', 3),
            ('approved_lease_summary', 'duration_from', 4),
            ('approved_lease_summary', 'duration_to', 5),
            ('approved_lease_summary', 'renewal_date', 6),
            ('approved_lease_summary', 'is_renewable', 7),
            ('approved_lease_summary', 'status', 8),
            ('approved_lease_summary', 'source_refreshed_at', 9),
            ('division_reference', 'div_code', 1),
            ('division_reference', 'div_name', 2),
            ('division_reference', 'status', 3),
            ('division_reference', 'source_refreshed_at', 4),
            ('estate_reference', 'estate_code', 1),
            ('estate_reference', 'estate_name', 2),
            ('estate_reference', 'status', 3),
            ('estate_reference', 'source_refreshed_at', 4),
            ('plot_summary', 'plot_code', 1),
            ('plot_summary', 'area', 2),
            ('plot_summary', 'status', 3),
            ('plot_summary', 'is_vacant', 4),
            ('plot_summary', 'zone_id', 5),
            ('plot_summary', 'source_refreshed_at', 6),
            ('recent_bill_summary', 'bill_date', 1),
            ('recent_bill_summary', 'due_date', 2),
            ('recent_bill_summary', 'bill_status', 3),
            ('recent_bill_summary', 'source_refreshed_at', 4),
            ('unit_reference', 'unit_code', 1),
            ('unit_reference', 'unit_desc', 2),
            ('unit_reference', 'status', 3),
            ('unit_reference', 'source_refreshed_at', 4)
    )
    SELECT count(*)
      INTO approved_column_count
      FROM expected
      JOIN information_schema.columns AS actual
        ON actual.table_schema = 'pms_demo_access'
       AND actual.table_name = expected.table_name
       AND actual.column_name = expected.column_name
       AND actual.ordinal_position = expected.ordinal_position;

    IF approved_column_count <> 31 THEN
        RAISE EXCEPTION 'approved view output columns differ from the reviewed contract';
    END IF;
END
$postapply_validation$;

COMMIT;

/*
10. COMPLETE ROLLBACK — COPY THIS SECTION INTO A SEPARATE DBA-REVIEWED
SESSION. It intentionally fails before removal if ownership, membership,
contents, role attributes, or tracked view dependencies differ from the
one-time proposal.

ROLLBACK_SQL_BEGIN

BEGIN;

DO $rollback_preconditions$
BEGIN
    IF (
        SELECT count(*)
          FROM pg_roles AS role
         WHERE role.rolname = 'pms_demo_view_owner'
           AND NOT role.rolcanlogin
           AND NOT role.rolsuper
           AND NOT role.rolcreatedb
           AND NOT role.rolcreaterole
           AND NOT role.rolinherit
           AND NOT role.rolreplication
           AND NOT role.rolbypassrls
    ) <> 1 THEN
        RAISE EXCEPTION 'view-owner role is absent or has unexpected attributes';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_roles AS role
         WHERE role.rolname = 'pms_demo_runtime'
           AND role.rolcanlogin
           AND NOT role.rolsuper
           AND NOT role.rolcreatedb
           AND NOT role.rolcreaterole
           AND NOT role.rolinherit
           AND NOT role.rolreplication
           AND NOT role.rolbypassrls
    ) <> 1 THEN
        RAISE EXCEPTION 'runtime role is absent or has unexpected attributes';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_auth_members AS membership
         WHERE membership.roleid IN (
                   to_regrole('pms_demo_view_owner'),
                   to_regrole('pms_demo_runtime')
               )
            OR membership.member IN (
                   to_regrole('pms_demo_view_owner'),
                   to_regrole('pms_demo_runtime')
               )
    ) THEN
        RAISE EXCEPTION 'a demo role has an unexpected membership relationship';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_namespace AS namespace
         WHERE namespace.nspname = 'pms_demo_access'
           AND namespace.nspowner = to_regrole('pms_demo_view_owner')
    ) <> 1 THEN
        RAISE EXCEPTION 'demo schema ownership differs from the proposal';
    END IF;

    IF (
        SELECT count(*)
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_demo_access'
           AND relation.relkind = 'v'
           AND relation.relowner = to_regrole('pms_demo_view_owner')
           AND relation.relname IN (
               'division_reference',
               'estate_reference',
               'unit_reference',
               'plot_summary',
               'approved_lease_summary',
               'recent_bill_summary'
           )
    ) <> 6 THEN
        RAISE EXCEPTION 'expected demo views or owners differ from the proposal';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'pms_demo_access'
           AND (
               relation.relkind <> 'v'
               OR relation.relname NOT IN (
                   'division_reference',
                   'estate_reference',
                   'unit_reference',
                   'plot_summary',
                   'approved_lease_summary',
                   'recent_bill_summary'
               )
           )
    ) THEN
        RAISE EXCEPTION 'the demo schema contains an unexpected object';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_rewrite AS rewrite
          JOIN pg_depend AS dependency ON dependency.objid = rewrite.oid
          JOIN pg_class AS referenced ON referenced.oid = dependency.refobjid
          JOIN pg_namespace AS referenced_namespace
            ON referenced_namespace.oid = referenced.relnamespace
          JOIN pg_class AS dependent ON dependent.oid = rewrite.ev_class
         WHERE referenced_namespace.nspname = 'pms_demo_access'
           AND referenced.relname IN (
               'division_reference',
               'estate_reference',
               'unit_reference',
               'plot_summary',
               'approved_lease_summary',
               'recent_bill_summary'
           )
           AND dependent.oid <> referenced.oid
    ) THEN
        RAISE EXCEPTION 'an external relation depends on an approved demo view';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_namespace AS namespace
         WHERE namespace.nspowner = to_regrole('pms_demo_runtime')
    ) OR EXISTS (
        SELECT 1
          FROM pg_class AS relation
         WHERE relation.relowner = to_regrole('pms_demo_runtime')
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc AS routine
         WHERE routine.proowner = to_regrole('pms_demo_runtime')
    ) OR EXISTS (
        SELECT 1
          FROM pg_database AS database
         WHERE database.datdba = to_regrole('pms_demo_runtime')
    ) THEN
        RAISE EXCEPTION 'runtime owns an unexpected object';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_namespace AS namespace
         WHERE namespace.nspowner = to_regrole('pms_demo_view_owner')
           AND namespace.nspname <> 'pms_demo_access'
    ) OR EXISTS (
        SELECT 1
          FROM pg_class AS relation
          JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
         WHERE relation.relowner = to_regrole('pms_demo_view_owner')
           AND NOT (
               namespace.nspname = 'pms_demo_access'
               AND relation.relname IN (
                   'division_reference',
                   'estate_reference',
                   'unit_reference',
                   'plot_summary',
                   'approved_lease_summary',
                   'recent_bill_summary'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc AS routine
         WHERE routine.proowner = to_regrole('pms_demo_view_owner')
    ) OR EXISTS (
        SELECT 1
          FROM pg_database AS database
         WHERE database.datdba = to_regrole('pms_demo_view_owner')
    ) THEN
        RAISE EXCEPTION 'view owner owns an unexpected object';
    END IF;
END
$rollback_preconditions$;

REVOKE SELECT ON
    pms_demo_access.division_reference,
    pms_demo_access.estate_reference,
    pms_demo_access.unit_reference,
    pms_demo_access.plot_summary,
    pms_demo_access.approved_lease_summary,
    pms_demo_access.recent_bill_summary
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
    pms_extract_2010_2023.extract_config,
    pms_extract_2010_2023.dim_division,
    pms_extract_2010_2023.dim_estate,
    pms_extract_2010_2023.dim_unit,
    pms_extract_2010_2023.dim_plot,
    pms_extract_2010_2023.dim_property_lease,
    pms_extract_2010_2023.fact_monthly_bills
FROM pms_demo_view_owner;

REVOKE USAGE ON SCHEMA pms_extract_2010_2023 FROM pms_demo_view_owner;

DROP ROLE pms_demo_runtime;
DROP ROLE pms_demo_view_owner;

COMMIT;

ROLLBACK_SQL_END
*/
