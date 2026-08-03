/*
AI Powered Port Management System
Filtered extraction snapshot: calendar years 2010-2023
Generated only from the supplied 590-table PostgreSQL schema inventory.

IMPORTANT:
1. Review 01_PRECHECK_SOURCE_COVERAGE.sql before running this file.
2. This script keeps current and legacy billing/revenue/payment sources SEPARATE.
3. Do not union them for modelling until row counts and totals are reconciled.
4. Change the date values below only if Finance confirms financial-year boundaries.
5. Replace schema "public" only if your source tables are stored elsewhere.
*/

BEGIN;

CREATE SCHEMA IF NOT EXISTS "pms_extract_2010_2023";

DROP TABLE IF EXISTS "pms_extract_2010_2023"."extract_config";
CREATE TABLE "pms_extract_2010_2023"."extract_config" (
    "start_date" date NOT NULL,
    "end_date_exclusive" date NOT NULL,
    "created_at" timestamptz NOT NULL DEFAULT now(),
    CHECK ("end_date_exclusive" > "start_date")
);

INSERT INTO "pms_extract_2010_2023"."extract_config"
    ("start_date", "end_date_exclusive")
VALUES
    (DATE '2010-01-01', DATE '2024-01-01');

-- Calendar-year window = 2010-01-01 through 2023-12-31.
-- For FY 2010-11 through FY 2023-24, use 2010-04-01 and 2024-04-01.


/* ============================================================
   A. SAFE DIMENSIONS AND RELATIONSHIP TABLES
   ============================================================ */


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_division";
CREATE TABLE "pms_extract_2010_2023"."dim_division" AS
SELECT
    "div_id",
    "div_code",
    "div_name",
    "div_desc",
    "status",
    "is_verified",
    "admin_id"
FROM "public"."division";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_unit";
CREATE TABLE "pms_extract_2010_2023"."dim_unit" AS
SELECT
    "unit_id",
    "unit_code",
    "unit_desc",
    "div_id",
    "status",
    "is_verified",
    "admin_id"
FROM "public"."unit";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_estate";
CREATE TABLE "pms_extract_2010_2023"."dim_estate" AS
SELECT
    "estate_id",
    "unit_id",
    "estate_code",
    "estate_name",
    "estate_desc",
    "status",
    "is_verified",
    "admin_id"
FROM "public"."estate";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_plot";
CREATE TABLE "pms_extract_2010_2023"."dim_plot" AS
SELECT
    "plot_id",
    "estate_id",
    "div_id",
    "unit_id",
    "plot_code",
    "rr_no",
    "street_no",
    "location",
    "city_survey_no",
    "city_survey_div",
    "area",
    "plot_desc",
    "status",
    "is_verified",
    "is_active",
    "zone_id",
    "zone_detail_id",
    "ward_id",
    "customer_code",
    "mbpt_road_connectivity",
    "from_date",
    "to_date",
    "reservation",
    "is_vacant",
    "rrzone2017",
    "pincode",
    "existing_plot_no",
    "prev_plot_id",
    "owner",
    "dept_name",
    "remarks"
FROM "public"."plot";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_customer_legacy";
CREATE TABLE "pms_extract_2010_2023"."dim_customer_legacy" AS
SELECT
    "customerid",
    "customercode",
    "commencedate",
    "billingeffectedon",
    "renewaldate",
    "rrplotno",
    "estateid",
    "wardid",
    "wardno",
    "streetno",
    "billperiodicity",
    "billingmonth",
    "rentrevtypeid",
    "isvacated",
    "vacateddate",
    "remark",
    "leaseagreementno",
    "agreementno",
    "agreementdate",
    "expirydate",
    "relatedcustomerid",
    "blockno",
    "blockvalue",
    "rrzone2012id",
    "rrzone2017id",
    "rrzone2023id",
    "issorapplicable",
    "sorapplicabledate",
    "constructiondate",
    "typeofconstructionid",
    "isstoppayment"
FROM "public"."mcustomer";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_tenant_legacy";
CREATE TABLE "pms_extract_2010_2023"."dim_tenant_legacy" AS
SELECT
    "TenantID",
    "CustomerID",
    "Name",
    "IsActive",
    "IsBillingTenant",
    "ModifiedDate"
FROM "public"."mtenant";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_applicant_safe";
CREATE TABLE "pms_extract_2010_2023"."dim_applicant_safe" AS
SELECT
    "applicant_id",
    "ind_org_name",
    "registration_timestamp",
    "role_id",
    "status",
    "registration_type",
    "org_type_id",
    "customer_code",
    "gst_reg_date",
    "tenant_group_id",
    "mbpt_tenant_id",
    "is_active",
    "is_billing_tenant"
FROM "public"."applicant_registration";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."bridge_applicant_tenancy";
CREATE TABLE "pms_extract_2010_2023"."bridge_applicant_tenancy" AS
SELECT
    "app_tenant_map_id",
    "applicant_id",
    "tenancy_id",
    "tenant_id",
    "customer_code",
    "agreement_number",
    "billable",
    "billable_as",
    "from_date",
    "to_date",
    "is_active",
    "is_deleted",
    "customer_grp_id",
    "customer_grp",
    "status"
FROM "public"."applicant_tenant_mapping";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."bridge_letout_tenancy_plot";
CREATE TABLE "pms_extract_2010_2023"."bridge_letout_tenancy_plot" AS
SELECT
    "sr_no",
    "letout_id",
    "unit_id",
    "tenancy_id",
    "tenant_id",
    "customer_code",
    "plot_id",
    "home_rate",
    "non_home_rate",
    "current_rent",
    "current_rent_commercial",
    "current_rent_residential",
    "rate_revision_interval",
    "next_revision_date",
    "percent_rate_revision",
    "amount_rate_revision",
    "next_escalation_date",
    "agreement_start_date",
    "agreement_end_date",
    "bill_periodicity",
    "bill_commencement_date",
    "commencement_date",
    "termination_date",
    "from_date",
    "to_date",
    "is_active",
    "is_alloted",
    "is_renewable",
    "renewable",
    "long_lease_years",
    "is_service_charge_applicable",
    "service_charge_applicable_required",
    "is_post_bill",
    "description",
    "description_renewal",
    "ratejustify"
FROM "public"."letout_tenancy_unit_mapping";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."bridge_plot_letout";
CREATE TABLE "pms_extract_2010_2023"."bridge_plot_letout" AS
SELECT
    "plot_letout_id",
    "plot_id",
    "let_out_id",
    "status",
    "prev_plot_id",
    "verificationremarks"
