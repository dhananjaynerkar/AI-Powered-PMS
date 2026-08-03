SELECT canonical_entity_id, plot_code, rr_no, location, area, plot_description,
       status, is_verified, is_active, is_vacant, owner, department_name, remarks,
       source_schema, source_table, source_record_id, source_refreshed_at
FROM pms_app.plot_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
ORDER BY canonical_entity_id
LIMIT :limit
