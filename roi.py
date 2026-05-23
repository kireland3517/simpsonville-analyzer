"""
roi.py
------
Uses Gemini to generate a pre-sale ROI report from vision analysis
summary data. Supports three detail levels and six buyer profiles.

Requires GEMINI_API_KEY to be set in the environment.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

from gemini_client import generate_text, get_api_key, get_detail_model
# Valid parameter values
DETAIL_LEVELS  = {"executive", "standard", "deep_dive"}
DETAIL_LEVEL_ORDER = ["executive", "standard", "deep_dive"]

# Human labels used in additive carry-forward prompts
_LEVEL_LABELS = {
    "executive": "Quick Wins",
    "standard":  "Balanced Approach",
    "deep_dive": "Leave Nothing Behind",
}

# Homeowner-facing summary of what each tab means (also stored on generated reports)
LEVEL_DESCRIPTIONS = {
    "executive": (
        "These are the must-fix items so buyers don't walk away at the first showing — "
        "the highest-leverage repairs and upgrades a good listing agent would say to "
        "handle before you put a sign in the yard. Think \"fix these 3–4 things and list it\" — "
        "the smallest scope with the biggest impact, not a stripped-down report."
    ),
    "standard": (
        "Everything in Quick Wins, plus what buyers expect when comparing your home to "
        "similar-era neighbors in the $295K–$305K range. You're not over-improving — "
        "you're meeting market expectation. Homes like 4 Kingfisher Dr ($289K, built 1996) "
        "and 305 Kingfisher Dr ($265K) set the bar: smooth ceilings, updated hardware, "
        "neutral paint. That's what this market expects from a well-prepared 1999 home."
    ),
    "deep_dive": (
        "Everything in Balanced Approach, plus what it takes to compete with newer homes "
        "for top dollar. Anchored to 307 Blue Heron Cir ($301K, built 2007) — full flooring, "
        "kitchen counters, jacuzzi-to-shower conversion, and the finishes buyers see in "
        "2000s-built comps. At this level you're competing with newer construction, not "
        "just same-age homes."
    ),
}
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
    """
    prior = prior or []
    seen = {_norm_name(item.get("name", "")) for item in prior if item.get("name")}
    merged = list(prior)

    extras: list[dict] = []
    for item in new:
        key = _norm_name(item.get("name", ""))
        if not key or key in seen:
            continue
        seen.add(key)
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

def _detail_block(detail_level: str) -> str:
    """Tone and verbosity rules injected into every prompt."""
    if detail_level == "executive":
        return """\
DETAIL LEVEL: QUICK WINS (executive)
------------------------------------
Audience: a homeowner who wants the smallest high-impact scope before listing.
Tone: plain, non-technical, encouraging. No jargon.
- Scope: ONLY the highest-leverage items (max 3 upgrades, 3 repairs) — least work, biggest impact.
- Descriptions: 1-2 sentences maximum each.
- Include diy_friendly flag and a 1-sentence diy_notes.
- Time estimates: 5 words max (e.g. "1-2 days").
- Same actionable detail as other levels — Quick Wins means fewer projects, not less information."""

    if detail_level == "standard":
        return """\
DETAIL LEVEL: BALANCED APPROACH (standard)
------------------------------------------
Audience: a homeowner who wants a solid, actionable plan.
Tone: balanced, informative, practical.
- Descriptions: 1-2 sentences maximum each.
- Include diy_friendly flag and a 1-sentence diy_notes.
- Time estimates: 5 words max (e.g. "1-2 days")."""

    # deep_dive
    return """\
DETAIL LEVEL: LEAVE NOTHING BEHIND (deep_dive)
----------------------------------------------
Audience: a detail-oriented homeowner who wants to understand each project fully.
Tone: forensic, precise, actionable. Write as if advising a client before listing.
- Descriptions: 2-3 sentences. Include location in the home and impact on buyer perception.
- Include diy_friendly flag and a 1-2 sentence diy_notes with skill context.
- Time estimates: concise (e.g. "2-3 days contractor / 1 weekend DIY")."""


