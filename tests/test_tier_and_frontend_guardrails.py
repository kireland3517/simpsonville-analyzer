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
    for bucket in ("upgrades", "repairs"):
        for item in line_items[bucket]:
            assert "rationale" not in item


def test_frontend_does_not_map_all_or_not_doing_to_nice_to_do_report_post():
    text = Path("static/index.html").read_text(encoding="utf-8")
    fn_start = text.index("function dmScheduleRoiUpdate()")
    fn_end = text.index("function dmSetLrpTier", fn_start)
    body = text[fn_start:fn_end]

    assert "dmLrpTier === 'all' || dmLrpTier === 'not_doing' || !dmLrpTier" in body
    assert "return;" in body
    assert "? 'nice_to_do' : dmLrpTier" not in body
    assert "tier: dmLrpTier" in body


def test_frontend_tier_save_alert_uses_backend_detail():
    text = Path("static/index.html").read_text(encoding="utf-8")
    fn_start = text.index("async function dmSaveTier")
    fn_end = text.index("function dmEditZone", fn_start)
    body = text[fn_start:fn_end]

    assert "const errorData = await res.json()" in body
    assert "detail = errorData.detail || detail" in body


def test_frontend_cost_save_alert_uses_backend_detail():
    text = Path("static/index.html").read_text(encoding="utf-8")
    fn_start = text.index("async function dmSaveCost")
    fn_end = text.index("function dmScheduleRoiUpdate", fn_start)
    body = text[fn_start:fn_end]

    assert "const errorData = await res.json()" in body
    assert "detail = errorData.detail || detail" in body


def test_frontend_forecasted_spend_column_is_editable():
    text = Path("static/index.html").read_text(encoding="utf-8")

    assert "Estimated Range" in text
    assert "Forecasted Spend" in text
    assert "data-forecast-row" in text
    assert "async function dmSaveForecastedSpend" in text
    assert "JSON.stringify({ forecasted_spend: amount })" in text


def test_frontend_budget_summary_has_estimated_and_forecast_lines():
    text = Path("static/index.html").read_text(encoding="utf-8")
    fn_start = text.index("const calcTierForecastedSpend")
    fn_end = text.index("const [mlo,mhi]", fn_start)
    body = text[fn_start:fn_end]

    assert "Estimated Range" in text
    assert "Forecasted Spend" in text
    assert "dm-forecast-total-must" in text
    assert "calcTierForecastedSpend" in text
    assert "setEl('dm-forecast-total-all'" in text
    assert "Number(r.forecasted_spend)" in body
    assert "DM_TBD_COST_KEYS" not in body
    assert "DM_NO_COST_KEYS" not in body


def test_decision_matrix_empty_placeholders_use_html_entities():
    text = Path("static/index.html").read_text(encoding="utf-8")
    cost_start = text.index("function dmFormatCostForRow")
    cost_end = text.index("function dmFormatForecastedSpendForRow", cost_start)
    forecast_start = cost_end
    forecast_end = text.index("function dmLabel", forecast_start)
    body = text[cost_start:forecast_end]

    assert "&mdash;" in body
    assert "â€”" not in body


def test_decision_matrix_edit_hints_use_html_entities():
    text = Path("static/index.html").read_text(encoding="utf-8")
    row_start = text.index("tbody.innerHTML = rows.map")
    row_end = text.index("if (!expanded) return mainRow", row_start)
    body = text[row_start:row_end]

    assert "&#9998;" in body
    assert "âœŽ" not in body


def test_decision_matrix_edit_cancel_buttons_use_text():
    text = Path("static/index.html").read_text(encoding="utf-8")
    cost_start = text.index("function dmEditCost")
    cost_end = text.index("async function dmSaveCost", cost_start)
    forecast_start = text.index("function dmEditForecastedSpend")
    forecast_end = text.index("async function dmSaveForecastedSpend", forecast_start)
    body = text[cost_start:cost_end] + text[forecast_start:forecast_end]

    assert ">Cancel</button>" in body
    assert "âœ•" not in body


def test_frontend_add_item_requires_decision_and_uses_backend_detail():
    text = Path("static/index.html").read_text(encoding="utf-8")
    fn_start = text.index("async function dmSaveAddItem")
    fn_end = text.index("function dmToggleLrpExpand", fn_start)
    body = text[fn_start:fn_end]

    assert "if (!decision) { alert('Please choose a decision.'); return; }" in body
    assert "forecasted_spend: forecastedSpend" in body
    assert "const errorData = await res.json()" in body
    assert "detail = errorData.detail || detail" in body


def test_frontend_add_item_supports_other_zone_by_default():
    text = Path("static/index.html").read_text(encoding="utf-8")
    zones_start = text.index("const DM_ZONES = [")
    zones_end = text.index("];", zones_start)
    zones_body = text[zones_start:zones_end]
    add_start = text.index("function dmShowAddItem")
    add_end = text.index("async function dmSaveAddItem", add_start)
    add_body = text[add_start:add_end]

    assert "'other', 'unmatched'" in zones_body
    assert "'other': 'zone-other'" in text
    assert "zoneEl.value = 'other'" in add_body
    assert "dmZoneClass('other')" in add_body


