"""
roi.py
──────
Uses Claude to generate a prioritized ROI report from vision analysis results
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
    "You are a real estate investment analyst specializing in residential renovation ROI. "
    "Given property data and condition findings, you produce prioritized repair and upgrade "
    "recommendations with realistic cost and value-add estimates."
)

NEIGHBORHOOD_CONTEXT = (
    "River Ridge subdivision, Simpsonville SC 29680. "
    "Recent comps range $265,000–$319,900. "
    "Subject market value $276,810."
)

_ERROR_RESULT: dict = {
    "estimated_arv": None,
    "upgrades":      None,
    "repairs":       None,
    "summary":       None,
}


# ─── Shared helpers (same pattern as analyzer.py) ────────────────────────────

def _extract_json(text: str) -> dict:
    """Pull the first {...} block from the response and parse it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text!r}")
    return json.loads(match.group())


def _fmt(value: Any, fallback: str = "unknown") -> str:
    """Return a clean string for a value that may be None."""
    return str(value) if value is not None else fallback


def _unique(items: list[str]) -> list[str]:
    """Deduplicate a list while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


# ─── Prompt builder ───────────────────────────────────────────────────────────

def _build_prompt(
    property_summary: dict,
    last_sale: dict,
    all_issues: list[str],
    all_upgrades: list[str],
) -> str:
    ps = property_summary
    ls = last_sale

    issues_block = (
        "\n".join(f"  - {i}" for i in all_issues)
        if all_issues else "  (none identified)"
    )
    upgrades_block = (
        "\n".join(f"  - {u}" for u in all_upgrades)
        if all_upgrades else "  (none identified)"
    )

    return f"""Property facts:
  Address:      {_fmt(ps.get("address"))}
  Sqft:         {_fmt(ps.get("sqft"))}
  Beds/Baths:   {_fmt(ps.get("beds"))} bed / {_fmt(ps.get("baths"))} bath
  Year built:   {_fmt(ps.get("year_built"))}
  Market value: ${_fmt(ps.get("market_value"))}
  Tax amount:   ${_fmt(ps.get("tax_amount"))} ({_fmt(ps.get("tax_year"))} tax year)

Last sale:
  Date:   {_fmt(ls.get("sale_date"))}
  Amount: ${_fmt(ls.get("sale_amount"))}
  Price/sqft: ${_fmt(ls.get("price_per_sqft"))}

Neighborhood context:
  {NEIGHBORHOOD_CONTEXT}

Condition issues found across photos:
{issues_block}

Upgrade opportunities found across photos:
{upgrades_block}

Return a JSON object with exactly these fields:
- estimated_arv: number (after repair value estimate in dollars)
- upgrades: list of objects, each with:
    - name: string
    - estimated_cost: number
    - estimated_value_add: number
    - roi_percent: number
    - priority: string, one of: high, medium, low
- repairs: list of objects, each with:
    - name: string
    - estimated_cost: number
    - priority: string, one of: critical, high, medium, low
- summary: string (2-3 sentence plain English summary)

Return only valid JSON, no explanation."""


# ─── Main public function ─────────────────────────────────────────────────────

def generate_roi_report(
    analyses: list[dict],
    property_summary: dict,
    last_sale: dict,
) -> dict:
    """
    Generate a prioritized ROI report from vision analysis results and property data.

    analyses        — list of dicts from analyzer.analyze_image(); entries with a
                      non-None "error" field are silently filtered out.
    property_summary — dict from attom.get_property_summary()
    last_sale        — dict from attom.get_last_sale()

    Returns a dict with keys: estimated_arv, upgrades, repairs, summary.
    On any failure returns those keys set to None plus an "error" key.
    """
    # 1. Filter out errored analyses
    clean = [a for a in analyses if a.get("error") is None]

    # 2. Aggregate issues and upgrades across all valid analyses
    raw_issues: list[str] = []
    raw_upgrades: list[str] = []
    for a in clean:
        if isinstance(a.get("issues"), list):
            raw_issues.extend(a["issues"])
        if isinstance(a.get("upgrades"), list):
            raw_upgrades.extend(a["upgrades"])

    all_issues = _unique(raw_issues)
    all_upgrades = _unique(raw_upgrades)

    # 3. Build prompt
    prompt = _build_prompt(property_summary, last_sale, all_issues, all_upgrades)

    # 4. Call Claude
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {**_ERROR_RESULT, "error": "ANTHROPIC_API_KEY environment variable is not set"}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=MODEL,
            max_tokens=2048,
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

    # 5. Sort upgrades by roi_percent descending
    if isinstance(result.get("upgrades"), list):
        result["upgrades"] = sorted(
            result["upgrades"],
            key=lambda u: float(u.get("roi_percent") or 0),
            reverse=True,
        )

    return result
