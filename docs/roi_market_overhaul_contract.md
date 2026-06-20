# ROI + Market Overhaul Contract

This contract is the source of truth for the multi-agent overhaul. No agent should add schema fields, API fields, enum values, formulas, or frontend state names that are not defined here.

## Non-Negotiables

- The Decision Matrix remains the evidence and condition source of truth.
- ROI flows must not mutate `decision_matrix_rows`, `decision_matrix_options`, `decision_matrix_scenarios`, matrix labels, evidence, selected options, costs, forecasted spend, or actual spend.
- ROI labels are separate financial-planning labels and must be stored outside Decision Matrix tables.
- Existing `/report/from-tier` must continue to work.
- ATTOM is optional at app startup; missing/failed ATTOM must fall back to cached/manual data.
- Backend ROI math is authoritative. Frontend may preview but must use backend scenario responses for saved state.
- Suggested listing price is editable and must not be presented as a guaranteed sale price.

## Database Schema

### `property_market_snapshots`

Stores normalized market/AVM snapshots. Multiple snapshots may exist; only one current snapshot should be used per property.

```sql
create table if not exists property_market_snapshots (
  id uuid primary key default gen_random_uuid(),
  property_id text not null,
  source text not null,
  source_payload jsonb,
  avm_value numeric,
  avm_low numeric,
  avm_high numeric,
  avm_confidence numeric,
  avm_confidence_label text,
  value_per_sqft numeric,
  beds numeric,
  baths numeric,
  sqft numeric,
  year_built integer,
  lot_size numeric,
  last_sale_date date,
  last_sale_price numeric,
  comp_count integer,
  comp_median_sale_price numeric,
  comp_median_ppsf numeric,
  as_is_estimate numeric,
  improved_listing_ceiling numeric,
  freshness_label text,
  pulled_at timestamptz,
  effective_date date,
  is_current boolean not null default false,
  manual_override boolean not null default false,
  created_at timestamptz not null default now()
);
```

Allowed `source` values: `attom`, `cached_attom`, `csv_comp_snapshot`, `manual_fallback`, `unavailable`.

Allowed `freshness_label` values: `fresh`, `stale`, `manual`, `missing`, `unavailable`.

Allowed `avm_confidence_label` values: `strong`, `limited`, `low`, `unknown`.

### `roi_seller_inputs`

Stores seller-specific financial assumptions.

```sql
create table if not exists roi_seller_inputs (
  id uuid primary key default gen_random_uuid(),
  property_id text not null,
  listing_price_override numeric,
  listing_price_source text,
  mortgage_payoff numeric,
  mortgage_payoff_updated_at timestamptz,
  commission_pct numeric,
  closing_cost_pct numeric,
  seller_credits numeric,
  other_seller_costs numeric,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (property_id)
);
```

Allowed `listing_price_source` values: `user_override`, `scenario_suggested`, `market_snapshot`, `manual_fallback`.

### `roi_item_overrides`

Stores ROI-only item overrides tied back to Decision Matrix rows/options.

```sql
create table if not exists roi_item_overrides (
  id uuid primary key default gen_random_uuid(),
  property_id text not null,
  matrix_row_id uuid not null,
  matrix_option_id uuid,
  include_override boolean,
  roi_bucket_override text,
  cost_low_override numeric,
  cost_high_override numeric,
  value_lift_low_override numeric,
  value_lift_high_override numeric,
  confidence_override text,
  override_reason text,
  cost_override_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (property_id, matrix_row_id)
);
```

Nullable override fields mean “use system suggestion.” Do not write defaults just to mirror suggestions.

Allowed `roi_bucket_override` values: `Must Do / Protect Sale`, `Highest ROI`, `Budget Priority`, `Optional`, `Skip`.

Allowed `confidence_override` values: `strong`, `limited`, `low`, `unknown`.

### `roi_report_snapshots`

Stores historical scenario outputs. Saved snapshots are immutable history and must not be silently recalculated.

