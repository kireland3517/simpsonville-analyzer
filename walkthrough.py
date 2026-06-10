"""
walkthrough.py
----------------
Read-only master template + owner note seeds for the seller walkthrough checklist.
Property-specific rows live in Supabase walkthrough_items table.
"""
from __future__ import annotations

from typing import Any

PROPERTY_ID = "130_kingfisher"
WALKTHROUGH_TABLE = "walkthrough_items"

_VISIBILITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
_ACTION_ORDER = {"fix": 0, "upgrade": 1, "assess": 2, "skip": 3}

_CONDITION_WEIGHTS = {1: 40, 2: 30, 3: 15, 4: 5, 5: 0}
_VISIBILITY_WEIGHTS = {"high": 25, "medium": 12, "low": 3}
_RISK_WEIGHTS = {"high": 30, "medium": 15, "low": 3}
_ACTION_WEIGHTS = {"fix": 20, "upgrade": 12, "assess": 8, "skip": -30}

_HIGH_IMPACT_ZONES = frozenset({
    "kitchen", "great room", "primary bathroom", "primary bedroom", "entry foyer", "exterior",
})

# (low, high) installed cost estimates keyed by component substring (lowercase).
_COMPONENT_COST_ANCHORS: list[tuple[str, int, int]] = [
    ("countertop", 1800, 4000),
    ("cabinet", 1500, 3500),
    ("interior paint", 3000, 5000),
    ("trim paint", 200, 450),
    ("closet paint", 400, 800),
    ("popcorn ceiling", 2500, 4500),
    ("garage door", 1600, 2400),
    ("water damage", 300, 900),
    ("ceiling water", 300, 900),
    ("fireplace", 150, 900),
    ("vanity", 500, 2000),
    ("bath modernization", 4500, 9000),
    ("flooring", 5000, 9500),
    ("pressure wash", 200, 500),
    ("front porch", 300, 1200),
    ("driveway", 300, 1200),
    ("gutters", 400, 1500),
    ("drainage", 400, 1500),
    ("exterior paint", 3500, 6500),
    ("exterior lighting", 800, 2000),
    ("landscaping", 500, 1500),
    ("light fixture", 150, 350),
    ("faucet", 150, 350),
    ("sink", 150, 500),
    ("appliance", 100, 4000),
    ("gfci", 150, 250),
    ("roof", 400, 14000),
    ("hvac", 100, 9000),
    ("water heater", 500, 1500),
    ("deck", 600, 1200),
    ("door", 100, 2500),
    ("window", 150, 800),
]

_PROJECT_GROUP_RULES: list[tuple[str, list[str]]] = [
    ("Interior Paint Refresh", ["interior paint", "trim paint", "closet paint", "walls / paint", "door paint", "popcorn ceiling"]),
    ("Master Bathroom", ["vanity", "bath modernization", "shower / tub", "primary bathroom"]),
    ("Kitchen Refresh", ["countertop", "cabinet", "sink", "appliance", "backsplash", "range", "dishwasher", "refrigerator"]),
    ("Exterior Curb Appeal", ["pressure wash", "front porch", "landscaping", "exterior paint", "exterior lighting", "driveway"]),
    ("Roof & Drainage", ["roof", "gutters", "drainage", "downspout"]),
    ("Garage", ["garage door", "garage floor", "garage walls"]),
    ("Ceiling & Moisture", ["ceiling water", "ceiling seam", "water intrusion", "water damage"]),
    ("Doors & Hardware", ["door assessment", "exterior doors", "door hardware", "door operation", "interior doors"]),
    ("Flooring", ["flooring"]),
    ("Fireplace", ["fireplace"]),
    ("HVAC & Mechanical", ["hvac", "water heater", "thermostat", "condensate", "dryer vent"]),
    ("Electrical Safety", ["gfci", "electrical panel", "smoke detector", "co detector"]),
    ("Plumbing", ["faucet", "leak", "drain", "toilet", "plumbing"]),
]


