# Rejected Designs — Walkthrough & ROI

Approaches evaluated and explicitly **not** adopted. Documented to prevent re-litigation and guide future work.

---

## 1. Full editable scoring grid (12+ columns)

**What it was:** Walkthrough table with inline dropdowns for Category, Condition (1–5), Action, Visibility, Risk, Cost, Priority — all seller-facing.

**Why rejected:** Overwhelming for homeowners walking a house. Exposed recommendation machinery on the evidence tab. Created overlap with ROI tab ("which improvements should I make?" answered twice).

**Instead:** Evidence-only default view (Zone, Component, Note, Looks Fine, Include). Internal fields behind Advanced toggle.

**Source:** `walkthrough_automation_simplification_138e5f67.plan.md`

---

## 2. Seller-facing 1–5 condition scores

**What it was:** Dropdown asking sellers to rate each component 1–5.

**Why rejected:** Inconsistent scoring across users; sellers don't think in numeric condition scales. Inference from natural-language notes is more reliable for Phase 1.

**Instead:** `condition_label` (`unknown`, `good`, `fair`, `poor`, `replace`) inferred from note keywords; `condition_score` kept internal for priority math.

---

## 3. Three seller-facing buckets on Walkthrough tab

**What it was:** Display Fix Before Listing / Consider Upgrading / Leave Alone badges per row on the walkthrough checklist.

**Why rejected:** Buckets are **decision** output, not **evidence** input. Showing them during data entry prejudges the seller's task and duplicates ROI output.

**Instead:** Buckets computed backend-only; ROI tab presents prioritized recommendations per budget scenario.

---

## 4. Static additive ROI scopes

**What it was:** Quick Wins → Balanced → Leave Nothing Behind, where each level carries forward all items from the prior level with additive prompts.

**Why rejected:** Static scopes are an AI exercise disconnected from the seller's actual question (*"I have $5k — what should I spend it on?"*). Encouraged over-improvement at higher tiers regardless of evidence.

**Instead:** Four budget scenarios with explicit budget constraints and evidence-tier weighting.

---

## 5. Photos as primary evidence source

**What it was:** ROI recommendations driven primarily by aggregated photo analysis; walkthrough as optional supplement.

**Why rejected:** Photo coverage is partial (~130 photos, not every component). Sellers have ground-truth knowledge photos miss (material type, operation status, age).

**Instead:** Walkthrough template = 100% component backbone. Photos supplement gaps. Walkthrough wins on conflict.

---

## 6. Gemini-based condition inference (Phase 1)

**What it was:** Send each `owner_note` to Gemini to classify condition and action.

**Why rejected:** Extra latency, cost, and non-determinism for ~130 rows. Rule-based keyword inference sufficient for MVP.

**Status:** Deferred to Phase 3, not rejected permanently.

**Source:** `walkthrough_automation_simplification_138e5f67.plan.md` — "You chose rule-based note inference for Phase 1"

---

## 7. Persisting assessment_prompt in owner_note or DB

**What it was:** Store generic assess text ("Assess fireplace — ignites?") in `owner_note` or a dedicated `assessment_prompt` column.

**Why rejected:**

- DB column: prompt library updates wouldn't apply without re-seeding
- `owner_note`: contaminated ROI evidence and appeared as seller observations

**Instead:** `assessment_prompt` computed at enrich time; placeholder in UI only.

---

## 8. Looks Fine clears owner_note

**What it was:** `apply_looks_fine()` set `owner_note: None` when marking fine.

**Why rejected:** Accidental data loss — seller clicks Looks Fine and loses *"Original laminate from 1999"*.

**Instead:** Looks Fine disabled when note exists; seller must clear note first. Toggle off only (no note deletion).

**Source:** `walkthrough_ui_state_fixes_40b16832.plan.md`

---

## 9. Forcing include_in_report=false on recalc when looks_fine=true

**What it was:** `prepare_walkthrough_row()` and `apply_calculated_persist_fields()` overwrote Include on every recalc.

**Why rejected:** Seller could not re-enable Include after marking Looks Fine. Include is a routing preference, not a computed consequence of looks fine.

**Instead:** Convenience default only at Looks Fine transition; never overwrite on subsequent PATCH/recalc.

---

## 10. include_in_report default true for untouched rows

**What it was:** New/template rows defaulted `include_in_report=true`.

**Why rejected:** Untouched rows with no evidence polluted ROI prompts. Empty rows should be opt-in, not opt-out.

**Instead:** Default false; auto-enable Include when seller enters an observation.

**Source:** `walkthrough_ui_state_fixes_40b16832.plan.md`, `walkthrough_items_v4.sql`

---

## 11. looks_fine sets condition_label to good

**What it was:** Treat Looks Fine as a condition assessment.

**Why rejected:** Semantically wrong — "no concerns" ≠ "confirmed good condition." A dismissed water heater isn't certified new.

**Instead:** `looks_fine` independent; `condition_display: "assumed_good"` enrich-only when looks_fine + unknown condition.

---

## 12. Inferring poor condition from category alone

**What it was:** Default unknown rows to fair/poor based on `dated` or `inspection_risk` category.

**Why rejected:** Would recommend upgrades/repairs for components the seller never evaluated.

**Instead:** Empty `owner_note` → `condition_label: unknown`. Category influences action inference only when observation exists.

---

## 13. Additive "include everything from Quick Wins plus…" prompt language

**What it was:** Each ROI level prompt explicitly carried forward prior level items.

**Why rejected:** Tied to rejected static scope model; doesn't respect budget constraints or evidence tiers.

**Instead:** Each scenario gets fresh selection from unified evidence package with budget-specific instructions.

---

## 14. Consumer-facing priority score on Walkthrough tab

**What it was:** Display computed priority score (0–100) per row to sellers.

**Why rejected:** Priority is a decision/output concept. Showing scores during evidence entry biases sellers and duplicates ROI ranking.

**Status:** Phase 2 — may appear in ROI decision cards, not walkthrough.

**Source:** `interior_walkthrough_integration_5eac8cc3.plan.md`

---

## 15. Checkbox observation schemas (Phase 2 — not Phase 1)

**What it was:** Per-component checkbox UI ("What's true about this item?") under the note field.

**Why deferred (not adopted in Phase 1):** Adds UI complexity before validating note-only workflow. Schema hook (`observations` jsonb) reserved for later.

**Source:** `walkthrough_automation_simplification_138e5f67.plan.md` Phase 2

---

## 16. Three-category model (cosmetic / functional / inspection_risk)

**What it was:** Original three-category taxonomy without `dated`.

**Why rejected:** "Dated but functional" is a huge seller decision category (20-year dishwasher, builder-grade fixtures) that doesn't fit functional or cosmetic cleanly.

**Instead:** Four categories including `dated`.
