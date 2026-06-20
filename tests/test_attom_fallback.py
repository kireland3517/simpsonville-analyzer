"""
Tests for attom.py fallback chain and main.py safe-wrapper functions.
No live ATTOM API is called; all tests run offline.
"""
import attom
import main


# ─── Original safe-wrapper tests (from remote) ───────────────────────────────

def test_attom_api_key_is_not_required_for_cached_property_summary(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATTOM_API_KEY", "bad-key-that-should-not-be-used")

    summary = attom.get_property_summary()
    last_sale = attom.get_last_sale()

    assert summary["address"] == "130 Kingfisher Dr, Simpsonville SC 29680"
    assert summary["market_value"] is None
    assert last_sale["sale_amount"] is None


def test_safe_property_summary_falls_back_when_attom_layer_raises(monkeypatch):
    def raise_error():
        raise RuntimeError("ATTOM unavailable")

    monkeypatch.setattr(main, "get_property_summary", raise_error)

    assert main._safe_property_summary() == {}


def test_safe_last_sale_falls_back_when_attom_layer_raises(monkeypatch):
    def raise_error():
        raise RuntimeError("ATTOM unavailable")

    monkeypatch.setattr(main, "get_last_sale", raise_error)

    assert main._safe_last_sale() == {}


# ─── New snapshot fallback tests ─────────────────────────────────────────────

def test_get_snapshot_never_raises_without_api_key(tmp_path, monkeypatch):
    """No ATTOM_API_KEY, no JSON files → returns hardcoded defaults without raising."""
    monkeypatch.delenv("ATTOM_API_KEY", raising=False)

    # Point attom file paths at empty tmp dir so no files exist
    monkeypatch.setattr(attom, "ASSESSMENT_FILE", tmp_path / "assessment.json")
    monkeypatch.setattr(attom, "SALES_FILE", tmp_path / "sales.json")

    snap = attom.get_or_refresh_market_snapshot("130_kingfisher", sb=None)

    assert snap["as_is_market_estimate"] == attom._DEFAULT_AS_IS
    assert snap["improved_listing_ceiling"] == attom._DEFAULT_CEILING
    assert snap["property_id"] == "130_kingfisher"


def test_normalize_attom_to_snapshot_hardcoded_fallback():
    """normalize_attom_to_snapshot uses default AVM when market value is missing."""
    snap = attom.normalize_attom_to_snapshot({}, source="attom_live")

    assert snap["as_is_market_estimate"] == attom._DEFAULT_AS_IS
    assert snap["improved_listing_ceiling"] == attom._DEFAULT_CEILING
    assert snap["source"] == "attom_live"


def test_normalize_attom_to_snapshot_reads_market_value_key():
    """normalize_attom_to_snapshot reads 'market_value' key from pre-parsed summary dict."""
    raw = {"market_value": 299_000}
    snap = attom.normalize_attom_to_snapshot(raw, source="attom_cached")

    assert snap["as_is_market_estimate"] == 299_000.0


def test_load_snapshot_from_files_no_files(tmp_path, monkeypatch):
    """load_snapshot_from_files returns hardcoded defaults when JSON files are absent."""
    monkeypatch.setattr(attom, "ASSESSMENT_FILE", tmp_path / "assessment.json")
    monkeypatch.setattr(attom, "SALES_FILE", tmp_path / "sales.json")

    snap = attom.load_snapshot_from_files()

    assert snap["as_is_market_estimate"] == attom._DEFAULT_AS_IS
    assert snap["freshness_label"] == "Default (no files)"


def test_get_snapshot_skips_live_api_when_key_blank(monkeypatch, tmp_path):
    """get_or_refresh_market_snapshot does not call fetch_live_attom when key is blank."""
    monkeypatch.setenv("ATTOM_API_KEY", "")
    monkeypatch.setattr(attom, "ASSESSMENT_FILE", tmp_path / "assessment.json")
    monkeypatch.setattr(attom, "SALES_FILE", tmp_path / "sales.json")

    called = []

    def mock_live(_key):
        called.append(True)
        raise attom.AttomApiError("should not be called")

    monkeypatch.setattr(attom, "fetch_live_attom", mock_live)

    snap = attom.get_or_refresh_market_snapshot("130_kingfisher", sb=None)

    assert not called, "fetch_live_attom should not be called with blank API key"
    assert snap["as_is_market_estimate"] == attom._DEFAULT_AS_IS
