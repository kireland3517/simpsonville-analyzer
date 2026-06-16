"""
roi.py
------
Uses Gemini to generate a pre-sale ROI report from vision analysis
summary data. Supports three detail levels and six buyer profiles.

Requires GEMINI_API_KEY to be set in the environment.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

from claude_client import generate_text, get_api_key, get_detail_model
# Valid parameter values
DETAIL_LEVELS  = {"spend_nothing", "budget_5k", "budget_15k", "maximize"}
DETAIL_LEVEL_ORDER = ["spend_nothing", "budget_5k", "budget_15k", "maximize"]

# Legacy report slot keys (pre-budget-scenario)
LEGACY_LEVEL_MAP = {
    "executive": "spend_nothing",
    "standard": "budget_15k",
    "deep_dive": "maximize",
}
LEGACY_LEVEL_REVERSE = {v: k for k, v in LEGACY_LEVEL_MAP.items()}

_LEVEL_LABELS = {
    "spend_nothing": "Spend Nothing",
    "budget_5k":     "$5,000 Budget",
    "budget_15k":    "$15,000 Budget",
    "maximize":      "Maximize Sale Price",
}

LEVEL_DESCRIPTIONS = {
    "spend_nothing": (
        "What absolutely has to be fixed before listing? Transaction-risk items only — "
        "water stains, structural issues, drainage, safety. Target under $2,000 total. "
        "No cosmetic upgrades."
    ),
    "budget_5k": (
        "I have about $5,000 — what should I spend it on? Highest-return improvements "
        "from your walkthrough evidence and photos. Prioritize confirmed and observed findings."
    ),
    "budget_15k": (
        "$15,000 prep plan — must-fix items plus highest-impact upgrades buyers notice "
        "when comparing your home to similar-era neighbors."
    ),
    "maximize": (
        "Highest expected market impact regardless of budget. Every evidence-backed "
        "improvement worth doing to compete for top dollar in the $295K–$305K range."
    ),
}


def normalize_detail_level(detail_level: str) -> str:
    return LEGACY_LEVEL_MAP.get(detail_level, detail_level)
BUYER_PROFILES = {
    "first_time_buyer", "young_family", "downsizer",
    "investor", "relocating_professional", "general",
}

# gemini-2.5-pro uses part of the output budget for internal reasoning
_PRO_MAX_TOKENS = 8192

SYSTEM_PROMPT = (
    "You are a licensed real estate agent and renovation consultant in Greenville County, "
    "South Carolina with 20 years of experience. You specialize in helping homeowners "
    "prepare properties for sale and maximize their net proceeds. You know local contractor "
    "rates, material costs at the Greenville SC Lowe's and Home Depot, and what buyers in "
    "the Simpsonville market actually pay for. You give specific, actionable advice with "
    "realistic local pricing — not national averages. You always return valid JSON only, "
    "with no commentary, preamble, or markdown formatting."
)

_ERROR_RESULT: dict = {
    "detail_level":        None,
    "buyer_profile":       None,
    "buyer_profile_notes": None,
    "executive_summary":   None,
    "project_timeline":    None,
    "upgrades":            None,
    "repairs":             None,
    "deal_killers":        None,
    "sc_considerations":   None,
}


# ── Helpers ────────────────────────────────────────────────────────────────

def _fmt(value: Any, fallback: str = "unknown") -> str:
    return str(value) if value is not None else fallback


def _norm_name(name: str) -> str:
    """Normalize an item name for deduplication across report levels."""
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    return " ".join(s.split())


_COST_FIELDS = ("estimated_cost", "materials_cost", "labor_cost", "estimated_value_add", "roi_percent")


def _merge_recommendations(
    prior: list[dict] | None,
    new: list[dict],
    max_count: int,
    *,
    sort_new: bool = True,
    sort_key=None,
    reverse: bool = True,
) -> list[dict]:
    """
    Build an additive list: all prior items first, then new non-duplicates.
    Prior items are never dropped when a higher detail level is generated.
    Cost fields from the first-seen (lower) detail level are always preserved
    so the same item never shows a different price across tabs.
    """
    prior = prior or []
    seen: dict[str, dict] = {}
    for item in prior:
        key = _norm_name(item.get("name", ""))
        if key:
            seen[key] = item
    merged = list(prior)

    extras: list[dict] = []
    for item in new:
        key = _norm_name(item.get("name", ""))
        if not key:
            continue
        if key in seen:
            # Item already exists — anchor its cost fields to the prior version
            prior_item = seen[key]
            for field in _COST_FIELDS:
                if prior_item.get(field) is not None:
                    item[field] = prior_item[field]
            continue
        seen[key] = item
        extras.append(item)

    if sort_new and sort_key and extras:
        extras.sort(key=sort_key, reverse=reverse)

    merged.extend(extras)
    return merged[:max_count]


def _prior_level(detail_level: str) -> str | None:
    """Return the detail level immediately below the given level, if any."""
    try:
        idx = DETAIL_LEVEL_ORDER.index(detail_level)
    except ValueError:
        return None
    return DETAIL_LEVEL_ORDER[idx - 1] if idx > 0 else None


def _prior_items_block(prior_report: dict, detail_level: str) -> str:
    """Prompt section listing items that must carry forward from the prior level."""
    prior_level = _prior_level(detail_level)
    if not prior_level:
        return ""

    prior_upgrades = prior_report.get("upgrades") or []
    prior_repairs  = prior_report.get("repairs") or []
    if not prior_upgrades and not prior_repairs:
        return ""

    prior_label = _LEVEL_LABELS.get(prior_level, prior_level)
    current_label = _LEVEL_LABELS.get(detail_level, detail_level)

    return f"""
PRIOR LEVEL: {prior_label.upper()} ({prior_level.upper()})
---------------------------------------------------------
The following items were already selected at the {prior_label} level.
You MUST include every item below in your response for {current_label}.
Do not replace, omit, or contradict them — only add additional items beyond these.

Prior upgrades (include all):
{json.dumps(prior_upgrades, indent=2)}

Prior repairs (include all):
{json.dumps(prior_repairs, indent=2)}
"""


# ── Detail-level blocks ────────────────────────────────────────────────────

_RATIONALE_INSTRUCTION = """
RATIONALE (required on EVERY upgrade and repair):
Include a "rationale" object with:
- evidence: array of {source: "walkthrough"|"photo"|"metadata", text: "<exact citation>"}
- tier: "confirmed"|"observed"|"inferred"
- reason: one sentence why this belongs in THIS budget scenario
- expected_impact: what happens if the seller does this (buyer appeal, risk reduction)
- confidence: "high"|"medium"|"low"
- market_impact: effect on showings and perceived value
Do not recommend items without citing evidence from the EVIDENCE block.
Do not upgrade inferred-tier items to confirmed. Spend Nothing must exclude inferred-tier items.
"""


def _detail_block(detail_level: str) -> str:
    """Budget scenario rules injected into every prompt."""
    level = normalize_detail_level(detail_level)
    label = _LEVEL_LABELS.get(level, level)

    if level == "spend_nothing":
        return f"""\