def _item(
    zone: str,
    component: str,
    layer: str,
    *,
    category: str = "functional",
    buyer_visibility: str = "medium",
    inspection_risk: str = "low",
    sort_order: int = 0,
    action: str = "assess",
    owner_note: str | None = None,
    source: str = "template",
    include_in_report: bool = True,
    condition_score: int | None = None,
    estimated_cost_low: int | None = None,
    estimated_cost_high: int | None = None,
) -> dict[str, Any]:
    return {
        "property_id": PROPERTY_ID,
        "zone": zone,
        "component": component,
        "layer": layer,
        "category": category,
        "condition_score": condition_score,
        "action": action,
        "owner_note": owner_note,
        "buyer_visibility": buyer_visibility,
        "inspection_risk": inspection_risk,
        "estimated_cost_low": estimated_cost_low,
        "estimated_cost_high": estimated_cost_high,
        "priority_score": None,
        "sort_order": sort_order,
        "include_in_report": include_in_report,
        "source": source,
    }


def _room_zone(zone: str, components: list[tuple], base_order: int, layer: str = "room") -> list[dict]:
    rows = []
    for i, comp in enumerate(components):
        name, cat, vis, risk = comp[:4]
        offset = comp[4] if len(comp) > 4 else i
        rows.append(_item(zone, name, layer, category=cat, buyer_visibility=vis,
                          inspection_risk=risk, sort_order=base_order + offset))
    return rows


