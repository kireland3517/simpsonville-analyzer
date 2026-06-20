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

def _make_row(row_id, tier="should_do", cost_low=1000, cost_high=2000, roi_quality="good",
              option_key="repair", decision_status="decision_required"):
    return {
        "id": row_id,
        "component": f"Component-{row_id}",
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
    If item value-add exceeds cap, result is capped.
    """
    snap = {
        "as_is_market_estimate":    276_810.0,
        "improved_listing_ceiling": 305_000.0,
        "confidence_label": "Medium",
    }
    # One item with cost_midpoint=20k, roi_quality="excellent" → value_add=30k (exceeds cap)
    row = _make_row("r1", tier="should_do", cost_low=20_000, cost_high=20_000, roi_quality="excellent")
    result = compute_scenario("full-recommended", [row], [], SELLER_INPUTS, snap)
    assert result.max_supported_lift == pytest.approx(28_190, abs=1)
    assert result.value_lift_capped <= result.max_supported_lift


def test_value_lift_not_capped_when_under_ceiling():
    snap = {
        "as_is_market_estimate":    276_810.0,
        "improved_listing_ceiling": 305_000.0,
        "confidence_label": "Medium",
    }
    # Small item — value add well under max supported lift
    row = _make_row("r1", tier="should_do", cost_low=1_000, cost_high=1_000, roi_quality="good")
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
    item = result.items[0]
    assert item.include_final is False
    assert item.include_override is False


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