BUDGET SCENARIO: {label}
-----------------------
Seller question: What absolutely has to be fixed before I list?
Budget: ~$0–$2,500 total — transaction-risk repairs ONLY.
- Include ONLY walkthrough-observed transaction-risk items (water stains, structural cracks, drainage, safety).
- NO cosmetic upgrades. Ignore inferred-tier evidence.
- Max 4 repairs, 0-1 upgrades only if safety-related.
{_RATIONALE_INSTRUCTION}"""

    if level == "budget_5k":
        return f"""\
BUDGET SCENARIO: {label}
-----------------------
Seller question: I have $5,000 — what should I spend it on?
Budget: stay within $1,500–$5,000 total investment.
- Prioritize Confirmed findings, then Observed. Inferred only if budget remains.
- Highest buyer-visible ROI: paint, fixtures, pressure wash, minor cosmetic refresh.
- Max 4 upgrades, 3 repairs; total cost must fit budget.
{_RATIONALE_INSTRUCTION}"""

    if level == "budget_15k":
        return f"""\
BUDGET SCENARIO: {label}
-----------------------
Seller question: I have $15,000 — what should I spend it on?
Budget: stay within $5,000–$15,000 total investment.
- Include all Spend Nothing items plus highest-impact upgrades from evidence.
- Prioritize Confirmed → Observed → selective Inferred.
- Max 6 upgrades, 5 repairs.
{_RATIONALE_INSTRUCTION}"""

    return f"""\
BUDGET SCENARIO: {label}
-----------------------
Seller question: Highest market impact regardless of budget.
Budget: no cap — optimize for sale price, not cost minimization.
- Include all evidence-backed improvements worth doing.
- Inferred-tier items OK with low-confidence label in rationale.
- Max 10 upgrades, 8 repairs.
{_RATIONALE_INSTRUCTION}"""


def _comp_anchoring_block(detail_level: str) -> str:
    """Comp and market framing — evidence-driven, not additive carry-forward."""
    level = normalize_detail_level(detail_level)
    base = """\
MARKET CONTEXT — 130 Kingfisher Dr, Simpsonville SC
---------------------------------------------------
Realistic ARV ceiling: $295,000–$305,000 (2,019 sqft, built 1999).
Comps: 4 Kingfisher Dr $289K (1996), 305 Kingfisher Dr $265K (2000), 307 Blue Heron Cir $301K (2007).
Select projects from the EVIDENCE block only — do not invent findings.
"""
    if level == "spend_nothing":
        return base + "Scope: deal-killers and inspection-risk only.\n"
    if level == "budget_5k":
        return base + "Scope: highest-ROI cosmetic refresh within $5K.\n"
    if level == "budget_15k":
        return base + "Scope: meet market expectation for a well-prepared 1999 home.\n"
    return base + "Scope: compete with newer construction for top dollar.\n"


# ── Buyer-profile blocks ───────────────────────────────────────────────────

def _profile_block(buyer_profile: str) -> str:
    if buyer_profile == "first_time_buyer":
        return """\
BUYER PROFILE: FIRST-TIME BUYER
--------------------------------
- This buyer is likely using FHA or USDA financing. Flag any item that would \
cause an FHA/USDA appraisal to fail (exposed wiring, roof condition, peeling paint, \
missing handrails, water intrusion, broken windows, HVAC not functional).
- Weight repairs over upgrades — this buyer needs move-in ready above all else.
- Note any item that affects loan eligibility or requires escrow holdback.
- Cosmetic upgrades matter only if they prevent low appraisal.
- buyer_profile_notes should include FHA/USDA-specific flags first."""

    if buyer_profile == "young_family":
        return """\
BUYER PROFILE: YOUNG FAMILY
----------------------------
- Weight safety items highest: handrails, trip hazards, electrical safety, \
pool/water hazards, deck structural integrity.
- Note yard size adequacy and outdoor living condition.
- Flag anything that could have lead paint risk (note: built 1999, so pre-1978 \
lead paint rules do NOT apply — state this explicitly in buyer_profile_notes).
- Weight family-friendly features: bedroom count, storage, kitchen functionality.
- buyer_profile_notes should lead with safety and kid-friendliness observations."""

    if buyer_profile == "downsizer":
        return """\
BUYER PROFILE: DOWNSIZER
-------------------------
- Weight low-maintenance upgrades: anything that reduces ongoing upkeep burden.
- Note accessibility: wide doorways, step-free entry, grab bar potential in baths.
- Flag any large yard or complex landscaping as a maintenance concern.
- Weight single-level living features.
- Note proximity to River Ridge amenities and Simpsonville downtown (5 min).
- buyer_profile_notes should emphasize maintenance burden and accessibility."""

    if buyer_profile == "investor":
        return """\
BUYER PROFILE: INVESTOR / LANDLORD
------------------------------------
- Switch all language to ROI math. Every upgrade must show return calculation.
- Add a rental_yield_estimate field to executive_summary: \
Simpsonville 3/2 currently rents for $1,600-$1,900/month (Zillow Rent Zestimate range).
- Cost per sqft context: subject is 2,019 sqft at $276,810 = $137/sqft. \
Comparable rentals in River Ridge rent for $0.85-$0.95/sqft/month.
- De-emphasize purely cosmetic upgrades unless they reduce vacancy or \
increase achievable rent by a demonstrable amount.
- Weight durable materials (LVP over hardwood, commercial-grade fixtures).
- buyer_profile_notes should be entirely focused on yield, cap rate, and \
maintenance cost reduction."""

    if buyer_profile == "relocating_professional":
        return """\
BUYER PROFILE: RELOCATING PROFESSIONAL
----------------------------------------
- Weight move-in ready items highest — this buyer cannot manage a renovation \
project remotely.
- Note commute context: 14 miles to downtown Greenville, approximately 20 min \
on I-385. Mention this in buyer_profile_notes.
- Weight modern finishes, updated kitchen, updated primary bath — these buyers \
compare to new construction in Woodruff Road corridor ($320K-$380K range).
- Flag anything requiring immediate contractor attention after move-in.
- Emphasize any features that compete with new construction (garage, sqft, lot size).
- buyer_profile_notes should address move-in readiness and new-construction comparison."""

    # general
    return """\
BUYER PROFILE: GENERAL
-----------------------
- Balanced weighting across all buyer types.
- No specific profile bias.
- buyer_profile_notes should provide general market observations about how \
the typical Simpsonville buyer (median age ~38, household income ~$85K) \
would react to the property's current condition."""


# ── Schema helpers ─────────────────────────────────────────────────────────