_ROOM_ZONES: list[tuple[str, list, int]] = [
    ("entry foyer", [
        ("Front door", "functional", "high", "medium"),
        ("Lockset / deadbolt", "functional", "high", "low"),
        ("Flooring", "cosmetic", "high", "low"),
        ("Baseboards", "cosmetic", "medium", "low"),
        ("Walls / paint", "cosmetic", "high", "low"),
        ("Light fixture", "cosmetic", "high", "low"),
        ("Smoke detector", "functional", "low", "medium"),
    ], 100),
    ("great room", [
        ("Flooring", "cosmetic", "high", "low"),
        ("Paint", "cosmetic", "high", "low"),
        ("Ceiling condition", "cosmetic", "medium", "low"),
        ("Ceiling seam", "functional", "low", "medium"),
        ("Windows", "functional", "high", "low"),
        ("Blinds / shades", "cosmetic", "medium", "low"),
        ("Light fixtures", "cosmetic", "high", "low"),
        ("Ceiling fan", "cosmetic", "high", "low"),
        ("Fireplace", "functional", "high", "low"),
        ("Built-ins", "cosmetic", "medium", "low"),
        ("Electrical outlets", "functional", "low", "medium"),
    ], 200),
    ("kitchen", [
        ("Cabinets", "cosmetic", "high", "low"),
        ("Cabinet hardware", "cosmetic", "high", "low"),
        ("Countertops", "dated", "high", "low"),
        ("Sink / faucet", "functional", "high", "medium"),
        ("Garbage disposal", "functional", "medium", "low"),
        ("Under-sink leaks", "functional", "low", "high"),
        ("Refrigerator", "dated", "high", "low"),
        ("Dishwasher", "dated", "high", "low"),
        ("Range / oven", "dated", "high", "low"),
        ("Microwave", "dated", "medium", "low"),
        ("Range hood", "dated", "medium", "low"),
        ("Appliances (overall)", "functional", "high", "low"),
        ("Backsplash", "cosmetic", "medium", "low"),
        ("Lighting", "cosmetic", "high", "low"),
        ("GFCI outlets", "functional", "low", "high"),
        ("Pantry shelving", "cosmetic", "low", "low"),
    ], 300),
    ("dining room", [
        ("Flooring", "cosmetic", "high", "low"),
        ("Walls", "cosmetic", "medium", "low"),
        ("Light fixture", "cosmetic", "high", "low"),
        ("Windows", "functional", "medium", "low"),
        ("Baseboards", "cosmetic", "medium", "low"),
    ], 400),
    ("primary bedroom", [
        ("Flooring", "cosmetic", "high", "low"),
        ("Paint", "cosmetic", "high", "low"),
        ("Closet paint", "cosmetic", "medium", "low"),
        ("Baseboards", "cosmetic", "medium", "low"),
        ("Windows", "functional", "medium", "low"),
        ("Closet doors", "cosmetic", "medium", "low"),
        ("Closet shelving", "cosmetic", "low", "low"),
        ("Ceiling fan", "cosmetic", "high", "low"),
        ("Light fixture", "cosmetic", "high", "low"),
        ("Outlets", "functional", "low", "low"),
        ("Door hardware", "cosmetic", "medium", "low"),
    ], 500),
    ("bedroom 2", [
        ("Flooring", "cosmetic", "high", "low"),
        ("Paint", "cosmetic", "medium", "low"),
        ("Baseboards", "cosmetic", "medium", "low"),
        ("Windows", "functional", "medium", "low"),
        ("Closet doors", "cosmetic", "medium", "low"),
        ("Ceiling fan", "cosmetic", "medium", "low"),
        ("Light fixture", "cosmetic", "medium", "low"),
        ("Outlets", "functional", "low", "low"),
        ("Door hardware", "cosmetic", "medium", "low"),
    ], 600),
    ("bedroom 3", [
        ("Flooring", "cosmetic", "high", "low"),
        ("Paint", "cosmetic", "medium", "low"),
        ("Baseboards", "cosmetic", "medium", "low"),
        ("Windows", "functional", "medium", "low"),
        ("Closet doors", "cosmetic", "medium", "low"),
        ("Ceiling fan", "cosmetic", "medium", "low"),
        ("Light fixture", "cosmetic", "medium", "low"),
        ("Outlets", "functional", "low", "low"),
        ("Door hardware", "cosmetic", "medium", "low"),
    ], 700),
    ("primary bathroom", [
        ("Vanity cabinet", "dated", "high", "low"),
        ("Vanity mirror", "cosmetic", "high", "low"),
        ("Vanity faucet", "functional", "high", "low"),
        ("Bath modernization", "cosmetic", "high", "low"),
        ("Shower / tub grout", "cosmetic", "high", "low"),
        ("Shower / tub caulk", "functional", "medium", "medium"),
        ("Tile condition", "cosmetic", "high", "low"),
        ("Toilet", "functional", "medium", "medium"),
        ("Exhaust fan", "functional", "low", "medium"),
        ("Lighting", "cosmetic", "high", "low"),
        ("GFCI outlet", "functional", "low", "high"),
        ("Towel bars / hardware", "cosmetic", "low", "low"),
    ], 800),
    ("full bath", [
        ("Vanity cabinet", "dated", "high", "low"),
        ("Vanity mirror", "cosmetic", "high", "low"),
        ("Vanity faucet", "functional", "high", "low"),
        ("Shower / tub grout", "cosmetic", "high", "low"),
        ("Shower / tub caulk", "functional", "medium", "medium"),
        ("Tile condition", "cosmetic", "high", "low"),
        ("Toilet", "functional", "medium", "medium"),
        ("Exhaust fan", "functional", "low", "medium"),
        ("Lighting", "cosmetic", "high", "low"),
        ("GFCI outlet", "functional", "low", "high"),
    ], 900),
    ("laundry room", [
        ("Washer hookups", "functional", "low", "medium"),
        ("Dryer vent", "functional", "low", "high"),
        ("Utility sink", "functional", "low", "medium"),
        ("Flooring", "cosmetic", "medium", "low"),
        ("Shelving", "cosmetic", "low", "low"),
        ("Lighting", "cosmetic", "low", "low"),
    ], 1000),
    ("hallways", [
        ("Paint", "cosmetic", "medium", "low"),
        ("Flooring", "cosmetic", "medium", "low"),
        ("Baseboards", "cosmetic", "medium", "low"),
        ("Smoke detectors", "functional", "low", "medium"),
        ("Light fixtures", "cosmetic", "medium", "low"),
        ("Linen closet shelving", "cosmetic", "low", "low"),
    ], 1100),
    ("sun room", [
        ("Outlets", "functional", "medium", "low"),
        ("Switch plates", "functional", "low", "low"),
        ("Ceiling fan / light fixture", "cosmetic", "high", "low"),
        ("Doors", "functional", "high", "low"),
    ], 1200),
    ("interior doors", [
        ("Door operation", "functional", "high", "low"),
        ("Door latching", "functional", "medium", "low"),
        ("Hinge condition", "functional", "low", "low"),
        ("Door hardware", "cosmetic", "high", "low"),
        ("Door paint", "cosmetic", "medium", "low"),
        ("Door assessment (all doors)", "functional", "high", "medium"),
    ], 1300),
    ("windows", [
        ("Window operation", "functional", "medium", "low"),
        ("Window locks", "functional", "low", "medium"),
        ("Screens", "functional", "low", "low"),
        ("Broken seals", "functional", "medium", "medium"),
        ("Window trim", "cosmetic", "medium", "low"),
    ], 1400),
    ("garage", [
        ("Garage door", "functional", "high", "high"),
        ("Garage floor", "cosmetic", "medium", "low"),
        ("Garage walls", "cosmetic", "low", "low"),
    ], 1500),
    ("whole house", [
        ("Interior paint — walls", "cosmetic", "high", "low"),
        ("Trim paint — baseboards + door frames", "cosmetic", "medium", "low"),
        ("Popcorn ceiling", "dated", "medium", "low"),
        ("Interior light fixtures", "cosmetic", "high", "low"),
        ("Flooring (overall)", "cosmetic", "high", "low"),
        ("Faucets — sinks / tubs", "functional", "medium", "medium"),
        ("Ceiling water damage", "inspection_risk", "medium", "high"),
    ], 1600),
]

