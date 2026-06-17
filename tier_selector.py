"""
tier_selector.py
----------------
Cumulative listing-readiness tier selection from decision matrix rows.
Deterministic — no LLM, no report projection.
"""
from __future__ import annotations

from typing import Any

from matrix_tiers import READINESS_TIERS, TIER_ORDER, normalize_tier

STATUS_PRIORITY = {
    "required_action": 0,
    "decision_required": 1,
    "monitor": 2,
    "informational": 3,
}

INSP_PRIORITY = {"high": 0, "medium": 1, "low": 2}


def _tier_index(tier: str | None) -> int:
    if tier in TIER_ORDER:
        return TIER_ORDER.index(tier)
    return 99


def tier_includes_row(minimum_tier: str | None, selected_tier: str) -> bool:
    """True when row minimum_tier is at or above urgency for the selected tier view."""
    minimum_tier = normalize_tier(minimum_tier)
    selected_tier = normalize_tier(selected_tier) or selected_tier
    if not minimum_tier or minimum_tier not in READINESS_TIERS:
        return False
    return _tier_index(minimum_tier) <= _tier_index(selected_tier)


def _selected_option(row: dict[str, Any]) -> dict[str, Any] | None:
    options = row.get("options") or []
    if not options:
        return None
    if row.get("seller_override") and row.get("selected_option_key"):
        for opt in options:
            if opt.get("option_key") == row["selected_option_key"]:
                return opt
    for opt in options:
        if opt.get("is_recommended"):
            return opt
    return options[0]


def _row_sort_key(row: dict[str, Any]) -> tuple:
    min_idx = _tier_index(normalize_tier(row.get("minimum_tier")))
    status = STATUS_PRIORITY.get(row.get("decision_status") or "", 9)
    insp = INSP_PRIORITY.get(row.get("inspection_risk") or "low", 9)
    opt = _selected_option(row)
    cost = float(opt.get("cost_high") or 0) if opt else 0
    return (min_idx, status, insp, cost)


def _exclusion_reason(row: dict[str, Any], selected_tier: str) -> str:
    min_t = normalize_tier(row.get("minimum_tier"))
    if not min_t:
        return "missing_minimum_tier"
    if min_t not in READINESS_TIERS:
        return "invalid_minimum_tier"
    if not tier_includes_row(min_t, selected_tier):
        return "below_tier"
    if not _selected_option(row):
        return "no_options"
    return "unknown"


def select_tier_from_rows(
    rows: list[dict[str, Any]],
    tier: str,
    *,
    matrix_id: str | None = None,
    property_id: str = "130_kingfisher",
) -> dict[str, Any]:
    """Select cumulative rows for a listing-readiness tier."""
    tier = normalize_tier(tier.strip().lower()) or ""
    if tier not in READINESS_TIERS:
        raise ValueError(f"Unknown tier: {tier!r}. Choose from: {sorted(READINESS_TIERS)}")

    selected_rows: list[dict[str, Any]] = []
    rows_excluded: list[dict[str, Any]] = []
    counts_by_minimum_tier: dict[str, int] = {t: 0 for t in TIER_ORDER}
    counts_by_recommended_action: dict[str, int] = {}
    counts_by_decision_status: dict[str, int] = {}

    sorted_rows = sorted(rows, key=_row_sort_key)

    for row in sorted_rows:
        min_t = normalize_tier(row.get("minimum_tier"))
        rec_t = normalize_tier(row.get("recommended_tier"))
        if min_t == "not_doing" or not tier_includes_row(min_t, tier):
            rows_excluded.append({
                "row_id": row.get("id"),
                "component": row.get("component"),
                "minimum_tier": min_t,
                "exclusion_reason": _exclusion_reason(row, tier),
            })
            continue

        opt = _selected_option(row)
        if not opt:
            rows_excluded.append({
                "row_id": row.get("id"),
                "component": row.get("component"),
                "minimum_tier": min_t,
                "exclusion_reason": "no_options",
            })
            continue

        cost_low = float(opt.get("cost_low") or 0)
        cost_high = float(opt.get("cost_high") or 0)
        action = opt.get("option_key") or row.get("recommended_action") or "?"

        selected_rows.append({
            "row_id": row.get("id"),
            "matrix_row_id": row.get("id"),
            "component": row.get("component"),
            "zone": row.get("zone"),
            "minimum_tier": min_t,
            "recommended_tier": rec_t,
            "decision_status": row.get("decision_status"),
            "recommended_action": row.get("recommended_action"),
            "option_key": action,
            "option_id": opt.get("id"),
            "cost_low": cost_low,
            "cost_high": cost_high,
            "rationale": opt.get("rationale") or {},
            "seller_override": bool(row.get("seller_override")),
        })

        if min_t in counts_by_minimum_tier:
            counts_by_minimum_tier[min_t] += 1
        counts_by_recommended_action[action] = counts_by_recommended_action.get(action, 0) + 1
        status = row.get("decision_status") or "?"
        counts_by_decision_status[status] = counts_by_decision_status.get(status, 0) + 1

    cost_low_total = sum(float(r.get("cost_low") or 0) for r in selected_rows)
    cost_high_total = sum(float(r.get("cost_high") or 0) for r in selected_rows)

    return {
        "property_id": property_id,
        "matrix_id": matrix_id,
        "tier": tier,
        "selected_rows": selected_rows,
        "selected_count": len(selected_rows),
        "cost_low_total": cost_low_total,
        "cost_high_total": cost_high_total,
        "counts_by_minimum_tier": counts_by_minimum_tier,
        "counts_by_recommended_action": counts_by_recommended_action,
        "counts_by_decision_status": counts_by_decision_status,
        "rows_excluded": rows_excluded,
        "excluded_count": len(rows_excluded),
    }


def select_for_tier(
    tier: str,
    *,
    sb,
    matrix_id: str | None = None,
    property_id: str = "130_kingfisher",
) -> dict[str, Any]:
    """
    Load matrix rows + options and return cumulative tier selection.
    """
    from decision_matrix import load_current_matrix, load_matrix_rows_with_options

    if sb is None:
        raise ValueError("Supabase client required")

    if matrix_id:
        resolved_id = matrix_id
    else:
        matrix = load_current_matrix(sb, property_id)
        if not matrix:
            raise ValueError(f"No decision matrix for property {property_id!r}")
        resolved_id = matrix["id"]

    rows = load_matrix_rows_with_options(sb, resolved_id)
    return select_tier_from_rows(
        rows,
        tier,
        matrix_id=resolved_id,
        property_id=property_id,
    )