def _assessment_schema(detail_level: str) -> str:
    """JSON schema for Call 1 (Assessment)."""
    timeline_block = """
  "project_timeline": {
    "total_weeks_hired": <number>,
    "total_weeks_diy": <number>,
    "recommended_sequence": ["<project 1>", "..."],
    "parallel_projects": ["<projects that can run simultaneously>"],
    "notes": "<string>"
  },"""

    sc_block = """
  "sc_considerations": ["<South Carolina-specific item buyers commonly flag>"],"""

    if _level_key(detail_level) == "spend_nothing":
        ex_fields = """\
    "current_value": <number>,
    "estimated_arv": <number>,
    "total_investment_low": <number>,
    "total_investment_high": <number>,
    "net_gain_low": <number>,
    "net_gain_high": <number>,
    "recommendation": "<plain English 2-3 sentence recommendation>",
    "market_position": "<how property sits vs comps at this scope>",
    "disclaimer": "This report is based on AI analysis of photos and public records. Validate cost estimates with local contractors." """
    else:
        ex_fields = """\
    "current_value": <number>,
    "estimated_arv": <number>,
    "total_investment_low": <number>,
    "total_investment_high": <number>,
    "net_gain_low": <number>,
    "net_gain_high": <number>,
    "recommendation": "<2-3 sentence recommendation>",
    "market_position": "<how property sits vs comps>",
    "disclaimer": "This report is based on AI analysis of photos and public records. Validate cost estimates with local contractors." """

    return f"""{{
  "detail_level": "<echo level used>",
  "buyer_profile": "<echo profile used>",
  "buyer_profile_notes": ["<observation specific to this buyer profile>"],
  "executive_summary": {{
{ex_fields}
  }},{timeline_block}
  "deal_killers": ["<item that could kill the sale or force a price reduction>"]{sc_block}
}}"""



def _rationale_schema() -> str:
    return """{
        "evidence": [{"source": "walkthrough|photo|metadata", "text": "<string>"}],
        "tier": "confirmed|observed|inferred",
        "reason": "<one sentence>",
        "expected_impact": "<what happens if seller does this>",
        "confidence": "high|medium|low",
        "market_impact": "<effect on showings and perceived value>"
      }"""


def _upgrades_schema() -> str:
    """Schema for Call 2 — upgrades only."""
    r = _rationale_schema()
    return f"""{{
  "upgrades": [
    {{
      "name": "<string>",
      "description": "<string>",
      "materials_cost": <number>,
      "labor_cost": <number>,
      "estimated_cost": <number>,
      "estimated_value_add": <number>,
      "roi_percent": <number>,
      "priority": "<high|medium|low>",
      "diy_friendly": <true|false>,
      "diy_notes": "<string>",
      "skill_level": "<beginner|intermediate|advanced|professional_only>",
      "time_estimate_contractor": "<string>",
      "time_estimate_diy": "<string>",
      "rationale": {r}
    }}
  ]
}}"""


def _repairs_schema() -> str:
    """Schema for Call 3 — repairs only."""
    r = _rationale_schema()
    return f"""{{
  "repairs": [
    {{
      "name": "<string>",
      "description": "<string>",
      "estimated_cost": <number>,
      "priority": "<critical|high|medium|low>",
      "diy_friendly": <true|false>,
      "diy_notes": "<string>",
      "time_estimate_contractor": "<string>",
      "time_estimate_diy": "<string>",
      "sc_disclosure_required": <true|false>,
      "safety_concern": <true|false>,
      "rationale": {r}
    }}
  ]
}}"""


def _build_upgrades_prompt(
    summary: dict,
    executive_summary: dict,
    detail_level: str,
    buyer_profile: str,
    prior_report: dict | None = None,
    walkthrough_block: str = "",
) -> str:
    """Call 2: return upgrades array only."""
    top_issues, top_upgrades = _INPUT_COUNTS[detail_level]
    issues_freq   = summary.get("issues_by_frequency", {})
    upgrades_freq = summary.get("upgrades_by_frequency", {})

    ex_json = json.dumps(executive_summary, indent=2)
    prior_block = _prior_items_block(prior_report, detail_level) if prior_report else ""
    prior_count = len((prior_report or {}).get("upgrades") or [])

    dated_section = ""
    if _level_key(detail_level) == "maximize":
        dated = summary.get("dated_features_by_frequency", {})
        jetted = [
            t for t in dated
            if "jetted" in t.lower() or "jacuzzi" in t.lower() or "garden tub" in t.lower()
        ]
        if jetted:
            dated_section = f"""
CONFIRMED DATED FEATURES (jetted tub — must address in upgrades)
----------------------------------------------------------------
{_list_block(jetted)}
"""

    return f"""You are preparing upgrade recommendations for 130 Kingfisher Dr, Simpsonville SC.
This is CALL 2 OF 3. Return ONLY the upgrades array — no repairs, no other fields.

{_detail_block(detail_level)}

{_comp_anchoring_block(detail_level)}

{_profile_block(buyer_profile)}

{_PROPERTY_CONTEXT}

{_GREENVILLE_COST_ANCHORS}

{walkthrough_block}

ASSESSMENT CONTEXT (from prior analysis)
-----------------------------------------
{ex_json}

TOP {top_issues} ISSUES BY WEIGHTED SCORE
------------------------------------------
{_freq_block(issues_freq, top_issues)}

TOP {top_upgrades} UPGRADE OPPORTUNITIES BY FREQUENCY
-------------------------------------------------------
{_freq_block(upgrades_freq, top_upgrades)}{dated_section}{prior_block}

INSTRUCTIONS
------------
{_upgrades_instructions(detail_level, prior_count=prior_count)}

Return this exact JSON (no markdown, no explanation):

{_upgrades_schema()}"""


def _build_repairs_prompt(
    summary: dict,
    executive_summary: dict,
    detail_level: str,
    buyer_profile: str,
    prior_report: dict | None = None,
    walkthrough_block: str = "",
) -> str:
    """Call 3: return repairs array only."""
    top_issues, _ = _INPUT_COUNTS[detail_level]
    issues_freq = summary.get("issues_by_frequency", {})
    crit_high   = summary.get("critical_and_high_issues", [])

    ex_json = json.dumps(executive_summary, indent=2)
    prior_block = _prior_items_block(prior_report, detail_level) if prior_report else ""
    prior_count = len((prior_report or {}).get("repairs") or [])

    crit_header = "CRITICAL AND HIGH DEAL-RISK ISSUES (always include — do not omit)"
    if _level_key(detail_level) == "maximize":
        crit_header = (
            "CRITICAL AND HIGH DEAL-RISK ISSUES "
            "(MUST ALL appear in repairs — consolidate if needed, omit none)"
        )

    crit_high_flags = summary.get("critical_and_high_flags") or []
    flags_block = ""
    if crit_high_flags:
        flags_block = f"""
INSPECTOR FLAGS FROM PHOTO ANALYSIS (critical/high photos only)
---------------------------------------------------------------
These are verbatim recommendations a licensed inspector would write.
Use this language to name and scope repair items — do not downgrade.
{_list_block(crit_high_flags[:20])}
"""

    return f"""You are preparing repair recommendations for 130 Kingfisher Dr, Simpsonville SC.
This is CALL 3 OF 3. Return ONLY the repairs array — no upgrades, no other fields.

{_detail_block(detail_level)}

{_comp_anchoring_block(detail_level)}

{_profile_block(buyer_profile)}

{_PROPERTY_CONTEXT}

{_GREENVILLE_COST_ANCHORS}

{_KNOWN_REPAIR_FACTS}

{walkthrough_block}

ASSESSMENT CONTEXT (from prior analysis)
-----------------------------------------
{ex_json}

{crit_header}
-------------------------------------------------------------------
{_list_block(crit_high)}
{flags_block}
TOP {top_issues} ISSUES BY WEIGHTED SCORE
------------------------------------------
{_freq_block(issues_freq, top_issues)}{prior_block}

{_REPAIR_SEVERITY_RULES}

INSTRUCTIONS
------------
{_repairs_instructions(detail_level, prior_count=prior_count)}

Return this exact JSON (no markdown, no explanation):

{_repairs_schema()}"""


