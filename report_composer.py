"""
report_composer.py
------------------
Project matrix scenario selections into ROI report line items.
Every line item carries matrix_row_id + option_id for traceability.
"""
from __future__ import annotations

from typing import Any

from scenario_selector import REPAIR_OPTION_KEYS, select_scenario_cumulative

_OPTION_LABELS = {
    "leave_as_is": "Leave as-is",
    "clean": "Clean / remediate",
    "repair": "Repair",
    "refresh": "Refresh / update",
    "replace": "Replace",
    "further_inspect": "Further inspection",
}

_DEDUP_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def format_matrix_evidence_block(rows: list[dict[str, Any]], limit: int = 40) -> str:
    """Compact matrix summary for assessment LLM call."""
    if not rows:
        return ""
    lines = [
        "DECISION MATRIX EVIDENCE (walkthrough + photo — authoritative for seller decisions)",
        "--------------------------------------------------------------------------------",
    ]
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            {"required_action": 0, "decision_required": 1, "monitor": 2, "informational": 3}
            .get(r.get("decision_status") or "", 9),
            r.get("zone") or "",
            r.get("component") or "",
        ),
    )
    for row in sorted_rows[:limit]:
        comp = row.get("component") or "?"
        status = row.get("decision_status") or "?"
        action = row.get("recommended_action") or "?"
        state = (row.get("current_state") or "")[:100]
        lines.append(f"  • [{status}] {comp} → {action}: {state}")
    if len(sorted_rows) > limit:
        lines.append(f"  … and {len(sorted_rows) - limit} more components")
    return "\n".join(lines)


def _value_add_estimate(cost: float, option_key: str, buyer_impact: str) -> float:
    if cost <= 0:
        return 0.0
    multipliers = {
        "replace": 0.55,
        "refresh": 0.75,
        "clean": 0.45,
        "repair": 0.35,
        "further_inspect": 0.1,
    }
    base = multipliers.get(option_key, 0.4)
    if buyer_impact == "high":
        base += 0.15
    elif buyer_impact == "low":
        base -= 0.1
    return round(cost * max(0.05, min(base, 0.9)), 0)


def _priority_from_row(row: dict[str, Any], option_key: str) -> str:
    if row.get("decision_status") == "required_action":
        return "critical" if row.get("inspection_risk") == "high" else "high"
    if option_key == "further_inspect":
        return "medium"
    if row.get("buyer_impact") == "high":
        return "high"
    return "medium"


def _rationale_from_row(row: dict[str, Any], option: dict[str, Any]) -> dict[str, Any]:
    refs = []
    for src in row.get("evidence_sources") or []:
        refs.append({
            "source": src.get("source"),
            "text": (src.get("text") or "")[:200],
        })
    rat = option.get("rationale") or {}
    return {
        "evidence": refs[:4],
        "tier": rat.get("tier") or row.get("confidence_tier") or "observed",
        "reason": rat.get("reason") or f"Matrix decision: {option.get('option_key')}",
        "expected_impact": f"Buyer impact {option.get('buyer_impact')}; inspection {option.get('inspection_risk_impact')}",
        "confidence": "high" if row.get("confidence_tier") == "confirmed" else "medium",
        "market_impact": option.get("marketability_impact") or row.get("marketability_risk") or "medium",
    }


def _line_item_from_selection(
    row: dict[str, Any],
    option: dict[str, Any],
    selection_item: dict[str, Any],
) -> dict[str, Any]:
    option_key = option.get("option_key") or selection_item.get("option_key")
    cost_low = float(selection_item.get("cost_low") or option.get("cost_low") or 0)
    cost_high = float(selection_item.get("cost_high") or option.get("cost_high") or 0)
    cost = round((cost_low + cost_high) / 2, 0)
    label = _OPTION_LABELS.get(option_key, option_key)
    name = f"{row.get('component') or 'Component'} — {label}"
    description = (row.get("current_state") or name)[:400]

    base = {
        "name": name,
        "description": description,
        "estimated_cost": cost,
        "matrix_row_id": row.get("id"),
        "option_id": option.get("id"),
        "option_key": option_key,
        "traceability": {
            "matrix_row_id": row.get("id"),
            "option_id": option.get("id"),
            "component_id": row.get("component_id"),
            "evidence_sources": row.get("evidence_sources") or [],
        },
        "rationale": _rationale_from_row(row, option),
    }

    if option_key in REPAIR_OPTION_KEYS:
        return {
            **base,
            "priority": _priority_from_row(row, option_key),
            "diy_friendly": option_key == "repair" and cost < 1500,
            "diy_notes": "Professional inspection recommended" if option_key == "further_inspect" else "",
            "time_estimate_contractor": "1–3 days" if option_key == "repair" else "2–4 hours",
            "time_estimate_diy": "N/A" if option_key == "further_inspect" else "1 weekend",
            "sc_disclosure_required": row.get("inspection_risk") == "high",
            "safety_concern": row.get("decision_status") == "required_action" and row.get("inspection_risk") == "high",
        }

    value_add = _value_add_estimate(cost, option_key, option.get("buyer_impact") or "medium")
    roi = round((value_add - cost) / cost * 100, 1) if cost > 0 else 0
    return {
        **base,
        "materials_cost": round(cost * 0.4, 0),
        "labor_cost": round(cost * 0.6, 0),
        "estimated_value_add": value_add,
        "roi_percent": roi,
        "priority": "high" if row.get("buyer_impact") == "high" else "medium",
        "diy_friendly": option_key in ("clean", "refresh") and cost < 2000,
        "diy_notes": "",
        "skill_level": "professional_only" if option_key == "replace" else "intermediate",
        "time_estimate_contractor": "1–5 days",
        "time_estimate_diy": "1–2 weekends",
    }


def compose_line_items(
    rows: list[dict[str, Any]],
    selection: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Turn scenario selection into upgrades + repairs arrays."""
    row_by_id = {r["id"]: r for r in rows if r.get("id")}
    upgrades: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []

    for item in selection.get("selected") or []:
        row = row_by_id.get(item.get("row_id"))
        if not row:
            continue
        options = row.get("options") or []
        opt = next(
            (o for o in options if o.get("option_key") == item.get("option_key")),
            None,
        )
        if not opt:
            continue
        line = _line_item_from_selection(row, opt, item)
        key = item.get("option_key")
        if key in REPAIR_OPTION_KEYS:
            repairs.append(line)
        elif key not in ("leave_as_is",):
            upgrades.append(line)

    repairs.sort(key=lambda r: _DEDUP_PRIORITY.get((r.get("priority") or "low").lower(), 9))
    upgrades.sort(key=lambda u: float(u.get("roi_percent") or 0), reverse=True)

    return {"upgrades": upgrades, "repairs": repairs}


def compose_for_scenario(
    rows: list[dict[str, Any]],
    scenario: str,
    buyer_profile: str = "general",
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Select scenario and compose line items."""
    selection = select_scenario_cumulative(rows, scenario, buyer_profile)
    line_items = compose_line_items(rows, selection)
    return selection, line_items
