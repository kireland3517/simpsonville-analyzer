from pathlib import Path

from report_composer import compose_line_items_from_tier
from tier_selector import select_tier_from_rows


def _row(row_id, tier, option_key="repair"):
    return {
        "id": row_id,
        "component": f"Component {row_id}",
        "zone": "zone",
        "minimum_tier": tier,
        "recommended_tier": tier,
        "decision_status": "required_action",
        "inspection_risk": "high",
        "buyer_impact": "high",
        "recommended_action": option_key,
        "selected_option_key": option_key,
        "current_state": f"State {row_id}",
        "evidence_sources": [{"source": "walkthrough", "text": f"Evidence {row_id}"}],
        "options": [{
            "id": f"opt-{row_id}",
            "option_key": option_key,
            "cost_low": 100,
            "cost_high": 200,
            "is_recommended": True,
            "buyer_impact": "high",
            "inspection_risk_impact": "reduces",
            "marketability_impact": "high",
            "rationale": {"tier": tier, "reason": "test"},
        }],
    }


def test_not_doing_rows_are_excluded_from_tier_selection_and_reports():
    rows = [
        _row("must", "must_do"),
        _row("skip", "not_doing"),
    ]

    selection = select_tier_from_rows(rows, "nice_to_do", matrix_id="matrix-1")
    line_items = compose_line_items_from_tier(rows, selection)

    assert [item["row_id"] for item in selection["selected_rows"]] == ["must"]
    assert selection["rows_excluded"][0]["row_id"] == "skip"
    emitted_ids = {
        item["matrix_row_id"]
        for bucket in ("upgrades", "repairs")
        for item in line_items[bucket]
    }
    assert "skip" not in emitted_ids


def test_frontend_does_not_map_all_or_not_doing_to_nice_to_do_report_post():
    text = Path("static/index.html").read_text(encoding="utf-8")
    fn_start = text.index("function dmScheduleRoiUpdate()")
    fn_end = text.index("function dmSetLrpTier", fn_start)
    body = text[fn_start:fn_end]

    assert "dmLrpTier === 'all' || dmLrpTier === 'not_doing' || !dmLrpTier" in body
    assert "return;" in body
    assert "? 'nice_to_do' : dmLrpTier" not in body
    assert "tier: dmLrpTier" in body
