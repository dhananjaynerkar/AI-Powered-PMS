SELECT canonical_entity_id, suit_case_number, suit_number, plot_number, suit_year,
       suit_date, court_id, remarks, tenancy_id, withdrawn, suit_reference_number,
       filed_by, status, current_stage, litigation_ground, customer_code,
       next_hearing_date, previous_hearing_date, source_schema, source_table,
       source_record_id, source_refreshed_at
FROM pms_app.legal_case_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
  AND (
    CAST(:as_of_date AS date) IS NULL
    OR suit_date::date <= CAST(:as_of_date AS date)
  )
ORDER BY suit_date DESC NULLS LAST, canonical_entity_id
LIMIT :limit
