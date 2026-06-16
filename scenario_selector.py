"""
scenario_selector.py
--------------------
Select matrix rows + options per budget scenario.
Deterministic — no LLM.
"""
from __future__ import annotations

from typing import Any

from roi import DETAIL_LEVEL_ORDER, normalize_detail_level

SCENARIO_BUDGETS: dict[str, int | None] = {
    "spend_nothing": 2_000,
    "budget_5k": 5_000,
    "budget_15k": 15_000,
    "maximize": None,
}

STATUS_PRIORITY = {
    "required_action": 0,
    "decision_required": 1,
    "monitor": 2,
    "informational": 3,
}

INSP_PRIORITY = {"high": 0, "medium": 1, "low": 2}

REPAIR_OPTION_KEYS = frozenset({"repair", "further_inspect"})
SKIP_OPTION_KEYS = frozenset({"leave_as_is"})


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
    status = STATUS_PRIORITY.get(row.get("decision_status") or "", 9)
    insp = INSP_PRIORITY.get(row.get("inspection_risk") or "low", 9)
    buyer = {"high": 0, "medium": 1, "low": 2}.get(row.get("buyer_impact") or "medium", 9)
    opt = _selected_option(row)
    cost = float(opt.get("cost_high") or 0) if opt else 999_999
    return (status, insp, buyer, cost)


def _eligible_for_scenario(row: dict[str, Any], scenario: str) -> bool:
    status = row.get("decision_status") or ""
    opt = _selected_option(row)
    if not opt:
        return False
    if opt.get("option_key") in SKIP_OPTION_KEYS:
        return False

    if scenario == "spend_nothing":
        if status != "required_action":
            return False
        if row.get("inspection_risk") != "high" and opt.get("option_key") not in REPAIR_OPTION_KEYS:
            return False
        return True

    if status == "informational":
        return False

    if scenario == "budget_5k":
        return status in ("required_action", "decision_required") or (
            status == "monitor" and opt.get("option_key") == "further_inspect"
        )

    return True


def _exclusion_reason(row: dict[str, Any], scenario: str, budget: int | None, spent: float) -> str:
    opt = _selected_option(row)
    if not opt:
        return "no_options"
    if opt.get("option_key") in SKIP_OPTION_KEYS:
        return "leave_as_is_selected"
    if not _eligible_for_scenario(row, scenario):
        return f"not_in_{scenario}_scope"
    cost = float(opt.get("cost_high") or 0)
    if budget is not None and spent + cost > budget:
        return "over_budget"
    return "lower_priority"


def select_scenario(
    rows: list[dict[str, Any]],
    scenario: str,
    *,
    buyer_profile: str = "general",
    prior_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return selected rows for a scenario. Prior selection carries forward
    when scenario is cumulative (budget_15k includes spend_nothing items).
    """
    scenario = normalize_detail_level(scenario)
    budget = SCENARIO_BUDGETS.get(scenario)
    if scenario not in SCENARIO_BUDGETS:
        raise ValueError(f"Unknown scenario: {scenario!r}")

    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    spent = 0.0

    if prior_selection:
        for item in prior_selection.get("selected") or []:
            rid = item.get("row_id")
            if rid and rid not in seen_ids:
                selected.append(item)
                seen_ids.add(rid)
                spent += float(item.get("cost_high") or 0)

    candidates = [
        r for r in rows
        if r.get("id") not in seen_ids and _eligible_for_scenario(r, scenario)
    ]
    candidates.sort(key=_row_sort_key)

    for row in candidates:
        opt = _selected_option(row)
        if not opt or opt.get("option_key") in SKIP_OPTION_KEYS:
            excluded.append({
                "row_id": row.get("id"),
                "component": row.get("component"),
                "reason": "leave_as_is_selected",
            })
            continue

        cost_low = float(opt.get("cost_low") or 0)
        cost_high = float(opt.get("cost_high") or 0)

        if budget is not None and spent + cost_high > budget:
            excluded.append({
                "row_id": row.get("id"),
                "component": row.get("component"),
                "reason": "over_budget",
                "cost_high": cost_high,
                "remaining_budget": max(0, budget - spent),
            })
            continue

        selected.append({
            "row_id": row.get("id"),
            "matrix_row_id": row.get("id"),
            "component": row.get("component"),
            "zone": row.get("zone"),
            "decision_status": row.get("decision_status"),
            "option_key": opt.get("option_key"),
            "option_id": opt.get("id"),
            "cost_low": cost_low,
            "cost_high": cost_high,
            "seller_override": bool(row.get("seller_override")),
        })
        seen_ids.add(row.get("id"))
        spent += cost_high

    for row in rows:
        if row.get("id") in seen_ids:
            continue
        excluded.append({
            "row_id": row.get("id"),
            "component": row.get("component"),
            "reason": _exclusion_reason(row, scenario, budget, spent),
        })

    return {
        "scenario": scenario,
        "buyer_profile": buyer_profile,
        "budget_cap": budget,
        "selected": selected,
        "excluded": excluded,
        "total_cost_low": sum(float(s.get("cost_low") or 0) for s in selected),
        "total_cost_high": spent,
        "selection_count": len(selected),
    }


def select_scenario_cumulative(
    rows: list[dict[str, Any]],
    scenario: str,
    buyer_profile: str = "general",
) -> dict[str, Any]:
    """Build selection cumulatively through DETAIL_LEVEL_ORDER up to scenario."""
    scenario = normalize_detail_level(scenario)
    prior: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    for level in DETAIL_LEVEL_ORDER:
        result = select_scenario(rows, level, buyer_profile=buyer_profile, prior_selection=prior)
        if level == scenario:
            break
        prior = result
    if result is None:
        raise ValueError(f"Unknown scenario: {scenario!r}")
    return result
