SELECT canonical_entity_id, tenancy_id, customer_code, agreement_number, billable,
       billable_as, valid_from, valid_to, status, source_schema, source_table,
       source_record_id, source_refreshed_at
FROM pms_app.tenancy_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
ORDER BY canonical_entity_id
LIMIT :limit
