import attom
import main


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
