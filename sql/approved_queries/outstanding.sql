SELECT canonical_entity_id, customer_code, current_plot_number, current_plot_area_sqm,
       current_contractual_rent, billed_arrears_current, outstanding_arrears_current,
       penalties_current, taxes_current, amount_to_be_recovered_current, status,
       source_schema, source_table, source_record_id, source_refreshed_at
FROM pms_app.outstanding_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
ORDER BY canonical_entity_id
LIMIT :limit