# ── Shared property context ────────────────────────────────────────────────

_PROPERTY_CONTEXT = """\
SUBJECT PROPERTY
----------------
Address:       130 Kingfisher Dr, Simpsonville SC 29680
Subdivision:   River Ridge, Greenville County SC
Specs:         3 bed / 2 bath / 2,019 sqft / built 1999 / 2-car attached garage
Lot:           0.35 acres (15,377 sqft)
Current value: $276,810 (ATTOM AVM)
AVM range:     Redfin $282,986 / Zillow $290,500 / Realtor.com $296,945
Last sold:     $170,000 - December 2017
Annual tax:    $3,446

RECENT COMPS - River Ridge subdivision
---------------------------------------
4 Kingfisher Dr:    3/2, 1,891 sqft, built 1996, sold Apr 2026 - $289,000 (100% of list)
102 Blue Heron Cir: 4/2, 2,290 sqft, built 2017, sold Apr 2026 - $319,900 (96% of list) [OUTLIER: newer construction]
307 Blue Heron Cir: 3/2, 1,782 sqft, built 2007, sold Dec 2025 - $301,000 (99% of list)
2 Turnstone Ct:     3/2, 1,238 sqft, built 1999, sold Sep 2025 - $252,000 (100% of list)
305 Kingfisher Dr:  3/2, 1,382 sqft, built 2000, sold Apr 2024 - $265,000
413 Kingfisher Dr:  4/2, 1,526 sqft, built 2002, sold Feb 2024 - $285,000

Market conditions: somewhat competitive, ~1% below list price, 58 days avg DOM
Subject advantage: largest sqft of all comparable comps at 2,019 sqft
Realistic ARV ceiling: $295,000-$305,000

LOCAL SUPPLIERS (use for pricing)
----------------------------------
- Lowe's: 1014 Woodruff Rd, Greenville SC 29607
- Home Depot: 2750 Laurens Rd, Greenville SC 29607
- Floor & Decor: Greenville SC (flooring)
- Labor rates: Greenville SC is 15-20% below national average"""


# ── Real-world cost anchors (Greenville SC, 2025) ─────────────────────────
# Sourced from Greenville/Simpsonville contractor quotes, HomeAdvisor/Angi
# regional data, and Lowe's/Home Depot materials at the local stores.
# Labor is 15-20% below national average. Use these ranges — never guess.

_GREENVILLE_COST_ANCHORS = """\
GREENVILLE SC REAL-WORLD COST ANCHORS (2025) — USE THESE RANGES, DO NOT GUESS
===============================================================================
Labor is 15-20% below national average. All figures are installed/completed cost.

REPAIRS
-------
Garage door — cosmetic panel dent/ding repair (filler + repaint):   $150–$350
Garage door — single panel replacement (steel, standard size):       $250–$500
Garage door — full door replacement (steel, 2-car, basic):         $1,200–$1,800
Garage door — full door replacement (steel, 2-car, insulated):     $1,600–$2,400
Garage door opener replacement:                                       $300–$600
Window glass — single-pane replacement (per window):                 $150–$350
Window glass — double-pane IGU replacement (per window):             $200–$500
Window full replacement (double-hung, vinyl, per window):            $400–$800
Ceiling water stain — drywall patch + prime + paint (per area):      $300–$700
Ceiling water stain — investigate source + remediate minor leak:     $400–$900
Active roof leak repair (flashing, shingles, minor):                 $400–$800
Roof replacement (30-yr architectural shingle, 2,019 sqft home):  $8,000–$14,000
Deck/porch structural post replacement (per post, wood):             $250–$600
Deck board replacement (per 100 sqft):                               $600–$1,200
Popcorn ceiling asbestos test (bulk sample, certified lab):          $250–$450
Popcorn ceiling removal + skim coat — per room (200–300 sqft):       $400–$800
Popcorn ceiling removal + skim coat — whole house (2,019 sqft):    $2,500–$4,500
Exterior siding repair — wood/hardboard patch (per section):         $300–$700
Exterior trim repair/repaint (per elevation):                        $300–$600
Interior drywall patch — hairline cracks, seams (per room):         $150–$350
Interior drywall patch — larger holes or water damage (per area):    $300–$700
Baseboard gap caulk + paint (whole house):                           $200–$450
Door frame trim repair/replace (per door):                           $100–$250
GFCI outlet installation (per outlet):                               $150–$250
Electrical panel inspection + minor repair:                          $200–$500
Plumbing — minor repair (faucet, supply line, minor leak):           $150–$350
Plumbing — toilet repair/replace:                                    $200–$500
HVAC service/tune-up:                                                $100–$200
HVAC filter + minor repair:                                          $100–$300
Crawl space moisture barrier (1,000 sqft):                           $800–$1,800
Mold remediation — minor (under 10 sqft):                            $500–$1,500

UPGRADES
--------
Interior paint — single room (labor + materials):                    $400–$800
Interior paint — whole house (2,019 sqft, 3/2):                  $3,000–$5,000
Exterior paint — full house (2,019 sqft):                          $3,500–$6,500
Light fixture replacement — ceiling/flush-mount (per fixture):       $150–$350
Light fixture replacement — dining chandelier:                       $300–$700
Ceiling fan with light (per unit, installed):                        $250–$500
Recessed lighting retrofit — per light (LED):                        $150–$300
Interior door hardware (knobs/levers, per door):                      $50–$120
Interior door hardware — whole house (10–12 doors):                  $500–$900
HVAC vent cover replacement — whole house (15–20 vents):             $150–$350
Kitchen cabinet painting + new hardware (labor + materials):       $1,500–$3,500
Kitchen cabinet refacing (per linear foot):                          $150–$300
Kitchen countertop — laminate replacement (per sqft installed):       $25–$50
Kitchen countertop — granite/quartz (per sqft installed):             $60–$100
Kitchen countertop — whole kitchen (30–40 sqft):                   $1,800–$4,000
Kitchen backsplash — subway tile, installed (per sqft):               $15–$30
Kitchen appliance package (range, dishwasher, microhood, basic):   $2,000–$4,000
Bathroom vanity replacement — hall bath (36", installed):            $500–$1,200
Bathroom vanity replacement — master bath (48–60", installed):       $800–$2,000
Bathroom tile — shower surround regrout/reseal:                      $200–$500
Bathroom tile — full shower retile (labor + materials):            $2,000–$4,500
Jetted/jacuzzi tub removal + walk-in shower conversion:            $4,500–$9,000
Flooring — LVP/LVT, whole house (2,019 sqft, materials + install): $5,000–$9,500
Flooring — carpet removal + LVP (per room, ~200 sqft):              $600–$1,400
Flooring — carpet replacement (per room, ~200 sqft):                $400–$900
Popcorn ceiling removal + smooth finish — whole house:             $2,500–$4,500
Crown molding installation (per room, labor + materials):            $400–$900
Crown molding — whole house:                                       $2,000–$4,500
Landscaping refresh — mulch, shrub trim, minor plantings:            $500–$1,500
Pressure wash — driveway + exterior:                                 $200–$500
Driveway crack repair — seal/fill (asphalt, whole driveway):         $300–$900
Driveway crack repair — concrete patch (sections):                   $400–$1,200
Roof drainage — gutter/downspout extension or French drain:          $400–$1,500
Fireplace service — gas log inspection + cleaning:                   $150–$350
Fireplace repair — non-functional gas fireplace:                     $300–$900
Exterior lighting — fixture replacement (per fixture):               $150–$400
Exterior lighting — whole-house refresh (6–10 fixtures):             $800–$2,000
Appliance assessment — technician visit (all major appliances):      $100–$250
Crawlspace access door — repair/replace:                             $150–$400
Staging — basic furniture rental (90 days):                        $1,500–$3,500"""