_SYSTEMS_ZONES: list[tuple[str, list, int]] = [
    ("exterior", [
        ("Roof condition", "inspection_risk", "low", "high"),
        ("Gutters / downspouts / drainage", "inspection_risk", "low", "high"),
        ("Siding damage", "inspection_risk", "medium", "high"),
        ("Rotten trim", "inspection_risk", "medium", "high"),
        ("Exterior caulk", "functional", "low", "medium"),
        ("Exterior paint", "cosmetic", "high", "low"),
        ("Pressure wash — house / driveway / deck", "cosmetic", "high", "low"),
        ("Driveway cracks", "cosmetic", "medium", "medium"),
        ("Walkway trip hazards", "inspection_risk", "medium", "high"),
        ("Deck condition", "inspection_risk", "high", "high"),
        ("Front porch repair / repaint", "cosmetic", "high", "low"),
        ("Fence condition", "cosmetic", "low", "medium"),
        ("Landscaping / yard", "cosmetic", "high", "low"),
        ("Exterior lighting", "cosmetic", "high", "low"),
        ("Exterior doors (sun room, front, garage)", "functional", "high", "medium"),
    ], 2000),
    ("structural / moisture", [
        ("Foundation cracks", "inspection_risk", "low", "high"),
        ("Water intrusion", "inspection_risk", "low", "high"),
        ("Crawlspace access door", "functional", "low", "medium"),
        ("Settlement signs", "inspection_risk", "low", "high"),
    ], 2100),
    ("hvac", [
        ("HVAC age", "inspection_risk", "low", "high"),
        ("HVAC service history", "functional", "low", "high"),
        ("Filter condition", "functional", "low", "medium"),
        ("Condensate line", "functional", "low", "medium"),
        ("Thermostat", "functional", "low", "low"),
    ], 2200),
    ("electrical", [
        ("GFCI / AFCI protection", "inspection_risk", "low", "high"),
        ("Electrical panel", "inspection_risk", "low", "high"),
        ("Missing cover plates", "functional", "low", "medium"),
        ("Smoke detectors", "functional", "low", "high"),
        ("CO detectors", "functional", "low", "high"),
    ], 2300),
    ("plumbing", [
        ("Active leaks", "inspection_risk", "low", "high"),
        ("Water pressure", "functional", "low", "medium"),
        ("Drain speed", "functional", "low", "medium"),
        ("Water heater age", "inspection_risk", "low", "high"),
    ], 2400),
]

