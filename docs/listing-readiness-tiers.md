# Listing Readiness Tiers — Architecture (Planned)

> **Status:** Approved direction — **not implemented yet.**  
> Current production still uses budget scenarios (`spend_nothing`, `budget_5k`, `budget_15k`, `maximize`).  
> This document defines the target model for Scenario Engine and report projection work.

---

## Summary

Replace the **budget-cap scenario model** with **listing-readiness tiers**. Remove `spend_nothing` entirely.

Seller question shifts from *"I have $X — what should I spend?"* to *"What must I do to list safely vs what improves competitiveness?"*

```
Evidence (frozen) → Decision Matrix (rows + options + tiers) → Tier selection → Report projection
```

Reports remain **projections**. Decisions are **the product**.

---

## Tier vocabulary

| Tier key | Seller-facing label (TBD) | Meaning |
|----------|---------------------------|---------|
| `must_do` | Must Do | Required for safety, inspection, functionality, odor remediation, or listing readiness |
| `should_do` | Should Do | Meaningful buyer-impact improvements with strong expected return |
| `nice_to_do` | Nice to Do | Optional presentation polish and competitive upgrades that are not required |

**Removed:** `spend_nothing` — its intent (transaction-risk-only) is absorbed by `must_do`.  
**Removed:** `aspirational` — merged into `nice_to_do` (no distinct inclusion rule in practice).

**Deprecated (future):** `budget_5k`, `budget_15k`, `maximize` as *primary* selection axes. Cost may remain informational on options and in report totals, but **tier** drives inclusion — not a greedy budget ceiling.

---

## Matrix row fields (new)

Every `decision_matrix_rows` record should eventually include:

| Field | Type | Purpose |
|-------|------|---------|
| `minimum_tier` | `must_do \| should_do \| nice_to_do \| not_doing` | Lowest tier at which this component's **recommended option** must appear in a plan, or seller-managed exclusion |
| `recommended_tier` | same enum | Tier where the system's recommended option belongs for a typical listing prep strategy |

### Derivation rules (deterministic — no LLM)

`minimum_tier` is derived from existing row signals:

| Signal | Typical `minimum_tier` |
|--------|------------------------|
| `decision_status = required_action` + safety / odor / structural | `must_do` |
| `decision_status = required_action` + cosmetic-only | `should_do` or `nice_to_do` (component rules) |
| `decision_status = decision_required` + high buyer impact | `should_do` |
| `decision_status = decision_required` + medium/low impact | `nice_to_do` |
| `decision_status = monitor` | `nice_to_do` or excluded until inspected |
| `decision_status = informational` | excluded from tier plans (no spend) |

`recommended_tier` may equal or exceed `minimum_tier` when the recommended option is more aggressive than the floor (e.g. countertops: minimum `nice_to_do`, recommended `should_do` if refresh is system pick). `not_doing` is a seller decision bucket and is excluded from report-generation tiers.

### Example assignments (130 Kingfisher)

| Component | `minimum_tier` | `recommended_tier` | Notes |
|-----------|----------------|-------------------|-------|
| Garage door (structural failure) | `must_do` | `must_do` | Replace required for listing readiness |
| Indoor air quality / smoke odor | `must_do` | `must_do` | Odor remediation before showings |
| Crawlspace (unknown moisture) | `must_do` | `must_do` | Inspect at minimum; repair may escalate tier |
| Gutters / drainage (required_action) | `must_do` | `must_do` | Inspection / water management |
| Popcorn ceilings | `should_do` | `should_do` | May elevate to `must_do` if seller strategy = aggressive prep |
| Countertops (serviceable Corian) | `nice_to_do` | `should_do` | Floor is optional; refresh is typical recommendation |
| Fireplace refresh | `nice_to_do` | `nice_to_do` | Competitive polish, not listing blocker |
| Interior paint (dated) | `nice_to_do` | `should_do` | High visibility — strategy-dependent |
| Exposed electrical wire | `must_do` | `must_do` | Safety |

---

## Scenario Engine (future)

### Module: `scenario_selector.py` (revision)

**Current (shipped, to be replaced):** budget caps + cumulative additive chain  
`spend_nothing` ⊂ `budget_5k` ⊂ `budget_15k` ⊂ `maximize`

**Target:** tier-based selection