# Short fingerprint of the cost anchor table. Changes automatically when
# _GREENVILLE_COST_ANCHORS is edited — used to detect stale cached reports.
PROMPT_VERSION = hashlib.sha1(_GREENVILLE_COST_ANCHORS.encode()).hexdigest()[:8]

# Photo-analysis-derived facts that Gemini must treat as ground truth.
# These override any ambiguous flag language (e.g. "panel OR full door").
_KNOWN_REPAIR_FACTS = """\
KNOWN REPAIR FACTS — DERIVED FROM PHOTO ANALYSIS (treat as ground truth)
-------------------------------------------------------------------------
These are confirmed findings from direct photo inspection. Do not downgrade,
reinterpret, or substitute a cheaper alternative scope.

1. GARAGE DOOR — FULL REPLACEMENT REQUIRED
   Photos show structural crack penetrating through the panel material with
   spider-web fracture pattern, exposed void/interior, and panel separation
   at the crack. Inspector flagged: "recommend full door replacement."
   Scope: Replace full 2-car garage door (steel, insulated). Do NOT scope
   this as a panel repair or partial repair. Cost: $1,600–$2,400 installed.

2. WINDOW GLASS — BROKEN PANE REPLACEMENT
   Cracked/broken window glass confirmed in photos. Safety hazard.
   Scope: Replace broken pane(s). Cost: $150–$500 per window depending on type.\
"""

# ── Prompt builders ────────────────────────────────────────────────────────

_REPAIR_SEVERITY_RULES = """\
REPAIR SEVERITY RULES - apply these consistently:
Mark as CRITICAL if ANY of these are true:
- Item will fail FHA/USDA/conventional appraisal
- Item is a known SC disclosure requirement
- Item poses safety risk (electrical, structural, water intrusion)
- Item will trigger immediate price reduction demand from any buyer
- Item involves exposed wiring, cracked window glass, damaged \
garage door panels, active water stains, or moisture damage

Mark as HIGH if:
- Item will appear on home inspection report
- Buyer will request repair credit
- Item is visually obvious to any buyer

Never downgrade a critical item to high or medium based on \
estimated repair cost or ease of fix. Severity is about buyer \
impact, not difficulty."""

# Input item counts per detail level
_INPUT_COUNTS = {
    "spend_nothing": (10, 8),
    "budget_5k":     (15, 12),
    "budget_15k":    (20, 18),
    "maximize":      (20, 20),
}

_RECOMMENDATION_LIMITS = {
    "spend_nothing": {"max_upgrades": 1, "max_repairs": 4},
    "budget_5k":     {"max_upgrades": 4, "max_repairs": 3},
    "budget_15k":    {"max_upgrades": 6, "max_repairs": 5},
    "maximize":      {"max_upgrades": 10, "max_repairs": 8},
}

_DEEP_DIVE_EXHAUSTIVE = (
    "Include every upgrade and repair worth considering, even lower "
    "ROI items. The homeowner wants the complete picture."
)

# Comp-anchored ARV ceilings — grounded in River Ridge sold comps, not open-ended AI estimates.
# Subject is 2,019 sqft (largest in comp set); ceiling tops out at $305K (307 Blue Heron $301K).
_ARV_BY_LEVEL: dict[str, int] = {
    "spend_nothing": 295_000,
    "budget_5k":     298_000,
    "budget_15k":    300_000,
    "maximize":      305_000,
}
_DEFAULT_MARKET_VALUE = 276_810.0

# Tokens that indicate a repair addresses photo-flagged critical/high deal-risk issues.
_CRITICAL_REPAIR_TOKENS = frozenset({
    "water", "stain", "moisture", "leak", "intrusion", "mold",
    "garage", "door", "panel", "window", "glass", "crack", "broken",
    "deck", "post", "structural", "split", "electrical", "wiring", "safety",
})

_PRI_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _level_key(detail_level: str) -> str:
    return normalize_detail_level(detail_level)


def _max_upgrades(detail_level: str) -> int:
    return _RECOMMENDATION_LIMITS[_level_key(detail_level)]["max_upgrades"]


def _max_repairs(detail_level: str) -> int:
    return _RECOMMENDATION_LIMITS[_level_key(detail_level)]["max_repairs"]


def _market_value(property_summary: dict) -> float:
    """ATTOM AVM — baseline for net gain math."""
    try:
        v = property_summary.get("market_value")
        return float(v) if v is not None else _DEFAULT_MARKET_VALUE
    except (TypeError, ValueError):
        return _DEFAULT_MARKET_VALUE


def _comp_anchored_arv(detail_level: str) -> int:
    return _ARV_BY_LEVEL.get(_level_key(detail_level), 300_000)


def _total_investment(upgrades: list, repairs: list) -> float:
    total = sum(float(u.get("estimated_cost") or 0) for u in upgrades)
    total += sum(float(r.get("estimated_cost") or 0) for r in repairs)
    return total


