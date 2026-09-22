USE masgold_v2;

-- ============================================================
-- masGold v2
-- Migration 010: Core Integrity Corrections
--
-- 1. JRD uses actual stored purity.
-- 2. Active gold price cannot mismatch quote/karat/activation.
-- 3. Only one default printer profile per document type.
-- 4. Only one ORIGINAL_FINALIZED document per invoice.
-- ============================================================


-- ============================================================
-- 1. GOLD PRICE INTEGRITY
--
-- active_gold_prices currently stores:
-- karat_id
-- quote_id
-- activation_id
--
-- We add a composite relationship so those three values
-- must actually belong together.
-- ============================================================

ALTER TABLE gold_price_activations

    ADD UNIQUE KEY uq_gold_activation_identity (
        id,
        quote_id,
        karat_id
    );


ALTER TABLE active_gold_prices

    ADD CONSTRAINT fk_active_gold_activation_identity

    FOREIGN KEY (
        activation_id,
        quote_id,
        karat_id
    )

    REFERENCES gold_price_activations (
        id,
        quote_id,
        karat_id
    );


-- ============================================================
-- 2. PRINTER DEFAULT INTEGRITY
--
-- Only one ACTIVE DEFAULT printer profile should exist
-- for each profile type.
--
-- MySQL UNIQUE indexes allow multiple NULL values.
-- ============================================================

ALTER TABLE printer_profiles

    ADD COLUMN default_profile_type VARCHAR(20)

    GENERATED ALWAYS AS (

        CASE

            WHEN is_default = TRUE
             AND is_active = TRUE

            THEN profile_type

            ELSE NULL

        END

    ) STORED,

    ADD UNIQUE KEY uq_default_printer_per_type (
        default_profile_type
    );


-- ============================================================
-- 3. ORIGINAL INVOICE DOCUMENT INTEGRITY
--
-- An invoice gets ONE immutable original finalized document.
--
-- Reprints can still be unlimited.
-- ============================================================

ALTER TABLE invoice_documents

    ADD COLUMN original_invoice_key BIGINT UNSIGNED

    GENERATED ALWAYS AS (

        CASE

            WHEN document_type = 'ORIGINAL_FINALIZED'

            THEN invoice_id

            ELSE NULL

        END

    ) STORED,

    ADD UNIQUE KEY uq_original_invoice_document (
        original_invoice_key
    );


-- ============================================================
-- 4. REBUILD JRD GOLD COMPONENT VIEW
--
-- Correct conversion:
--
-- Equivalent base gold =
--
--     physical grams
--     × item purity
--     ÷ base-karat purity
--
-- Current base = 21K = 0.875
--
-- Therefore standard results remain:
--
-- 21K = 1.000 × grams
-- 18K = 0.750 / 0.875
-- 24K = 1.000 / 0.875
--
-- But this also works correctly if unusual purities
-- are introduced later.
-- ============================================================

CREATE OR REPLACE VIEW vw_jrd_current_gold_components AS

SELECT
    'NEW_STOCK' AS component_type,

    k.id AS karat_id,
    k.karat_value,

    CAST(
        COALESCE(
            SUM(
                CASE

                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'NEW_STOCK'

                    THEN i.weight_grams

                    ELSE 0

                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS physical_grams,

    CAST(

        COALESCE(
            SUM(
                CASE

                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'NEW_STOCK'

                    THEN i.weight_grams

                    ELSE 0

                END
            ),
            0
        )

        * k.purity

        /
        (
            SELECT bk.purity
            FROM jrd_settings js
            JOIN karats bk
                ON bk.id = js.base_karat_id
            WHERE js.id = 1
        )

        AS DECIMAL(18,3)

    ) AS equivalent_21_grams

FROM karats k

LEFT JOIN inventory_items i
    ON i.karat_id = k.id

GROUP BY
    k.id,
    k.karat_value,
    k.purity


UNION ALL


SELECT
    'OLD_STOCK' AS component_type,

    k.id AS karat_id,
    k.karat_value,

    CAST(
        COALESCE(
            SUM(
                CASE

                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'OLD_STOCK'

                    THEN i.weight_grams

                    ELSE 0

                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS physical_grams,

    CAST(

        COALESCE(
            SUM(
                CASE

                    WHEN i.inventory_status = 'IN_STOCK'
                     AND i.stock_classification = 'OLD_STOCK'

                    THEN i.weight_grams

                    ELSE 0

                END
            ),
            0
        )

        * k.purity

        /
        (
            SELECT bk.purity
            FROM jrd_settings js
            JOIN karats bk
                ON bk.id = js.base_karat_id
            WHERE js.id = 1
        )

        AS DECIMAL(18,3)

    ) AS equivalent_21_grams

FROM karats k

LEFT JOIN inventory_items i
    ON i.karat_id = k.id

GROUP BY
    k.id,
    k.karat_value,
    k.purity


UNION ALL


SELECT
    'OLD_GOLD' AS component_type,

    k.id AS karat_id,
    k.karat_value,

    CAST(
        COALESCE(
            og.balance_grams,
            0
        )
        AS DECIMAL(18,3)
    ) AS physical_grams,

    CAST(

        COALESCE(
            og.balance_grams,
            0
        )

        * k.purity

        /
        (
            SELECT bk.purity
            FROM jrd_settings js
            JOIN karats bk
                ON bk.id = js.base_karat_id
            WHERE js.id = 1
        )

        AS DECIMAL(18,3)

    ) AS equivalent_21_grams

FROM karats k

LEFT JOIN vw_old_gold_balances og
    ON og.karat_id = k.id;


-- ------------------------------------------------------------
-- Rebuild totals because underlying view changed.
-- ------------------------------------------------------------

CREATE OR REPLACE VIEW vw_jrd_current_gold_totals AS

SELECT

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN component_type = 'NEW_STOCK'
                    THEN equivalent_21_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS new_stock_equivalent_21,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN component_type = 'OLD_STOCK'
                    THEN equivalent_21_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS old_stock_equivalent_21,

    CAST(
        COALESCE(
            SUM(
                CASE
                    WHEN component_type = 'OLD_GOLD'
                    THEN equivalent_21_grams
                    ELSE 0
                END
            ),
            0
        )
        AS DECIMAL(18,3)
    ) AS old_gold_equivalent_21,

    CAST(
        COALESCE(
            SUM(equivalent_21_grams),
            0
        )
        AS DECIMAL(18,3)
    ) AS total_gold_equivalent_21

FROM vw_jrd_current_gold_components;


-- ============================================================
-- REGISTER MIGRATION
-- ============================================================

INSERT INTO schema_migrations (
    version,
    name
)
VALUES (
    10,
    'Core integrity: JRD purity, price linkage and printing uniqueness'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);