"""
roi_engine.py
─────────────
Deterministic ROI scenario math engine.

All computation is pure Python — no LLM calls, no DB writes to matrix tables.
The engine reads decision_matrix_rows and decision_matrix_options (never writes them)
and writes only to roi_item_overrides / roi_seller_inputs / roi_report_snapshots.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Literal, Optional

# ─── Constants ────────────────────────────────────────────────────────────────

# Seller input defaults used when no row exists in roi_seller_inputs
_DEFAULT_COMMISSION_PCT = 5.5
_DEFAULT_CLOSING_COSTS  = 3_500.0

ScenarioName = Literal["must-do-only", "highest-roi", "full-recommended", "custom"]

# Mapping from decision_matrix_rows.minimum_tier to roi_bucket vocabulary
_TIER_TO_BUCKET: dict[str, str] = {
    "must_do":    "must_do",
    "should_do":  "should_do",
    "nice_to_do": "nice_to_do",
}

# Option types where no value estimate is meaningful (inspection pending or no work done).
_NO_ESTIMATE_OPTIONS = {"further_inspect", "leave_as_is"}

# Component-level Cost vs. Value recoup rates — South Atlantic / Greenville SC (2024).
# Value: fraction of cost recouped at resale (e.g. 0.98 = 98 cents per dollar spent).
# Source: Remodeling Magazine Cost vs. Value 2024, South Atlantic region.
_COMPONENT_RECOUP: dict[str, float] = {
    # ── Exterior ──────────────────────────────────────────────────────────────
    "Deck":                                           0.65,   # wood deck replacement
    "Driveway cracks":                                0.85,   # driveway reseal / refresh
    "Exterior lighting":                              0.80,   # exterior lighting upgrade
    "Front porch repair / repaint":                   0.90,   # entry/porch curb appeal
    "Landscaping / yard":                             0.95,   # curb appeal landscaping
    "Pressure wash — house / driveway / deck":   1.00,   # preserves value, low cost
    "Window Screens":                                 0.80,   # window screen replacement
    # ── Garage ────────────────────────────────────────────────────────────────
    "Garage door":                                    0.98,   # #1 ROI project South Atlantic
    "Garage floor":                                   0.75,   # garage floor clean/epoxy
    "Garage walls":                                   0.70,   # garage wall paint/clean
    "Garage walls, ceiling":                          0.70,
    # ── Great Room ────────────────────────────────────────────────────────────
    "Ceiling fan":                                    0.80,   # ceiling fan refresh
    # ── Interior Doors ────────────────────────────────────────────────────────
    "Door hardware":                                  0.80,   # hardware refresh
    # ── Kitchen ───────────────────────────────────────────────────────────────
    "Cabinets":                                       0.75,   # minor kitchen remodel proxy
    "Cabinets, hardware, faucet":                     0.75,
    "Countertops":                                    0.72,   # countertop refresh
    "Lighting":                                       0.75,   # kitchen lighting upgrade
    # ── Primary Bathroom ──────────────────────────────────────────────────────
    "Bath modernization":                             0.65,   # mid-range bathroom remodel
    "Double vanity with mirrors":                     0.70,
    "Modernize and replace fan/cover":                0.65,
    "Vanity cabinet":                                 0.68,
    "Vanity mirror":                                  0.75,
    # ── Guest Bathroom ────────────────────────────────────────────────────────
    "Vanity, mirror, toilet lever, fan/cover":        0.70,
    # ── Primary Bedroom ───────────────────────────────────────────────────────
    "Closet paint":                                   0.90,
    # ── Whole House ───────────────────────────────────────────────────────────
    "Ceiling water damage":                           0.85,   # ceiling repair
    "Flooring (overall)":                             0.75,   # mid-range flooring
    "Indoor air quality":                             0.70,   # smoke odor remediation
    "Interior light fixtures":                        0.75,
    "Interior paint — walls":                    1.05,   # highest ROI whole-house item
    "Popcorn ceiling":                                0.60,   # popcorn removal (varies)
    "Trim paint — baseboards + door frames":     1.00,
}

# Quality-based fallback recoup rates for components not in the lookup above.
# roi_quality values in this DB: "none", "medium", "high", "manual"
_QUALITY_FALLBACK_RECOUP: dict[str, float] = {
    "high":   0.85,   # cosmetic upgrades, refreshes
    "medium": 0.75,   # cleaning, minor repairs
    "manual": 0.70,   # manually priced items (conservative default)
    # "none" intentionally omitted — inspection items, no estimate possible
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclasses.dataclass
class RoiItem:
    matrix_row_id:       str
    component:           str
    zone:                str
    option_key:          str
    cost_low:            float
    cost_high:           float
    cost_midpoint:       float
    estimated_value_add: Optional[float]  # None = no estimate possible
    roi_quality:         str              # raw text from decision_matrix_options
    roi_bucket_suggested: str
    roi_bucket_override:  Optional[str]
    roi_bucket_final:    str
    include_suggested:   bool
    include_override:    Optional[bool]
    include_final:       bool
    matrix_label:        str   # read-only; never editable in UI


@dataclasses.dataclass
class RoiResult:
    scenario:               str
    items:                  list[RoiItem]
    total_cost_low:         float
    total_cost_high:        float
    total_cost_midpoint:    float
    value_lift_uncapped:    float
    max_supported_lift:     float
    value_lift_capped:      float
    roi_pct:                Optional[float]
    net_proceeds:           float
    listing_price:          float
    scenario_listing_price: float   # as_is + value_lift_capped for this scenario
    seller_inputs_used:     dict
    confidence:             str
    generated_at:           str


# ─── Core math functions ───────────────────────────────────────────────────────

def compute_net_proceeds(
    listing_price: float,
    selected_work_cost: float,
    mortgage_payoff: float,
    commission_pct: float,
    closing_costs: float,
    seller_credits: float,
    other_seller_costs: float,
) -> float:
    """
    Net proceeds formula (commission_pct is a percentage, e.g. 5.5 not 0.055):

        commission   = listing_price × (commission_pct / 100)
        net_proceeds = listing_price
                     − selected_work_cost
                     − mortgage_payoff
                     − commission
                     − closing_costs
                     − seller_credits
                     − other_seller_costs
    """
    commission = listing_price * (commission_pct / 100.0)
    return (
        listing_price
        - selected_work_cost
        - mortgage_payoff
        - commission
        - closing_costs
        - seller_credits
        - other_seller_costs
    )


def compute_roi_pct(total_cost: float, capped_lift: float) -> Optional[float]:
    """ROI % = ((capped_lift - total_cost) / total_cost) * 100. None if cost is 0."""
    if total_cost == 0:
        return None
    return ((capped_lift - total_cost) / total_cost) * 100.0


def _value_add_from_component(
    component: str,
    option_key: str,
    roi_quality: str,
    cost_midpoint: float,
) -> Optional[float]:
    """
    Estimate resale value add using Cost vs. Value regional lookup (South Atlantic 2024).

    Returns None when no meaningful estimate is possible:
      - option is further_inspect or leave_as_is (work scope unknown)
      - cost is zero (nothing to recoup)
      - component not in lookup and quality is "none" (safety/systems items)
    """
    if option_key in _NO_ESTIMATE_OPTIONS:
        return None
    if cost_midpoint <= 0:
        return None

    # Component-specific recoup rate takes priority
    recoup = _COMPONENT_RECOUP.get(component)

    # Fall back to quality bucket if component not mapped
    if recoup is None:
        recoup = _QUALITY_FALLBACK_RECOUP.get(roi_quality)

    if recoup is None:
        return None

    return round(cost_midpoint * recoup, 2)


# ─── Item building ─────────────────────────────────────────────────────────────

def _build_roi_item(
    row: dict,
    option: dict,
    override: Optional[dict],
) -> RoiItem:
    """
    Construct a RoiItem from a matrix row + its selected option + any saved override.

    row     — dict from decision_matrix_rows
    option  — dict from decision_matrix_options (the selected/recommended option)
    override — dict from roi_item_overrides, or None
    """
    cost_low      = float(option.get("cost_low") or 0)
    cost_high     = float(option.get("cost_high") or 0)
    cost_midpoint = (cost_low + cost_high) / 2.0

    roi_quality         = (option.get("roi_quality") or "").lower().strip()
    estimated_value_add = _value_add_from_component(
        component   = row.get("component") or "",
        option_key  = option.get("option_key") or "",
        roi_quality = roi_quality,
        cost_midpoint = cost_midpoint,
    )

    raw_tier = row.get("minimum_tier") or "nice_to_do"
    bucket_suggested = _TIER_TO_BUCKET.get(raw_tier, "nice_to_do")

    bucket_override  = override.get("roi_bucket_override") if override else None
    bucket_final     = bucket_override if bucket_override is not None else bucket_suggested

    # include_suggested: True unless bucket is "exclude" or row is informational-only
    include_suggested = bucket_final != "exclude" and row.get("decision_status") != "informational"

    include_override = override.get("include_override") if override else None
    include_final    = include_override if include_override is not None else include_suggested

    component = row.get("component") or ""
    zone      = row.get("zone") or ""

    return RoiItem(
        matrix_row_id        = str(row.get("id") or ""),
        component            = component,
        zone                 = zone,
        option_key           = option.get("option_key") or "",
        cost_low             = cost_low,
        cost_high            = cost_high,
        cost_midpoint        = cost_midpoint,
        estimated_value_add  = estimated_value_add,
        roi_quality          = roi_quality,
        roi_bucket_suggested = bucket_suggested,
        roi_bucket_override  = bucket_override,
        roi_bucket_final     = bucket_final,
        include_suggested    = include_suggested,
        include_override     = include_override,
        include_final        = include_final,
        matrix_label         = f"{component} — {zone}" if zone else component,
    )


def _build_all_items(
    matrix_rows: list[dict],
    overrides: list[dict],
) -> list[RoiItem]:
    """
    Build the full RoiItem list from matrix rows, their best option, and saved overrides.

    matrix_rows  — each row dict must include an "options" key (list of option dicts)
                   OR we select from the row's selected_option_key.
    overrides    — list of roi_item_overrides rows
    """
    override_map: dict[str, dict] = {
        str(ov.get("matrix_row_id")): ov for ov in (overrides or [])
    }

    items: list[RoiItem] = []
    for row in matrix_rows:
        row_id   = str(row.get("id") or "")
        options  = row.get("options") or []
        override = override_map.get(row_id)

        # Pick the best option: selected by seller, or flagged recommended, or first
        selected_key = row.get("selected_option_key")
        option: Optional[dict] = None
        if selected_key:
            option = next((o for o in options if o.get("option_key") == selected_key), None)
        if option is None:
            option = next((o for o in options if o.get("is_recommended")), None)
        if option is None and options:
            option = options[0]
        if option is None:
            # Row has no options — skip it
            continue

        items.append(_build_roi_item(row, option, override))

    return items


# ─── Scenario filters ──────────────────────────────────────────────────────────

def _filter_must_do_only(items: list[RoiItem]) -> list[RoiItem]:
    return [it for it in items if it.include_final and it.roi_bucket_final == "must_do"]


def _filter_highest_roi(items: list[RoiItem]) -> list[RoiItem]:
    """
    All must_do rows first (always included regardless of ROI).
    Then append should_do / nice_to_do rows sorted by marginal ROI descending,
    stopping when the next item's marginal ROI is negative.
    """
    must_do  = [it for it in items if it.include_final and it.roi_bucket_final == "must_do"]
    optional = [
        it for it in items
        if it.include_final and it.roi_bucket_final in ("should_do", "nice_to_do")
    ]

    def marginal_roi(it: RoiItem) -> float:
        cost  = it.cost_midpoint
        lift  = it.estimated_value_add or 0.0
        return (lift - cost) / cost if cost > 0 else 0.0

    optional_sorted = sorted(optional, key=marginal_roi, reverse=True)

    selected = list(must_do)
    for it in optional_sorted:
        if marginal_roi(it) < 0:
            break
        selected.append(it)
    return selected


def _filter_full_recommended(items: list[RoiItem]) -> list[RoiItem]:
    return [it for it in items if it.include_final and it.roi_bucket_final != "exclude"]


def _filter_custom(items: list[RoiItem]) -> list[RoiItem]:
    return [it for it in items if it.include_final and it.roi_bucket_final != "exclude"]


# ─── Main entry point ──────────────────────────────────────────────────────────

def compute_scenario(
    scenario: ScenarioName,
    matrix_rows: list[dict],
    overrides: list[dict],
    seller_inputs: dict,
    snapshot: dict,
) -> RoiResult:
    """
    Compute a full ROI result for the given scenario.

    matrix_rows   — list of decision_matrix_rows dicts, each with an "options" key
    overrides     — list of roi_item_overrides dicts (may be empty)
    seller_inputs — roi_seller_inputs dict (or defaults dict)
    snapshot      — property_market_snapshots dict
    """
    all_items = _build_all_items(matrix_rows, overrides)

    # Apply scenario filter
    if scenario == "must-do-only":
        selected = _filter_must_do_only(all_items)
    elif scenario == "highest-roi":
        selected = _filter_highest_roi(all_items)
    elif scenario == "full-recommended":
        selected = _filter_full_recommended(all_items)
    elif scenario == "custom":
        selected = _filter_custom(all_items)
    else:
        selected = _filter_full_recommended(all_items)

    # Aggregate costs
    total_cost_low      = sum(it.cost_low      for it in selected)
    total_cost_high     = sum(it.cost_high     for it in selected)
    total_cost_midpoint = sum(it.cost_midpoint for it in selected)

    # Value lift
    as_is   = float(snapshot.get("as_is_market_estimate") or 0)
    ceiling = float(snapshot.get("improved_listing_ceiling") or 0)
    max_supported_lift  = max(0.0, ceiling - as_is)
    value_lift_uncapped = sum(it.estimated_value_add or 0.0 for it in selected)
    value_lift_capped   = min(value_lift_uncapped, max_supported_lift)

    # Seller inputs — explicit None-check so 0.0 is honoured, not treated as "missing"
    def _sf(key: str, default: float) -> float:
        v = seller_inputs.get(key)
        return float(v) if v is not None else float(default)

    _lp = seller_inputs.get("listing_price")
    listing_price      = float(_lp) if _lp is not None else float(ceiling or as_is)
    mortgage_payoff    = _sf("mortgage_payoff",    0)
    commission_pct     = _sf("commission_pct",     _DEFAULT_COMMISSION_PCT)
    closing_costs      = _sf("closing_costs",      _DEFAULT_CLOSING_COSTS)
    seller_credits     = _sf("seller_credits",     0)
    other_seller_costs = _sf("other_seller_costs", 0)

    net_proceeds = compute_net_proceeds(
        listing_price      = listing_price,
        selected_work_cost = total_cost_midpoint,
        mortgage_payoff    = mortgage_payoff,
        commission_pct     = commission_pct,
        closing_costs      = closing_costs,
        seller_credits     = seller_credits,
        other_seller_costs = other_seller_costs,
    )

    roi_pct = compute_roi_pct(total_cost_midpoint, value_lift_capped)

    return RoiResult(
        scenario               = scenario,
        items                  = selected,
        total_cost_low         = total_cost_low,
        total_cost_high        = total_cost_high,
        total_cost_midpoint    = total_cost_midpoint,
        value_lift_uncapped    = value_lift_uncapped,
        max_supported_lift     = max_supported_lift,
        value_lift_capped      = value_lift_capped,
        roi_pct                = roi_pct,
        net_proceeds           = net_proceeds,
        listing_price          = listing_price,
        scenario_listing_price = round(as_is + value_lift_capped),
        seller_inputs_used     = {
            "listing_price":      listing_price,
            "mortgage_payoff":    mortgage_payoff,
            "commission_pct":     commission_pct,
            "closing_costs":      closing_costs,
            "seller_credits":     seller_credits,
            "other_seller_costs": other_seller_costs,
        },
        confidence          = snapshot.get("confidence_label") or "Unknown",
        generated_at        = datetime.now(timezone.utc).isoformat(),
    )


def build_all_roi_items(matrix_rows: list[dict], overrides: list[dict]) -> list[RoiItem]:
    """Return the full unfiltered item list for comparison and risk analysis."""
    return _build_all_items(matrix_rows, overrides)
