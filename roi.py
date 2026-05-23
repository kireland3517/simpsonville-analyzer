"""
roi.py
──────
Uses Claude to generate a detailed ROI report from vision analysis results
and property data. Requires ANTHROPIC_API_KEY to be set in the environment.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import anthropic

MODEL = "claude-sonnet-4-5"

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
    "executive_summary":  None,
    "project_timeline":   None,
    "upgrades":           None,
    "repairs":            None,
    "deal_killers":       None,
    "sc_considerations":  None,
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Pull the first {...} block from the response and parse it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text!r}")
    return json.loads(match.group())


def _fmt(value: Any, fallback: str = "unknown") -> str:
    return str(value) if value is not None else fallback


# ─── Prompt ───────────────────────────────────────────────────────────────────

def _build_prompt(summary: dict) -> str:
    total   = summary.get("total_photos", 0)
    cond    = summary.get("condition_summary", {})
    finish  = summary.get("finish_quality_summary", {})
    issues_freq   = summary.get("issues_by_frequency", {})
    upgrades_freq = summary.get("upgrades_by_frequency", {})
    issues_room   = summary.get("issues_by_room", {})
    upgrades_room = summary.get("upgrades_by_room", {})

    def freq_block(freq_dict: dict, top: int = 30) -> str:
        items = list(freq_dict.items())[:top]
        if not items:
            return "  (none identified)"
        return "\n".join(f"  [{count:>3}x]  {text}" for text, count in items)

    def room_block(room_dict: dict) -> str:
        if not room_dict:
            return "  (none identified)"
        lines = []
        for room, items in sorted(room_dict.items()):
            for item in items:
                lines.append(f"  {room:20s}  {item}")
        return "\n".join(lines)

    cond_str   = "  " + ", ".join(f"{k}: {v}" for k, v in cond.items()) if cond else "  (no data)"
    finish_str = "  " + ", ".join(f"{k}: {v}" for k, v in finish.items()) if finish else "  (no data)"

    return f"""You are preparing a pre-sale renovation report for the owner of this property.

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
Realistic ARV ceiling: $295,000-$305,000 (102 Blue Heron is newer construction - not achievable for a 1999 build)

PHOTO ANALYSIS SUMMARY ({total} photos analyzed)
-------------------------------------------------
Overall condition distribution:
{cond_str}

Finish quality distribution:
{finish_str}

ISSUES BY FREQUENCY (count x issue text)
-----------------------------------------
{freq_block(issues_freq)}

UPGRADES BY FREQUENCY (count x upgrade text)
---------------------------------------------
{freq_block(upgrades_freq)}

ISSUES BY ROOM
--------------
{room_block(issues_room)}

UPGRADES BY ROOM
----------------
{room_block(upgrades_room)}

LOCAL SUPPLIERS (use for pricing)
----------------------------------
- Lowe's: 1014 Woodruff Rd, Greenville SC 29607
- Home Depot: 2750 Laurens Rd, Greenville SC 29607
- Floor & Decor: Greenville SC (flooring)
- Labor rates: Greenville SC is 15-20% below national average

INSTRUCTIONS
------------
1. Target audience: homeowner preparing to sell - not an investor
2. Goal: close the gap between $276,810 and the $295,000-$305,000 ARV ceiling
3. High-frequency issues (seen in many photos) indicate systemic problems - weight them more heavily
4. Use actual Greenville SC contractor and material rates
5. Be specific about what work needs to be done - not generic descriptions
6. Sort upgrades by roi_percent descending
7. Return at most 8 upgrades and 8 repairs - include deck/structural issues; they must not be omitted
8. Keep descriptions concise (1-2 sentences each); put detail in diy_notes only if diy_friendly is true

Return a single JSON object with this exact structure (no markdown, no explanation):