FROM "public"."plot_letout_mapping";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_billable_tenancy";
CREATE TABLE "pms_extract_2010_2023"."dim_billable_tenancy" AS
SELECT
    "tenancy_id",
    "agreement_number",
    "tenancy_type",
    "tenant_type",
    "bill_periodicity",
    "allotment_basis",
    "applicant_id",
    "ind_org_name",
    "billable",
    "billable_as",
    "customer_grp",
    "individual_shear",
    "tenant_from_date",
    "tenant_to_date"
FROM "public"."v_billable_tenancy";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_property_lease";
CREATE TABLE "pms_extract_2010_2023"."dim_property_lease" AS
SELECT
    "tenant_id",
    "tenancy_id",
    "customer_code",
    "property_id",
    "estate_id",
    "unit_id",
    "agreement_number",
    "tenancy_type",
    "tenant_type",
    "lease_type_id",
    "description",
    "purpose",
    "allotment_basis",
    "bill_periodicity",
    "rate_revision_period",
    "rate",
    "percent_rate_revision",
    "amount_rate_revision",
    "is_upfront_premium",
    "upfront_premium_amt",
    "security_deposit_type",
    "security_deposit_amt",
    "total_security_deposit",
    "date_of_agreement",
    "duration_from",
    "duration_to",
    "renewal_date",
    "is_renewable",
    "description_renewal",
    "is_renewal_clause",
    "suit_exist",
    "is_sor_applicable",
    "sor_applicable_date",
    "status",
    "remarks",
    "taxremarks"
FROM "public"."applicant_property_mapping";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_letout_area";
CREATE TABLE "pms_extract_2010_2023"."fact_letout_area" AS
SELECT
    "letout_b_area_id",
    "let_out_id",
    "tenancy_id",
    "area_type_id",
    "area",
    "from_date",
    "to_date",
    "consumed_fsi",
    "built_up_area_rec",
    "built_up_area_commercial",
    "is_active",
    "status",
    "verification_remarks"
FROM "public"."letout_b_area";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_letout_fsi";
CREATE TABLE "pms_extract_2010_2023"."fact_letout_fsi" AS
SELECT
    "letout_fsi_id",
    "let_out_id",
    "fsi_type_id",
    "fsi",
    "from_date",
    "to_date"
FROM "public"."letout_fsi";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_lease_particulars_snapshot";
CREATE TABLE "pms_extract_2010_2023"."dim_lease_particulars_snapshot" AS
SELECT
    "lease_particulars_id",
    "cust_code",
    "plot_no_rr_no_04_12",
    "plot_no_rr_no_12_17",
    "plot_no_rr_no_current",
    "ready_reconer_zone_04_12",
    "ready_reconer_zone_12_17",
    "ready_reconer_zone_current",
    "plot_area_sqm_04_12",
    "plot_area_sqm_12_17",
    "plot_area_sqm_currrent",
    "builtup_area_sqm_04_12",
    "builtup_area_sqm_12_17",
    "builtup_area_sqm_current",
    "tenure_04_12",
    "tenure_12_17",
    "tenure_current",
    "date_of_commencement_of_current_lease_04_12",
    "date_of_commencement_of_current_lease_12_17",
    "date_of_commencement_of_current_lease_current",
    "date_of_epiry_of_current_lease_04_12",
    "date_of_epiry_of_current_lease_12_17",
    "date_of_epiry_of_current_lease_current",
    "sanctioned_user_04_12",
    "sanctioned_user_12_17",
    "sanctioned_user_current",
    "present_user_04_12",
    "present_user_12_17",
    "present_user_current",
    "renewal_clause_04_12",
    "renewal_clause_12_17",
    "renewal_clause_current",
    "contractual_rent_04_12",
    "contractual_rent_12_17",
    "contractual_rent_current",
    "revised_rent_rates_sqm_month_04_12",
    "revised_rent_rates_sqm_month_12_17",
    "revised_rent_rates_sqm_month_current",
    "date_of_revision_04_12",
    "date_of_revision_12_17",
    "date_of_revision_current",
    "additional_rent_if_billed_04_12",
    "additional_rent_if_billed_12_17",
    "additional_rent_if_billed_current",
    "billed_arrears_04_12",
    "billed_arrears_12_17",
    "billed_arrears_current",
    "outstanding_arrears_04_12",
    "outstanding_arrears_12_17",
    "outstanding_arrears_current",
    "interest_rate_04_12",
    "interest_rate_12_17",
    "interest_rate_current",
    "penalties_04_12",
    "penalties_12_17",
    "penalties_current",
    "gst_04_12",
    "gst_12_17",
    "gst_current",
    "taxes_04_12",
    "taxes_12_17",
    "taxes_current",
    "notice_issued_04_12",
    "notice_issued_12_17",
    "notice_issued_current",
    "suit_filled_04_12",
    "suit_filled_12_17",
    "suit_filled_current",
    "mrtp_notice_issued_04_12",
    "mrtp_notice_issued_12_17",
    "mrtp_notice_issued_current",
    "amount_to_be_recovered_04_12",
    "amount_to_be_recovered_12_17",
    "amount_to_be_recovered_current",
    "status"
FROM "public"."lease_particulars";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."dim_verified_tenancy_snapshot";
CREATE TABLE "pms_extract_2010_2023"."dim_verified_tenancy_snapshot" AS
SELECT
    "tenancy_id",
    "allotment_basis",
    "hand_file_no",
    "tr_br_no",
    "tr_br_date",
    "agreement_number",
    "agreement_t_and_c",
    "type_of_tenancy",
    "from_date",
    "to_date",
    "type_of_tenant",
    "renewal_clause",
    "sanction_use",
    "building_identification_no",
    "building_name",
    "cessed_building",
    "area",
    "commencement_date",
    "expiry_date",
    "consumed_fsi",
    "vacated",
    "date_of_vacation",
    "category",
    "Plot no./ RR no."
FROM "public"."verified_tenancy_data";


/* ============================================================
   B. REVENUE, BILLING AND PAYMENT FACTS
   ============================================================ */


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_revenue_current";
CREATE TABLE "pms_extract_2010_2023"."fact_revenue_current" AS
SELECT
    s."revenue_no",
    s."revenue_date",
    s."inward_no",
    s."remark",
    s."revenue_coll_type",
    s."revenue_type",
    s."amount",
    s."ui_revenue_no",
    s."ref_no",
    s."ref_type",
    s."cash_bill_id",
    s."bill_code",
    s."div_id",
    s."isauditorrevenue",
    s."isautogenerated",
    s."uniqueno",
    s."update_timestamp"
