"""
report_composer.py
------------------
Project matrix scenario and tier selections into ROI report line items.
Every line item carries matrix_row_id + matrix_option_id for traceability.
"""
from __future__ import annotations

from typing import Any

from scenario_selector import REPAIR_OPTION_KEYS, select_scenario_cumulative
from matrix_tiers import TIER_ORDER, normalize_tier

_OPTION_LABELS = {
    "leave_as_is": "Leave as-is",
    "clean": "Clean / remediate",
    "repair": "Repair",
    "refresh": "Refresh / update",
    "replace": "Replace",
    "further_inspect": "Further inspection",
}

_DEDUP_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}

TIER_TO_DETAIL_LEVEL = {
    "must_do": "spend_nothing",
    "should_do": "budget_5k",
    "nice_to_do": "maximize",
}

TIER_LABELS = {
    "must_do": "Must Do",
    "should_do": "Should Do",
    "nice_to_do": "Nice To Do",
}

_TIER_SORT = {t: i for i, t in enumerate(TIER_ORDER)}


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


def format_tier_evidence_block(
    rows: list[dict[str, Any]],
    tier_selection: dict[str, Any],
    *,
    limit: int = 64,
) -> str:
    """Matrix evidence for a listing-readiness tier — authoritative for assessment."""
    tier = tier_selection.get("tier") or "?"
    label = TIER_LABELS.get(tier, tier.replace("_", " ").title())
    selected_ids = {r.get("row_id") for r in tier_selection.get("selected_rows") or []}
    selected_full = [r for r in rows if r.get("id") in selected_ids]
    if not selected_full:
        return ""

    selection_by_row = {
        r.get("row_id"): r for r in tier_selection.get("selected_rows") or []
    }
    cost_lo = tier_selection.get("cost_low_total") or 0
    cost_hi = tier_selection.get("cost_high_total") or 0
    count = tier_selection.get("selected_count") or len(selected_full)

    lines = [
        f"LISTING READINESS TIER: {label.upper()} (matrix projection — authoritative for this report)",
        "--------------------------------------------------------------------------------",
        f"Selected {count} components | Estimated spend ${cost_lo:,.0f}–${cost_hi:,.0f}",
        "Base executive_summary.recommendation and market_position on these matrix selections.",
        "Photo analysis summary is supplementary context only — do not contradict the tier plan.",
        "",
        "SELECTED MATRIX ROWS:",
    ]

    sorted_rows = sorted(
        selected_full,
        key=lambda r: (
            {"required_action": 0, "decision_required": 1, "monitor": 2, "informational": 3}
            .get(r.get("decision_status") or "", 9),
            _TIER_SORT.get(r.get("minimum_tier"), 99),
            r.get("zone") or "",
            r.get("component") or "",
        ),
    )
    for row in sorted_rows[:limit]:
        sel = selection_by_row.get(row.get("id")) or {}
        comp = row.get("component") or "?"
        status = row.get("decision_status") or "?"
        min_t = row.get("minimum_tier") or "?"
        action = sel.get("option_key") or row.get("recommended_action") or "?"
        state = (row.get("current_state") or "")[:100]
        evidence_bits = []
        for src in (row.get("evidence_sources") or [])[:2]:
            text = (src.get("text") or "")[:80]
            if text:
                evidence_bits.append(f"{src.get('source') or 'evidence'}: {text}")
        ev = f" [{'; '.join(evidence_bits)}]" if evidence_bits else ""
        lines.append(f"  • [{status}|{min_t}] {comp} → {action}: {state}{ev}")

    if len(sorted_rows) > limit:
        lines.append(f"  … and {len(sorted_rows) - limit} more selected components")
    return "\n".join(lines)


def _important_leave_as_is(row: dict[str, Any]) -> bool:
    if row.get("decision_status") == "required_action":
        return True
    if row.get("minimum_tier") == "must_do":
        return True
    if row.get("inspection_risk") == "high":
        return True
    return False


def classify_tier_line_bucket(row: dict[str, Any], option_key: str) -> str | None:
    """Map tier selection to repairs, upgrades, or omit (leave_as_is)."""
    if option_key == "leave_as_is":
        return None
    status = row.get("decision_status") or ""
    if status == "required_action" and option_key in ("replace", "repair", "further_inspect"):
        return "repair"
    if option_key in ("clean", "refresh", "replace"):
        return "upgrade"
    if option_key in ("repair", "further_inspect"):
        return "repair"
    return "upgrade"


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
        "matrix_option_id": option.get("id"),
        "option_key": option_key,
        "traceability": {
            "matrix_row_id": row.get("id"),
            "option_id": option.get("id"),
            "matrix_option_id": option.get("id"),
            "component_id": row.get("component_id"),
            "evidence_sources": row.get("evidence_sources") or [],
        },
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