OWNER_NOTE_SEEDS: dict[tuple[str, str], dict[str, Any]] = {
    ("whole house", "Trim paint — baseboards + door frames"): {
        "owner_note": "2 gallons trim paint needed for baseboards + door frames",
        "action": "upgrade",
        "source": "owner",
    },
    ("sun room", "Outlets"): {"owner_note": "7 outlets observed", "source": "owner"},
    ("sun room", "Switch plates"): {"owner_note": "2 switches observed", "source": "owner"},
    ("sun room", "Ceiling fan / light fixture"): {
        "owner_note": "1 ceiling fan/light fixture; ~200 sqft room", "source": "owner",
    },
    ("sun room", "Doors"): {"owner_note": "2 doors; ~200 sqft total", "source": "owner"},
    ("interior doors", "Door assessment (all doors)"): {
        "owner_note": "Assess all doors for repair/paint/replace including crawl space",
        "action": "assess", "source": "owner",
    },
    ("exterior", "Exterior doors (sun room, front, garage)"): {
        "owner_note": "3 exterior doors: sun room, front, garage",
        "action": "assess", "source": "owner",
    },
    ("kitchen", "Countertops"): {
        "owner_note": "Assess for replacement — dated laminate?",
        "action": "assess", "source": "owner",
    },
    ("exterior", "Pressure wash — house / driveway / deck"): {
        "owner_note": "Pressure wash and assess entire house, driveway, deck",
        "action": "assess", "source": "owner",
    },
    ("exterior", "Exterior lighting"): {
        "owner_note": "Assess exterior lighting", "action": "assess", "source": "owner",
    },
    ("exterior", "Front porch repair / repaint"): {
        "owner_note": "Patch/repair/repaint front porch", "action": "fix", "source": "owner",
    },
    ("exterior", "Landscaping / yard"): {
        "owner_note": "Landscaping and yard refresh needed", "action": "assess", "source": "owner",
    },
    ("whole house", "Popcorn ceiling"): {
        "owner_note": "Popcorn ceiling throughout — assess removal",
        "action": "assess", "source": "owner",
    },
    ("whole house", "Interior paint — walls"): {
        "owner_note": "Paint interior walls and trim", "action": "upgrade", "source": "owner",
    },
    ("whole house", "Ceiling water damage"): {
        "owner_note": "Repair water damaged ceilings",
        "action": "fix", "inspection_risk": "high", "source": "owner",
    },
    ("great room", "Ceiling seam"): {
        "owner_note": "Vaulted ceiling top seam needs assessment", "action": "assess", "source": "owner",
    },
    ("great room", "Fireplace"): {
        "owner_note": "Assess fireplace — ignites? remote? gas logs? mantel?",
        "action": "assess", "source": "owner",
    },
    ("primary bathroom", "Vanity mirror"): {
        "owner_note": "Evaluate vanity/mirror replacement", "action": "assess", "source": "owner",
    },
    ("primary bedroom", "Closet paint"): {
        "owner_note": "Master closet repaint", "action": "upgrade", "source": "owner",
    },
    ("primary bathroom", "Bath modernization"): {
        "owner_note": "Modernization of master bathroom", "action": "upgrade", "source": "owner",
    },
    ("kitchen", "Cabinets"): {
        "owner_note": "Kitchen cabinets — hardware, damage, repair/replacement",
        "action": "assess", "source": "owner",
    },
    ("kitchen", "Sink / faucet"): {
        "owner_note": "Kitchen sink — fixtures and leaks", "action": "assess", "source": "owner",
    },
    ("kitchen", "Appliances (overall)"): {
        "owner_note": "Full appliance assessment", "action": "assess", "source": "owner",
    },
    ("whole house", "Flooring (overall)"): {
        "owner_note": "Floor evaluation — whole house", "action": "assess", "source": "owner",
    },
    ("whole house", "Interior light fixtures"): {
        "owner_note": "Assess interior light fixtures throughout", "action": "assess", "source": "owner",
    },
    ("garage", "Garage door"): {
        "owner_note": "Garage door, floor, and walls — door has confirmed structural crack",
        "action": "fix", "inspection_risk": "high", "source": "owner",
    },
    ("exterior", "Driveway cracks"): {
        "owner_note": "Driveway cracks — assess repair scope", "action": "assess", "source": "owner",
    },
    ("whole house", "Faucets — sinks / tubs"): {
        "owner_note": "Assess all faucets — sinks and bathtubs", "action": "assess", "source": "owner",
    },
    ("exterior", "Gutters / downspouts / drainage"): {
        "owner_note": "Roof drainage solution needed",
        "action": "fix", "inspection_risk": "high", "source": "owner",
    },
}