FROM "public"."cash_revenue_data" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE "revenue_date" >= c."start_date" AND "revenue_date" < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_revenue_legacy";
CREATE TABLE "pms_extract_2010_2023"."fact_revenue_legacy" AS
SELECT
    s."revenueid",
    s."revenueno",
    s."revenuedate",
    s."collectionmodeid",
    s."amount",
    s."divisionid",
    s."inwardid",
    s."wdid",
    s."isauditorrevenue",
    s."isautogenerated",
    s."uniqueno",
    s."xpaymenttypeid",
    s."xpayment",
    s."xcollection",
    s."customer_code",
    s."modifieddate"
FROM "public"."trevenue" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE "revenuedate" >= c."start_date" AND "revenuedate" < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_bills_simple";
CREATE TABLE "pms_extract_2010_2023"."fact_bills_simple" AS
SELECT
    s."bill_id",
    s."bill_code",
    s."tenant_id",
    s."bill_date",
    s."cur_bill_amount",
    s."cur_bill_tax_amount",
    s."cur_bill_total_amount",
    s."arrear_amount",
    s."prev_payment",
    s."payment_upto_date",
    s."interest_on_arrears",
    s."total_bill_amount",
    s."is_verified",
    s."status",
    s."remarks"
FROM "public"."bills" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE "bill_date" >= c."start_date" AND "bill_date" < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_cash_monthly_bills";
CREATE TABLE "pms_extract_2010_2023"."fact_cash_monthly_bills" AS
SELECT
    s."cash_bill_id",
    s."bill_code",
    s."tenancy_id",
    s."total_head_amount",
    s."total_tax_amount",
    s."total_final_amount",
    s."bill_status",
    s."total_paid_amount",
    s."total_balance_amount",
    s."mode_of_payment",
    s."type_of_payment",
    s."head_paid_amount",
    s."tax_paid_amount",
    s."bill_creation_date",
    s."bill_due_date",
    s."bill_date",
    s."receiveddate",
    s."tdsamount",
    s."billchargeid"
FROM "public"."cash_monthly_final_bills" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE("bill_date", "bill_creation_date", "receiveddate"::date) >= c."start_date" AND COALESCE("bill_date", "bill_creation_date", "receiveddate"::date) < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_monthly_bills";
CREATE TABLE "pms_extract_2010_2023"."fact_monthly_bills" AS
SELECT
    s."bill_id",
    s."bill_code",
    s."bill_creation_date",
    s."total_head_amount",
    s."total_tax_amount",
    s."final_amount",
    s."is_verified",
    s."bill_status",
    s."tenancy_id",
    s."lease_type",
    s."bill_periodicity",
    s."bill_year",
    s."unit_id",
    s."bill_month",
    s."bill_date",
    s."due_date",
    s."on_hold",
    s."is_approved",
    s."customer_id",
    s."bill_remark",
    s."periodfrom",
    s."periodto",
    s."amount",
    s."totalretrenchedamount",
    s."totalcreditnoteadjustedamount",
    s."sgstrate",
    s."sgst",
    s."cgstrate",
    s."cgst",
    s."muamount",
    s."adjustedamount",
    s."tdsamount",
    s."amountsor",
    s."sgstsor",
    s."cgstsor",
    s."muamountsor",
    s."typeofbillid",
    s."demandnoticeid"
FROM "public"."monthly_final_bills" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE("bill_date", "bill_creation_date", "periodfrom"::date) >= c."start_date" AND COALESCE("bill_date", "bill_creation_date", "periodfrom"::date) < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_legacy_general_bills";
CREATE TABLE "pms_extract_2010_2023"."fact_legacy_general_bills" AS
SELECT
    s."generalbillid",
    s."billyearmonth",
    s."customerid",
    s."customerCode",
    s."tenantid",
    s."billnumber",
    s."billdate",
    s."periodfrom",
    s."periodto",
    s."duedate",
    s."amount",
    s."totalretrenchedamount",
    s."totalcreditnoteadjustedamount",
    s."sgstrate",
    s."sgst",
    s."cgstrate",
    s."cgst",
    s."muamount",
    s."adjustedamount",
    s."tdsamount",
    s."amountsor",
    s."sgstsor",
    s."cgstsor",
    s."muamountsor",
    s."bill_creation_date",
    s."total_head_amount",
    s."total_tax_amount",
    s."bill_code",
    s."is_verified",
    s."is_approved",
    s."rr_no",
    s."area_in_sqm",
    s."estate_name",
    s."tenure",
    s."gl_code",
    s."sacode",
    s."nature_of_charge",
    s."nature_charge_head_tax",
    s."pre_post_tax_applicable",
    s."tax_amount",
    s."nature_of_tax",
    s."tax_percentage",
    s."tax_period_from",
    s."tax_period_to",
    s."final_amount",
    s."bill_amount",
    s."status",
    s."billcharge",
    s."bill_category",
    s."plot_area"
FROM "public"."tgeneralbill" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE("billdate"::date, "bill_creation_date", "periodfrom"::date) >= c."start_date" AND COALESCE("billdate"::date, "bill_creation_date", "periodfrom"::date) < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_monthly_bill_heads";
CREATE TABLE "pms_extract_2010_2023"."fact_monthly_bill_heads" AS
SELECT
    s."head_id",
    s."bill_id",
    s."gl_code",
    s."bill_amount",
    s."sacode",
    s."from_date",
    s."to_date",
    s."nature_of_charge",
    s."paid_amount",
    s."paid_status",
    s."nature_charge_type_id",
    s."nature_charge_head_tax",
    s."pre_post_tax_applicable",
    s."total_retrenched_amount",
    s."sgst_rate",
    s."sgst",
    s."cgst_rate",
    s."cgst",
    s."mu_amount",
    s."tds_amount",
    s."bill_number",
    s."bill_code",
    s."customer_id",
    s."billdate",
    s."gstbilldate",
    s."interestcalculationdate",
    s."amountsor",
    s."sgstsor",
    s."cgstsor"
