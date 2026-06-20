-- ROI + Market Data Overhaul — new tables only
-- No existing tables or columns are altered.
-- Run this in the Supabase SQL Editor (or via psql).

-- ---------------------------------------------------------------------------
-- property_market_snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_market_snapshots (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id              text NOT NULL,
    source                   text NOT NULL,           -- attom_live | attom_cached | manual
    as_is_market_estimate    numeric NOT NULL,         -- primary value used in ROI math
    improved_listing_ceiling numeric NOT NULL,         -- ARV ceiling from comps
    attom_avm                numeric,                  -- raw ATTOM mktttlvalue if available
    comp_data                jsonb,                    -- array of Comp objects
    raw_attom_response       jsonb,                    -- full ATTOM API response; null if file/manual
    freshness_label          text,
    confidence_label         text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    expires_at               timestamptz               -- null = no expiry; +30d for live fetches
);
CREATE INDEX IF NOT EXISTS idx_pms_property_created
    ON property_market_snapshots (property_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- roi_seller_inputs  (one row per property; upsert on change)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roi_seller_inputs (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id        text UNIQUE NOT NULL,
    listing_price      numeric,                        -- null → use improved_listing_ceiling as default
    mortgage_payoff    numeric NOT NULL DEFAULT 0,
    commission_pct     numeric NOT NULL DEFAULT 5.5,   -- 5.5 means 5.5%, NOT 0.055
    closing_costs      numeric NOT NULL DEFAULT 0,
    seller_credits     numeric NOT NULL DEFAULT 0,
    other_seller_costs numeric NOT NULL DEFAULT 0,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- roi_item_overrides  (one row per property+matrix_row pair)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roi_item_overrides (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         text NOT NULL,
    matrix_row_id       uuid NOT NULL
                            REFERENCES decision_matrix_rows(id) ON DELETE CASCADE,
    roi_bucket_override text,     -- null = no override; values: must_do|should_do|nice_to_do|exclude
    include_override    boolean,  -- null = no override
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (property_id, matrix_row_id)
);
CREATE INDEX IF NOT EXISTS idx_rio_property
    ON roi_item_overrides (property_id);

-- ---------------------------------------------------------------------------
-- roi_report_snapshots  (append-only history)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roi_report_snapshots (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id             text NOT NULL,
    scenario_name           text NOT NULL,             -- must-do-only|highest-roi|full-recommended|custom
    seller_inputs_snapshot  jsonb NOT NULL,
    item_overrides_snapshot jsonb NOT NULL DEFAULT '[]',
    result_snapshot         jsonb NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rrs_property_created
    ON roi_report_snapshots (property_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Verification (run manually before and after to confirm no matrix table drift)
-- SELECT 'decision_matrices'       AS tbl, count(*) FROM decision_matrices;
-- SELECT 'decision_matrix_rows'    AS tbl, count(*) FROM decision_matrix_rows;
-- SELECT 'decision_matrix_options' AS tbl, count(*) FROM decision_matrix_options;
-- SELECT 'decision_matrix_scenarios' AS tbl, count(*) FROM decision_matrix_scenarios;
-- ---------------------------------------------------------------------------