def build_template_rows(property_id: str = PROPERTY_ID) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for zone, components, base in _ROOM_ZONES:
        for row in _room_zone(zone, components, base):
            row["property_id"] = property_id
            rows.append(row)
    for zone, components, base in _SYSTEMS_ZONES:
        for row in _room_zone(zone, components, base, layer="systems"):
            row["property_id"] = property_id
            rows.append(row)
    return rows


def apply_owner_seeds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        merged = {**row}
        key = (row["zone"], row["component"])
        if key in OWNER_NOTE_SEEDS:
            merged.update(OWNER_NOTE_SEEDS[key])
        out.append(merged)
    return out


def seed_rows(property_id: str = PROPERTY_ID) -> list[dict[str, Any]]:
    return apply_owner_seeds(build_template_rows(property_id))


def _estimate_cost_range(row: dict[str, Any]) -> tuple[int | None, int | None]:
    component = (row.get("component") or "").lower()
    zone = (row.get("zone") or "").lower()
    text = f"{component} {zone}"
    for key, lo, hi in _COMPONENT_COST_ANCHORS:
        if key in text:
            return lo, hi
    action = row.get("action") or "assess"
    if action == "assess":
        return 100, 500
    if row.get("layer") == "systems":
        return 200, 1500
    return 150, 750


def _derive_project_group(row: dict[str, Any]) -> str:
    component = (row.get("component") or "").lower()
    for group, keys in _PROJECT_GROUP_RULES:
        if any(k in component for k in keys):
            return group
    zone = (row.get("zone") or "").replace("_", " ").title()
    return zone or "General"


def _derive_report_type(row: dict[str, Any], bucket: str) -> str:
    action = row.get("action") or "assess"
    if action == "skip" or bucket == "Leave Alone":
        return "none"
    if action == "fix" or row.get("layer") == "systems" or row.get("category") == "inspection_risk":
        return "repair"
    if action == "upgrade" or row.get("category") in ("cosmetic", "dated"):
        return "upgrade"
    if row.get("inspection_risk") == "high":
        return "repair"
    return "upgrade" if row.get("buyer_visibility") == "high" else "repair"


def _derive_recommendation_bucket(row: dict[str, Any], priority: int) -> str:
    action = row.get("action") or "assess"
    if action == "skip":
        return "Leave Alone"
    if action == "fix" or row.get("inspection_risk") == "high":
        return "Fix Before Listing"
    if row.get("category") == "dated" and action == "skip":
        return "Leave Alone"
    if action == "upgrade":
        return "Consider Upgrading"
    if row.get("category") in ("cosmetic", "dated") and row.get("buyer_visibility") == "high":
        return "Consider Upgrading"
    if priority >= 55 and row.get("inspection_risk") in ("high", "medium"):
        return "Fix Before Listing"
    if priority >= 45 and row.get("buyer_visibility") == "high":
        return "Consider Upgrading"
    if priority < 25:
        return "Leave Alone"
    return "Consider Upgrading"


def _derive_urgency(row: dict[str, Any]) -> str:
    risk = row.get("inspection_risk") or "low"
    condition = row.get("condition_score")
    if risk == "high" or (condition is not None and condition <= 2):
        return "high"
    if risk == "medium" or (condition is not None and condition == 3):
        return "medium"
    return "low"


def _derive_buyer_impact(row: dict[str, Any]) -> str:
    visibility = row.get("buyer_visibility") or "medium"
    zone = (row.get("zone") or "").lower()
    if visibility == "high" or zone in _HIGH_IMPACT_ZONES:
        return "high"
    if visibility == "medium":
        return "medium"
    return "low"


def _derive_roi_confidence(row: dict[str, Any], bucket: str) -> str:
    if bucket == "Leave Alone":
        return "low"
    if row.get("category") in ("cosmetic", "dated") and row.get("buyer_visibility") == "high":
        return "high"
    if row.get("action") == "fix" and row.get("inspection_risk") == "high":
        return "high"
    if row.get("buyer_visibility") == "high":
        return "medium"
    return "low"