FROM "public"."monthly_head_final_bills" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE "billdate"::date >= c."start_date" AND "billdate"::date < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_monthly_bill_taxes";
CREATE TABLE "pms_extract_2010_2023"."fact_monthly_bill_taxes" AS
SELECT
    s."tax_id",
    s."bill_id",
    s."gl_code",
    s."tax_amount",
    s."nature_of_tax",
    s."tax_percentage",
    s."tax_period_from",
    s."tax_period_to",
    s."paid_amount",
    s."paid_status",
    s."bill_head_id",
    s."bill_item_id",
    s."bill_code",
    s."head_id",
    s."sacode",
    s."nature_of_charge",
    s."nature_charge_head_tax",
    s."pre_post_tax_applicable",
    s."sgst_rate",
    s."sgst",
    s."cgst_rate",
    s."cgst",
    s."mu_amount",
    s."tds_amount",
    s."bill_number",
    s."customer_id",
    s."billdate",
    s."gstbilldate",
    s."amountsor",
    s."sgstsor",
    s."cgstsor"
FROM "public"."monthly_taxes_final_bills" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE "billdate"::date >= c."start_date" AND "billdate"::date < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_payment_current";
CREATE TABLE "pms_extract_2010_2023"."fact_payment_current" AS
SELECT
    s."payment_history_id",
    s."cash_bill_id",
    s."bill_code",
    s."amount",
    s."transaction_date",
    s."payment_date",
    s."gl_code",
    s."payment_delay_by_year",
    s."payment_delay_by_month",
    s."payment_delay_by_day",
    s."interest_amount",
    s."interest_billed",
    s."process_type",
    s."head_amount",
    s."head_balance_amount",
    s."settlement_type",
    s."bill_head_id",
    s."bill_item_id",
    s."is_final",
    s."tds_amt"
FROM "public"."cash_payment_history" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE("payment_date", "transaction_date"::date) >= c."start_date" AND COALESCE("payment_date", "transaction_date"::date) < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_payment_legacy";
CREATE TABLE "pms_extract_2010_2023"."fact_payment_legacy" AS
SELECT
    s."id",
    s."paymenttypeid",
    s."generalbillid",
    s."revenueid",
    s."receiveddate",
    s."amount",
    s."tdsamount",
    s."paymentmodeid",
    s."customerid",
    s."billchargeid",
    s."billnumber",
    s."billdate",
    s."customercode",
    s."interest_amount",
    s."duedate"
FROM "public"."tpaymentmarking" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE("receiveddate"::date, "billdate"::date, "createddate"::date) >= c."start_date" AND COALESCE("receiveddate"::date, "billdate"::date, "createddate"::date) < c."end_date_exclusive";


/* ============================================================
   C. TAX, RENT AND LEASE-CALCULATION INPUTS
   ============================================================ */


DROP TABLE IF EXISTS "pms_extract_2010_2023"."rule_tax_master";
CREATE TABLE "pms_extract_2010_2023"."rule_tax_master" AS
SELECT
    s."tax_id",
    s."tax_name",
    s."tax_desc",
    s."tax_code",
    s."tax_in_percent",
    s."tax_percent",
    s."fixed_tax_amount",
    s."valid_from",
    s."valid_upto",
    s."is_active",
    s."tax_type_id",
    s."tax_rev_id",
    s."gl_code",
    s."advance_tax",
    s."bill_periodicity",
    s."tax_type",
    s."tax_category",
    s."tax_month",
    s."is_head_amount",
    s."sa_code",
    s."pre_post_tax_applicable",
    s."status",
    s."applicable_in_report",
    s."bill_charge_id"
FROM "public"."m_taxes" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."valid_upto", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."valid_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."rule_tax_history";
CREATE TABLE "pms_extract_2010_2023"."rule_tax_history" AS
SELECT
    s."tax_revision_id",
    s."tax_id",
    s."tax_name",
    s."tax_desc",
    s."tax_code",
    s."tax_in_percent",
    s."tax_percent",
    s."fixed_tax_amount",
    s."valid_from",
    s."valid_upto",
    s."is_active",
    s."tax_type_id",
    s."tax_rev_id",
    s."bill_periodicity",
    s."tax_type",
    s."tax_category",
    s."tax_month",
    s."advance_tax",
    s."gl_code",
    s."is_head_amount",
    s."sa_code",
    s."pre_post_tax_applicable",
    s."revision_desc"
FROM "public"."m_taxes_history" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."valid_upto", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."valid_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."rule_tax_period";
CREATE TABLE "pms_extract_2010_2023"."rule_tax_period" AS
SELECT
    s."tax_period_id",
    s."tax_id",
    s."tax_percent",
    s."fixed_tax_amount",
    s."valid_from",
    s."valid_upto"
FROM "public"."m_taxes_period" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."valid_upto", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."valid_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."rule_property_tax_rates";
CREATE TABLE "pms_extract_2010_2023"."rule_property_tax_rates" AS
SELECT
    s."tax_rate_id",
    s."tax_period_from",
    s."tax_period_to",
    s."gen_tax",
    s."wtr_tax",
    s."sewr_tax",
    s."wbt",
    s."sbt",
    s."egc",
    s."edc",
    s."prop",
    s."sum_wbt_sbt_egc"
FROM "public"."m_tax_rates" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."tax_period_to", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."tax_period_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."bridge_tenant_tax";
CREATE TABLE "pms_extract_2010_2023"."bridge_tenant_tax" AS
SELECT
    "tenant_tax_id",
    "tenant_id",
    "applicable_tax_id",
    "is_active"
FROM "public"."tenant_taxes_mapping";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."bridge_applicant_tax";
CREATE TABLE "pms_extract_2010_2023"."bridge_applicant_tax" AS
SELECT
    s."app_tax_map_id",
    s."agreement_number",
    s."applicable_tax_codes",
    s."tax_id",
    s."tenancy_id",
    s."from_date",
    s."to_date",
    s."old_customer_code",
    s."is_manual_taxes",
    s."is_monthly_taxes",
    s."manual_tax_value",
    s."is_applicable"
FROM "public"."applicant_tax_mapping" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."to_date", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."from_date", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_rent_slab";
CREATE TABLE "pms_extract_2010_2023"."fact_rent_slab" AS
SELECT
    s."rent_slab_id",
    s."tenancy_id",
    s."letout_id",
    s."period_from",
    s."period_to",
    s."calculated_rent",
    s."revision_desc",
    s."rent_as_per",
    s."rate_type",
    s."billed_rate",
    s."letout_type_id",
    s."customer_code",
    s."hire_fees"
