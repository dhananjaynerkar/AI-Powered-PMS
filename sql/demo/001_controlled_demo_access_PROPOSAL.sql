/*
CONTROLLED LOCAL DEMO ACCESS — REVIEW-ONLY SQL PROPOSAL

This file has not been executed. Review every statement before an authorized
database administrator applies it. Set the role password out of band with
psql's \password command; no credential belongs in this repository.
*/

BEGIN;

DO $provision$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pms_demo_runtime') THEN
        CREATE ROLE pms_demo_runtime
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END
$provision$;

ALTER ROLE pms_demo_runtime SET default_transaction_read_only = on;
ALTER ROLE pms_demo_runtime SET statement_timeout = '5s';
ALTER ROLE pms_demo_runtime SET search_path = pms_demo_access;

CREATE SCHEMA IF NOT EXISTS pms_demo_access;
REVOKE ALL ON SCHEMA pms_demo_access FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM pms_demo_runtime;
REVOKE ALL ON SCHEMA pms_extract_2010_2023 FROM pms_demo_runtime;
REVOKE ALL ON ALL TABLES IN SCHEMA pms_extract_2010_2023 FROM pms_demo_runtime;
GRANT USAGE ON SCHEMA pms_demo_access TO pms_demo_runtime;

CREATE OR REPLACE VIEW pms_demo_access.division_reference
WITH (security_barrier = true) AS
SELECT division.div_code,
       division.div_name,
       division.status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
FROM pms_extract_2010_2023.dim_division AS division;

CREATE OR REPLACE VIEW pms_demo_access.estate_reference
WITH (security_barrier = true) AS
SELECT estate.estate_code,
       estate.estate_name,
       estate.status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
FROM pms_extract_2010_2023.dim_estate AS estate;

CREATE OR REPLACE VIEW pms_demo_access.unit_reference
WITH (security_barrier = true) AS
SELECT unit.unit_code,
       unit.unit_desc,
       unit.status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
FROM pms_extract_2010_2023.dim_unit AS unit;

CREATE OR REPLACE VIEW pms_demo_access.plot_summary
WITH (security_barrier = true) AS
SELECT plot.plot_code,
       plot.area,
       plot.status,
       plot.is_vacant,
       plot.zone_id,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
FROM pms_extract_2010_2023.dim_plot AS plot;

CREATE OR REPLACE VIEW pms_demo_access.approved_lease_summary
WITH (security_barrier = true) AS
SELECT lease.agreement_number,
       lease.tenancy_type,
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

CREATE OR REPLACE VIEW pms_demo_access.recent_bill_summary
WITH (security_barrier = true) AS
SELECT bill.bill_code,
       bill.bill_date,
       bill.due_date,
       bill.total_head_amount,
       bill.total_tax_amount,
       bill.final_amount,
       bill.bill_status,
       (SELECT max(config.created_at)
          FROM pms_extract_2010_2023.extract_config AS config) AS source_refreshed_at
FROM pms_extract_2010_2023.fact_monthly_bills AS bill
WHERE bill.bill_status = 'A';

REVOKE ALL ON ALL TABLES IN SCHEMA pms_demo_access FROM PUBLIC;
GRANT SELECT ON
    pms_demo_access.division_reference,
    pms_demo_access.estate_reference,
    pms_demo_access.unit_reference,
    pms_demo_access.plot_summary,
    pms_demo_access.approved_lease_summary,
    pms_demo_access.recent_bill_summary
TO pms_demo_runtime;

COMMIT;

/* After approval and execution, the administrator must run interactively:
   \password pms_demo_runtime
*/