```python
select_for_tier(matrix_rows, tier: str, buyer_profile: str) -> TierSelection
```

### Selection rules

1. **Cumulative tiers:** `must_do` ⊂ `should_do` ⊂ `nice_to_do`; `not_doing` is non-cumulative and excluded from report generation.
   Viewing `should_do` includes all `must_do` rows; viewing `nice_to_do` includes all tiers.

2. **Inclusion:** Row included when `row.minimum_tier <= selected_tier` (ordinal compare), except `not_doing` rows.

3. **Option choice:** Use `recommended_action` / seller override unless tier policy allows downgrade (e.g. `nice_to_do` view may show `leave_as_is` where viable).

4. **Exclusion reasons:** `below_tier`, `informational`, `no_viable_option`, `seller_leave_as_is`.

5. **Cost:** Report `total_cost_low/high` as sum of selected options — informational, not a gate.

6. **Buyer profile:** Still applies weighting within a tier (e.g. first-time buyer → emphasize safety items in `must_do` narrative).

### API (target)

```
GET /decision-matrix/tiers/{tier}          → selection preview (replaces /scenarios/{scenario})
POST /report?tier=should_do                → report projection for tier
```

Legacy `/decision-matrix/scenarios/*` and `spend_nothing` remain during migration window, then removed.

### Table: `decision_matrix_scenarios` → `decision_matrix_tier_plans` (optional rename)

| Column | Change |
|--------|--------|
| `scenario` | → `tier` (`must_do`, `should_do`, `nice_to_do`) |
| `selection_policy` | Tier ordinal + buyer_profile weights |
| `selected_rows` | Same shape; `exclusion_reason` uses tier vocabulary |

Migration: `decision_matrix_v3.sql` (not written yet).

---

## Report projection (future)

`report_composer.py` maps tier selection → `upgrades[]` / `repairs[]` (unchanged traceability contract).

| Tier view | Seller sees |
|-----------|-------------|
| `must_do` | Non-negotiable listing blockers + safety |
| `should_do` | Must-do + high-ROI prep |
| `nice_to_do` | Full plan — optional polish and competitive positioning |

Assessment LLM call: inject matrix summary filtered to selected tier rows (same fix as Phase 6, tier-aware).

Report cache keys: `{tier}_{buyer_profile}` (e.g. `should_do_general`).  
Legacy keys (`budget_15k_general`) deprecated after cutover.

---

## UI (future)

### ROI tab

Replace budget tabs:

| Old | New |
|-----|-----|
| Spend Nothing | *(removed)* |
| $5,000 Budget | Must Do |
| $15,000 Budget | Should Do |
| Maximize | Nice to Do |

Decision Workspace scenario preview dropdown uses tier labels.

### Matrix row display

Collapsed row: show `minimum_tier` badge + `recommended_tier` if different.  
Expanded panel: tier rationale ("Why must-do: inspection risk high, odor detected").

---

## Relationship to `decision_status`

| `decision_status` | Typical tier floor |
|-------------------|-------------------|
| `required_action` | `must_do` (default) |
| `decision_required` | `should_do` or `nice_to_do` |
| `monitor` | `nice_to_do` (inspect first) |
| `informational` | excluded |

Tiers and `decision_status` are complementary: status = urgency class; tier = listing-plan inclusion.

---

## Implementation phases (future — not started)

| Phase | Scope |
|-------|-------|
| **10** | Schema: `minimum_tier`, `recommended_tier` on rows; deterministic tier assignment in `decision_matrix.py` |
| **11** | Revise `scenario_selector.py` → tier selection; `GET /decision-matrix/tiers/{tier}` |
| **12** | `report_composer.py` + `POST /report` keyed by tier; migrate cache keys |
| **13** | UI: tier tabs, workspace preview, remove `spend_nothing` |
| **14** | Deprecate budget scenarios + legacy report slots; architecture doc final update |

**Do not implement Phases 10–14 until explicitly approved.**

---

## Related docs

- [architecture.md](architecture.md) — current system (budget scenarios shipped)
- [ux-decisions.md](ux-decisions.md) — §9 readiness tier decision
- [decision_matrix_completion plan](../.cursor/plans/decision_matrix_completion_b8e4f12a.plan.md) — Phases 3–9 complete; 10–14 added
