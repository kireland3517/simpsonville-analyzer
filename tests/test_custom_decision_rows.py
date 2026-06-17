import main


class ExecuteResult:
    def __init__(self, data=None):
        self.data = data or []


class InsertQuery:
    def __init__(self, table, payload):
        self.table = table
        self.payload = payload

    def execute(self):
        row = dict(self.payload)
        if self.table.name == "decision_matrix_rows":
            row["id"] = "row-custom-1"
        self.table.inserts.append(row)
        return ExecuteResult([row])


class Table:
    def __init__(self, name):
        self.name = name
        self.inserts = []

    def insert(self, payload):
        return InsertQuery(self, payload)


class FakeSupabase:
    def __init__(self):
        self.tables = {
            "decision_matrix_rows": Table("decision_matrix_rows"),
            "decision_matrix_options": Table("decision_matrix_options"),
        }

    def table(self, name):
        return self.tables[name]


def test_custom_decision_row_insert_includes_required_row_and_option_fields(monkeypatch):
    sb = FakeSupabase()
    monkeypatch.setattr(main, "_sb", lambda: sb)
    monkeypatch.setattr(main, "load_current_matrix", lambda _sb, _property_id: {"id": "matrix-1"})
    monkeypatch.setattr(main, "_matrix_cache_clear", lambda: None)

    result = main.add_custom_decision_row(main.DecisionMatrixCustomRow(
        zone="whole_house",
        component="Air Vent Covers",
        minimum_tier="must_do",
        selected_option_key="replace",
        cost_low=0,
        cost_high=0,
        forecasted_spend=1250,
    ))

    row = sb.tables["decision_matrix_rows"].inserts[0]
    option = sb.tables["decision_matrix_options"].inserts[0]
    assert result["row"]["id"] == "row-custom-1"
    assert row["component_id"].startswith("custom:")
    assert row["confidence_tier"] == "seller_added"
    assert row["current_state"] == "Seller-added item: Air Vent Covers"
    assert row["decision_status"] == "required_action"
    assert row["recommended_action"] == "replace"
    assert row["forecasted_spend"] == 1250
    assert option == {
        "row_id": "row-custom-1",
        "option_key": "replace",
        "cost_low": 0,
        "cost_high": 0,
        "buyer_impact": "medium",
        "inspection_risk_impact": "reduces",
        "marketability_impact": "medium",
        "roi_quality": "manual",
        "feasibility": "recommended",
        "is_recommended": True,
        "rationale": {"source": "seller_custom_row"},
    }
