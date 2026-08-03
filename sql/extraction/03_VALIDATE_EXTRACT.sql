/*
VALIDATE THE FILTERED EXTRACT

Run after 02_BUILD_FILTERED_EXTRACT.sql.
This script is read-only.
*/

-- 1. Row counts for every extracted table.
SELECT
    schemaname,
    relname AS table_name,
    n_live_tup::bigint AS estimated_rows
FROM pg_stat_user_tables
WHERE schemaname = 'pms_extract_2010_2023'
ORDER BY relname;

-- For exact row counts, run this generated SQL selectively on the largest tables.
SELECT format(
    'SELECT %L AS table_name, count(*) AS exact_rows FROM %I.%I;',
    table_name, table_schema, table_name
) AS exact_count_sql
FROM information_schema.tables
WHERE table_schema = 'pms_extract_2010_2023'
  AND table_type = 'BASE TABLE'
ORDER BY table_name;

-- 2. Verify all date-filtered facts stay inside the requested window.
SELECT 'fact_revenue_current' AS source,
       min("revenue_date") AS min_date,
       max("revenue_date") AS max_date,
       count(*) AS rows
FROM "pms_extract_2010_2023"."fact_revenue_current"
UNION ALL
SELECT 'fact_revenue_legacy',
       min("revenuedate"), max("revenuedate"), count(*)
FROM "pms_extract_2010_2023"."fact_revenue_legacy"
UNION ALL
SELECT 'fact_monthly_bills',
       min(COALESCE("bill_date","bill_creation_date","periodfrom"::date)),
       max(COALESCE("bill_date","bill_creation_date","periodfrom"::date)),
       count(*)
FROM "pms_extract_2010_2023"."fact_monthly_bills"
UNION ALL
SELECT 'fact_legacy_general_bills',
       min(COALESCE("billdate"::date,"bill_creation_date","periodfrom"::date)),
       max(COALESCE("billdate"::date,"bill_creation_date","periodfrom"::date)),
       count(*)
FROM "pms_extract_2010_2023"."fact_legacy_general_bills"
UNION ALL
SELECT 'fact_payment_current',
       min(COALESCE("payment_date","transaction_date"::date)),
       max(COALESCE("payment_date","transaction_date"::date)),
       count(*)
FROM "pms_extract_2010_2023"."fact_payment_current"
UNION ALL
SELECT 'fact_payment_legacy',
       min(COALESCE("receiveddate"::date,"billdate"::date)),
       max(COALESCE("receiveddate"::date,"billdate"::date)),
       count(*)
FROM "pms_extract_2010_2023"."fact_payment_legacy"
ORDER BY source;

-- 3. Compare current and legacy source totals. Large overlap may indicate duplicates.
SELECT
    "source_table",
    min("month_start") AS first_month,
    max("month_start") AS last_month,
    sum("transaction_count") AS transactions,
    sum("amount_total") AS amount_total
FROM "pms_extract_2010_2023"."model_revenue_monthly_by_source"
GROUP BY "source_table"
ORDER BY "source_table";

SELECT
    "source_table",
    min("month_start") AS first_month,
    max("month_start") AS last_month,
    sum("bill_count") AS bills,
    sum("head_amount") AS head_amount,
    sum("tax_amount") AS tax_amount,
    sum("final_amount") AS final_amount
FROM "pms_extract_2010_2023"."model_billing_monthly_by_source"
GROUP BY "source_table"
ORDER BY "source_table";

SELECT
    "source_table",
    min("month_start") AS first_month,
    max("month_start") AS last_month,
    sum("payment_count") AS payments,
    sum("paid_amount") AS paid_amount,
    sum("interest_amount") AS interest_amount
FROM "pms_extract_2010_2023"."model_payment_monthly_by_source"
GROUP BY "source_table"
ORDER BY "source_table";

-- 4. Duplicate primary/business identifiers in important extracts.
SELECT 'fact_revenue_current.revenue_no' AS check_name,
       count(*) - count(DISTINCT "revenue_no") AS duplicate_count
FROM "pms_extract_2010_2023"."fact_revenue_current"
UNION ALL
SELECT 'fact_revenue_legacy.revenueid',
       count(*) - count(DISTINCT "revenueid")
FROM "pms_extract_2010_2023"."fact_revenue_legacy"
UNION ALL
SELECT 'fact_monthly_bills.bill_id',
       count(*) - count(DISTINCT "bill_id")
