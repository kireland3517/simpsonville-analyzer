"""
roi.py
------
Uses Gemini to generate a pre-sale ROI report from vision analysis
summary data. Supports three detail levels and six buyer profiles.

Requires GEMINI_API_KEY to be set in the environment.
"""
from __future__ import annotations

import json
from typing import Any

from gemini_client import generate_text, get_api_key, get_detail_model
# Valid parameter values
DETAIL_LEVELS  = {"executive", "standard", "deep_dive"}
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


# ── Detail-level blocks ────────────────────────────────────────────────────

def _detail_block(detail_level: str) -> str:
    """Tone and verbosity rules injected into every prompt."""
    if detail_level == "executive":
        return """\
DETAIL LEVEL: EXECUTIVE
-----------------------
Audience: a busy homeowner who wants the bottom line in plain English.
Tone: plain, non-technical, encouraging. No jargon.
- Descriptions: 1 sentence maximum each.
- No DIY notes, no time estimates, no materials."""

    if detail_level == "standard":
        return """\
DETAIL LEVEL: STANDARD
----------------------
Audience: a homeowner who wants a solid, actionable plan.
Tone: balanced, informative, practical.
- Descriptions: 1-2 sentences maximum each.
- Include diy_friendly flag and a 1-sentence diy_notes.
- Time estimates: 5 words max (e.g. "1-2 days")."""

    # deep_dive
    return """\
DETAIL LEVEL: DEEP DIVE
-----------------------
Audience: a detail-oriented homeowner who wants to understand each project fully.
Tone: forensic, precise, actionable. Write as if advising a client before listing.
- Descriptions: 2-3 sentences. Include location in the home and impact on buyer perception.
- Include diy_friendly flag and a 1-2 sentence diy_notes with skill context.
- Time estimates: concise (e.g. "2-3 days contractor / 1 weekend DIY")."""


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
    """JSON schema for Call 1 (Assessment) — standard and executive levels only."""
    timeline_block = "" if detail_level == "executive" else """
  "project_timeline": {
    "total_weeks_hired": <number>,
    "total_weeks_diy": <number>,
    "recommended_sequence": ["<project 1>", "..."],
    "parallel_projects": ["<projects that can run simultaneously>"],
    "notes": "<string>"
  },"""

    sc_block = "" if detail_level == "executive" else """
  "sc_considerations": ["<South Carolina-specific item buyers commonly flag>"],"""

    if detail_level == "executive":
        ex_fields = """\
    "current_value": <number>,
    "estimated_arv": <number>,
    "net_gain_low": <number>,
    "net_gain_high": <number>,
    "recommendation": "<plain English 2-3 sentence recommendation>",
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
) -> str:
    """Call 2: return upgrades array only (max 5, sorted by roi_percent desc)."""
    top_issues, top_upgrades = _INPUT_COUNTS[detail_level]
    issues_freq   = summary.get("issues_by_frequency", {})
    upgrades_freq = summary.get("upgrades_by_frequency", {})

    ex_json = json.dumps(executive_summary, indent=2)

    return f"""You are preparing upgrade recommendations for 130 Kingfisher Dr, Simpsonville SC.
This is CALL 2 OF 3. Return ONLY the upgrades array — no repairs, no other fields.

{_detail_block(detail_level)}

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
{_freq_block(upgrades_freq, top_upgrades)}

INSTRUCTIONS
------------
1. Return max 5 upgrades sorted by roi_percent descending (highest ROI first)
2. Use the estimated_arv and investment range from the assessment context for pricing
3. Use Greenville SC contractor rates (15-20% below national average)
4. Apply the tone rules from the detail level above
5. CRITICAL: Return ONLY the upgrades JSON object. No repairs. No explanation.
   Entire response must be valid JSON fitting in 2000 tokens.

Return this exact JSON (no markdown, no explanation):

{_upgrades_schema()}"""


def _build_repairs_prompt(
    summary: dict,
    executive_summary: dict,
    detail_level: str,
    buyer_profile: str,
) -> str:
    """Call 3: return repairs array only (max 5, sorted by priority)."""
    top_issues, _ = _INPUT_COUNTS[detail_level]
    issues_freq = summary.get("issues_by_frequency", {})
    crit_high   = summary.get("critical_and_high_issues", [])

    ex_json = json.dumps(executive_summary, indent=2)

    return f"""You are preparing repair recommendations for 130 Kingfisher Dr, Simpsonville SC.