FROM "public"."rent_slab" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."period_to", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."period_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_rent_slab_sor";
CREATE TABLE "pms_extract_2010_2023"."fact_rent_slab_sor" AS
SELECT
    s."rent_sor_id",
    s."tenancy_id",
    s."letout_id",
    s."period_from",
    s."period_to",
    s."calculated_rent",
    s."revision_desc",
    s."rent_as_per",
    s."rate_type",
    s."billed_rate",
    s."letout_type_id",
    s."customer_code",
    s."hire_fees"
FROM "public"."rent_slab_sor" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."period_to", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."period_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_additional_rent";
CREATE TABLE "pms_extract_2010_2023"."fact_additional_rent" AS
SELECT
    s."additional_rent_id",
    s."tenancy_id",
    s."letout_id",
    s."additional_rent",
    s."additional_rent_type",
    s."description",
    s."is_one_time",
    s."increment_applicable",
    s."increment_interval",
    s."next_increment_date",
    s."increment_in_percent",
    s."increment_fixed_amt",
    s."taxes_applicable",
    s."area_affected",
    s."is_service_charge_applicable",
    s."is_active",
    s."reference_id",
    s."commencement_date",
    s."bill_periodicity",
    s."no_of_months",
    s."breach",
    s."date_of_breach",
    s."date_of_inspection",
    s."date_of_payment",
    s."commencement_end_date",
    s."mode_of_payment"
FROM "public"."additional_rent" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."commencement_end_date", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."commencement_date", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_additional_rent_slab";
CREATE TABLE "pms_extract_2010_2023"."fact_additional_rent_slab" AS
SELECT
    s."additional_rent_slab_id",
    s."additional_rent_id",
    s."tenancy_id",
    s."letout_id",
    s."period_from",
    s."period_to",
    s."calculated_rent",
    s."description",
    s."rent_as_per",
    s."rate_type",
    s."billed_rate",
    s."additional_rent_type_id"
FROM "public"."additional_rent_slab" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."period_to", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."period_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_liability_rent";
CREATE TABLE "pms_extract_2010_2023"."fact_liability_rent" AS
SELECT
    s."lbty_rent_details_id",
    s."tenancy_id",
    s."from_date",
    s."to_date",
    s."rate_per_sqm",
    s."rent_amount",
    s."effective_date",
    s."revision_date"
FROM "public"."lbty_rent_details" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."to_date", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."from_date", DATE '0001-01-01') < c."end_date_exclusive";


/* ============================================================
   D. LAND-RATE AND VALUATION INPUTS
   ============================================================ */


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_plot_fair_market_value";
CREATE TABLE "pms_extract_2010_2023"."fact_plot_fair_market_value" AS
SELECT
    s."plot_fair_mkt_value_id",
    s."plot_id",
    s."fair_mkt_value",
    s."tr_value",
    s."from_date",
    s."to_date"
FROM "public"."plot_fair_mkt_value" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."to_date"::date, DATE '9999-12-31') >= c."start_date" AND COALESCE(s."from_date"::date, DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_plot_rr_land_value";
CREATE TABLE "pms_extract_2010_2023"."fact_plot_rr_land_value" AS
SELECT
    s."plot_rr_land_value_id",
    s."plot_id",
    s."rr_land_value",
    s."from_date",
    s."to_date",
    s."future_date"
FROM "public"."plot_rr_land_value" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."to_date", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."from_date", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_plot_sor_market_value";
CREATE TABLE "pms_extract_2010_2023"."fact_plot_sor_market_value" AS
SELECT
    s."plot_sor_market_value_id",
    s."plot_id",
    s."sor_mkt_value",
    s."from_date",
    s."to_date"
FROM "public"."plot_sor_market_value" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."to_date", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."from_date", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."rule_zone_rate";
CREATE TABLE "pms_extract_2010_2023"."rule_zone_rate" AS
SELECT
    s."zone_rate_id",
    s."zone_id",
    s."zone_detail_id",
    s."rate_applicable_from",
    s."rate_applicable_upto",
    s."annual_increment",
    s."next_annual_increment_date",
    s."status",
    s."desc",
    s."non_home_rate",
    s."home_rate"
FROM "public"."zone_rate" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."rate_applicable_upto", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."rate_applicable_from", DATE '0001-01-01') < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."rule_generic_rate";
CREATE TABLE "pms_extract_2010_2023"."rule_generic_rate" AS
SELECT
    s."rate_id",
    s."rate_type_id",
    s."effective_from",
    s."effective_to",
    s."is_active",
    s."rate_value"
FROM "public"."rates" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."effective_to", DATE '9999-12-31') >= c."start_date" AND COALESCE(s."effective_from", DATE '0001-01-01') < c."end_date_exclusive";


/* ============================================================
   E. INSPECTION, BREACH AND LEGAL-RISK INPUTS
   ============================================================ */


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_inspection";
CREATE TABLE "pms_extract_2010_2023"."fact_inspection" AS
SELECT
    s."inspection_rpt_id",
    s."let_out_id",
    s."inspection_date",
    s."status",
    s."is_verified",
    s."insp_type_id",
    s."is_occupant_available",
    s."observation_date",
    s."occupied_since",
    s."customer_code",
    s."tenancy_type",
    s."is_vacant_plot",
    s."division_id",
    s."unit_id",
    s."plot_id",
    s."estate_id",
    s."tenure",
    s."renewal_clause",
    s."type_of_lease",
    s."sanction_user",
    s."type_of_struct",
    s."desc_of_struct",
    s."bltup_area",
    s."fsi_used_in_record",
    s."suit_status_no",
    s."reservation",
    s."zone_perm_fsi",
    s."rmk_major_1",
    s."rmk_major_2",
    s."rmk_major_3",
    s."rmk_major_4",
    s."rmk_major_5",
    s."rmk_major_6",
    s."rmk_major_7",
    s."rmk_major_8",
    s."rmk_major_9",
    s."rmk_major_10",
    s."rmk_minor_1",
    s."rmk_minor_2",
    s."rmk_minor_3"
FROM "public"."inspection_rpt" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."inspection_date", s."observation_date") >= c."start_date" AND COALESCE(s."inspection_date", s."observation_date") < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_inspection_encroachment";
CREATE TABLE "pms_extract_2010_2023"."fact_inspection_encroachment" AS
SELECT
    s."insp_encroachment_id",
    s."inspection_rpt_id",
    s."area",
    s."enchroachment_date",
    s."removal_date",
    s."remarks",
    s."detection_date",
    s."survey_area",
    s."survey_remark",
    s."breach_autho_unautho",
    s."encroachment_details",
    s."is_deleted",
    s."location_ench"
