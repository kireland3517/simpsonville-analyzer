"""
matrix_options.py
-----------------
Deterministic option generation per decision matrix row.
Costs from Greenville anchors — no LLM scoring.
"""
from __future__ import annotations

from typing import Any

from evidence import _norm
from walkthrough import _COMPONENT_COST_ANCHORS

OPTION_KEYS = (
    "leave_as_is", "clean", "repair", "refresh", "replace", "further_inspect",
)

# (component_substring, option_key) -> (low, high, anchor_label)
_COST_TABLE: list[tuple[str, str, int, int, str]] = [
    ("garage door", "replace", 1600, 2400, "greenville_garage_door_full"),
    ("garage door", "further_inspect", 150, 350, "greenville_garage_inspect"),
    ("countertop", "leave_as_is", 0, 0, "zero"),
    ("countertop", "refresh", 1800, 4000, "greenville_countertop_whole"),
    ("countertop", "replace", 1800, 4000, "greenville_countertop_whole"),
    ("indoor air quality", "clean", 500, 2500, "greenville_odor_remediation"),
    ("odor", "clean", 500, 2500, "greenville_odor_remediation"),
    ("smoke", "clean", 500, 2500, "greenville_odor_remediation"),
    ("deck", "repair", 600, 1200, "greenville_deck_boards"),
    ("deck", "further_inspect", 200, 500, "greenville_structural_inspect"),
    ("deck", "replace", 2500, 8000, "greenville_deck_rebuild"),
    ("crawlspace", "further_inspect", 200, 500, "greenville_crawl_inspect"),
    ("crawlspace", "repair", 800, 1800, "greenville_moisture_barrier"),
    ("ceiling water", "repair", 300, 900, "greenville_ceiling_water"),
    ("ceiling water", "further_inspect", 200, 500, "greenville_leak_investigate"),
    ("water damage", "repair", 300, 900, "greenville_ceiling_water"),
    ("popcorn ceiling", "refresh", 2500, 4500, "greenville_popcorn_whole"),
    ("popcorn ceiling", "leave_as_is", 0, 0, "zero"),
    ("pressure wash", "clean", 200, 500, "greenville_pressure_wash"),
    ("landscaping", "clean", 500, 1500, "greenville_landscaping"),
    ("driveway", "repair", 300, 1200, "greenville_driveway"),
    ("driveway", "refresh", 300, 900, "greenville_driveway_seal"),
    ("fireplace", "further_inspect", 150, 350, "greenville_fireplace_service"),
    ("fireplace", "repair", 300, 900, "greenville_fireplace_repair"),
    ("exterior lighting", "refresh", 800, 2000, "greenville_exterior_lighting"),
    ("hvac", "further_inspect", 100, 200, "greenville_hvac_tuneup"),
    ("electrical panel", "further_inspect", 200, 500, "greenville_panel_inspect"),
    ("plumbing", "further_inspect", 150, 350, "greenville_plumbing_minor"),
    ("water heater", "further_inspect", 100, 250, "greenville_appliance_assess"),
    ("smoke detector", "repair", 50, 150, "greenville_smoke_detector"),
    ("interior paint", "refresh", 3000, 5000, "greenville_interior_paint"),
    ("trim paint", "refresh", 200, 450, "greenville_trim_paint"),
    ("garage floor", "clean", 150, 400, "greenville_garage_clean"),
    ("garage walls", "clean", 200, 500, "greenville_garage_paint"),
    ("gutter", "repair", 400, 1500, "greenville_drainage"),
    ("drainage", "repair", 400, 1500, "greenville_drainage"),
    ("vanity", "refresh", 500, 2000, "greenville_vanity"),
    ("faucet", "repair", 150, 350, "greenville_faucet"),
    ("sink", "repair", 150, 500, "greenville_sink"),
    ("flooring", "refresh", 5000, 9500, "greenville_flooring"),
    ("front porch", "repair", 300, 1200, "greenville_front_porch"),
]

