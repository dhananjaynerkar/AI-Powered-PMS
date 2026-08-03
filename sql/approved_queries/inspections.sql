SELECT canonical_entity_id, inspection_date, status, is_verified, observation_date,
       customer_code, tenancy_type, is_vacant_plot, plot_id, tenure, renewal_clause,
       structure_description, built_up_area, major_remark_1, minor_remark_1,
       source_schema, source_table, source_record_id, source_refreshed_at
FROM pms_app.inspection_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
  AND (
    CAST(:as_of_date AS date) IS NULL
    OR inspection_date::date <= CAST(:as_of_date AS date)
  )
ORDER BY inspection_date DESC NULLS LAST, canonical_entity_id
LIMIT :limit
