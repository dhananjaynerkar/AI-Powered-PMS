SELECT canonical_entity_id, tenancy_id, customer_code, agreement_number, tenancy_type,
       tenant_type, description, purpose, allotment_basis, bill_periodicity, rate,
       percent_rate_revision, amount_rate_revision, security_deposit_amount,
       total_security_deposit, agreement_date, duration_from, duration_to, renewal_date,
       is_renewable, status, remarks, source_schema, source_table, source_record_id,
       source_refreshed_at
FROM pms_app.agreement_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
ORDER BY canonical_entity_id
LIMIT :limit
