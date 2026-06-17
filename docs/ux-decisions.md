# UX Decisions — Walkthrough & ROI

Major design decisions for the seller walkthrough and ROI experience. Sourced from `.cursor/plans/` and implemented behavior in `walkthrough.py`, `evidence.py`, `static/index.html`.

---

## 1. Evidence vs decision — tab separation

**Decision:** Walkthrough collects facts; ROI tab makes budget recommendations.

**Rationale:** Sellers were seeing recommendations in two places (walkthrough columns + ROI tab). That created confusion about which surface was authoritative.

**Implementation:**

- Walkthrough default view: Zone, Component, Note, Looks Fine, Include
- ROI tab: budget scenarios with decision cards and rationale blocks
- Walkthrough subtitle: *"Collect observations — budget recommendations are on the ROI tab"*

---

## 2. Two-layer walkthrough structure

**Decision:** Split checklist into Layer 1 (room-by-room) and Layer 2 (hidden issues & transaction risk).

**Rationale:** Sellers think in rooms during a walk-through, but inspectors and lenders flag systems-level risks separately. "Inspection Risk" was renamed to **Hidden Issues & Transaction Risk** — more seller-friendly.

**Layers:**

| Layer key | Seller name |
|-----------|-------------|
| `room` | Room-by-Room Seller Assessment |
| `systems` | Hidden Issues & Transaction Risk |

---

## 3. Template locked; property rows editable

**Decision:** Master template in `walkthrough.py` is read-only. Only Supabase `walkthrough_items` rows are editable per property.

**Rationale:** Prevents template drift and keeps ~130 components consistent across properties. Seeding is idempotent via `POST /walkthrough-items/seed`.

---

## 4. Assessment prompt vs owner note (two fields, one input)

**Decision:** `assessment_prompt` is computed at enrich time and shown as a **placeholder only**. `owner_note` stores real seller observations.

**Rationale:** Generic "Assess fireplace…" text was polluting `owner_note` and leaking into ROI evidence. Property-specific facts stay in seeds; generic guidance lives in `ASSESSMENT_PROMPTS`.

**Rule:** Never persist placeholder text on blur/save.

---

## 5. Looks Fine semantics

**Decision:** `looks_fine` means *"seller has no concerns"* — not *"item is objectively good."*

**Rationale:** A water heater marked Looks Fine is dismissed from recommendations, not certified new. Store `looks_fine` independently from `condition_label`.

**UI:** Looks Fine dims the row with a checkmark. Backend sets `action=skip` and excludes from evidence unless `include_in_report` overrides.

---

## 6. Rule-based condition inference (Phase 1)

**Decision:** Infer `condition_label` from `owner_note` keywords only — not from `looks_fine`, not from category alone.

**Rationale:** Faster, deterministic, no extra Gemini calls. Gemini inference deferred to Phase 3.

**Key semantic:** Age/dated keywords → **fair** (not poor). Defect keywords → **poor**. Example: `"Original laminate from 1999"` → fair → action upgrade.

---

## 7. Unified evidence package with precedence

**Decision:** Merge walkthrough + photo summary + property metadata into one evidence block for ROI.

**Precedence:** walkthrough > photos > metadata

**Confidence tiers:** confirmed, observed, inferred, unknown

**Rationale:** Sellers correct photo misreads (e.g. photo says granite, seller says laminate → use laminate). Prevents recommending $7k replacements from build-year alone.

---

## 8. Budget scenarios replace static detail levels *(current — superseded by §9)*

**Decision:** Replace Quick Wins / Balanced / Leave Nothing Behind with budget scenarios (shipped).

| Scenario | Seller question |
|----------|-----------------|
| Spend Nothing | What absolutely has to be fixed? |
| $5,000 Budget | Highest return within ~$5k |
| $15,000 Budget | Balanced prep plan |
| Maximize Sale Price | Highest market impact, no cap |

**Rationale:** Static additive scopes answered *"What's wrong?"* not *"I have $5k — what should I spend it on?"*

Each ROI item includes a **rationale** block: evidence citations, tier, and reason.

---