def _comp_anchoring_block(detail_level: str) -> str:
    """Comp and market framing injected into every prompt — drives item selection."""
    if detail_level == "executive":
        return """\
COMP ANCHORING — QUICK WINS
---------------------------
Goal: fix what loses buyers at the first showing. Highest leverage only.
A good listing agent would say: "Fix these 3–4 things and list it."

Anchor to: buyer objections at ANY price point — not a renovation plan.
Prioritize deal-killers and first-impression problems: water stains, broken glass,
damaged garage doors, safety hazards, obvious deferred maintenance.
Do NOT recommend projects just because comps have them. Recommend what stops
buyers from making an offer or triggers immediate price reduction demands."""

    if detail_level == "standard":
        return """\
COMP ANCHORING — BALANCED APPROACH
-----------------------------------
Goal: meet market expectation in the $295,000–$305,000 range without over-improving.
You are not trying to beat newer construction — you are matching what successful
same-era listings delivered.

Primary comps (same subdivision, similar build era):
- 4 Kingfisher Dr:    3/2, 1,891 sqft, built 1996, sold Apr 2026 — $289,000
- 305 Kingfisher Dr:  3/2, 1,382 sqft, built 2000, sold Apr 2024 — $265,000

Market bar at this price point: smooth ceilings (no popcorn), updated hardware,
neutral paint — the condition buyers expect walking through a well-prepared listing.
307 Blue Heron Cir ($301K, Dec 2025) illustrates the finish level buyers compare
against: updated surfaces, cohesive neutrals, no dated fixtures screaming "1999."

Include all Quick Wins items plus medium-priority updates that close the gap to
what these comps likely had at sale. Cosmetic upgrades belong here; exhaustive
renovation belongs at Leave Nothing Behind."""

    # deep_dive
    return """\
COMP ANCHORING — LEAVE NOTHING BEHIND
-------------------------------------
Goal: compete for top dollar against newer construction, not just same-age homes.
The seller wants maximum ARV — every meaningful gap to newer comps should be addressed.

Primary comp (newer build, top of realistic ARV range):
- 307 Blue Heron Cir: 3/2, 1,782 sqft, built 2007, sold Dec 2025 — $301,000

Reference same-era floor (from Balanced Approach):
- 4 Kingfisher Dr ($289K, 1996) and 305 Kingfisher Dr ($265K, 2000)

At this level include all Balanced Approach items plus upgrades that close the
1999-vs-2007 gap: jacuzzi/jetted tub to walk-in shower conversion, full flooring
replacement, kitchen countertop upgrade, and other finishes buyers expect in a
2000s-built home. You are positioning against newer construction on Woodruff Road
corridor comps, not merely matching minimum market expectation."""


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

    if detail_level == "executive":
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



def _upgrades_schema() -> str:
    """Schema for Call 2 — upgrades only (no sc_disclosure_note, tools_needed, local_suppliers)."""
    return """{
  "upgrades": [
    {
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
      "time_estimate_diy": "<string>"
    }
  ]
}"""


def _repairs_schema() -> str:
    """Schema for Call 3 — repairs only (no sc_disclosure_note, safety_note verbosity)."""
    return """{
  "repairs": [
    {
      "name": "<string>",
      "description": "<string>",
      "estimated_cost": <number>,
      "priority": "<critical|high|medium|low>",
      "diy_friendly": <true|false>,
      "diy_notes": "<string>",
      "time_estimate_contractor": "<string>",
      "time_estimate_diy": "<string>",
      "sc_disclosure_required": <true|false>,
      "safety_concern": <true|false>
    }
  ]
}"""