def _normalize_upgrade(u: dict) -> dict:
    """Recalculate roi_percent from cost and value_add so row math is consistent."""
    out = dict(u)
    cost = float(out.get("estimated_cost") or 0)
    value_add = float(out.get("estimated_value_add") or 0)
    if cost > 0 and value_add > 0:
        out["roi_percent"] = round((value_add - cost) / cost * 100, 1)
    return out


def _issue_tokens(text: str) -> set[str]:
    return set(_norm_name(text).split())


def _repair_is_critical(repair: dict, summary: dict) -> bool:
    """True when repair clearly addresses a photo-flagged critical/high issue."""
    if repair.get("safety_concern"):
        return True
    blob = _norm_name(f"{repair.get('name', '')} {repair.get('description', '')}")
    words = set(blob.split())
    if not words:
        return False

    crit_issues = summary.get("critical_and_high_issues") or []
    for issue in crit_issues[:30]:
        overlap = words & _issue_tokens(issue)
        if len(overlap) >= 2:
            return True

    if len(words & _CRITICAL_REPAIR_TOKENS) >= 2:
        return True
    if words & {"water", "leak", "moisture", "stain", "intrusion"}:
        return True
    if words & {"garage", "door"} and words & {"panel", "damage", "dent", "crack"}:
        return True
    if words & {"window", "glass", "crack", "broken"}:
        return True
    if words & {"deck", "post"} and words & {"crack", "split", "structural"}:
        return True
    return False


def _enforce_repair_priorities(repairs: list, summary: dict) -> list:
    """Never let known deal-risk repairs be tagged below critical/high."""
    out: list[dict] = []
    for r in repairs:
        item = dict(r)
        pri = (item.get("priority") or "medium").lower()
        if _repair_is_critical(item, summary) and _PRI_ORDER.get(pri, 9) > _PRI_ORDER["high"]:
            item["priority"] = "critical"
        out.append(item)
    return out


def _sync_executive_summary(
    ex: dict,
    detail_level: str,
    property_summary: dict,
    upgrades: list,
    repairs: list,
) -> dict:
    """Override AI ARV/investment/net with comp-anchored values tied to line items."""
    out = dict(ex or {})
    market = _market_value(property_summary)
    arv = _comp_anchored_arv(detail_level)
    investment = _total_investment(upgrades, repairs)
    net = arv - market - investment

    out["current_value"] = round(market)
    out["estimated_arv"] = arv
    out["total_investment_low"] = round(investment)
    out["total_investment_high"] = round(investment * 1.10)
    out["net_gain_low"] = round(net - investment * 0.10)
    out["net_gain_high"] = round(net)
    return out


def _upgrades_instructions(detail_level: str, prior_count: int = 0) -> str:
    n = _max_upgrades(detail_level)
    shared = f"""1. Return max {n} upgrades total sorted by roi_percent descending (highest ROI first)
2. Use the estimated_arv and investment range from the assessment context for pricing
3. Use Greenville SC contractor rates (15-20% below national average)
4. Apply the tone rules from the detail level above"""

    if prior_count:
        add_n = max(0, n - prior_count)
        shared += f"""
5. You MUST include all {prior_count} prior-level upgrades listed above — do not replace or omit any
6. Add up to {add_n} additional upgrade(s) beyond the prior items to reach the max of {n} total"""

    level = _level_key(detail_level)
    if level == "spend_nothing":
        return shared + """
7. Upgrades are rare at this level — only if safety-critical
8. CRITICAL: Return ONLY the upgrades JSON object with rationale on every item."""

    if level == "budget_5k":
        return shared + """
7. Total estimated_cost across all upgrades MUST stay within $5,000
8. Prioritize confirmed and observed evidence tiers
9. CRITICAL: Return ONLY the upgrades JSON object with rationale on every item."""

    if level == "budget_15k":
        return shared + """
7. Total estimated_cost across all upgrades MUST stay within $15,000
8. Include prior-level must-fix context where relevant
9. CRITICAL: Return ONLY the upgrades JSON object with rationale on every item."""

    return shared + f"""
7. {_DEEP_DIVE_EXHAUSTIVE}
8. Optimize for maximum sale price — evidence-backed only
9. CRITICAL: Return ONLY the upgrades JSON object with rationale on every item."""


def _repairs_instructions(detail_level: str, prior_count: int = 0) -> str:
    n = _max_repairs(detail_level)
    shared = f"""1. Return max {n} repairs total sorted by priority (critical → high → medium → low)
2. Set each repair's priority field using the repair severity rules above
3. Use the investment range from the assessment context for pricing
4. Use Greenville SC contractor rates (15-20% below national average)
5. Apply the tone rules from the detail level above
6. Flag sc_disclosure_required=true for any item SC law requires sellers to disclose"""

    if prior_count:
        add_n = max(0, n - prior_count)
        shared += f"""
7. You MUST include all {prior_count} prior-level repairs listed above — do not replace or omit any
8. Add up to {add_n} additional repair(s) beyond the prior items to reach the max of {n} total"""

    level = _level_key(detail_level)
    if level == "spend_nothing":
        return shared + """
7. Transaction-risk repairs ONLY from walkthrough evidence — water stains, structural, drainage
8. Do NOT include inferred-tier repairs
9. CRITICAL: Return ONLY the repairs JSON object with rationale on every item."""

    if level == "budget_5k":
        return shared + """
7. Include must-fix repairs plus any repair that fits within overall $5K budget scenario
8. CRITICAL: Return ONLY the repairs JSON object with rationale on every item."""

    if level == "budget_15k":
        return shared + """
7. Include all spend_nothing repairs plus additional evidence-backed repairs
8. CRITICAL: Return ONLY the repairs JSON object with rationale on every item."""

    return shared + f"""
7. {_DEEP_DIVE_EXHAUSTIVE}
8. Include repair for every critical/high deal-risk issue from evidence
9. CRITICAL: Return ONLY the repairs JSON object with rationale on every item."""


def _freq_block(d: dict, top: int) -> str:
    """Format a frequency or weighted-score dict for prompt injection."""
    items = list(d.items())[:top]
    if not items:
        return "  (none)"
    lines = []
    for text, value in items:
        if isinstance(value, float):
            lines.append(f"  [{value:>5.1f} pts]  {text}")
        else:
            lines.append(f"  [{value:>3}x]  {text}")
    return "\n".join(lines)


def _list_block(items: list[str]) -> str:
    """Format a plain list of strings for prompt injection."""
    if not items:
        return "  (none)"
    return "\n".join(f"  - {text}" for text in items)