```sql
create table if not exists roi_report_snapshots (
  id uuid primary key default gen_random_uuid(),
  property_id text not null,
  scenario_name text not null,
  market_snapshot_id uuid,
  scenario_inputs jsonb not null,
  scenario_outputs jsonb not null,
  created_at timestamptz not null default now(),
  created_by text
);
```

Allowed `scenario_name` values: `must_do_only`, `highest_roi_plan`, `full_recommended_plan`, `custom_plan`.

## API Contract

All new endpoints are property-scoped even though the current default property is `130_kingfisher`.

### `GET /properties/{property_id}/market-summary`

Returns the current normalized market snapshot and source status.

```json
{
  "property_id": "130_kingfisher",
  "snapshot": {},
  "status": "fresh",
  "source": "attom",
  "message": null
}
```

`status` values: `fresh`, `stale`, `manual`, `missing`, `unavailable`.

### `POST /properties/{property_id}/market-refresh`

Attempts live ATTOM refresh. Must not fail app startup if `ATTOM_API_KEY` is missing.

```json
{
  "property_id": "130_kingfisher",
  "snapshot": {},
  "status": "fresh",
  "used_fallback": false,
  "message": null
}
```

### `GET /properties/{property_id}/roi/scenario?scenario_name=highest_roi_plan`

Returns authoritative persisted scenario state using saved inputs and item overrides.

### `POST /properties/{property_id}/roi/scenario/preview`

Returns authoritative scenario math for unsaved frontend edits. Must not persist changes.

Request shape:

```json
{
  "scenario_name": "custom_plan",
  "seller_inputs": {},
  "item_overrides": []
}
```

### `PATCH /properties/{property_id}/roi/inputs`

Persists seller financial inputs only.

Allowed fields: `listing_price_override`, `listing_price_source`, `mortgage_payoff`, `commission_pct`, `closing_cost_pct`, `seller_credits`, `other_seller_costs`, `notes`.

### `PATCH /properties/{property_id}/roi/items/{row_id}`

Persists ROI-only item overrides only.

Allowed fields: `include_override`, `roi_bucket_override`, `cost_low_override`, `cost_high_override`, `value_lift_low_override`, `value_lift_high_override`, `confidence_override`, `override_reason`, `cost_override_note`.

### `POST /properties/{property_id}/roi/report-snapshots`

Saves the current scenario output to `roi_report_snapshots`.

### Existing Compatibility

`POST /report/from-tier` must remain compatible and continue generating matrix-tier ROI reports. It may read new market context later, but it must not require new ROI tables to exist for old behavior.

## Scenario Output Shape

All scenario endpoints must return this top-level shape:

```json
{
  "property_id": "130_kingfisher",
  "scenario_name": "highest_roi_plan",
  "market_snapshot_id": null,
  "market_summary": {},
  "seller_inputs": {},
  "summary": {},
  "items": [],
  "proceeds": {},
  "warnings": []
}
```

Each item must include:

```json
{
  "matrix_row_id": "uuid",
  "matrix_option_id": "uuid-or-null",
  "matrix_label": "Must Do",
  "component": "Garage door",
  "zone": "garage",
  "selected_option_key": "repair",
  "include_suggested": true,
  "include_override": null,
  "include_final": true,
  "roi_bucket_suggested": "Must Do / Protect Sale",
  "roi_bucket_override": null,
  "roi_bucket_final": "Must Do / Protect Sale",
  "cost_low": 0,
  "cost_high": 0,
  "expected_cost": 0,
  "value_lift_low": null,
  "value_lift_high": null,
  "expected_value_lift": null,
  "roi_pct_low": null,
  "roi_pct_high": null,
  "expected_roi_pct": null,
  "net_gain_low": null,
  "net_gain_high": null,
  "confidence": "unknown",
  "reason": "",
  "evidence_sources": [],
  "traceability": {}
}
```

`matrix_label` is display-only and derived from existing matrix tier labels.

