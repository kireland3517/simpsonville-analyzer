# Open Questions — UX & Walkthrough

Unresolved decisions as of project knowledge setup. Update this file when choices are made.

---

## State machine implementation

The UI state machine plan (`walkthrough_ui_state_fixes_40b16832.plan.md`) is **approved for implementation** but todos remain pending.

| Question | Options | Notes |
|----------|---------|-------|
| Badge placement | Status column vs inline pills on each row | Plan shows both patterns; pick one for consistency |
| Untouched visual | No badge vs explicit "Untouched" label | Plan default: no badge (absence = untouched) |
| Included badge | Always show when `include_in_report=true` vs only when non-default | Plan: show whenever true, even with No Concerns |

---

## Looks Fine UX details

| Question | Options | Notes |
|----------|---------|-------|
| Minimal label for looks fine | Dimmed row + checkmark only vs "Assumed Good" badge | Automation plan says checkmark sufficient; state-fix plan adds "No Concerns" badge |
| Zone bulk confirm | Silent bulk vs confirmation dialog | Destructive-ish (dismisses many rows); Vercel guidelines favor confirm or Undo |
| Toggle affordance | Button shows "Looks Fine" / "✓ Fine" states | Reversible toggle approved; exact copy TBD |

---

## Include toggle behavior

| Question | Options | Notes |
|----------|---------|-------|
| Include label copy | "Include" vs "Send to ROI" vs "Include in report" | Sellers may not know what "report" means |
| No Concerns + Included | Allow routing dismissed items to ROI as negative evidence? | Evidence plan supports "SELLER CONFIRMED OK"; is this a common workflow? |
| Re-enable after recalc | Is decoupling fully shipped? | Plan approved; verify backend no longer forces false on recalc |

---

## Recalculate & Advanced toggle

| Question | Options | Notes |
|----------|---------|-------|
| Recalculate button | Remove from seller header vs move to Advanced only | Automation plan recommends hide; some sellers may need explicit refresh after migration |
| Advanced toggle default | Hidden (dev-only) vs visible with "Show details" link | Hidden default approved; discoverability for power users unclear |
| Who sees computed fields | Dev-only vs seller opt-in | Affects trust — sellers seeing "action: upgrade" may confuse evidence/decision split |

---

## ROI tab & budget scenarios

> **Direction set:** Budget scenarios will be replaced by listing-readiness tiers. `spend_nothing` removed. See [listing-readiness-tiers.md](listing-readiness-tiers.md).

| Question | Options | Notes |
|----------|---------|-------|
| Sparse walkthrough threshold | When to show "Add observations…" banner | Plan says "few notes" but no number (3? 10? % of rows?) |
| Inferred-tier warning copy | Subtle pill vs modal on first Maximize regen | Trust layer for age-only recommendations |
| Rationale default in UI | Collapsed vs expanded per item | Print: expanded by default; screen behavior TBD |
| Tier tab layout | Resolved — three cumulative tiers (aspirational merged into nice_to_do) | Shipped |
| Popcorn `minimum_tier` | `should_do` vs `must_do` for aggressive prep strategy | Seller strategy may elevate tier |
| Buyer profile in UI | Expose all 6 profiles vs general-only default | README documents 6 profiles; SPA may not surface all |

---

## Phase 2 — observations checklists

| Question | Options | Notes |
|----------|---------|-------|
| Checkbox schemas per component | Standard set (material, age, damage) vs component-specific | `observations` jsonb column exists; UI not designed |
| Checkboxes vs note | Checkboxes infer condition first, note overrides | Plan says observations read first in Phase 2 inference |
| Mobile layout | Checkboxes under note vs expandable panel | 130 rows — density concern |

---

## Phase 3 — AI inference

| Question | Options | Notes |
|----------|---------|-------|
| Gemini condition inference | Replace rules vs hybrid (rules + AI for ambiguous notes) | Deferred from Phase 1 |
| Photo upload per row | Attach photo to walkthrough component | Would strengthen confirmed tier |
| Richer ROI labels | `medium-high` confidence vs High/Medium/Low | Schema extension TBD |

---

## Print & accessibility

| Question | Options | Notes |
|----------|---------|-------|
| First-print wait UX | Full-screen "Preparing print…" vs progress count | 10–30s if uncached; needs clear feedback (Vercel: loading ellipsis) |
| Walkthrough in print | Include evidence table vs ROI-only print | Walkthrough plan says "included in print" for interior integration |
| Keyboard on 130-row table | Virtualize vs zone collapse default | Vercel guidelines require virtualization >50 items |

---

## Data hygiene

| Question | Options | Notes |
|----------|---------|-------|
| Sanitize legacy prompt rows | One-time `POST /sanitize-prompts` vs migration-only | State-fix plan targets `130_kingfisher` specifically |
| v4 migration timing | Ship backend first vs migration first | Deploy order documented: backend + UI → SQL → sanitize → refresh |

---

## Terminology

| Question | Options | Notes |
|----------|---------|-------|
| "No Concerns" vs "Looks Fine" | User-facing label | Internal field is `looks_fine`; badge copy undecided |
| Layer 2 name length | Full "Hidden Issues & Transaction Risk" vs shortened nav label | Sidebar space vs clarity |
| "Evidence" vs "Observations" | Tab subtitle and badge naming | "Observation" used in state machine; "evidence" in architecture docs |

---

## How to resolve

Use `/grill-me` (once installed) or `/vercel-design-review` to stress-test options before implementing. Record decisions here and mirror into `ux-decisions.md`.