_DEFAULT_COSTS: dict[str, tuple[int, int, str]] = {
    "leave_as_is": (0, 0, "zero"),
    "clean": (200, 800, "greenville_generic_clean"),
    "repair": (200, 900, "greenville_generic_repair"),
    "refresh": (500, 2500, "greenville_generic_refresh"),
    "replace": (800, 3500, "greenville_generic_replace"),
    "further_inspect": (150, 400, "greenville_generic_inspect"),
}

_ODOR_SIGNALS = ("smoke odor", "cigarette", "tobacco", "odor", "air quality")
_REPLACE_SIGNALS = (
    "full replacement", "structural crack", "missing panel", "replacement required",
)


def _walkthrough_anchor(component: str) -> tuple[int, int] | None:
    comp = _norm(component)
    for key, lo, hi in _COMPONENT_COST_ANCHORS:
        if key in comp:
            return lo, hi
    return None


def _cost_for_option(component: str, option_key: str) -> tuple[int, int, str]:
    comp = _norm(component)
    for substr, key, lo, hi, label in _COST_TABLE:
        if key == option_key and substr in comp:
            return lo, hi, label
    if option_key == "refresh":
        anchor = _walkthrough_anchor(component)
        if anchor:
            return anchor[0], anchor[1], "walkthrough_anchor"
    if option_key == "replace":
        anchor = _walkthrough_anchor(component)
        if anchor:
            return int(anchor[0] * 1.2), int(anchor[1] * 1.4), "walkthrough_anchor_scaled"
    return _DEFAULT_COSTS.get(option_key, (200, 900, "greenville_generic"))


def _impact_for_option(
    option_key: str,
    row: dict[str, Any],
) -> tuple[str, str, str, str]:
    """buyer_impact, inspection_risk_impact, marketability_impact, roi_quality"""
    row_buyer = row.get("buyer_impact") or "medium"
    row_insp = row.get("inspection_risk") or "low"
    row_mkt = row.get("marketability_risk") or "medium"

    if option_key == "leave_as_is":
        buyer = "neutral" if row_buyer != "high" else "low"
        insp = "increases" if row_insp == "high" else "neutral"
        mkt = "low" if row_mkt == "high" else "neutral"
        roi = "low" if row.get("decision_status") == "required_action" else "none"
    elif option_key == "clean":
        buyer = "medium" if row_buyer in ("high", "medium") else "low"
        insp = "reduces" if row_insp != "low" else "neutral"
        mkt = "medium"
        roi = "medium"
    elif option_key == "repair":
        buyer = "medium" if row_buyer == "low" else "high"
        insp = "reduces"
        mkt = "medium"
        roi = "medium" if row_buyer != "high" else "high"
    elif option_key == "refresh":
        buyer = "high" if row_buyer == "high" else "medium"
        insp = "neutral"
        mkt = "high" if row_mkt in ("high", "medium") else "medium"
        roi = "high" if row_buyer == "high" else "medium"
    elif option_key == "replace":
        buyer = "high"
        insp = "reduces"
        mkt = "high"
        roi = "medium"
    else:  # further_inspect
        buyer = "low"
        insp = "neutral"
        mkt = "low"
        roi = "none"

    return buyer, insp, mkt, roi


def _rationale_for_option(option_key: str, row: dict[str, Any], cost_source: str) -> dict[str, Any]:
    comp = row.get("component") or ""
    refs = row.get("evidence_sources") or []
    reason_parts = [f"{option_key.replace('_', ' ')} for {comp}"]
    if row.get("current_state"):
        reason_parts.append(row["current_state"][:120])
    return {
        "reason": ". ".join(reason_parts)[:300],
        "cost_source": cost_source,
        "evidence_refs": refs[:5],
        "tier": row.get("confidence_tier") or "observed",
    }


