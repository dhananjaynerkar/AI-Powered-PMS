SELECT canonical_entity_id, bill_code, amount, transaction_date, payment_date,
       interest_amount, head_amount, head_balance_amount, settlement_type, is_final,
       tds_amount, source_schema, source_table, source_record_id, source_refreshed_at
FROM pms_app.payment_360
WHERE (
  CAST(:canonical_entity_id AS text) IS NULL
  OR canonical_entity_id = CAST(:canonical_entity_id AS text)
)
  AND (
    CAST(:as_of_date AS date) IS NULL
    OR payment_date::date <= CAST(:as_of_date AS date)
  )
ORDER BY payment_date DESC NULLS LAST, canonical_entity_id
LIMIT :limit