FROM "pms_extract_2010_2023"."fact_monthly_bills"
UNION ALL
SELECT 'fact_legacy_general_bills.generalbillid',
       count(*) - count(DISTINCT "generalbillid")
FROM "pms_extract_2010_2023"."fact_legacy_general_bills"
UNION ALL
SELECT 'fact_payment_current.payment_history_id',
       count(*) - count(DISTINCT "payment_history_id")
FROM "pms_extract_2010_2023"."fact_payment_current";

-- 5. Missing target values and unusable dates.
SELECT
    'revenue_current' AS dataset,
    count(*) AS rows,
    count(*) FILTER (WHERE "amount" IS NULL) AS missing_target,
    count(*) FILTER (WHERE "revenue_date" IS NULL) AS missing_date
FROM "pms_extract_2010_2023"."fact_revenue_current"
UNION ALL
SELECT
    'revenue_legacy',
    count(*),
    count(*) FILTER (WHERE "amount" IS NULL),
    count(*) FILTER (WHERE "revenuedate" IS NULL)
FROM "pms_extract_2010_2023"."fact_revenue_legacy"
UNION ALL
SELECT
    'land_value_observations',
    count(*),
    count(*) FILTER (WHERE "target_value" IS NULL),
    count(*) FILTER (WHERE "observation_date" IS NULL)
FROM "pms_extract_2010_2023"."model_land_value_observations";

-- 6. Land-value target coverage. Keep target meanings separate.
SELECT
    "target_name",
    count(*) AS observations,
    count(DISTINCT "plot_id") AS plots,
    min("observation_date") AS first_date,
    max("observation_date") AS last_date,
    min("target_value") AS minimum_value,
    max("target_value") AS maximum_value
FROM "pms_extract_2010_2023"."model_land_value_observations"
GROUP BY "target_name"
ORDER BY "target_name";

-- 7. Relationship coverage for modelling and tenant-scoped backend queries.
SELECT
    count(*) AS bridge_rows,
    count(*) FILTER (WHERE "tenancy_id" IS NULL) AS missing_tenancy_id,
    count(*) FILTER (WHERE "customer_code" IS NULL) AS missing_customer_code,
    count(*) FILTER (WHERE "plot_id" IS NULL) AS missing_plot_id,
    count(*) FILTER (WHERE "letout_id" IS NULL) AS missing_letout_id
FROM "pms_extract_2010_2023"."bridge_letout_tenancy_plot";

SELECT
    count(*) AS plots,
    count(*) FILTER (WHERE "area" IS NULL) AS missing_area,
    count(*) FILTER (WHERE "estate_id" IS NULL) AS missing_estate,
    count(*) FILTER (WHERE "zone_id" IS NULL) AS missing_zone,
    count(*) FILTER (WHERE "location" IS NULL OR btrim("location") = '') AS missing_location
FROM "pms_extract_2010_2023"."dim_plot";

-- 8. Embedding-source readiness. These rows contain narrative text only.
SELECT
    "source_table",
    "entity_type",
    count(*) AS narrative_rows,
    min(length("text_content")) AS min_length,
    max(length("text_content")) AS max_length,
    avg(length("text_content"))::numeric(12,2) AS avg_length
FROM "pms_extract_2010_2023"."embedding_source_text"
GROUP BY "source_table", "entity_type"
ORDER BY narrative_rows DESC;

-- 9. Tax-rule overlap candidates. These require Finance/Legal review.
SELECT
    a."tax_id" AS tax_id_a,
    b."tax_id" AS tax_id_b,
    a."tax_code",
    a."valid_from" AS a_from,
    a."valid_upto" AS a_to,
    b."valid_from" AS b_from,
    b."valid_upto" AS b_to
FROM "pms_extract_2010_2023"."rule_tax_master" a
JOIN "pms_extract_2010_2023"."rule_tax_master" b
  ON a."tax_id" < b."tax_id"
 AND a."tax_code" IS NOT DISTINCT FROM b."tax_code"
 AND daterange(
       COALESCE(a."valid_from", DATE '0001-01-01'),
       COALESCE(a."valid_upto" + 1, DATE '9999-12-31'),
       '[)'
     )
     &&
     daterange(
       COALESCE(b."valid_from", DATE '0001-01-01'),
       COALESCE(b."valid_upto" + 1, DATE '9999-12-31'),
       '[)'
     )
ORDER BY a."tax_code", a."valid_from";