def _build_upgrades_prompt(
    summary: dict,
    executive_summary: dict,
    detail_level: str,
    buyer_profile: str,
    prior_report: dict | None = None,
) -> str:
    """Call 2: return upgrades array only."""
    top_issues, top_upgrades = _INPUT_COUNTS[detail_level]
    issues_freq   = summary.get("issues_by_frequency", {})
    upgrades_freq = summary.get("upgrades_by_frequency", {})

    ex_json = json.dumps(executive_summary, indent=2)
    prior_block = _prior_items_block(prior_report, detail_level) if prior_report else ""
    prior_count = len((prior_report or {}).get("upgrades") or [])

    dated_section = ""
    if detail_level == "deep_dive":
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
) -> str:
    """Call 3: return repairs array only."""
    top_issues, _ = _INPUT_COUNTS[detail_level]
    issues_freq = summary.get("issues_by_frequency", {})
    crit_high   = summary.get("critical_and_high_issues", [])

    ex_json = json.dumps(executive_summary, indent=2)
    prior_block = _prior_items_block(prior_report, detail_level) if prior_report else ""
    prior_count = len((prior_report or {}).get("repairs") or [])

    crit_header = "CRITICAL AND HIGH DEAL-RISK ISSUES (always include — do not omit)"
    if detail_level == "deep_dive":
        crit_header = (
            "CRITICAL AND HIGH DEAL-RISK ISSUES "
            "(MUST ALL appear in repairs — consolidate if needed, omit none)"
        )

    return f"""You are preparing repair recommendations for 130 Kingfisher Dr, Simpsonville SC.
This is CALL 3 OF 3. Return ONLY the repairs array — no upgrades, no other fields.

{_detail_block(detail_level)}

{_comp_anchoring_block(detail_level)}

{_profile_block(buyer_profile)}

{_PROPERTY_CONTEXT}

ASSESSMENT CONTEXT (from prior analysis)
-----------------------------------------
{ex_json}

{crit_header}
-------------------------------------------------------------------
{_list_block(crit_high)}

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
    # (top_issues, top_upgrades)
    "executive": (10, 10),
    "standard":  (20, 20),
    "deep_dive": (20, 20),
}

# Max items returned per recommendation call
_RECOMMENDATION_LIMITS = {
    "executive":  {"max_upgrades": 3, "max_repairs": 3},
    "standard":   {"max_upgrades": 5, "max_repairs": 5},
    "deep_dive":  {"max_upgrades": 8, "max_repairs": 8},
}

_DEEP_DIVE_EXHAUSTIVE = (
    "Include every upgrade and repair worth considering, even lower "
    "ROI items. The homeowner wants the complete picture."
)


def _max_upgrades(detail_level: str) -> int:
    return _RECOMMENDATION_LIMITS[detail_level]["max_upgrades"]


def _max_repairs(detail_level: str) -> int:
    return _RECOMMENDATION_LIMITS[detail_level]["max_repairs"]


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

    if detail_level == "executive":
        return shared + """
7. Select ONLY items that fix buyer objections at first showing — highest leverage, not comp-matching cosmetics
8. CRITICAL: Return ONLY the upgrades JSON object. No repairs. No explanation."""

    if detail_level == "standard":
        next_n = 8 if prior_count else 6
        return shared + f"""
{next_n}. Add medium-priority upgrades that match same-era comp expectations (4 Kingfisher, 305 Kingfisher bar)
{next_n + 1}. CRITICAL: Return ONLY the upgrades JSON object. No repairs. No explanation."""

    # deep_dive
    next_n = 9 if prior_count else 5
    return shared + f"""
{next_n}. {_DEEP_DIVE_EXHAUSTIVE}
{next_n + 1}. Add upgrades that close the 1999-vs-2007 gap per the 307 Blue Heron comp anchor
{next_n + 2}. You MUST include a jetted/jacuzzi tub conversion as one upgrade: remove the \
master bath jetted tub and convert to a walk-in tile shower with frameless glass
{next_n + 3}. CRITICAL: Return ONLY the upgrades JSON object. No repairs. No explanation."""


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

    if detail_level == "executive":
        next_n = 9 if prior_count else 7
        return shared + f"""
{next_n}. Include ONLY repairs that kill deals at first showing — buyer objections, not comp-matching cosmetics
{next_n + 1}. CRITICAL: Return ONLY the repairs JSON object. No upgrades. No explanation."""

    if detail_level == "standard":
        next_n = 9 if prior_count else 7
        return shared + f"""
{next_n}. Add repairs needed to meet same-era comp bar (4 Kingfisher, 305 Kingfisher expectations)
{next_n + 1}. CRITICAL: Return ONLY the repairs JSON object. No upgrades. No explanation."""

    # deep_dive
    next_n = 11 if prior_count else 7
    return shared + f"""
{next_n}. {_DEEP_DIVE_EXHAUSTIVE}
{next_n + 1}. You MUST include a repair entry for EVERY issue listed under CRITICAL AND HIGH \
DEAL-RISK ISSUES — consolidate related issues into shared repair entries where sensible, \
but do not omit any critical/high issue
{next_n + 2}. Include lower-priority repairs that standard would skip, up to the max of {n} entries
{next_n + 3}. CRITICAL: Return ONLY the repairs JSON object. No upgrades. No explanation."""


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


def _build_assessment_prompt(summary: dict, detail_level: str, buyer_profile: str) -> str:
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
    room_section = "" if detail_level == "deep_dive" else f"""
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
7. project_timeline should cover ONLY the upgrades and repairs selected at this detail level
8. Return ONLY the assessment JSON — no upgrades array, no repairs array