FROM "public"."inspection_encroachment" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."detection_date", s."enchroachment_date") >= c."start_date" AND COALESCE(s."detection_date", s."enchroachment_date") < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_inspection_construction";
CREATE TABLE "pms_extract_2010_2023"."fact_inspection_construction" AS
SELECT
    s."inspection_constr_id",
    s."inspection_rpt_id",
    s."constr_type_id",
    s."area",
    s."detection_date",
    s."remarks",
    s."survey_area",
    s."survey_remark",
    s."breach_autho_unautho",
    s."is_deleted",
    s."location_unauth"
FROM "public"."inspection_construction" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE s."detection_date" >= c."start_date" AND s."detection_date" < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_inspection_change_of_use";
CREATE TABLE "pms_extract_2010_2023"."fact_inspection_change_of_use" AS
SELECT
    s."inspection_change_id",
    s."inspection_rpt_id",
    s."extent",
    s."present_use",
    s."existing_use_area",
    s."sanctioned_use",
    s."from_date",
    s."to_date",
    s."remarks",
    s."detection_date",
    s."survey_area",
    s."survey_remark",
    s."breach_autho_unautho",
    s."is_deleted",
    s."location_cou"
FROM "public"."inspection_change_of_use" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."detection_date", s."from_date") >= c."start_date" AND COALESCE(s."detection_date", s."from_date") < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_inspection_sublet";
CREATE TABLE "pms_extract_2010_2023"."fact_inspection_sublet" AS
SELECT
    s."inspection_sublet_id",
    s."inspection_rpt_id",
    s."extent",
    s."area",
    s."location",
    s."remarks",
    s."from_date",
    s."to_date",
    s."detection_date",
    s."survey_area",
    s."survey_remark",
    s."breach_autho_unautho",
    s."is_deleted"
FROM "public"."inspection_sublet" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."detection_date", s."from_date") >= c."start_date" AND COALESCE(s."detection_date", s."from_date") < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_inspection_unauthorized_repairs";
CREATE TABLE "pms_extract_2010_2023"."fact_inspection_unauthorized_repairs" AS
SELECT
    s."inspection_repairs_id",
    s."inspection_rpt_id",
    s."area",
    s."remarks",
    s."detection_date",
    s."survey_area",
    s."survey_remark",
    s."breach_autho_unautho",
    s."repair_type_id",
    s."is_deleted",
    s."location_repair"
FROM "public"."inspection_unauthorized_repairs" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE s."detection_date" >= c."start_date" AND s."detection_date" < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_breach";
CREATE TABLE "pms_extract_2010_2023"."fact_breach" AS
SELECT
    s."breach_rpt_id",
    s."let_out_id",
    s."inspection_rpt_id",
    s."nature_of_breach",
    s."breach_subtype_id",
    s."breach_area",
    s."breach_status_id",
    s."self_dclr_breach",
    s."status",
    s."is_verified",
    s."breach_desc",
    s."breach_end_date",
    s."breach_end_status",
    s."breach_start_date",
    s."breach_rpt_date",
    s."breach_description",
    s."desc_of_breach"
FROM "public"."breach_rpt" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."breach_rpt_date", s."breach_start_date") >= c."start_date" AND COALESCE(s."breach_rpt_date", s."breach_start_date") < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_mrtp_notice";
CREATE TABLE "pms_extract_2010_2023"."fact_mrtp_notice" AS
SELECT
    s."mrtp_notice_id",
    s."inspection_rpt_id",
    s."premises_desc",
    s."contractual_relation",
    s."construction_type",
    s."inspection_date",
    s."site_location",
    s."devp_nature",
    s."remark",
    s."status",
    s."construction_status",
    s."action_taken",
    s."mrtp_notice_date",
    s."mrtp_notice_no",
    s."subject",
    s."heading_mrtp",
    s."notice_date",
    s."ref_no",
    s."additional_info",
    s."fy_mrtp_notice_no",
    s."mode_of_service",
    s."service_date",
    s."is_vacant_plot",
    s."let_out_id",
    s."customer_code",
    s."unit_id",
    s."div_id",
    s."estate_id",
    s."plot_id"
FROM "public"."legal_mrtp_notice" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."mrtp_notice_date", s."notice_date", s."inspection_date") >= c."start_date" AND COALESCE(s."mrtp_notice_date", s."notice_date", s."inspection_date") < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_legal_notice";
CREATE TABLE "pms_extract_2010_2023"."fact_legal_notice" AS
SELECT
    s."notice_id",
    s."tenancy_id",
    s."tenancy_date",
    s."plot_area",
    s."from_date",
    s."remark",
    s."party_issue",
    s."quit_date",
    s."is_deleted",
    s."status",
    s."action_taken",
    s."notice_issue_date",
    s."notice_no",
    s."tenancy_expiry_date",
    s."tenancy_type",
    s."customer_code",
    s."fy_notice_no",
    s."mode_of_service",
    s."service_date"
FROM "public"."legal_notice_details" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE COALESCE(s."notice_issue_date", s."service_date", s."from_date") >= c."start_date" AND COALESCE(s."notice_issue_date", s."service_date", s."from_date") < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."fact_legal_suit";
CREATE TABLE "pms_extract_2010_2023"."fact_legal_suit" AS
SELECT
    s."suit_case_id",
    s."suit_case_no",
    s."suit_no",
    s."plot_no",
    s."suit_year",
    s."suit_date",
    s."hand_file_no",
    s."court_id",
    s."remarks",
    s."tenancy_id",
    s."suit_stage_id",
    s."withdrawn",
    s."suit_type_id",
    s."letout_id",
    s."suit_ref_no",
    s."suit_filed_by",
    s."termination_notice",
    s."status",
    s."is_deleted",
    s."current_suit_stage",
    s."case_year",
    s."legal_file_no",
    s."litigation_ground",
    s."customer_code",
    s."mrtp_notice_no",
    s."next_hearing_date",
    s."previous_hearing_date"
