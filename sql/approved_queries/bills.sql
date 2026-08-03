SELECT canonical_entity_id, bill_code, bill_creation_date, total_head_amount,
       total_tax_amount, final_amount, bill_status, tenancy_id, bill_year, bill_month,
       bill_date, due_date, amount, source_schema, source_table, source_record_id,
       source_refreshed_at
FROM pms_app.bill_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
  AND (
    CAST(:as_of_date AS date) IS NULL
    OR bill_date::date <= CAST(:as_of_date AS date)
  )
ORDER BY bill_date DESC NULLS LAST, canonical_entity_id
LIMIT :limit
