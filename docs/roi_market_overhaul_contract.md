# ROI + Market Overhaul — Architecture Contract

This document is the source of truth for the Simpsonville Analyzer Market + ROI Overhaul.
No agent should add schema fields, API shapes, enum values, formulas, or frontend state names
that are not defined here.

---

## Non-Negotiables

- The Decision Matrix remains the evidence and condition source of truth.
- ROI flows must NEVER write to `decision_matrix_rows`, `decision_matrix_options`,
  `decision_matrix_scenarios`, `decision_matrices`, or `property_decision_state`.
- Existing `POST /report/from-tier` must continue to work unchanged.
- `ATTOM_API_KEY` must NOT be required for app startup.
  Missing or failed ATTOM falls back to cached files → hardcoded defaults. Never crashes.
- Backend ROI math is authoritative. Frontend may preview but must call backend for saved state.
- Suggested listing price is editable and must not be presented as a guaranteed sale price.

---

## Enums

```
market_snapshot_source:
  attom_live | attom_cached | manual

market_snapshot_confidence:
  high | medium | low | stale

roi_bucket:
  must_do | should_do | nice_to_do | exclude
```

---

## ROI Field Flow

```
decision_matrix_rows.minimum_tier       → roi_bucket_suggested  (read-only; from matrix)
roi_item_overrides.roi_bucket_override  → roi_bucket_override    (nullable)
roi_bucket_final = roi_bucket_override ?? roi_bucket_suggested

scenario eligibility logic              → include_suggested
roi_item_overrides.include_override     → include_override        (nullable)
include_final = include_override ?? include_suggested
```

ROI engine reads matrix tables; never writes to them.

---

## Database Schema

> 4 new tables only. No changes to any existing table.

### `property_market_snapshots`

```sql
CREATE TABLE IF NOT EXISTS property_market_snapshots (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id               text NOT NULL,
  source                    text NOT NULL,
  as_is_market_estimate     numeric NOT NULL,
  improved_listing_ceiling  numeric NOT NULL,
  attom_avm                 numeric,
  comp_data                 jsonb,
  raw_attom_response        jsonb,
  freshness_label           text,
  confidence_label          text,
  created_at                timestamptz NOT NULL DEFAULT now(),
  expires_at                timestamptz
);

CREATE INDEX IF NOT EXISTS idx_pms_property_id
  ON property_market_snapshots(property_id);
CREATE INDEX IF NOT EXISTS idx_pms_property_created
  ON property_market_snapshots(property_id, created_at DESC);
```

### `roi_seller_inputs`

One row per property. Upserted on change.

```sql
CREATE TABLE IF NOT EXISTS roi_seller_inputs (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id         text UNIQUE NOT NULL,
  listing_price       numeric,
  mortgage_payoff     numeric NOT NULL DEFAULT 0,
  commission_pct      numeric NOT NULL DEFAULT 5.5,
  closing_costs       numeric NOT NULL DEFAULT 0,
  seller_credits      numeric NOT NULL DEFAULT 0,
  other_seller_costs  numeric NOT NULL DEFAULT 0,
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rsi_property_id
  ON roi_seller_inputs(property_id);
```

`commission_pct` is a percentage value (5.5 = 5.5%, not 0.055).

### `roi_item_overrides`

```sql
CREATE TABLE IF NOT EXISTS roi_item_overrides (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id          text NOT NULL,
  matrix_row_id        uuid NOT NULL REFERENCES decision_matrix_rows(id) ON DELETE CASCADE,
  roi_bucket_override  text,
  include_override     boolean,
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (property_id, matrix_row_id)
);

CREATE INDEX IF NOT EXISTS idx_rio_property_id
  ON roi_item_overrides(property_id);
```

Null override fields mean "use system suggestion". Never write defaults just to mirror suggestions.

### `roi_report_snapshots`

Saved snapshots are immutable history — never silently recalculated.

```sql
CREATE TABLE IF NOT EXISTS roi_report_snapshots (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  property_id              text NOT NULL,
  scenario_name            text NOT NULL,
  seller_inputs_snapshot   jsonb NOT NULL,
  item_overrides_snapshot  jsonb NOT NULL DEFAULT '[]',
  result_snapshot          jsonb NOT NULL,
  created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rrs_property_id
  ON roi_report_snapshots(property_id);
CREATE INDEX IF NOT EXISTS idx_rrs_property_created
  ON roi_report_snapshots(property_id, created_at DESC);
```

---

## API Contract

All new endpoints are property-scoped even though the current default is `130_kingfisher`.

### `GET /properties/{property_id}/market-summary`

Returns current normalized market snapshot.

```json
{
  "property_id": "130_kingfisher",
  "snapshot": {
    "as_is_market_estimate": 276810.0,
    "improved_listing_ceiling": 305000.0,
    "attom_avm": 276810.0,
    "freshness_label": "Cached (file)",
    "confidence_label": "Medium",
    "source": "attom_cached"
  },
  "listing_range": { "low": 276810.0, "high": 305000.0, "mid": 290905.0 },
  "comps": []
}
```