def _build_assessment_prompt(
    summary: dict,
    detail_level: str,
    buyer_profile: str,
    matrix_block: str = "",
) -> str:
    """Call 1: photo summary → executive_summary, deal_killers, timeline, SC notes, profile notes."""
    top_issues, top_upgrades = _INPUT_COUNTS[detail_level]

    total          = summary.get("total_photos", 0)
    total_issues   = summary.get("total_unique_issues", "?")
    total_upgrades_count = summary.get("total_unique_upgrades", "?")
    cond           = summary.get("condition_summary", {})
    finish         = summary.get("finish_quality_summary", {})
    risk           = summary.get("deal_risk_summary", {})
    dated_freq     = summary.get("dated_features_by_frequency", {})
    issues_freq    = summary.get("issues_by_frequency", {})
    upgrades_freq  = summary.get("upgrades_by_frequency", {})
    issues_room    = summary.get("issues_by_room", {})
    upgrades_room  = summary.get("upgrades_by_room", {})

    def room_block(d: dict, top_items: int) -> str:
        if not d:
            return "  (none)"
        lines = []
        for room, items in sorted(d.items()):
            for item in items[:top_items]:
                lines.append(f"  {room:20s}  {item}")
        return "\n".join(lines)

    cond_str   = "  " + ", ".join(f"{k}: {v}" for k, v in cond.items()) or "  (no data)"
    finish_str = "  " + ", ".join(f"{k}: {v}" for k, v in finish.items()) or "  (no data)"
    risk_str   = "  " + ", ".join(f"{k}: {v}" for k, v in risk.items()) or "  (no data)"

    # deep_dive uses a compact summary — room breakdown would waste token budget
    room_section = "" if _level_key(detail_level) == "maximize" else f"""
ISSUES BY ROOM (top {top_issues} per room)
------------------------------------------
{room_block(issues_room, top_issues)}

UPGRADES BY ROOM (top {top_upgrades} per room)
------------------------------------------------
{room_block(upgrades_room, top_upgrades)}"""

    return f"""You are preparing a pre-sale property assessment for 130 Kingfisher Dr, Simpsonville SC.
This is CALL 1 OF 3. Return ONLY the assessment sections — no upgrades list, no repairs list.

{_detail_block(detail_level)}

{_comp_anchoring_block(detail_level)}

{_profile_block(buyer_profile)}

{_PROPERTY_CONTEXT}

PHOTO ANALYSIS SUMMARY ({total} photos — {total_issues} unique issues, {total_upgrades_count} unique upgrades identified)
--------------------------------------------------------------------------------------------------------------------------
Condition distribution:
{cond_str}

Finish quality distribution:
{finish_str}

Deal risk distribution:
{risk_str}

DATED FEATURES BY FREQUENCY (top 15)
--------------------------------------
{_freq_block(dated_freq, 15)}

TOP {top_issues} ISSUES BY WEIGHTED SCORE
------------------------------------------
{_freq_block(issues_freq, top_issues)}

TOP {top_upgrades} UPGRADES BY FREQUENCY
------------------------------------------
{_freq_block(upgrades_freq, top_upgrades)}{room_section}

{matrix_block}

{_REPAIR_SEVERITY_RULES}

INSTRUCTIONS
------------
1. Apply the COMP ANCHORING rules above — they define what belongs at this detail level
2. High-frequency items are systemic problems across many rooms — weight them heavily
3. Use Greenville SC contractor rates (15-20% below national average)
4. Apply the detail level and buyer profile rules above
5. Apply the repair severity rules above when classifying deal_killers and \
assessing buyer impact in executive_summary
6. In executive_summary.recommendation, write for a homeowner in plain English and \
reference the comp anchoring frame (buyer objections / same-era comps / newer-build comps)
7. executive_summary.estimated_arv MUST be exactly ${_comp_anchored_arv(detail_level):,} \
(comp-anchored — do not exceed this ceiling)
8. executive_summary.current_value MUST be the ATTOM AVM (${_DEFAULT_MARKET_VALUE:,.0f} unless updated)
9. project_timeline should cover ONLY the upgrades and repairs selected at this detail level
10. Return ONLY the assessment JSON — no upgrades array, no repairs array

Return this exact JSON (no markdown, no explanation):

{_assessment_schema(detail_level)}"""


def generate_roi_report(
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    detail_level: str = "standard",
    buyer_profile: str = "general",
    prior_report: dict | None = None,
    walkthrough_block: str = "",
    matrix_block: str = "",
    matrix_line_items: dict[str, list] | None = None,
) -> dict:
    """
    Generate a pre-sale ROI report using three sequential Gemini calls (all levels):
      Call 1 — Assessment: executive_summary, deal_killers, timeline, SC notes
      Call 2 — Upgrades: sorted by ROI (executive 3 / standard 5 / deep_dive 8)
      Call 3 — Repairs:  sorted by priority (executive 3 / standard 5 / deep_dive 8)

    When prior_report is provided (standard or deep_dive), upgrades and repairs from
    the prior level are passed into the prompt and enforced in the merge step so each
    tab is additive and never contradicts the level below it.
    """
    detail_level = normalize_detail_level(detail_level)
    if detail_level not in DETAIL_LEVELS:
        return {**_ERROR_RESULT, "error": f"Invalid detail_level {detail_level!r}. Choose from: {sorted(DETAIL_LEVELS)}"}
    if buyer_profile not in BUYER_PROFILES:
        return {**_ERROR_RESULT, "error": f"Invalid buyer_profile {buyer_profile!r}. Choose from: {sorted(BUYER_PROFILES)}"}
    if not summary.get("total_photos"):
        return {**_ERROR_RESULT, "error": "Empty analysis summary — nothing to report on"}

    if not get_api_key():
        return {**_ERROR_RESULT, "error": "GEMINI_API_KEY environment variable is not set"}

    # ── Call 1: Assessment ─────────────────────────────────────────
    print("  [1/3] Assessment call...")
    # gemini-2.5-pro can spend much of the output budget on internal reasoning
    assessment, err = generate_text(
        _build_assessment_prompt(summary, detail_level, buyer_profile, matrix_block=matrix_block),
        system=SYSTEM_PROMPT,
        max_tokens=_PRO_MAX_TOKENS,
        label="Assessment",
    )
    if err:
        return {**_ERROR_RESULT, "error": err}

    executive_summary = assessment.get("executive_summary") or {}

    if matrix_line_items is not None:
        upgrades_result = {"upgrades": matrix_line_items.get("upgrades") or []}
        repairs_result = {"repairs": matrix_line_items.get("repairs") or []}
    else:
        # ── Call 2: Upgrades ───────────────────────────────────────────
        print("  [2/3] Upgrades call...")
        upgrades_result, err = generate_text(
            _build_upgrades_prompt(
                summary, executive_summary, detail_level, buyer_profile, prior_report,
                walkthrough_block=walkthrough_block,
            ),
            system=SYSTEM_PROMPT,
            max_tokens=_PRO_MAX_TOKENS,
            label="Upgrades",
        )
        if err:
            return {**_ERROR_RESULT, "error": err}

        # ── Call 3: Repairs ────────────────────────────────────────────
        print("  [3/3] Repairs call...")
        repairs_result, err = generate_text(
            _build_repairs_prompt(
                summary, executive_summary, detail_level, buyer_profile, prior_report,
                walkthrough_block=walkthrough_block,
            ),
            system=SYSTEM_PROMPT,
            max_tokens=_PRO_MAX_TOKENS,
            label="Repairs",
        )
        if err:
            return {**_ERROR_RESULT, "error": err}

    # ── Merge (additive when prior_report is set) ───────────────────
    limits   = _RECOMMENDATION_LIMITS[detail_level]
    upgrades: list = upgrades_result.get("upgrades", []) if isinstance(upgrades_result.get("upgrades"), list) else []
    repairs:  list = repairs_result.get("repairs",  []) if isinstance(repairs_result.get("repairs"), list) else []

    prior_upgrades = (prior_report or {}).get("upgrades") or []
    prior_repairs  = (prior_report or {}).get("repairs") or []

    upgrades = _merge_recommendations(
        prior_upgrades,
        upgrades,
        limits["max_upgrades"],
        sort_key=lambda u: float(u.get("roi_percent") or 0),
    )
    upgrades = [_normalize_upgrade(u) for u in upgrades]

    repairs = _merge_recommendations(
        prior_repairs,
        repairs,
        limits["max_repairs"],
        sort_key=lambda r: _PRI_ORDER.get((r.get("priority") or "low").lower(), 9),
        reverse=False,
    )
    repairs = _enforce_repair_priorities(repairs, summary)

    executive_summary = _sync_executive_summary(
        executive_summary, detail_level, property_summary, upgrades, repairs,
    )

    return {
        "detail_level":        assessment.get("detail_level",        detail_level),
        "buyer_profile":       assessment.get("buyer_profile",       buyer_profile),
        "level_description":   LEVEL_DESCRIPTIONS.get(detail_level, ""),
        "buyer_profile_notes": assessment.get("buyer_profile_notes", []),
        "executive_summary":   executive_summary,
        "project_timeline":    assessment.get("project_timeline"),
        "deal_killers":        assessment.get("deal_killers",        []),
        "sc_considerations":   assessment.get("sc_considerations",   []),
        "upgrades":            upgrades,
        "repairs":             repairs,
        "prompt_version":      PROMPT_VERSION,
        "report_source":       "matrix" if matrix_line_items is not None else "legacy",
    }


