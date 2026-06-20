"""
Matrix preservation tests — verifies that compute_scenario() never mutates
the matrix_rows input or writes to matrix-owned tables.

These are pure-Python tests (no DB, no LLM).  Integration-level tests that
verify no DB table rows were altered should be run against a test Supabase
project using the verification SELECTs in the migration SQL comments.
"""
import copy
import sys
import os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from roi_engine import compute_scenario

SNAPSHOT = {
    "as_is_market_estimate":    276_810.0,
    "improved_listing_ceiling": 305_000.0,
    "confidence_label":         "Medium",
}

SELLER_INPUTS = {
    "listing_price":      305_000.0,
    "mortgage_payoff":    0.0,
    "commission_pct":     5.5,
    "closing_costs":      3_500.0,
    "seller_credits":     0.0,
    "other_seller_costs": 0.0,
}

def _make_rows():
    return [
        {
            "id": "row-1",
            "component": "Roof",
            "zone": "Exterior",
            "minimum_tier": "must_do",
            "decision_status": "required_action",
            "selected_option_key": "repair",
            "options": [{"option_key": "repair", "cost_low": 8000, "cost_high": 12000,
                         "roi_quality": "excellent", "is_recommended": True}],
        },
        {
            "id": "row-2",
            "component": "Interior Paint",
            "zone": "Main Level",
            "minimum_tier": "should_do",
            "decision_status": "decision_required",
            "selected_option_key": "refresh",
            "options": [{"option_key": "refresh", "cost_low": 1500, "cost_high": 3000,
                         "roi_quality": "good", "is_recommended": True}],
        },
        {
            "id": "row-3",
            "component": "Landscaping",
            "zone": "Exterior",
            "minimum_tier": "nice_to_do",
            "decision_status": "decision_required",
            "selected_option_key": "clean",
            "options": [{"option_key": "clean", "cost_low": 200, "cost_high": 800,
                         "roi_quality": "fair", "is_recommended": True}],
        },
    ]


@pytest.mark.parametrize("scenario", [
    "must-do-only", "highest-roi", "full-recommended", "custom"
])
def test_compute_scenario_does_not_mutate_rows(scenario):
    """compute_scenario must not alter the list or any dict within it."""
    rows         = _make_rows()
    rows_before  = copy.deepcopy(rows)

    compute_scenario(scenario, rows, [], SELLER_INPUTS, SNAPSHOT)

    assert len(rows) == len(rows_before), "Row list length changed"
    for before, after in zip(rows_before, rows):
        assert before == after, f"Row {after['id']} was mutated"


def test_overrides_do_not_mutate_rows():
    """Applying overrides must not write back into the matrix_rows dicts."""
    rows = _make_rows()
    overrides = [
        {"matrix_row_id": "row-1", "roi_bucket_override": "nice_to_do", "include_override": None},
        {"matrix_row_id": "row-2", "roi_bucket_override": None, "include_override": False},
    ]
    rows_before = copy.deepcopy(rows)

    compute_scenario("custom", rows, overrides, SELLER_INPUTS, SNAPSHOT)

    for before, after in zip(rows_before, rows):
        assert before == after, f"Row {after['id']} was mutated after override"


def test_multiple_calls_produce_same_result():
    """Engine is stateless — same inputs always produce same outputs."""
    rows     = _make_rows()
    inputs   = dict(SELLER_INPUTS)
    snap     = dict(SNAPSHOT)
    result_a = compute_scenario("full-recommended", rows, [], inputs, snap)
    result_b = compute_scenario("full-recommended", rows, [], inputs, snap)

    assert result_a.total_cost_midpoint == pytest.approx(result_b.total_cost_midpoint)
    assert result_a.net_proceeds        == pytest.approx(result_b.net_proceeds)
    assert result_a.roi_pct             == pytest.approx(result_b.roi_pct or 0, abs=0.01)


def test_roi_items_matrix_label_is_read_only_composite():
    """matrix_label is derived from component+zone; it is never a writable field."""
    rows   = _make_rows()
    result = compute_scenario("full-recommended", rows, [], SELLER_INPUTS, SNAPSHOT)
    for item in result.items:
        expected = f"{item.component} — {item.zone}" if item.zone else item.component
        assert item.matrix_label == expected, (
            f"matrix_label for {item.component} does not match expected format"
        )