FROM "public"."legal_suit_cases" AS s
CROSS JOIN "pms_extract_2010_2023"."extract_config" AS c
WHERE s."suit_date" >= c."start_date" AND s."suit_date" < c."end_date_exclusive";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."bridge_legal_suit_plot";
CREATE TABLE "pms_extract_2010_2023"."bridge_legal_suit_plot" AS
SELECT
    "suit_plot_map_id",
    "suit_case_id",
    "plot_no",
    "rr_no",
    "plot_id",
    "is_deleted"
FROM "public"."legal_suit_plot_mapping";


DROP TABLE IF EXISTS "pms_extract_2010_2023"."bridge_legal_suit_tenancy";
CREATE TABLE "pms_extract_2010_2023"."bridge_legal_suit_tenancy" AS
SELECT
    "suit_case_id",
    "suit_no",
    "tenancy_id",
    "is_deleted",
    "suit_tenancy_map_id"
FROM "public"."legal_suit_tenancy_mapping";


/* ============================================================
   F. SOURCE-SPECIFIC MODEL BASES
   Do not combine current and legacy sources until QA confirms
   that they do not represent the same transactions.
   ============================================================ */

DROP TABLE IF EXISTS "pms_extract_2010_2023"."model_revenue_monthly_by_source";
CREATE TABLE "pms_extract_2010_2023"."model_revenue_monthly_by_source" AS
SELECT
    'cash_revenue_data'::text AS "source_table",
    date_trunc('month', "revenue_date")::date AS "month_start",
    "div_id"::text AS "division_key",
    "revenue_type"::text AS "revenue_type",
    "revenue_coll_type"::text AS "collection_type",
    count(*)::bigint AS "transaction_count",
    sum("amount")::numeric AS "amount_total"
FROM "pms_extract_2010_2023"."fact_revenue_current"
GROUP BY 1,2,3,4,5

UNION ALL

SELECT
    'trevenue'::text AS "source_table",
    date_trunc('month', "revenuedate")::date AS "month_start",
    "divisionid"::text AS "division_key",
    "xpayment"::text AS "revenue_type",
    "xcollection"::text AS "collection_type",
    count(*)::bigint AS "transaction_count",
    sum("amount")::numeric AS "amount_total"
FROM "pms_extract_2010_2023"."fact_revenue_legacy"
GROUP BY 1,2,3,4,5;

DROP TABLE IF EXISTS "pms_extract_2010_2023"."model_billing_monthly_by_source";
CREATE TABLE "pms_extract_2010_2023"."model_billing_monthly_by_source" AS
SELECT
    'monthly_final_bills'::text AS "source_table",
    date_trunc('month', COALESCE("bill_date", "bill_creation_date", "periodfrom"::date))::date AS "month_start",
    "tenancy_id"::text AS "tenancy_key",
    "unit_id"::text AS "unit_key",
    count(*)::bigint AS "bill_count",
    sum(COALESCE("total_head_amount", "amount", 0))::numeric AS "head_amount",
    sum(COALESCE("total_tax_amount", 0))::numeric AS "tax_amount",
    sum(COALESCE("final_amount", 0))::numeric AS "final_amount"
FROM "pms_extract_2010_2023"."fact_monthly_bills"
GROUP BY 1,2,3,4

UNION ALL

SELECT
    'tgeneralbill'::text AS "source_table",
    date_trunc('month', COALESCE("billdate"::date, "bill_creation_date", "periodfrom"::date))::date AS "month_start",
    COALESCE("tenantid"::text, "customerCode"::text, "customerid"::text) AS "tenancy_key",
    NULL::text AS "unit_key",
    count(*)::bigint AS "bill_count",
    sum(COALESCE("total_head_amount", "bill_amount", "amount", 0))::numeric AS "head_amount",
    sum(COALESCE("total_tax_amount", "tax_amount", 0))::numeric AS "tax_amount",
    sum(COALESCE("final_amount", 0))::numeric AS "final_amount"
FROM "pms_extract_2010_2023"."fact_legacy_general_bills"
GROUP BY 1,2,3,4;

DROP TABLE IF EXISTS "pms_extract_2010_2023"."model_payment_monthly_by_source";
CREATE TABLE "pms_extract_2010_2023"."model_payment_monthly_by_source" AS
SELECT
    'cash_payment_history'::text AS "source_table",
    date_trunc('month', COALESCE("payment_date", "transaction_date"::date))::date AS "month_start",
    "bill_code"::text AS "bill_key",
    count(*)::bigint AS "payment_count",
    sum(COALESCE("amount", 0))::numeric AS "paid_amount",
    sum(COALESCE("interest_amount", 0))::numeric AS "interest_amount"
FROM "pms_extract_2010_2023"."fact_payment_current"
GROUP BY 1,2,3

UNION ALL

SELECT
    'tpaymentmarking'::text AS "source_table",
    date_trunc('month', COALESCE("receiveddate"::date, "billdate"::date))::date AS "month_start",
    COALESCE("billnumber"::text, "generalbillid"::text) AS "bill_key",
    count(*)::bigint AS "payment_count",
    sum(COALESCE("amount", 0))::numeric AS "paid_amount",
    sum(COALESCE("interest_amount", 0))::numeric AS "interest_amount"
FROM "pms_extract_2010_2023"."fact_payment_legacy"
GROUP BY 1,2,3;

DROP TABLE IF EXISTS "pms_extract_2010_2023"."model_land_value_observations";
CREATE TABLE "pms_extract_2010_2023"."model_land_value_observations" AS
SELECT
    "plot_id"::text AS "plot_id",
    "from_date"::date AS "observation_date",
    'FAIR_MARKET_VALUE'::text AS "target_name",
    "fair_mkt_value"::numeric AS "target_value",
    'plot_fair_mkt_value'::text AS "source_table"
FROM "pms_extract_2010_2023"."fact_plot_fair_market_value"
WHERE "fair_mkt_value" IS NOT NULL

UNION ALL

SELECT
    "plot_id"::text,
    "from_date",
    'READY_RECKONER_LAND_VALUE',
    "rr_land_value"::numeric,
    'plot_rr_land_value'
FROM "pms_extract_2010_2023"."fact_plot_rr_land_value"
WHERE "rr_land_value" IS NOT NULL

UNION ALL

SELECT
    "plot_id"::text,
    "from_date",
    'SOR_MARKET_VALUE',
    "sor_mkt_value"::numeric,
    'plot_sor_market_value'
FROM "pms_extract_2010_2023"."fact_plot_sor_market_value"
WHERE "sor_mkt_value" IS NOT NULL;