def compose_line_items_from_tier(
    rows: list[dict[str, Any]],
    tier_selection: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Turn tier selection into upgrades, repairs, and leave-as-is decision summary."""
    row_by_id = {r["id"]: r for r in rows if r.get("id")}
    upgrades: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    decision_summary: list[dict[str, Any]] = []

    for item in tier_selection.get("selected_rows") or []:
        row = row_by_id.get(item.get("row_id"))
        if not row:
            continue
        option_key = item.get("option_key") or ""
        options = row.get("options") or []
        opt = next(
            (o for o in options if o.get("option_key") == option_key),
            None,
        )
        if not opt:
            continue

        bucket = classify_tier_line_bucket(row, option_key)
        if bucket is None:
            if _important_leave_as_is(row):
                decision_summary.append({
                    "matrix_row_id": row.get("id"),
                    "component": row.get("component"),
                    "decision_status": row.get("decision_status"),
                    "minimum_tier": row.get("minimum_tier"),
                    "option_key": option_key,
                    "note": "Leave as-is — included in decision summary for seller awareness",
                })
            continue

        line = _line_item_from_selection(row, opt, item)
        if bucket == "repair":
            repairs.append(line)
        else:
            upgrades.append(line)

    repairs.sort(key=lambda r: _DEDUP_PRIORITY.get((r.get("priority") or "low").lower(), 9))
    upgrades.sort(key=lambda u: float(u.get("roi_percent") or 0), reverse=True)

    return {
        "upgrades": upgrades,
        "repairs": repairs,
        "decision_summary": decision_summary,
    }


def compose_report_from_tier(
    rows: list[dict[str, Any]],
    tier_selection: dict[str, Any],
    *,
    summary: dict[str, Any],
    property_summary: dict[str, Any],
    last_sale: dict[str, Any],
    buyer_profile: str = "general",
    walkthrough_block: str = "",
) -> dict[str, Any]:
    """Generate ROI report JSON from listing-readiness tier selection."""
    from roi import generate_roi_report

    tier = normalize_tier(tier_selection["tier"]) or tier_selection["tier"]
    detail_level = TIER_TO_DETAIL_LEVEL.get(tier, "maximize")
    line_items = compose_line_items_from_tier(rows, tier_selection)

    selected_ids = {r.get("row_id") for r in tier_selection.get("selected_rows") or []}
    selected_full = [r for r in rows if r.get("id") in selected_ids]
    matrix_block = format_tier_evidence_block(selected_full, tier_selection)

    report = generate_roi_report(
        summary,
        property_summary,
        last_sale,
        detail_level=detail_level,
        buyer_profile=buyer_profile,
        prior_report=None,
        walkthrough_block=walkthrough_block,
        matrix_block=matrix_block,
        matrix_line_items=line_items,
    )
    if report.get("error"):
        return report

    tier_label = TIER_LABELS.get(tier, tier)
    report["projection_source"] = "matrix_tier"
    report["matrix_id"] = tier_selection.get("matrix_id")
    report["tier"] = tier
    report["listing_readiness_tier"] = tier
    report["selected_row_count"] = tier_selection.get("selected_count")
    report["decision_summary"] = line_items.get("decision_summary") or []
    report["report_source"] = "matrix_tier"
    report["level_description"] = (
        f"Listing readiness: {tier_label} "
        f"({tier_selection.get('selected_count', 0)} components, "
        f"${tier_selection.get('cost_low_total', 0):,.0f}–"
        f"${tier_selection.get('cost_high_total', 0):,.0f})"
    )

    for bucket in ("upgrades", "repairs"):
        for item in report.get(bucket) or []:
            if item.get("option_id") and not item.get("matrix_option_id"):
                item["matrix_option_id"] = item["option_id"]

    return report


def compose_for_scenario(
    rows: list[dict[str, Any]],
    scenario: str,
    buyer_profile: str = "general",
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Select scenario and compose line items."""
    selection = select_scenario_cumulative(rows, scenario, buyer_profile)
    line_items = compose_line_items(rows, selection)
    return selection, line_items