### `POST /properties/{property_id}/market-refresh`

Triggers refresh. Must not fail if `ATTOM_API_KEY` is missing.

Body: `{ "force_live": false }`

### `GET /properties/{property_id}/seller-inputs`

Returns current seller inputs or defaults.

```json
{ "seller_inputs": { "listing_price": null, "mortgage_payoff": 0, "commission_pct": 5.5,
                     "closing_costs": 0, "seller_credits": 0, "other_seller_costs": 0 } }
```

### `PATCH /properties/{property_id}/seller-inputs`

Upserts seller inputs.

### `GET /properties/{property_id}/roi-overrides`

Returns all item overrides for property.

### `PATCH /properties/{property_id}/roi-overrides/{row_id}`

Updates a single item override. Allowed fields: `roi_bucket_override`, `include_override`.

### `POST /properties/{property_id}/roi-compute`

Runs deterministic scenario math.

Body: `{ "scenario": "full-recommended" }`

Allowed `scenario` values: `must-do-only | highest-roi | full-recommended | custom`

Returns `RoiResult` as JSON (see ROI Engine section below).

### `POST /properties/{property_id}/roi-snapshots`

Saves current scenario result as immutable snapshot.

Body: `{ "scenario": "full-recommended" }`

---

## ROI Engine

### Formulas

```
commission_amount  = listing_price × (commission_pct / 100)
net_proceeds       = listing_price − selected_work_cost − mortgage_payoff
                     − commission_amount − closing_costs − seller_credits − other_seller_costs

max_supported_lift = improved_listing_ceiling − as_is_market_estimate
value_lift_capped  = min(sum of estimated_value_add for included items, max_supported_lift)

roi_pct            = ((value_lift_capped − total_cost_midpoint) / total_cost_midpoint) × 100
                     Returns None if total_cost_midpoint == 0
```

### Scenario Filters

| Scenario | Logic |
|---|---|
| `must-do-only` | Items where `roi_bucket_final == "must_do"` AND `include_final == True` |
| `highest-roi` | All must_do + should_do items where `include_final == True` |
| `full-recommended` | All items where `include_final == True` |
| `custom` | All items where `include_final == True` (user has set overrides manually) |

### Value-Add Bridge (temporary)

`decision_matrix_options.roi_quality` is TEXT. Until a numeric column is added:

```python
_ROI_QUALITY_MULTIPLIER = {"excellent": 1.5, "good": 1.25, "fair": 1.0, "poor": 0.5}
estimated_value_add = cost_midpoint × multiplier(roi_quality)
```

### Default Seller Inputs (when no DB row exists)

```
listing_price       = improved_listing_ceiling (from snapshot)
mortgage_payoff     = 0
commission_pct      = 5.5
closing_costs       = 3500
seller_credits      = 0
other_seller_costs  = 0
```

---

## ATTOM Fallback Chain

1. Unexpired row in `property_market_snapshots` DB table (checked first if `force_live=False`)
2. Live ATTOM API (if `ATTOM_API_KEY` env var is set and non-empty)
3. Local `attom_assessment.json` / `attom_sales.json` files
4. Hardcoded defaults: `as_is_market_estimate=276810`, `improved_listing_ceiling=305000`

Never raises. Always returns a snapshot dict.

---

## Frontend State Contract

Frontend state names (JS variables):
- `spCurrentScenario` — active scenario tab
- `spLastResult` — last RoiResult from /roi-compute
- `spInitialized` — bool, set on first ROI tab visit
- `pdLoaded` — bool, set on first Property Data tab visit

Frontend functions:
- `spSetScenario(scenario)` — switches tab + recomputes
- `spLoadSellerInputs()` — GET /seller-inputs, populates form
- `spDebouncedSave()` — 600ms debounce → spSaveInputs()
- `spSaveInputs()` — PATCH /seller-inputs, then spCompute()
- `spLoadMarketSummary()` — GET /market-summary, updates market bar
- `spCompute()` — POST /roi-compute, renders result
- `spRenderResult(result)` — updates summary cards + items table
- `spToggleInclude(rowId, checked)` — PATCH override, recomputes
- `spShowBucketOverride(rowId, current)` — prompt-based bucket override
- `spSaveSnapshot()` — POST /roi-snapshots
- `spInit()` — called once on first ROI tab visit
- `pdLoadMarketSummary()` — GET /market-summary, updates Property Data tab
- `pdRefreshMarket()` — POST /market-refresh + re-renders

Matrix labels (`component`, `zone`, `minimum_tier`) come from `RoiItem` in API response.
They are display-only and never editable in the UI.

---

## Test Requirements

- `tests/test_roi_engine.py` — pure Python golden math, no DB or API
- `tests/test_attom_fallback.py` — fallback chain coverage
- `tests/test_matrix_preservation.py` — verify `compute_scenario()` never mutates input rows

All tests must pass via `pytest` with no live DB or ATTOM API required.