This is CALL 3 OF 3. Return ONLY the repairs array — no upgrades, no other fields.

{_detail_block(detail_level)}

{_profile_block(buyer_profile)}

{_PROPERTY_CONTEXT}

ASSESSMENT CONTEXT (from prior analysis)
-----------------------------------------
{ex_json}

CRITICAL AND HIGH DEAL-RISK ISSUES (always include — do not omit)
-------------------------------------------------------------------
{_list_block(crit_high)}

TOP {top_issues} ISSUES BY WEIGHTED SCORE
------------------------------------------
{_freq_block(issues_freq, top_issues)}

{_REPAIR_SEVERITY_RULES}

INSTRUCTIONS
------------
1. Return max 5 repairs sorted by priority (critical → high → medium → low)
2. Set each repair's priority field using the repair severity rules above
3. Use the investment range from the assessment context for pricing
4. Use Greenville SC contractor rates (15-20% below national average)
5. Apply the tone rules from the detail level above
6. Flag sc_disclosure_required=true for any item SC law requires sellers to disclose
7. CRITICAL: Return ONLY the repairs JSON object. No upgrades. No explanation.
   Entire response must be valid JSON fitting in 2000 tokens.

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
    "deep_dive": (8,  6),   # fewer inputs → richer output per item
}


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
This is CALL 1 OF 2. Return ONLY the assessment sections — no upgrades list, no repairs list.

{_detail_block(detail_level)}

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
1. Goal: assess whether this property can reach the $295,000-$305,000 ARV ceiling
2. High-frequency items are systemic problems across many rooms — weight them heavily
3. Use Greenville SC contractor rates (15-20% below national average)
4. Apply the detail level and buyer profile rules above
5. Apply the repair severity rules above when classifying deal_killers and \
assessing buyer impact in executive_summary
6. Return ONLY the assessment JSON — no upgrades array, no repairs array

Return this exact JSON (no markdown, no explanation):

{_assessment_schema(detail_level)}"""


def generate_roi_report(
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    detail_level: str = "standard",
    buyer_profile: str = "general",
) -> dict:
    """
    Generate a pre-sale ROI report using three sequential Gemini calls (all levels):
      Call 1 — Assessment (2048 tokens): executive_summary, deal_killers,
                project_timeline, sc_considerations, buyer_profile_notes
      Call 2 — Upgrades  (2048 tokens): upgrades array, max 5, sorted by ROI
      Call 3 — Repairs   (2048 tokens): repairs array,  max 5, sorted by priority

    Differences between detail levels affect tone and input item counts only:
      executive:  top 10 issues/upgrades in prompt, concise 1-sentence descriptions
      standard:   top 20 issues/upgrades, 1-2 sentence descriptions
      deep_dive:  top  8 issues/6 upgrades (focused), 2-3 sentence descriptions
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
        _build_upgrades_prompt(summary, executive_summary, detail_level, buyer_profile),
        system=SYSTEM_PROMPT,
        max_tokens=_PRO_MAX_TOKENS,
        label="Upgrades",
    )
    if err:
        return {**_ERROR_RESULT, "error": err}

    # ── Call 3: Repairs ────────────────────────────────────────────
    print("  [3/3] Repairs call...")
    repairs_result, err = generate_text(
        _build_repairs_prompt(summary, executive_summary, detail_level, buyer_profile),
        system=SYSTEM_PROMPT,
        max_tokens=_PRO_MAX_TOKENS,
        label="Repairs",
    )
    if err:
        return {**_ERROR_RESULT, "error": err}

    # ── Merge ──────────────────────────────────────────────────────
    upgrades: list = upgrades_result.get("upgrades", [])
    repairs:  list = repairs_result.get("repairs",  [])

    if isinstance(upgrades, list):
        upgrades = sorted(
            upgrades,
            key=lambda u: float(u.get("roi_percent") or 0),
            reverse=True,
        )

    return {
        "detail_level":        assessment.get("detail_level",        detail_level),
        "buyer_profile":       assessment.get("buyer_profile",       buyer_profile),
        "buyer_profile_notes": assessment.get("buyer_profile_notes", []),
        "executive_summary":   executive_summary,
        "project_timeline":    assessment.get("project_timeline"),
        "deal_killers":        assessment.get("deal_killers",        []),
        "sc_considerations":   assessment.get("sc_considerations",   []),
        "upgrades":            upgrades,
        "repairs":             repairs,
    }

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
