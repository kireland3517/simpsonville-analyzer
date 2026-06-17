import main


class ExecuteResult:
    def __init__(self, data=None):
        self.data = data or []


class Query:
    def __init__(self, table, data=None):
        self.table = table
        self.data = data or []
        self.upserts = []
        self.selected = None
        self.filters = {}

    def select(self, value):
        self.selected = value
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def maybe_single(self):
        return self

    def upsert(self, payload):
        self.upserts.append(payload)
        self.table.upserts.append(payload)
        return self

    def execute(self):
        if self.filters.get("id"):
            row = self.table.rows.get(self.filters["id"])
            return ExecuteResult(row)
        return ExecuteResult(list(self.table.rows.values()))


class Table:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.upserts = []

    def select(self, value):
        return Query(self, list(self.rows.values())).select(value)

    def upsert(self, payload):
        return Query(self).upsert(payload)


class FakeSupabase:
    def __init__(self, rows=None):
        self.photo_table = Table(rows)

    def table(self, name):
        assert name == "photo_analyses"
        return self.photo_table


def test_save_photo_analysis_writes_memory_and_supabase():
    main.analysis_cache.clear()
    sb = FakeSupabase()
    result = {"room_type": "kitchen", "issues": []}

    main._save_photo_analysis("photo-1", "https://example/photo", result, sb=sb)

    assert main.analysis_cache["photo-1"] == result
    assert sb.photo_table.upserts == [{
        "id": "photo-1",
        "filename": "photo-1",
        "base_url": "https://example/photo",
        "analysis": result,
    }]


def test_analyze_results_loads_supabase_when_memory_cache_is_cold(monkeypatch):
    main.analysis_cache.clear()
    rows = {
        "photo-1": {"id": "photo-1", "analysis": {"room_type": "garage"}},
        "photo-2": {"id": "photo-2", "analysis": {"room_type": "kitchen"}},
    }
    monkeypatch.setattr(main, "_sb", lambda: FakeSupabase(rows))

    result = main.analyze_results()

    assert result == {
        "photo-1": {"room_type": "garage"},
        "photo-2": {"room_type": "kitchen"},
    }
