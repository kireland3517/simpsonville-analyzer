"""
market_summary.py
─────────────────
Market snapshot retrieval, listing-range computation, and comp data for the
Property Data tab and ROI engine.
"""
from __future__ import annotations

from attom import get_or_refresh_market_snapshot

# ─── Hardcoded River Ridge comps (fallback when comp_data not in snapshot) ────
# Source: _PROPERTY_CONTEXT in roi.py
_HARDCODED_COMPS = [
    {
        "address":    "4 Kingfisher Dr",
        "beds": 3, "baths": 2, "sqft": 1891, "year_built": 1996,
        "sale_date":  "2026-04", "sale_price": 289_000,
        "pct_of_list": 100, "notes": "",
    },
    {
        "address":    "102 Blue Heron Cir",
        "beds": 4, "baths": 2, "sqft": 2290, "year_built": 2017,
        "sale_date":  "2026-04", "sale_price": 319_900,
        "pct_of_list": 96,
        "notes": "OUTLIER: newer construction",
    },
    {
        "address":    "307 Blue Heron Cir",
        "beds": 3, "baths": 2, "sqft": 1782, "year_built": 2007,
        "sale_date":  "2025-12", "sale_price": 301_000,
        "pct_of_list": 99, "notes": "",
    },
    {
        "address":    "2 Turnstone Ct",
        "beds": 3, "baths": 2, "sqft": 1238, "year_built": 1999,
        "sale_date":  "2025-09", "sale_price": 252_000,
        "pct_of_list": 100, "notes": "",
    },
    {
        "address":    "305 Kingfisher Dr",
        "beds": 3, "baths": 2, "sqft": 1382, "year_built": 2000,
        "sale_date":  "2024-04", "sale_price": 265_000,
        "pct_of_list": None, "notes": "",
    },
    {
        "address":    "413 Kingfisher Dr",
        "beds": 4, "baths": 2, "sqft": 1526, "year_built": 2002,
        "sale_date":  "2024-02", "sale_price": 285_000,
        "pct_of_list": None, "notes": "",
    },
]


# ─── Public functions ──────────────────────────────────────────────────────────

def compute_listing_range(snapshot: dict) -> dict:
    """Return {low, high, mid} from a market snapshot."""
    low  = float(snapshot.get("as_is_market_estimate") or 0)
    high = float(snapshot.get("improved_listing_ceiling") or 0)
    mid  = round((low + high) / 2) if low and high else high or low
    return {"low": low, "high": high, "mid": mid}


def get_comp_data(snapshot: dict) -> list[dict]:
    """Return comp list from the snapshot, falling back to hardcoded River Ridge comps."""
    comps = snapshot.get("comp_data")
    if comps and isinstance(comps, list) and len(comps) > 0:
        return comps
    return _HARDCODED_COMPS


def get_market_summary(property_id: str, sb) -> dict:
    """
    Load the most recent market snapshot for the property and return the full
    summary object used by GET /properties/{id}/market-summary.
    """
    snapshot = get_or_refresh_market_snapshot(property_id, sb)
    return {
        "snapshot":        snapshot,
        "listing_range":   compute_listing_range(snapshot),
        "comps":           get_comp_data(snapshot),
        "freshness_label": snapshot.get("freshness_label", "Unknown"),
        "confidence_label": snapshot.get("confidence_label", "Unknown"),
    }


def refresh_market_snapshot(property_id: str, sb, *, force_live: bool = False) -> dict:
    """Force-refresh the market snapshot and return it."""
    return get_or_refresh_market_snapshot(property_id, sb, force_live=force_live or True)