## 9. Listing-readiness tiers replace budget scenarios *(canonical)*

**Decision:** Use listing-readiness tiers as the seller-facing decision model:

| Tier | Seller question |
|------|-----------------|
| Must Do | What must be fixed for safety, inspection, odor, or listing readiness? |
| Should Do | What improvements have strong buyer impact and return? |
| Nice to Do | What optional polish helps presentation or competitiveness but isn't necessary? |
| Not Doing | What has the seller explicitly excluded? |

**Rationale:** Sellers think in listing readiness, not arbitrary budget buckets. `spend_nothing` duplicated `must_do` intent. Tiers are cumulative (`must_do` ⊂ `should_do` ⊂ `nice_to_do`); `not_doing` is a matrix decision bucket, not a report-generation tier. Cost remains visible on options but does not gate inclusion.

**Matrix rows** will carry `minimum_tier` and `recommended_tier` (e.g. garage door → `must_do`; countertops → `nice_to_do` min / `should_do` rec).

**Spec:** [listing-readiness-tiers.md](listing-readiness-tiers.md)  
**Phases:** 10–14 in [decision_matrix_completion plan](../.cursor/plans/decision_matrix_completion_b8e4f12a.plan.md)

---

## 9. Include as routing flag (approved, partially implemented)

**Decision (approved in state-machine plan):** `include_in_report` is a routing flag orthogonal to evidence state — not a fourth mutually exclusive state.

| Evidence state | Include default | Seller can override |
|----------------|-----------------|---------------------|
| Untouched | OFF | Yes |
| Observation (note present) | ON | Yes |
| No Concerns (looks fine) | OFF | Yes |

**Status:** Plan approved; implementation tracked in `walkthrough_ui_state_fixes_40b16832.plan.md`.

---

## 10. State machine for seller evidence (approved)

**Decision:** Seller transitions between Untouched → Observation → No Concerns. Looks Fine **disabled** when a note exists; seller must clear note first.

**Rationale:** Prevents accidental loss of observations (e.g. *"Original laminate from 1999"*) when clicking Looks Fine.

**Visible badges:** Observation, No Concerns, Included (routing)

---

## 11. Advanced toggle for internal fields

**Decision:** Condition, action, category, visibility, risk, cost, priority, bucket — computed backend-only, hidden behind a developer **Advanced** toggle.

**Rationale:** Power users and debugging need visibility; sellers should not see recommendation machinery on the evidence tab.

---

## 12. Zone bulk "Mark remaining as Looks Fine"

**Decision:** Zone headers offer bulk-dismiss for untouched rows; skip rows with existing `owner_note`.

**Rationale:** Seller walks room → types ~20 observations → marks rest as fine. Target ~20 notes, not 130.

---

## 13. Four categories (not three)

**Decision:** `cosmetic`, `functional`, `dated`, `inspection_risk`

**Rationale:** `dated` is a distinct seller mental model — works but looks old (20-year dishwasher, builder-grade vanity). Not broken, not risky.

---

## 14. buyer_visibility on every row

**Decision:** Store `high | medium | low` visibility on template rows.

**Rationale:** Drives ROI prioritization (countertops high, water heater low) without exposing the field to sellers by default.

---

## 15. Photos supplemental, not primary

**Decision:** Walkthrough template provides 100% component coverage; photos fill gaps where walkthrough is silent.

**Rationale:** Not every component is photographed. Walkthrough is seller ground truth; photos strengthen confidence where available.

---

## 16. Print with expanded deep detail

**Decision:** `printReport()` is async — prefetches all deep-detail panels before `window.print()`.

**Rationale:** Print CSS previously hid deep panels. Seller hands contractor a complete plan with how-to detail.

---

## Decision log

| Date | Decision | Plan reference |
|------|----------|----------------|
| — | Interior walkthrough integration | `interior_walkthrough_integration_5eac8cc3.plan.md` |
| — | Automation simplification + evidence package | `walkthrough_automation_simplification_138e5f67.plan.md` |
| — | UI state machine + note protection | `walkthrough_ui_state_fixes_40b16832.plan.md` |