{{
  "executive_summary": {{
    "current_value": <number>,
    "estimated_arv": <number>,
    "total_investment_low": <number>,
    "total_investment_high": <number>,
    "net_gain_low": <number>,
    "net_gain_high": <number>,
    "recommendation": "<2-3 sentence recommendation for the seller>",
    "market_position": "<brief description of how this property sits vs comps>",
    "disclaimer": "This report is based on AI analysis of photos and public records. Cost estimates should be validated with local contractor quotes before making decisions."
  }},
  "project_timeline": {{
    "total_weeks_hired": <number>,
    "total_weeks_diy": <number>,
    "recommended_sequence": ["<project 1>", "<project 2>", "..."],
    "parallel_projects": ["<projects that can run simultaneously>"],
    "notes": "<string>"
  }},
  "upgrades": [
    {{
      "name": "<string>",
      "description": "<specific description of exactly what work is needed>",
      "materials_needed": ["<item with quantity>"],
      "materials_cost": <number>,
      "labor_cost": <number>,
      "estimated_cost": <number>,
      "estimated_value_add": <number>,
      "roi_percent": <number>,
      "priority": "<high|medium|low>",
      "diy_friendly": <true|false>,
      "diy_notes": "<specific reason why or why not>",
      "skill_level": "<beginner|intermediate|advanced|professional_only>",
      "time_estimate_contractor": "<e.g. 2-3 days>",
      "time_estimate_diy": "<e.g. 2 weekends or not recommended>",
      "tools_needed": ["<tool name>"],
      "local_suppliers": ["<store name and location>"]
    }}
  ],
  "repairs": [
    {{
      "name": "<string>",
      "description": "<specific description of the issue and fix>",
      "estimated_cost": <number>,
      "priority": "<critical|high|medium|low>",
      "diy_friendly": <true|false>,
      "diy_notes": "<string>",
      "time_estimate_contractor": "<string>",
      "time_estimate_diy": "<string>",
      "sc_disclosure_required": <true|false>,
      "sc_disclosure_note": "<why SC law requires or does not require disclosure>",
      "safety_concern": <true|false>,
      "safety_note": "<string, empty if not a safety concern>"
    }}
  ],
  "deal_killers": [
    "<item that could kill a sale or force a price reduction if not addressed>"
  ],
  "sc_considerations": [
    "<South Carolina-specific item buyers commonly flag in this market>"
  ]
}}"""


# ─── Main public function ─────────────────────────────────────────────────────

def generate_roi_report(
    summary: dict,
    property_summary: dict,
    last_sale: dict,
) -> dict:
    """
    Generate a detailed pre-sale ROI report using Claude.

    summary          -- pre-processed analysis summary from run_roi.build_analysis_summary()
                        Keys: total_photos, condition_summary, finish_quality_summary,
                        issues_by_frequency, upgrades_by_frequency,
                        issues_by_room, upgrades_by_room
    property_summary -- dict from attom.get_property_summary() (context only)
    last_sale        -- dict from attom.get_last_sale() (context only)

    Returns a dict with keys: executive_summary, project_timeline, upgrades,
    repairs, deal_killers, sc_considerations.
    On any failure returns those keys set to None plus an "error" key.
    """
    if not summary.get("total_photos"):
        return {**_ERROR_RESULT, "error": "Empty analysis summary — nothing to report on"}

    prompt = _build_prompt(summary)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {**_ERROR_RESULT, "error": "ANTHROPIC_API_KEY environment variable is not set"}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        return {**_ERROR_RESULT, "error": str(exc)}

    response_text = message.content[0].text
    try:
        result = _extract_json(response_text)
    except (ValueError, json.JSONDecodeError) as exc:
        return {**_ERROR_RESULT, "error": str(exc)}

    # Sort upgrades by roi_percent descending
    if isinstance(result.get("upgrades"), list):
        result["upgrades"] = sorted(
            result["upgrades"],
            key=lambda u: float(u.get("roi_percent") or 0),
            reverse=True,
        )

    return result
