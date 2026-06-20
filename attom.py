"""
attom.py
────────
Reads ATTOM property data from local JSON cache files or the live ATTOM API.

Fallback chain:
  1. Live ATTOM API  (if ATTOM_API_KEY in env and no unexpired DB snapshot)
  2. Local JSON files  (attom_assessment.json + attom_sales.json in repo root)
  3. Hardcoded defaults  (276,810 AVM / 305,000 ceiling)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Resolve file paths relative to this module so they work from any cwd.
_HERE = Path(__file__).parent
ASSESSMENT_FILE = _HERE / "attom_assessment.json"
SALES_FILE      = _HERE / "attom_sales.json"

ADDRESS = "130 Kingfisher Dr, Simpsonville SC 29680"

# Hardcoded fallback constants (match roi.py _DEFAULT_MARKET_VALUE / _ARV_BY_LEVEL["maximize"])
_DEFAULT_AS_IS    = 276_810.0
_DEFAULT_CEILING  = 305_000.0
_SNAPSHOT_TTL_DAYS = 30


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    """Load a JSON file; return empty dict on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _dig(obj: Any, *keys) -> Any:
    """Safely traverse nested dicts/lists; returns None if any key is missing."""
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


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Original public functions (preserved for backwards compatibility) ─────────

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


# ─── New market-snapshot functions ────────────────────────────────────────────

class AttomApiError(Exception):
    """Raised when the live ATTOM API call fails."""


def fetch_live_attom(attom_api_key: str) -> dict:
    """
    Call the ATTOM property detail endpoint for the subject property.
    Returns the raw JSON response dict.
    Raises AttomApiError on non-200 or network error.
    """
    import urllib.request
    import urllib.error

    url = (
        "https://api.attomdata.com/propertyapi/v1.0.0/property/detail"
        "?address1=130+Kingfisher+Dr&address2=Simpsonville+SC+29680"
    )
    req = urllib.request.Request(
        url,
        headers={"apikey": attom_api_key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AttomApiError(f"ATTOM HTTP {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise AttomApiError(f"ATTOM request failed: {exc}") from exc


def normalize_attom_to_snapshot(raw: dict, source: str = "attom_live") -> dict:
    """
    Transform a raw ATTOM API response into the property_market_snapshots row shape.
    Also accepts the output of get_property_summary() (file-based dict).
    """
    prop = _first_property(raw) if "property" in raw else None

    # AVM from nested ATTOM structure
    attom_avm = _dig(prop, "assessment", "market", "mktttlvalue") if prop else None
    # Fallback: accept pre-parsed dict with "market_value" key (from get_property_summary)
    if attom_avm is None:
        attom_avm = raw.get("market_value")

    try:
        as_is = float(attom_avm) if attom_avm is not None else _DEFAULT_AS_IS
    except (TypeError, ValueError):
        as_is = _DEFAULT_AS_IS

    # Confidence based on source and data completeness
    if source == "attom_live":
        freshness_label  = "Fresh (live)"
        confidence_label = "High"
    elif attom_avm is not None:
        freshness_label  = "Cached (file)"
        confidence_label = "Medium"
    else:
        freshness_label  = "Cached (file — partial)"
        confidence_label = "Low"

    return {
        "property_id":              "130_kingfisher",
        "source":                   source,
        "as_is_market_estimate":    as_is,
        "improved_listing_ceiling": _DEFAULT_CEILING,
        "attom_avm":                attom_avm,
        "comp_data":                None,
        "raw_attom_response":       raw if source == "attom_live" else None,
        "freshness_label":          freshness_label,
        "confidence_label":         confidence_label,
        "created_at":               _now_utc(),
        "expires_at":               None,
    }


def load_snapshot_from_files() -> dict:
    """
    Build a market snapshot dict from the local JSON cache files.
    Returns a hardcoded-default snapshot if files are missing.
    """
    assessment   = _load(ASSESSMENT_FILE)
    has_assessment = bool(assessment)
    summary      = get_property_summary()

    if has_assessment and summary.get("market_value") is not None:
        source = "attom_cached"
        raw_for_normalize = summary
    elif has_assessment:
        source = "attom_cached"
        raw_for_normalize = summary
    else:
        return {
            "property_id":              "130_kingfisher",
            "source":                   "attom_cached",
            "as_is_market_estimate":    _DEFAULT_AS_IS,
            "improved_listing_ceiling": _DEFAULT_CEILING,
            "attom_avm":                None,
            "comp_data":                None,
            "raw_attom_response":       None,
            "freshness_label":          "Default (no files)",
            "confidence_label":         "Stale",
            "created_at":               _now_utc(),
            "expires_at":               None,
        }

    snapshot = normalize_attom_to_snapshot(raw_for_normalize, source=source)

    if not has_assessment:
        snapshot["confidence_label"] = "Low"
        snapshot["freshness_label"]  = "Cached (file — partial)"

    return snapshot


def get_or_refresh_market_snapshot(
    property_id: str,
    sb,
    *,
    force_live: bool = False,
) -> dict:
    """
    Return a market snapshot using the freshness hierarchy:
      1. Unexpired DB snapshot (if not force_live)
      2. Live ATTOM API (if ATTOM_API_KEY set)
      3. Local JSON files
      4. Hardcoded defaults

    Never raises — always returns a snapshot dict.
    sb may be None (skips DB reads/writes; used in tests or scripts).
    """
    if sb and not force_live:
        try:
            resp = (
                sb.table("property_market_snapshots")
                .select("*")
                .eq("property_id", property_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            if rows:
                row     = rows[0]
                expires = row.get("expires_at")
                if expires is None:
                    return row
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    if exp_dt > datetime.now(timezone.utc):
                        return row
                except (ValueError, AttributeError):
                    return row
        except Exception:
            pass

    attom_key = os.environ.get("ATTOM_API_KEY", "").strip()
    if attom_key and attom_key not in ("REPLACE_ME_or_leave_blank", ""):
        try:
            raw  = fetch_live_attom(attom_key)
            snap = normalize_attom_to_snapshot(raw, source="attom_live")
            snap["property_id"] = property_id

            from datetime import timedelta
            snap["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(days=_SNAPSHOT_TTL_DAYS)
            ).isoformat()

            if sb:
                try:
                    sb.table("property_market_snapshots").insert(snap).execute()
                except Exception:
                    pass

            return snap
        except AttomApiError:
            pass

    snap = load_snapshot_from_files()
    snap["property_id"] = property_id

    if sb:
        try:
            sb.table("property_market_snapshots").insert(snap).execute()
        except Exception:
            pass

    return snap