-- Safe narrative source for later embeddings.
-- Exact numbers/dates must still be retrieved from their fact tables.
DROP TABLE IF EXISTS "pms_extract_2010_2023"."embedding_source_text";
CREATE TABLE "pms_extract_2010_2023"."embedding_source_text" AS
SELECT
    'plot'::text AS "entity_type",
    "plot_id"::text AS "entity_id",
    'plot'::text AS "source_table",
    "plot_id"::text AS "source_record_id",
    NULL::date AS "event_date",
    concat_ws(' | ', NULLIF(btrim("plot_desc"), ''), NULLIF(btrim("remarks"), ''), NULLIF(btrim("reservation"), '')) AS "text_content"
FROM "pms_extract_2010_2023"."dim_plot"
WHERE length(concat_ws(' ', "plot_desc", "remarks", "reservation")) >= 20

UNION ALL

SELECT
    'revenue', "revenue_no"::text, 'cash_revenue_data', "revenue_no"::text,
    "revenue_date",
    btrim("remark")
FROM "pms_extract_2010_2023"."fact_revenue_current"
WHERE "remark" IS NOT NULL AND length(btrim("remark")) >= 20

UNION ALL

SELECT
    'bill', "bill_id"::text, 'bills', "bill_id"::text,
    "bill_date",
    btrim("remarks")
FROM "pms_extract_2010_2023"."fact_bills_simple"
WHERE "remarks" IS NOT NULL AND length(btrim("remarks")) >= 20

UNION ALL

SELECT
    'bill', "bill_id"::text, 'monthly_final_bills', "bill_id"::text,
    COALESCE("bill_date", "bill_creation_date"),
    btrim("bill_remark")
FROM "pms_extract_2010_2023"."fact_monthly_bills"
WHERE "bill_remark" IS NOT NULL AND length(btrim("bill_remark")) >= 20

UNION ALL

SELECT
    'additional_rent', "additional_rent_id"::text, 'additional_rent', "additional_rent_id"::text,
    "commencement_date",
    btrim("description")
FROM "pms_extract_2010_2023"."fact_additional_rent"
WHERE "description" IS NOT NULL AND length(btrim("description")) >= 20

UNION ALL

SELECT
    'inspection', "inspection_rpt_id"::text, 'inspection_rpt', "inspection_rpt_id"::text,
    COALESCE("inspection_date", "observation_date"),
    concat_ws(' | ',
        NULLIF(btrim("desc_of_struct"), ''),
        NULLIF(btrim("rmk_major_1"), ''), NULLIF(btrim("rmk_major_2"), ''),
        NULLIF(btrim("rmk_major_3"), ''), NULLIF(btrim("rmk_minor_1"), ''),
        NULLIF(btrim("rmk_minor_2"), ''), NULLIF(btrim("rmk_minor_3"), '')
    )
FROM "pms_extract_2010_2023"."fact_inspection"
WHERE length(concat_ws(' ', "desc_of_struct", "rmk_major_1", "rmk_major_2", "rmk_major_3",
                           "rmk_minor_1", "rmk_minor_2", "rmk_minor_3")) >= 20

UNION ALL

SELECT
    'breach', "breach_rpt_id"::text, 'breach_rpt', "breach_rpt_id"::text,
    COALESCE("breach_rpt_date", "breach_start_date"),
    concat_ws(' | ',
        NULLIF(btrim("nature_of_breach"), ''),
        NULLIF(btrim("breach_desc"), ''),
        NULLIF(btrim("breach_description"), ''),
        NULLIF(btrim("desc_of_breach"), '')
    )
FROM "pms_extract_2010_2023"."fact_breach"
WHERE length(concat_ws(' ', "nature_of_breach", "breach_desc",
                           "breach_description", "desc_of_breach")) >= 20

UNION ALL

SELECT
    'mrtp_notice', "mrtp_notice_id"::text, 'legal_mrtp_notice', "mrtp_notice_id"::text,
    COALESCE("mrtp_notice_date", "notice_date", "inspection_date"),
    concat_ws(' | ',
        NULLIF(btrim("subject"), ''),
        NULLIF(btrim("premises_desc"), ''),
        NULLIF(btrim("devp_nature"), ''),
        NULLIF(btrim("remark"), ''),
        NULLIF(btrim("additional_info"), ''),
        NULLIF(btrim("action_taken"), '')
    )
FROM "pms_extract_2010_2023"."fact_mrtp_notice"
WHERE length(concat_ws(' ', "subject", "premises_desc", "devp_nature",
                           "remark", "additional_info", "action_taken")) >= 20

UNION ALL

SELECT
    'legal_suit', "suit_case_id"::text, 'legal_suit_cases', "suit_case_id"::text,
    "suit_date",
    concat_ws(' | ',
        NULLIF(btrim("remarks"), ''),
        NULLIF(btrim("litigation_ground"), ''),
        NULLIF(btrim("termination_notice"), '')
    )
FROM "pms_extract_2010_2023"."fact_legal_suit"
WHERE length(concat_ws(' ', "remarks", "litigation_ground", "termination_notice")) >= 20;

DELETE FROM "pms_extract_2010_2023"."embedding_source_text"
WHERE "text_content" IS NULL
   OR length(btrim("text_content")) < 20;

-- These indexes accelerate local QA and subsequent ETL.
CREATE INDEX IF NOT EXISTS "idx_extract_revenue_current_date"
    ON "pms_extract_2010_2023"."fact_revenue_current" ("revenue_date");
CREATE INDEX IF NOT EXISTS "idx_extract_revenue_legacy_date"
    ON "pms_extract_2010_2023"."fact_revenue_legacy" ("revenuedate");
CREATE INDEX IF NOT EXISTS "idx_extract_monthly_bill_date"
    ON "pms_extract_2010_2023"."fact_monthly_bills" ("bill_date");
CREATE INDEX IF NOT EXISTS "idx_extract_payment_current_date"
    ON "pms_extract_2010_2023"."fact_payment_current" ("transaction_date");
CREATE INDEX IF NOT EXISTS "idx_extract_plot_id"
    ON "pms_extract_2010_2023"."dim_plot" ("plot_id");
CREATE INDEX IF NOT EXISTS "idx_extract_bridge_tenancy"
    ON "pms_extract_2010_2023"."bridge_letout_tenancy_plot" ("tenancy_id");
CREATE INDEX IF NOT EXISTS "idx_extract_embedding_entity"
    ON "pms_extract_2010_2023"."embedding_source_text" ("entity_type", "entity_id");

COMMIT;