def calculate_priority_score(row: dict[str, Any]) -> int:
    score = 0
    condition = row.get("condition_score")
    if condition is not None:
        score += _CONDITION_WEIGHTS.get(condition, 0)
    score += _VISIBILITY_WEIGHTS.get(row.get("buyer_visibility") or "low", 3)
    score += _RISK_WEIGHTS.get(row.get("inspection_risk") or "low", 3)
    score += _ACTION_WEIGHTS.get(row.get("action") or "assess", 8)
    return min(100, max(0, score))


def calculate_walkthrough_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Compute system/AI fields from row inputs. Does not mutate row."""
    priority = calculate_priority_score(row)
    bucket = _derive_recommendation_bucket(row, priority)
    cost_lo, cost_hi = _estimate_cost_range(row)
    return {
        "estimated_cost_low": cost_lo,
        "estimated_cost_high": cost_hi,
        "priority_score": priority,
        "recommendation_bucket": bucket,
        "report_type": _derive_report_type(row, bucket),
        "roi_confidence": _derive_roi_confidence(row, bucket),
        "buyer_impact": _derive_buyer_impact(row),
        "urgency": _derive_urgency(row),
        "project_group": _derive_project_group(row),
    }


def enrich_walkthrough_item(row: dict[str, Any]) -> dict[str, Any]:
    """Merge calculated fields; respect user overrides for cost and priority."""
    out = {**row}
    calc = calculate_walkthrough_fields(row)

    out["recommendation_bucket"] = calc["recommendation_bucket"]
    out["report_type"] = calc["report_type"]
    out["roi_confidence"] = calc["roi_confidence"]
    out["buyer_impact"] = calc["buyer_impact"]
    out["urgency"] = calc["urgency"]
    out["project_group"] = calc["project_group"]

    if not row.get("cost_overridden"):
        out["estimated_cost_low"] = calc["estimated_cost_low"]
        out["estimated_cost_high"] = calc["estimated_cost_high"]
    if not row.get("priority_overridden"):
        out["priority_score"] = calc["priority_score"]
    elif row.get("priority_score") is None:
        out["priority_score"] = calc["priority_score"]

    return out


def enrich_walkthrough_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_walkthrough_item(r) for r in rows]


def apply_calculated_persist_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Return DB-safe fields to persist after calculation."""
    enriched = enrich_walkthrough_item(row)
    updates: dict[str, Any] = {
        "recommendation_bucket": enriched.get("recommendation_bucket"),
        "report_type": enriched.get("report_type"),
        "roi_confidence": enriched.get("roi_confidence"),
        "buyer_impact": enriched.get("buyer_impact"),
        "urgency": enriched.get("urgency"),
        "project_group": enriched.get("project_group"),
    }
    if not row.get("cost_overridden"):
        updates["estimated_cost_low"] = enriched.get("estimated_cost_low")
        updates["estimated_cost_high"] = enriched.get("estimated_cost_high")
    if not row.get("priority_overridden"):
        updates["priority_score"] = enriched.get("priority_score")
    return updates


def recalculate_all_items(sb, property_id: str = PROPERTY_ID) -> dict:
    rows = load_walkthrough_items(sb, property_id)
    updated = 0
    for row in rows:
        fields = apply_calculated_persist_fields(row)
        try:
            sb.table(WALKTHROUGH_TABLE).update({**fields, "updated_at": "now()"}).eq("id", row["id"]).execute()
            updated += 1
        except Exception:
            pass
    return {"recalculated": updated, "total": len(rows)}


def _sort_rows_for_prompt(rows: list[dict], *, for_repairs: bool) -> list[dict]:
    def key(r: dict) -> tuple:
        ps = r.get("priority_score")
        if ps is not None:
            return (0, -ps)
        if for_repairs:
            return (1, _RISK_ORDER.get(r.get("inspection_risk") or "low", 9),
                    _ACTION_ORDER.get(r.get("action") or "assess", 9))
        return (1, _VISIBILITY_ORDER.get(r.get("buyer_visibility") or "low", 9),
                _ACTION_ORDER.get(r.get("action") or "assess", 9))

    return sorted(rows, key=key)


