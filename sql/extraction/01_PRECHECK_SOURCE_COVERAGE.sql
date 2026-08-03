/*
PRECHECK ONLY — READ-ONLY DATA PROFILING

Run this before 02_BUILD_FILTERED_EXTRACT.sql.
It reports total rows, 2010-2023 rows and date coverage for candidate sources.
No source data is changed.
*/

DROP TABLE IF EXISTS pg_temp.pms_source_coverage;
CREATE TEMP TABLE pms_source_coverage (
    source_table text,
    date_column text,
    table_exists boolean,
    total_rows bigint,
    rows_2010_2023 bigint,
    min_date date,
    max_date date,
    error_message text
);

DO $$
DECLARE
    r record;
    sql_text text;
BEGIN
    FOR r IN
        SELECT *
        FROM (VALUES
            ('cash_revenue_data'::text, 'revenue_date'::text),
            ('trevenue'::text, 'revenuedate'::text),
            ('bills'::text, 'bill_date'::text),
            ('cash_monthly_final_bills'::text, 'bill_date'::text),
            ('monthly_final_bills'::text, 'bill_date'::text),
            ('tgeneralbill'::text, 'billdate'::text),
            ('cash_payment_history'::text, 'transaction_date'::text),
            ('tpaymentmarking'::text, 'receiveddate'::text),
            ('rent_slab'::text, 'period_from'::text),
            ('rent_slab_sor'::text, 'period_from'::text),
            ('additional_rent'::text, 'commencement_date'::text),
            ('additional_rent_slab'::text, 'period_from'::text),
            ('lbty_rent_details'::text, 'from_date'::text),
            ('plot_fair_mkt_value'::text, 'from_date'::text),
            ('plot_rr_land_value'::text, 'from_date'::text),
            ('plot_sor_market_value'::text, 'from_date'::text),
            ('inspection_rpt'::text, 'inspection_date'::text),
            ('breach_rpt'::text, 'breach_rpt_date'::text),
            ('legal_mrtp_notice'::text, 'mrtp_notice_date'::text),
            ('legal_notice_details'::text, 'notice_issue_date'::text),
            ('legal_suit_cases'::text, 'suit_date'::text)
        ) AS v(table_name, date_column)
    LOOP
        IF to_regclass(format('%I.%I', 'public', r.table_name)) IS NULL THEN
            INSERT INTO pms_source_coverage
            VALUES (r.table_name, r.date_column, false, NULL, NULL, NULL, NULL,
                    'table not found in public schema');
            CONTINUE;
        END IF;

        BEGIN
            sql_text := format(
                'INSERT INTO pms_source_coverage
                 SELECT %L, %L, true,
                        count(*)::bigint,
                        count(*) FILTER (
                            WHERE %I::date >= DATE ''2010-01-01''
                              AND %I::date <  DATE ''2024-01-01''
                        )::bigint,
                        min(%I::date),
                        max(%I::date),
                        NULL
                 FROM %I.%I',
                r.table_name, r.date_column,
                r.date_column, r.date_column,
                r.date_column, r.date_column,
                'public', r.table_name
            );
            EXECUTE sql_text;
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO pms_source_coverage
            VALUES (r.table_name, r.date_column, true, NULL, NULL, NULL, NULL,
                    SQLERRM);
        END;
    END LOOP;
END $$;

SELECT *
FROM pms_source_coverage
ORDER BY
    CASE WHEN error_message IS NULL THEN 0 ELSE 1 END,
    rows_2010_2023 DESC NULLS LAST,
    source_table;

-- Tables present in the supplied schema that should be checked for model dimensions.
SELECT
    table_name,
    table_type
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
      'division','unit','estate','plot','mcustomer','mtenant',
      'applicant_registration','applicant_tenant_mapping',
      'applicant_property_mapping','letout_tenancy_unit_mapping',
      'plot_letout_mapping','v_billable_tenancy','lease_particulars',
      'verified_tenancy_data','letout_b_area','letout_fsi'
  )
ORDER BY table_name;

-- Confirm sensitive fields exist but are intentionally excluded from the safe extract.
SELECT
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND (
      lower(column_name) LIKE '%password%'
      OR lower(column_name) LIKE '%aadhaar%'
      OR lower(column_name) LIKE '%aadhar%'
      OR lower(column_name) LIKE '%otp%'
      OR lower(column_name) LIKE '%pan%'
      OR lower(column_name) LIKE '%phone%'
      OR lower(column_name) LIKE '%mobile%'
      OR lower(column_name) LIKE '%email%'
      OR lower(column_name) LIKE '%hostip%'
  )
ORDER BY table_name, ordinal_position;
