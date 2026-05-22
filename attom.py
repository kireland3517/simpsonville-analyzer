"""
attom.py
────────
Reads local ATTOM JSON cache files and returns clean structured data.
No API calls are made — all data comes from the cached files.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

ASSESSMENT_FILE = Path("attom_assessment.json")
SALES_FILE = Path("attom_sales.json")

ADDRESS = "130 Kingfisher Dr, Simpsonville SC 29680"


def _load(path: Path) -> dict:
    """Load a JSON file and return its contents, or an empty dict on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _dig(obj: Any, *keys) -> Any:
    """Safely traverse nested dicts/lists. Returns None if any key is missing."""
    for key in keys:
        if obj is None:
            return None
        if isinstance(obj, list):
            try:
                obj = obj[key]
            except (IndexError, TypeError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _first_property(data: dict) -> Optional[dict]:
    """Return the first property record from an ATTOM response, or None."""
    props = data.get("property")
    if isinstance(props, list) and props:
        return props[0]
    return None


# ─── Public functions ─────────────────────────────────────────────────────────

def get_property_summary() -> dict:
    """
    Read attom_assessment.json and return key property facts.

    Field sources
    ─────────────
    year_built   → property[0].summary.yearbuilt
    beds         → property[0].building.rooms.beds
    baths        → property[0].building.rooms.bathstotal
    sqft         → property[0].building.size.universalsize
    market_value → property[0].assessment.market.mktttlvalue
    tax_amount   → property[0].assessment.tax.taxamt
    tax_year     → property[0].assessment.tax.taxyear
    """
    data = _load(ASSESSMENT_FILE)
    prop = _first_property(data)

    return {
        "address":      ADDRESS,
        "year_built":   _dig(prop, "summary", "yearbuilt"),
        "beds":         _dig(prop, "building", "rooms", "beds"),
        "baths":        _dig(prop, "building", "rooms", "bathstotal"),
        "sqft":         _dig(prop, "building", "size", "universalsize"),
        "market_value": _dig(prop, "assessment", "market", "mktttlvalue"),
        "tax_amount":   _dig(prop, "assessment", "tax", "taxamt"),
        "tax_year":     _dig(prop, "assessment", "tax", "taxyear"),
    }


def get_last_sale() -> dict:
    """
    Read attom_sales.json and return the most recent sale record.

    Field sources
    ─────────────
    sale_date      → property[0].salehistory[0].saleTransDate
    sale_amount    → property[0].salehistory[0].amount.saleamt
    price_per_sqft → property[0].salehistory[0].calculation.pricepersizeunit
    """
    data = _load(SALES_FILE)
    prop = _first_property(data)
    sale = _dig(prop, "salehistory", 0)

    return {
        "sale_date":      _dig(sale, "saleTransDate"),
        "sale_amount":    _dig(sale, "amount", "saleamt"),
        "price_per_sqft": _dig(sale, "calculation", "pricepersizeunit"),
    }
