SELECT canonical_entity_id, tenant_name, customer_code, registration_timestamp,
       registration_type, organization_type_id, status, source_schema, source_table,
       source_record_id, source_refreshed_at
FROM pms_app.tenant_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
ORDER BY canonical_entity_id
LIMIT :limit