def levels_up_to(detail_level: str) -> list[str]:
    """Return budget scenarios from spend_nothing through the requested level."""
    detail_level = normalize_detail_level(detail_level)
    if detail_level not in DETAIL_LEVELS:
        return []
    idx = DETAIL_LEVEL_ORDER.index(detail_level)
    return DETAIL_LEVEL_ORDER[: idx + 1]


def generate_all_roi_reports(
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    buyer_profile: str = "general",
    walkthrough_block: str = "",
) -> dict[str, dict]:
    """
    Generate executive → standard → deep_dive in sequence so each level
    includes all items from the level below it.
    """
    reports: dict[str, dict] = {}
    prior: dict | None = None

    for level in DETAIL_LEVEL_ORDER:
        print(f"\n=== Generating [{level}] ===")
        report = generate_roi_report(
            summary,
            property_summary,
            last_sale,
            detail_level=level,
            buyer_profile=buyer_profile,
            prior_report=prior,
            walkthrough_block=walkthrough_block,
        )
        if report.get("error"):
            return {"error": report["error"], "partial": reports}
        reports[level] = report
        prior = report

    return reports

# ── On-demand deep detail ──────────────────────────────────────────────────

_ITEM_DETAIL_SCHEMA = """{
  "name": "<echo the item name>",
  "before_description": "<what it looks like now at 130 Kingfisher Dr>",
  "after_description": "<what it will look like when complete>",
  "step_by_step": [
    "1. <first step>",
    "2. <second step>"
  ],
  "product_recommendations": [
    "<brand and product line only — no model numbers or SKUs. Do not name a specific store. Include approx price range. Example: 'Clopay insulated steel 2-car garage door — $800–$1,200 materials'>"
  ],
  "contractor_questions": [
    "<question to ask when getting quotes from local contractors>"
  ],
  "common_mistakes": [
    "<mistake homeowners or contractors commonly make on this project>"
  ],
  "estimated_time_breakdown": {
    "prep": "<e.g. 2 hours>",
    "work": "<e.g. 1 day>",
    "cleanup_and_cure": "<e.g. 1 hour + 24h cure>",
    "total_contractor": "<e.g. 1-2 days>",
    "total_diy": "<e.g. 1 weekend>"
  }
}"""


def get_item_detail(name: str, item_type: str, description: str = "", issues: str = "") -> dict:
    """
    Generate deep how-to detail for a single upgrade or repair item.

    item_type: "upgrade" | "repair"
    description / issues: grounding context from the original photo-analysis report.
    Returns a detail dict, or a dict with an "error" key on failure.
    """
    if item_type not in ("upgrade", "repair"):
        return {"error": f"item_type must be 'upgrade' or 'repair', got {item_type!r}"}

    if not get_api_key():
        return {"error": "GEMINI_API_KEY environment variable is not set"}

    verb = "upgrading" if item_type == "upgrade" else "repairing"

    observed_block = ""
    if description or issues:
        parts = []
        if issues:
            parts.append(f"Observed issues from photo analysis: {issues}")
        if description:
            parts.append(f"Report summary: {description}")
        observed_block = "\n".join(parts) + "\n\nOnly expand on what was actually observed above. Do not invent symptoms or problems not mentioned.\n"

    prompt = f"""You are a home improvement expert advising on a pre-sale renovation at:
130 Kingfisher Dr, Simpsonville SC 29680 — 3 bed / 2 bath / 2,019 sqft / built 1999

Provide deep how-to detail for {verb}: "{name}"

{observed_block}Context:
- Target market: Greenville SC (labor rates 15-20% below national average)
- Goal: maximize resale value, target ARV $295,000-$305,000
- Local suppliers: Lowe's (1014 Woodruff Rd), Home Depot (2750 Laurens Rd), Floor & Decor (Greenville)
- Buyer pool: typical Greenville SC move-up buyers, expect home-inspection scrutiny

{_GREENVILLE_COST_ANCHORS}

Return this exact JSON (no markdown, no explanation):

{_ITEM_DETAIL_SCHEMA}"""

    result, err = generate_text(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=4096,
        label=f"ItemDetail:{name}",
        model=get_detail_model(),
    )
    if err:
        return {"error": err}
    return result


_MD_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")


def _rich_text(s: str) -> str:
    """Convert **bold** markdown to HTML <strong> tags (HTML-escaped otherwise)."""
    if "**" not in s:
        return html.escape(s, quote=False)
    escaped = html.escape(s, quote=False)
    return _MD_BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def _format_detail_value(value: Any) -> Any:
    if isinstance(value, str):
        return _rich_text(value)
    if isinstance(value, list):
        return [_format_detail_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _format_detail_value(v) for k, v in value.items()}
    return value


def format_item_detail_for_display(detail: dict) -> dict:
    """Apply markdown formatting to all string fields in a detail response."""
    return _format_detail_value(detail)
