"""Golden-path math tests for roi_engine.py — no DB, no LLM, no network."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from roi_engine import (
    compute_net_proceeds,
    compute_roi_pct,
    compute_scenario,
    RoiItem,
)

# ─── Fixtures ──────────────────────────────────────────────────────────────────

SNAPSHOT = {
    "as_is_market_estimate":    276_810.0,
    "improved_listing_ceiling": 305_000.0,
    "confidence_label":         "Medium",
}

SELLER_INPUTS = {
    "listing_price":      305_000.0,
    "mortgage_payoff":    200_000.0,
    "commission_pct":     5.5,
    "closing_costs":      3_500.0,
    "seller_credits":     2_000.0,
    "other_seller_costs": 0.0,
}

def _make_row(row_id, tier="should_do", cost_low=1000, cost_high=2000, roi_quality="high",
              option_key="repair", decision_status="decision_required", component=None):
    # Default component "Door hardware" maps to 0.80 recoup in _COMPONENT_RECOUP.
    # Pass component= to use a specific lookup entry.
    return {
        "id": row_id,
        "component": component or "Door hardware",
        "zone": "Main Level",
        "minimum_tier": tier,
        "decision_status": decision_status,
        "selected_option_key": option_key,
        "options": [{
            "option_key": option_key,
            "cost_low": cost_low,
            "cost_high": cost_high,
            "roi_quality": roi_quality,
            "is_recommended": True,
        }],
    }


# ─── compute_net_proceeds ──────────────────────────────────────────────────────

def test_net_proceeds_basic():
    """
    listing=300k, payoff=200k, commission=5.5%, closing=3500, credits=2000
    commission = 300_000 * 0.055 = 16_500
    net = 300_000 - 15_000 - 200_000 - 16_500 - 3_500 - 2_000 - 0 = 63_000
    """
    result = compute_net_proceeds(
        listing_price=300_000,
        selected_work_cost=15_000,
        mortgage_payoff=200_000,
        commission_pct=5.5,
        closing_costs=3_500,
        seller_credits=2_000,
        other_seller_costs=0,
    )
    assert result == pytest.approx(63_000, abs=0.01)


def test_net_proceeds_zero_payoff():
    """No mortgage — seller keeps more."""
    result = compute_net_proceeds(
        listing_price=305_000,
        selected_work_cost=10_000,
        mortgage_payoff=0,
        commission_pct=5.5,
        closing_costs=3_500,
        seller_credits=0,
        other_seller_costs=0,
    )
    # commission = 305_000 * 0.055 = 16_775
    # net = 305_000 - 10_000 - 0 - 16_775 - 3_500 - 0 - 0 = 274_725
    assert result == pytest.approx(274_725, abs=0.01)


def test_net_proceeds_commission_is_percentage_not_decimal():
    """commission_pct=5.5 must be treated as 5.5%, NOT 0.055."""
    # If incorrectly treated as 0.055, commission would be ~$16.50 instead of $16,500
    result = compute_net_proceeds(300_000, 0, 0, 5.5, 0, 0, 0)
    expected_commission = 300_000 * 0.055  # 16_500
    expected = 300_000 - expected_commission
    assert result == pytest.approx(expected, abs=0.01)


# ─── compute_roi_pct ───────────────────────────────────────────────────────────

def test_roi_pct_positive():
    result = compute_roi_pct(total_cost=15_000, capped_lift=25_000)
    # ((25000 - 15000) / 15000) * 100 = 66.666...
    assert result == pytest.approx(66.667, abs=0.1)


def test_roi_pct_negative():
    result = compute_roi_pct(total_cost=10_000, capped_lift=7_000)
    assert result == pytest.approx(-30.0, abs=0.1)


def test_roi_pct_zero_cost():
    assert compute_roi_pct(total_cost=0, capped_lift=10_000) is None


def test_roi_pct_zero_lift_zero_cost():
    assert compute_roi_pct(total_cost=0, capped_lift=0) is None


# ─── value-lift cap ────────────────────────────────────────────────────────────

def test_value_lift_capped_when_over_ceiling():
    """
    Ceiling=305k, as-is=276,810 → max_supported_lift=28,190
    "Garage door" has 0.98 recoup. At cost=30k → value_add=29.4k, which exceeds cap.
    """
    snap = {
        "as_is_market_estimate":    276_810.0,
        "improved_listing_ceiling": 305_000.0,
        "confidence_label": "Medium",
    }
    row = _make_row("r1", tier="should_do", cost_low=30_000, cost_high=30_000,
                    component="Garage door")
    result = compute_scenario("full-recommended", [row], [], SELLER_INPUTS, snap)
    assert result.max_supported_lift == pytest.approx(28_190, abs=1)
    assert result.value_lift_capped <= result.max_supported_lift
    assert result.value_lift_capped < result.value_lift_uncapped  # was capped


def test_value_lift_not_capped_when_under_ceiling():
    snap = {
        "as_is_market_estimate":    276_810.0,
        "improved_listing_ceiling": 305_000.0,
        "confidence_label": "Medium",
    }
    # "Door hardware" at 0.80 recoup, cost=1k → value_add=800, well under 28,190 cap
    row = _make_row("r1", tier="should_do", cost_low=1_000, cost_high=1_000)
    result = compute_scenario("full-recommended", [row], [], SELLER_INPUTS, snap)
    assert result.value_lift_capped == result.value_lift_uncapped


# ─── Scenario filters ──────────────────────────────────────────────────────────

def test_must_do_only_filters_correctly():
    rows = [
        _make_row("r1", tier="must_do"),
        _make_row("r2", tier="should_do"),
        _make_row("r3", tier="nice_to_do"),
    ]
    result = compute_scenario("must-do-only", rows, [], SELLER_INPUTS, SNAPSHOT)
    included = [it for it in result.items if it.include_final and it.roi_bucket_final == "must_do"]
    assert len(included) == 1
    assert included[0].matrix_row_id == "r1"


def test_full_recommended_includes_all_buckets():
    rows = [
        _make_row("r1", tier="must_do"),
        _make_row("r2", tier="should_do"),
        _make_row("r3", tier="nice_to_do"),
    ]
    result = compute_scenario("full-recommended", rows, [], SELLER_INPUTS, SNAPSHOT)
    included = [it for it in result.items if it.include_final]
    assert len(included) == 3


def test_include_override_false_excludes_item():
    rows  = [_make_row("r1", tier="must_do")]
    overrides = [{"matrix_row_id": "r1", "include_override": False, "roi_bucket_override": None}]
    result = compute_scenario("full-recommended", rows, overrides, SELLER_INPUTS, SNAPSHOT)
    # Item is excluded: result.items contains only selected items, so it should be empty.
    assert result.items == []
    # Total costs should be zero since nothing is selected.
    assert result.total_cost_midpoint == 0


def test_bucket_override_changes_final_bucket():
    rows     = [_make_row("r1", tier="nice_to_do")]
    overrides = [{"matrix_row_id": "r1", "roi_bucket_override": "must_do", "include_override": None}]
    result = compute_scenario("full-recommended", rows, overrides, SELLER_INPUTS, SNAPSHOT)
    item = result.items[0]
    assert item.roi_bucket_suggested == "nice_to_do"
    assert item.roi_bucket_override  == "must_do"
    assert item.roi_bucket_final     == "must_do"


# ─── Matrix read-only guarantee ────────────────────────────────────────────────

def test_compute_scenario_does_not_mutate_input_rows():
    """compute_scenario must not modify the matrix_rows list or its dicts."""
    rows = [_make_row("r1", tier="must_do")]
    original_tier = rows[0]["minimum_tier"]
    original_len  = len(rows)
    compute_scenario("full-recommended", rows, [], SELLER_INPUTS, SNAPSHOT)
    assert rows[0]["minimum_tier"] == original_tier
    assert len(rows) == original_len


# ─── Mortgage payoff reactivity ────────────────────────────────────────────────

def test_mortgage_payoff_150k_correct_net():
    """
    listing=305k, payoff=150k, 5.5% commission, closing=3500, credits=2000, work=1000
    commission = 305000 * 0.055 = 16775
    net = 305000 - 1000 - 150000 - 16775 - 3500 - 2000 - 0 = 131725
    """
    row    = _make_row("r1", tier="must_do", cost_low=1_000, cost_high=1_000)
    inputs = {**SELLER_INPUTS, "mortgage_payoff": 150_000.0}
    result = compute_scenario("full-recommended", [row], [], inputs, SNAPSHOT)
    assert result.net_proceeds == pytest.approx(131_725, abs=0.01)


def test_mortgage_payoff_zero_increases_net_by_payoff_amount():
    """Setting payoff from 150k to 0 increases net proceeds by exactly 150k."""
    row      = _make_row("r1", tier="must_do", cost_low=1_000, cost_high=1_000)
    base     = {**SELLER_INPUTS, "listing_price": 305_000.0}
    r_150k   = compute_scenario("full-recommended", [row], [], {**base, "mortgage_payoff": 150_000.0}, SNAPSHOT)
    r_zero   = compute_scenario("full-recommended", [row], [], {**base, "mortgage_payoff": 0.0},       SNAPSHOT)
    assert r_zero.net_proceeds == pytest.approx(r_150k.net_proceeds + 150_000, abs=0.01)


def test_explicit_zero_mortgage_payoff_is_not_overridden_by_default():
    """Explicit 0.0 payoff must NOT fall back to any non-zero default."""
    row    = _make_row("r1", tier="must_do", cost_low=0, cost_high=0,
                       roi_quality="none", option_key="further_inspect")
    inputs = {**SELLER_INPUTS, "listing_price": 305_000.0, "mortgage_payoff": 0.0}
    result = compute_scenario("full-recommended", [row], [], inputs, SNAPSHOT)
    # commission = 305000 * 0.055 = 16775; closing=3500; credits=2000; work=0
    # net = 305000 - 0 - 0 - 16775 - 3500 - 2000 = 282725
    assert result.net_proceeds == pytest.approx(282_725, abs=0.01)