def test_frontend_has_must_do_plan_print_flow():
    text = Path("static/index.html").read_text(encoding="utf-8")

    assert "onclick=\"printMustDoPlan()\"" in text
    assert "aria-label=\"Print selected Decision Matrix scopes\"" in text
    assert 'id="dm-print-root"' in text
    assert "function buildMustDoPlanPrintHtml()" in text
    assert "async function printMustDoPlan()" in text
    assert "is-printing-dm-plan" in text
    assert "data-dm-print-tier=\"all\"" in text
    assert "data-dm-print-tier=\"must_do\" checked" in text
    assert "data-dm-print-tier=\"should_do\"" in text
    assert "data-dm-print-tier=\"nice_to_do\"" in text
    assert "data-dm-print-tier=\"not_doing\"" in text
    assert "data-dm-print-column=\"zone\" checked" in text
    assert "data-dm-print-column=\"decision\" checked" in text
    assert "data-dm-print-column=\"estimated_range\" checked" in text
    assert "data-dm-print-column=\"forecasted_spend\" checked" in text
    assert "data-dm-print-column=\"condition\" checked" in text
    assert "data-dm-print-column=\"note\" checked" in text
    assert "&#128424;" in text


def test_print_flow_filters_to_selected_tiers_and_includes_summaries():
    text = Path("static/index.html").read_text(encoding="utf-8")
    rows_start = text.index("function dmPrintRowsForTiers")
    rows_end = text.index("function buildMustDoPlanPrintHtml()", rows_start)
    rows_body = text[rows_start:rows_end]
    print_start = rows_end
    print_end = text.index("async function printMustDoPlan()", print_start)
    print_body = text[print_start:print_end]

    assert "tierSet.has(normalizeTier(row.minimum_tier) || 'must_do')" in rows_body
    assert "function dmSelectedPrintTiers()" in text
    assert "if (checked.includes('all')) return ['must_do', 'should_do', 'nice_to_do', 'not_doing'];" in text
    assert "dmPrintScopeTitle(tiers)" in print_body
    assert "{ key: 'estimated_range', label: 'Estimated Range'" in text
    assert "{ key: 'forecasted_spend', label: 'Forecasted Spend'" in text
    assert "{ key: 'condition', label: 'Condition'" in text
    assert "{ key: 'note', label: 'Note'" in text
    assert "forecastedSpend = rows.reduce" in print_body
    assert "columns.map(column => `<th>${esc(column.label)}</th>`).join('')" in print_body
    assert "columns.map(column => column.cell(row)).join('')" in print_body
    assert "selectedColumnKeys.has('estimated_range')" in print_body
    assert "selectedColumnKeys.has('forecasted_spend')" in print_body
    assert "${summaryCards}" in print_body
    assert "Rationale / Notes" not in print_body


def test_print_column_preferences_are_persisted():
    text = Path("static/index.html").read_text(encoding="utf-8")

    assert "const DM_PRINT_COLUMN_STORAGE_KEY = 'dmPrintColumns'" in text
    assert "function dmSelectedPrintColumns()" in text
    assert "function dmSavePrintColumnPrefs()" in text
    assert "function dmLoadPrintColumnPrefs()" in text
    assert "localStorage.setItem(DM_PRINT_COLUMN_STORAGE_KEY" in text
    assert "localStorage.getItem(DM_PRINT_COLUMN_STORAGE_KEY)" in text
    assert "dmLoadPrintColumnPrefs();" in text
    assert "input.addEventListener('change', dmSavePrintColumnPrefs)" in text
    assert "{ key: 'component', label: 'Component / Item', locked: true" in text


def test_frontend_does_not_render_rationale_blocks():
    text = Path("static/index.html").read_text(encoding="utf-8")

    assert "renderRationaleBlock" not in text
    assert "Why this recommendation exists" not in text
    assert "rationale-block" not in text
    assert "<strong>Rationale:</strong>" not in text


def test_report_composer_does_not_emit_rationale_payloads():
    text = Path("report_composer.py").read_text(encoding="utf-8")

    assert '"rationale"' not in text
    assert "_rationale_from_row" not in text


def test_frontend_can_edit_condition_and_note_metadata():
    text = Path("static/index.html").read_text(encoding="utf-8")

    assert "Edit condition" in text
    assert "Edit note" in text
    assert "async function dmEditCondition" in text
    assert "async function dmEditNote" in text
    assert "dmSaveTextMeta(rowId, 'current_state'" in text
    assert "dmSaveTextMeta(rowId, 'walkthrough_notes'" in text


def test_backend_accepts_condition_and_note_metadata():
    text = Path("main.py").read_text(encoding="utf-8")

    assert "current_state: str | None = None" in text
    assert "walkthrough_notes: str | None = None" in text
    assert 'update["current_state"] = body.current_state.strip()' in text
    assert 'update["walkthrough_notes"] = body.walkthrough_notes.strip()' in text


def test_not_doing_is_allowed_by_latest_matrix_tier_migration():
    sql = Path("migrations/decision_matrix_v5_not_doing_tier.sql").read_text(encoding="utf-8")

    assert "decision_matrix_rows_minimum_tier_check" in sql
    assert "decision_matrix_rows_recommended_tier_check" in sql
    assert "'not_doing'" in sql


def test_row_cost_columns_are_added_by_latest_matrix_migration():
    sql = Path("migrations/decision_matrix_v6_row_costs.sql").read_text(encoding="utf-8")

    assert "alter table decision_matrix_rows" in sql
    assert "add column if not exists cost_low numeric" in sql
    assert "add column if not exists cost_high numeric" in sql


def test_forecasted_spend_column_is_added_by_latest_matrix_migration():
    sql = Path("migrations/decision_matrix_v8_forecasted_spend.sql").read_text(encoding="utf-8")

    assert "alter table decision_matrix_rows" in sql
    assert "add column if not exists forecasted_spend numeric" in sql
