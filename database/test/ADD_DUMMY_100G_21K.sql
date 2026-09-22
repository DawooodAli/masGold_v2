-- masGold v2 TEST ONLY
-- Adds +100.000 g to 21K old-gold balance using the EXISTING movement system.
-- Safe semantic type: ADJUSTMENT_IN.
--
-- Delete/reverse this test movement later; do not treat it as real customer gold.

INSERT INTO old_gold_movements (
    movement_type_id,
    karat_id,
    weight_delta_grams,
    reference_type,
    reference_code,
    notes,
    occurred_at
)
SELECT
    mt.id,
    k.id,
    100.000,
    'TEST',
    'DUMMY-OLD-GOLD-21K-100G',
    'رصيد كسر تجريبي 100 جم لاختبار محولة من الكسر',
    NOW()
FROM old_gold_movement_types mt
JOIN karats k
    ON k.karat_value = 21
WHERE
    mt.code = 'ADJUSTMENT_IN'
    AND mt.expected_direction IN ('IN', 'EITHER')
    AND mt.is_active = 1
LIMIT 1;


-- Verify balance after insert.
SELECT
    k.id AS karat_id,
    k.karat_value,
    COALESCE(
        SUM(og.weight_delta_grams),
        0
    ) AS old_gold_balance_grams
FROM karats k
LEFT JOIN old_gold_movements og
    ON og.karat_id = k.id
GROUP BY
    k.id,
    k.karat_value
ORDER BY
    k.karat_value;