Return this exact JSON (no markdown, no explanation):

{_assessment_schema(detail_level)}"""


def generate_roi_report(
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    detail_level: str = "standard",
    buyer_profile: str = "general",
    prior_report: dict | None = None,
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
        _build_assessment_prompt(summary, detail_level, buyer_profile),
        system=SYSTEM_PROMPT,
        max_tokens=_PRO_MAX_TOKENS,
        label="Assessment",
    )
    if err:
        return {**_ERROR_RESULT, "error": err}

    executive_summary = assessment.get("executive_summary") or {}

    # ── Call 2: Upgrades ───────────────────────────────────────────
    print("  [2/3] Upgrades call...")
    upgrades_result, err = generate_text(
        _build_upgrades_prompt(
            summary, executive_summary, detail_level, buyer_profile, prior_report,
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

    _pri_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    repairs = _merge_recommendations(
        prior_repairs,
        repairs,
        limits["max_repairs"],
        sort_key=lambda r: _pri_order.get((r.get("priority") or "low").lower(), 9),
        reverse=False,
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
    }


def levels_up_to(detail_level: str) -> list[str]:
    """Return detail levels from executive through the requested level."""
    if detail_level not in DETAIL_LEVELS:
        return []
    idx = DETAIL_LEVEL_ORDER.index(detail_level)
    return DETAIL_LEVEL_ORDER[: idx + 1]


def generate_all_roi_reports(
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    buyer_profile: str = "general",
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
    "<specific product name — brand, model/SKU if applicable, where to buy locally, approx price>"
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


def get_item_detail(name: str, item_type: str) -> dict:
    """
    Generate deep how-to detail for a single upgrade or repair item.

    item_type: "upgrade" | "repair"
    Returns a detail dict, or a dict with an "error" key on failure.
    """
    if item_type not in ("upgrade", "repair"):
        return {"error": f"item_type must be 'upgrade' or 'repair', got {item_type!r}"}

    if not get_api_key():
        return {"error": "GEMINI_API_KEY environment variable is not set"}

    verb = "upgrading" if item_type == "upgrade" else "repairing"
    prompt = f"""You are a home improvement expert advising on a pre-sale renovation at:
130 Kingfisher Dr, Simpsonville SC 29680 — 3 bed / 2 bath / 2,019 sqft / built 1999

Provide deep how-to detail for {verb}: "{name}"

Context:
- Target market: Greenville SC (labor rates 15-20% below national average)
- Goal: maximize resale value, target ARV $295,000-$305,000
- Local suppliers: Lowe's (1014 Woodruff Rd), Home Depot (2750 Laurens Rd), Floor & Decor (Greenville)
- Buyer pool: typical Greenville SC move-up buyers, expect home-inspection scrutiny

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
        return html.escape(s)
    escaped = html.escape(s)
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