def build_walkthrough_prompt_block(rows: list[dict[str, Any]]) -> str:
    enriched = enrich_walkthrough_items(rows)
    included = [r for r in enriched if r.get("include_in_report", True)]
    if not included:
        return ""

    upgrade_rows = _sort_rows_for_prompt(
        [r for r in included if r.get("report_type") == "upgrade" or r.get("action") == "upgrade"],
        for_repairs=False,
    )
    repair_rows = _sort_rows_for_prompt(
        [r for r in included if r.get("report_type") == "repair" or r.get("action") == "fix"],
        for_repairs=True,
    )
    assess_rows = [r for r in included if r.get("action") == "assess" and r not in upgrade_rows and r not in repair_rows]
    upgrade_rows = upgrade_rows + assess_rows[:5]

    def fmt(r: dict) -> str:
        parts = [
            f"- [{r.get('zone', '').title()}] {r['component']}",
            f"category={r.get('category', '—')}",
            f"action={r.get('action', 'assess')}",
            f"bucket={r.get('recommendation_bucket', '—')}",
            f"visibility={r.get('buyer_visibility', '—')}",
            f"risk={r.get('inspection_risk', '—')}",
        ]
        if r.get("condition_score"):
            parts.append(f"condition={r['condition_score']}/5")
        if r.get("priority_score") is not None:
            parts.append(f"priority={r['priority_score']}")
        if r.get("project_group"):
            parts.append(f"group=\"{r['project_group']}\"")
        if r.get("owner_note"):
            parts.append(f"note=\"{r['owner_note']}\"")
        if r.get("estimated_cost_low") or r.get("estimated_cost_high"):
            lo = r.get("estimated_cost_low") or "?"
            hi = r.get("estimated_cost_high") or "?"
            parts.append(f"cost=${lo}–${hi}")
        if r.get("action") == "assess":
            parts.append("scope=evaluate and quote")
        return " | ".join(parts)

    lines = [
        "SELLER WALKTHROUGH — PROPERTY-SPECIFIC FINDINGS (treat as ground truth)",
        "---------------------------------------------------------------------------",
        "These rows come from the homeowner walkthrough checklist.",
        "Items with action=fix MUST appear in repairs. action=upgrade MUST appear in upgrades.",
        "action=assess items should be scoped as evaluate-and-quote with realistic cost ranges.",
        "category=dated + action=skip → do NOT recommend unless condition_score <= 2.",
        "Consolidate related items (e.g. interior paint + closet paint + trim paint → one paint upgrade).",
        "",
    ]

    if upgrade_rows:
        lines.append("WALKTHROUGH — UPGRADE CANDIDATES:")
        lines.extend(fmt(r) for r in upgrade_rows[:20])
        lines.append("")

    if repair_rows:
        lines.append("WALKTHROUGH — REPAIR CANDIDATES:")
        lines.extend(fmt(r) for r in repair_rows[:20])
        lines.append("")

    return "\n".join(lines)


def load_walkthrough_items(sb, property_id: str = PROPERTY_ID) -> list[dict[str, Any]]:
    if not sb:
        return []
    try:
        result = (
            sb.table(WALKTHROUGH_TABLE)
            .select("*")
            .eq("property_id", property_id)
            .order("sort_order")
            .execute()
        )
        return result.data or []
    except Exception:
        return []


def seed_walkthrough_items(sb, property_id: str = PROPERTY_ID, *, force: bool = False) -> dict:
    if not sb:
        return {"seeded": 0, "skipped": 0, "total": 0, "error": "Supabase not configured"}

    existing = load_walkthrough_items(sb, property_id)
    if existing and not force:
        return {"seeded": 0, "skipped": len(existing), "total": len(existing)}

    rows = seed_rows(property_id)
    if existing and force:
        existing_keys = {(r["zone"], r["component"], r["layer"]) for r in existing}
        rows = [r for r in rows if (r["zone"], r["component"], r["layer"]) not in existing_keys]

    if not rows:
        return {"seeded": 0, "skipped": len(existing), "total": len(existing)}

    seeded = 0
    for row in rows:
        try:
            sb.table(WALKTHROUGH_TABLE).insert(row).execute()
            seeded += 1
        except Exception:
            pass

    total = len(load_walkthrough_items(sb, property_id))
    if seeded:
        recalculate_all_items(sb, property_id)
    return {"seeded": seeded, "skipped": max(0, total - seeded), "total": total}
