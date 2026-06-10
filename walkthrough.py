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


def compute_priority_score(row: dict[str, Any]) -> int | None:
    return row.get("priority_score")


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
    included = [r for r in rows if r.get("include_in_report", True)]
    if not included:
        return ""

    upgrade_rows = _sort_rows_for_prompt(
        [r for r in included if r.get("action") in ("upgrade", "assess") and r.get("layer") == "room"],
        for_repairs=False,
    )
    repair_rows = _sort_rows_for_prompt(
        [r for r in included if r.get("action") in ("fix", "assess") or r.get("layer") == "systems"],
        for_repairs=True,
    )

    def fmt(r: dict) -> str:
        parts = [
            f"- [{r.get('zone', '').title()}] {r['component']}",
            f"category={r.get('category', '—')}",
            f"action={r.get('action', 'assess')}",
            f"visibility={r.get('buyer_visibility', '—')}",
            f"risk={r.get('inspection_risk', '—')}",
        ]
        if r.get("condition_score"):
            parts.append(f"condition={r['condition_score']}/5")
        if r.get("owner_note"):
            parts.append(f"note=\"{r['owner_note']}\"")
        if r.get("estimated_cost_low") or r.get("estimated_cost_high"):
            lo = r.get("estimated_cost_low") or "?"
            hi = r.get("estimated_cost_high") or "?"
            parts.append(f"cost=${lo}–${hi}")
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
    return {"seeded": seeded, "skipped": max(0, total - seeded), "total": total}