def viable_option_keys(row: dict[str, Any]) -> list[str]:
    """Return only viable option keys for this row — not all six."""
    comp = _norm(row.get("component") or "")
    note = _norm(row.get("walkthrough_notes") or "")
    blob = _norm(f"{comp} {note} {row.get('current_state') or ''}")
    status = row.get("decision_status") or "decision_required"
    rec = row.get("recommended_action") or "further_inspect"

    if "garage door" in comp:
        return ["replace", "further_inspect"]

    if "countertop" in comp:
        if "serviceable" in blob or "no cracking" in blob:
            return ["leave_as_is", "refresh", "replace"]
        return ["refresh", "replace"]

    if "indoor air quality" in comp or any(s in blob for s in _ODOR_SIGNALS):
        return ["clean", "further_inspect"]

    if "crawlspace" in comp:
        return ["further_inspect", "repair"]

    if comp == "deck" or "deck condition" in comp:
        return ["further_inspect", "repair", "replace"]

    if "ceiling water" in comp or "water damage" in comp:
        if "source" in blob or "not confirmed" in blob:
            return ["further_inspect", "repair"]
        return ["repair", "refresh"]

    if status == "informational":
        return ["leave_as_is"]

    if status == "monitor":
        return ["further_inspect", "leave_as_is"]

    if status == "required_action":
        keys = [rec] if rec in OPTION_KEYS else ["repair"]
        if rec != "further_inspect" and "further_inspect" not in keys:
            if any(s in blob for s in ("unknown", "not confirmed", "evaluate", "assess")):
                keys.append("further_inspect")
        return list(dict.fromkeys(keys))

    # decision_required — offer meaningful choices
    if rec == "leave_as_is":
        return ["leave_as_is", "refresh", "repair"]
    if rec == "clean":
        return ["leave_as_is", "clean", "refresh"]
    if rec == "refresh":
        return ["leave_as_is", "refresh", "replace"]
    if rec == "repair":
        return ["leave_as_is", "repair", "replace"]
    if rec == "replace":
        return ["replace", "further_inspect"]
    if rec == "further_inspect":
        return ["further_inspect", "repair"]

    return ["leave_as_is", rec] if rec in OPTION_KEYS else ["further_inspect", "repair"]


def pick_recommended_option(options: list[dict[str, Any]], row: dict[str, Any]) -> str:
    if row.get("seller_override") and row.get("selected_option_key"):
        return row["selected_option_key"]
    preset = row.get("recommended_action")
    for opt in options:
        if opt["option_key"] == preset:
            return preset
    for opt in options:
        if opt.get("feasibility") == "recommended":
            return opt["option_key"]
    for opt in options:
        if opt.get("feasibility") == "viable":
            return opt["option_key"]
    return options[0]["option_key"] if options else "further_inspect"


def build_options_for_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    keys = viable_option_keys(row)
    preset_rec = row.get("recommended_action")
    options: list[dict[str, Any]] = []

    for key in keys:
        lo, hi, cost_source = _cost_for_option(row.get("component") or "", key)
        buyer, insp, mkt, roi = _impact_for_option(key, row)
        feasibility = "viable"
        if key == preset_rec:
            feasibility = "recommended"
        elif key == "leave_as_is" and row.get("decision_status") == "required_action":
            feasibility = "discouraged"
        elif key == "replace" and "garage door" in _norm(row.get("component") or ""):
            if key == "replace":
                feasibility = "recommended"

        options.append({
            "option_key": key,
            "cost_low": lo,
            "cost_high": hi,
            "buyer_impact": buyer,
            "inspection_risk_impact": insp,
            "marketability_impact": mkt,
            "roi_quality": roi,
            "feasibility": feasibility,
            "is_recommended": False,
            "rationale": _rationale_for_option(key, row, cost_source),
        })

    rec_key = pick_recommended_option(options, row)
    comp = _norm(row.get("component") or "")
    if "countertop" in comp and row.get("buyer_impact") == "high":
        refresh = next((o for o in options if o["option_key"] == "refresh"), None)
        if refresh and row.get("recommended_action") == "leave_as_is":
            rec_key = "refresh"
    for opt in options:
        opt["is_recommended"] = opt["option_key"] == rec_key
        if "countertop" in comp:
            if opt["option_key"] == rec_key:
                opt["feasibility"] = "recommended"
            elif opt["option_key"] == "leave_as_is" and rec_key != "leave_as_is":
                opt["feasibility"] = "viable"

    return options


def all_option_keys() -> frozenset[str]:
    return frozenset(OPTION_KEYS)


def blocked_option_keys(row: dict[str, Any], viable: list[str]) -> list[str]:
    return [k for k in OPTION_KEYS if k not in viable]