## Scenario Rules

- `must_do_only`: include `Must Do / Protect Sale` plus forced-on items.
- `highest_roi_plan`: include `Must Do / Protect Sale`, `Highest ROI`, plus forced-on items.
- `full_recommended_plan`: include `Must Do / Protect Sale`, `Highest ROI`, `Budget Priority`, plus forced-on items.
- `custom_plan`: include exactly the current include toggles/overrides from the frontend payload or persisted overrides.

Explicit `include_override` always wins over scenario default inclusion.

## ROI Formulas

### Cost

```text
expected_cost = (cost_low + cost_high) / 2
```

If only one cost exists, use it for low, high, and expected.

### Value Lift

```text
expected_value_lift = (value_lift_low + value_lift_high) / 2
```

If no credible value lift source exists, leave value lift and ROI fields null and set confidence to `unknown` or `low`.

### ROI Percent

```text
roi_pct = ((value_lift - cost) / cost) * 100
net_gain = value_lift - cost
```

### Conservative / Expected / Upside

- Conservative: high cost, low value lift.
- Expected: midpoint cost, midpoint value lift.
- Upside: low cost, high value lift.

### Value-Lift Cap

```text
max_supported_lift = improved_listing_ceiling - as_is_market_estimate
total_claimed_value_lift <= max_supported_lift
```

Apply the cap across all selected items together. If uncapped item lifts exceed the cap, return capped scenario totals and include a warning.

### Net Proceeds

```text
agent_commission = listing_price * commission_pct
closing_costs = listing_price * closing_cost_pct

estimated_net_proceeds =
  listing_price
  - selected_work_cost
  - mortgage_payoff
  - agent_commission
  - closing_costs
  - seller_credits
  - other_seller_costs
```

Return conservative, expected, and upside proceeds.

## Source Priority

### Cost

1. User override in `roi_item_overrides`
2. Selected matrix option cost
3. Matrix row cost
4. Component default rule
5. Unknown

### Value Lift

1. User override in `roi_item_overrides`
2. Component ROI rule
3. Market-adjusted default
4. Unknown

### Listing Price

1. User override in `roi_seller_inputs`
2. Scenario-adjusted suggested listing price
3. ATTOM AVM / comp-supported range
4. Manual fallback

### Market Data

1. Fresh ATTOM snapshot
2. Cached ATTOM snapshot
3. CSV/comp pipeline snapshot
4. Manual fallback
5. Missing/unavailable

## Frontend State Contract

Frontend state names must match backend fields:

- `scenarioName`
- `sellerInputs`
- `itemOverrides`
- `marketSummary`
- `scenarioSummary`
- `scenarioItems`
- `proceeds`
- `warnings`

Frontend controls:

- Scenario selector: `must_do_only`, `highest_roi_plan`, `full_recommended_plan`, `custom_plan`
- Editable seller inputs: listing price, mortgage payoff, commission %, closing cost %, seller credits, other seller costs
- Item controls: include toggle, ROI bucket dropdown, cost override, value-lift override, confidence override, reset-to-suggestion

Decision Matrix labels must render read-only beside editable ROI buckets.

## ATTOM Fallback Behavior

- Missing `ATTOM_API_KEY`: return cached/manual snapshot if available; otherwise `status = "unavailable"`.
- ATTOM timeout or malformed response: log the failure, do not crash, return fallback snapshot.
- ATTOM refresh must not alter Decision Matrix records.
- Property Data tab must show source and freshness clearly.

## Test Requirements

- Matrix preservation tests for market refresh, ROI overrides, scenario preview, scenario save, and report regeneration.
- ROI math tests for ROI %, net gain, proceeds, conservative/expected/upside, negative-ROI must-do repairs, and value-lift cap.
- API tests for stable response shapes and non-persisting preview.
- Frontend guardrail tests for read-only matrix labels and reload-safe ROI overrides.
- Compatibility test that `/report/from-tier` still returns matrix-traceable report items.
