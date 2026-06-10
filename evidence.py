"""
evidence.py
-----------
Unified evidence package: walkthrough + photo analysis + property metadata.
Feeds budget-driven ROI scenarios with explicit precedence and confidence tiers.
"""
from __future__ import annotations

import re
from typing import Any

from walkthrough import enrich_walkthrough_items, prepare_walkthrough_row

# Walkthrough zone → photo analysis room_type aliases
_ZONE_ROOM_ALIASES: dict[str, list[str]] = {
    "kitchen": ["kitchen"],
    "great room": ["living room", "great room", "family room"],
    "primary bathroom": ["primary bathroom", "master bathroom", "bathroom"],
    "primary bedroom": ["primary bedroom", "master bedroom", "bedroom"],
    "entry foyer": ["entry", "foyer", "hallway"],
    "sun room": ["sun room", "sunroom", "bonus room"],
    "garage": ["garage"],
    "exterior": ["exterior", "outside", "front", "backyard", "deck", "patio"],
    "laundry": ["laundry", "utility"],
    "dining room": ["dining room"],
    "guest bathroom": ["guest bathroom", "bathroom"],
    "whole house": ["whole house", "interior", "unknown"],
}

# Component substring → keywords to match photo issue/upgrade text
_COMPONENT_PHOTO_KEYS: list[tuple[str, list[str]]] = [
    ("countertop", ["countertop", "counter top", "laminate counter", "granite counter"]),
    ("cabinet", ["cabinet", "cupboard"]),
    ("fireplace", ["fireplace", "mantel", "gas log"]),
    ("vanity", ["vanity", "cultured marble"]),
    ("flooring", ["flooring", "floor", "carpet", "hardwood", "tile floor", "laminate floor"]),
    ("light fixture", ["light fixture", "lighting", "chandelier", "ceiling fan"]),
    ("paint", ["paint", "wall color", "trim"]),
    ("garage door", ["garage door"]),
    ("deck", ["deck", "railing"]),
    ("driveway", ["driveway"]),
    ("gutter", ["gutter", "downspout", "drainage"]),
    ("roof", ["roof", "shingle"]),
    ("window", ["window"]),
    ("door", ["door"]),
    ("appliance", ["appliance", "refrigerator", "range", "dishwasher"]),
    ("faucet", ["faucet", "fixture"]),
    ("sink", ["sink"]),
    ("toilet", ["toilet"]),
    ("water heater", ["water heater"]),
    ("hvac", ["hvac", "furnace", "air handler"]),
    ("dryer vent", ["dryer vent", "dryer"]),
    ("gfci", ["gfci", "outlet"]),
    ("ceiling", ["ceiling", "popcorn"]),
]

_PROPERTY_FACTS_DEFAULT: dict[str, Any] = {
    "address": "130 Kingfisher Dr, Simpsonville SC 29680",
    "year_built": 1999,
    "sqft": 2019,
    "beds": 3,
    "baths": 2,
    "market_value": 276_810,
    "subdivision": "River Ridge, Greenville County SC",
}


def default_property_facts() -> dict[str, Any]:
    return dict(_PROPERTY_FACTS_DEFAULT)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _component_keys(component: str) -> list[str]:
    comp = _norm(component)
    keys = []
    for key, patterns in _COMPONENT_PHOTO_KEYS:
        if key in comp or any(p in comp for p in patterns):
            keys.append(key)
    if not keys:
        for word in comp.split():
            if len(word) > 3:
                keys.append(word)
    return keys or [comp]


def _zone_rooms(zone: str) -> list[str]:
    z = _norm(zone)
    return _ZONE_ROOM_ALIASES.get(z, [z])


def _match_photo_texts(
    zone: str,
    component: str,
    photo_summary: dict[str, Any],
) -> list[str]:
    rooms = _zone_rooms(zone)
    comp_keys = _component_keys(component)
    matched: list[str] = []

    issues_by_room = photo_summary.get("issues_by_room") or {}
    upgrades_by_room = photo_summary.get("upgrades_by_room") or {}

    for room, texts in {**issues_by_room, **upgrades_by_room}.items():
        room_n = _norm(room)
        if not any(alias in room_n or room_n in alias for alias in rooms):
            continue
        for text in texts:
            t = _norm(text)
            if any(k in t for k in comp_keys):
                if text not in matched:
                    matched.append(text)

    dated_freq = photo_summary.get("dated_features_by_frequency") or {}
    for text, _count in dated_freq.items():
        t = _norm(text)
        if any(k in t for k in comp_keys):
            if text not in matched:
                matched.append(text)

    crit = photo_summary.get("critical_and_high_issues") or []
    for text in crit:
        t = _norm(text)
        if any(k in t for k in comp_keys):
            if text not in matched:
                matched.append(text)

    return matched[:5]


def _agree(note: str, photo_observations: list[str]) -> bool:
    if not note or not photo_observations:
        return False
    n = _norm(note)
    aging = ("laminate", "dated", "original", "builder", "worn", "popcorn", "1999")
    damage = ("stain", "crack", "leak", "damage", "broken", "structural")
    for photo in photo_observations:
        p = _norm(photo)
        if any(k in n and k in p for k in aging):
            return True
        if any(k in n and k in p for k in damage):
            return True
        if "laminate" in n and ("laminate" in p or "counter" in p):
            return True
    return False


def _classify_tier(
    walkthrough_note: str | None,
    photo_observations: list[str],
    looks_fine: bool,
    property_context: list[str],
) -> str:
    if looks_fine:
        return "unknown"
    has_note = bool(walkthrough_note and walkthrough_note.strip())
    has_photo = bool(photo_observations)
    if has_note and has_photo and _agree(walkthrough_note or "", photo_observations):
        return "confirmed"
    if has_note or has_photo:
        return "observed"
    if property_context:
        return "inferred"
    return "unknown"


def _property_context_for_row(
    row: dict[str, Any],
    property_facts: dict[str, Any],
) -> list[str]:
    if row.get("owner_note") or row.get("looks_fine"):
        return []
    year = property_facts.get("year_built")
    category = row.get("category") or ""
    if year and category in ("dated", "cosmetic"):
        return [f"House built {year}; finish may be original to construction"]
    if year and row.get("category") == "inspection_risk":
        return [f"House built {year}; age may affect remaining useful life"]
    return []


def build_evidence_package(
    walkthrough_rows: list[dict[str, Any]],
    photo_summary: dict[str, Any],
    property_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts = property_facts or default_property_facts()
    prepared_rows = [prepare_walkthrough_row(r) for r in walkthrough_rows]
    enriched = enrich_walkthrough_items(prepared_rows)

    components: list[dict[str, Any]] = []
    dismissed: list[str] = []
    walkthrough_only: list[str] = []
    photo_only: list[str] = []
    matched_photo_texts: set[str] = set()

    for row in enriched:
        note = (row.get("owner_note") or "").strip() or None
        photos = _match_photo_texts(row.get("zone", ""), row.get("component", ""), photo_summary)
        for p in photos:
            matched_photo_texts.add(p)
        prop_ctx = _property_context_for_row(row, facts)
        tier = _classify_tier(note, photos, bool(row.get("looks_fine")), prop_ctx)

        entry = {
            "zone": row.get("zone"),
            "component": row.get("component"),
            "layer": row.get("layer"),
            "walkthrough_note": note,
            "looks_fine": bool(row.get("looks_fine")),
            "photo_observations": photos,
            "property_context": prop_ctx,
            "confidence_tier": tier,
            "template_category": row.get("category"),
            "template_risk": row.get("inspection_risk"),
            "include_in_report": row.get("include_in_report", True),
        }
        components.append(entry)

        if row.get("looks_fine"):
            dismissed.append(f"[{row.get('zone', '').title()}] {row['component']}")
        elif note and not photos:
            walkthrough_only.append(f"[{row.get('zone', '').title()}] {row['component']}: \"{note}\"")
        elif photos and not note and not row.get("looks_fine"):
            pass  # photo fills gap on component entry

    # Photo findings with no component match
    all_photo_texts: list[str] = []
    for texts in (photo_summary.get("issues_by_room") or {}).values():
        all_photo_texts.extend(texts)
    for texts in (photo_summary.get("upgrades_by_room") or {}).values():
        all_photo_texts.extend(texts)
    for text, _ in (photo_summary.get("dated_features_by_frequency") or {}).items():
        all_photo_texts.append(text)
    for text in photo_summary.get("critical_and_high_issues") or []:
        all_photo_texts.append(text)

    seen = set()
    for text in all_photo_texts:
        if text in matched_photo_texts or text in seen:
            continue
        seen.add(text)
        photo_only.append(text)

    actionable = sum(
        1 for c in components
        if c["confidence_tier"] in ("confirmed", "observed")
        and not c["looks_fine"]
        and (c["walkthrough_note"] or c["photo_observations"])
    )
    needs_input = sum(
        1 for c in components
        if not c["looks_fine"]
        and not c["walkthrough_note"]
        and not c["photo_observations"]
        and c["confidence_tier"] == "unknown"
    )

    return {
        "property_facts": facts,
        "components": components,
        "photo_only_findings": photo_only[:15],
        "walkthrough_only_findings": walkthrough_only,
        "dismissed": dismissed,
        "summary": {
            "actionable": actionable,
            "dismissed": len(dismissed),
            "needs_input": needs_input,
        },
    }


_SCENARIO_TIER_RULES: dict[str, str] = {
    "spend_nothing": "Use Confirmed and Observed walkthrough findings only. Ignore Inferred-tier items.",
    "budget_5k": "Prioritize Confirmed, then Observed. Include Inferred only if budget remains.",
    "budget_15k": "Prioritize Confirmed, then Observed, then selective Inferred.",
    "maximize": "Include all tiers; label Inferred items with low confidence.",
    # Legacy keys
    "executive": "Use Confirmed and Observed walkthrough findings only. Ignore Inferred-tier items.",
    "standard": "Prioritize Confirmed, then Observed, then selective Inferred.",
    "deep_dive": "Include all tiers; label Inferred items with low confidence.",
}


def format_evidence_prompt(package: dict[str, Any], scenario: str = "budget_15k") -> str:
    facts = package.get("property_facts") or {}
    components = package.get("components") or []

    confirmed: list[str] = []
    observed: list[str] = []
    inferred: list[str] = []
    dismissed: list[str] = []

    for c in components:
        zone = (c.get("zone") or "").title()
        comp = c.get("component") or ""
        note = c.get("walkthrough_note")
        photos = c.get("photo_observations") or []
        tier = c.get("confidence_tier") or "unknown"

        if c.get("looks_fine"):
            dismissed.append(f"- [{zone}] {comp}")
            continue

        if tier == "confirmed":
            parts = [f"- [{zone}] {comp}:"]
            if note:
                parts.append(f"walkthrough=\"{note}\"")
            if photos:
                parts.append(f"photo=\"{photos[0]}\"")
            confirmed.append(" ".join(parts))
        elif tier == "observed":
            if note:
                observed.append(f"- [{zone}] {comp}: \"{note}\" (walkthrough)")
            elif photos:
                observed.append(f"- [{zone}] {comp}: \"{photos[0]}\" (photo)")
        elif tier == "inferred":
            ctx = c.get("property_context") or []
            if ctx:
                inferred.append(f"- [{zone}] {comp}: {ctx[0]} (inferred)")

    for text in package.get("photo_only_findings") or []:
        observed.append(f"- [Photo] {text}")

    lines = [
        "EVIDENCE SOURCES (read in this order)",
        "--------------------------------------",
        "1. Walkthrough observations — HIGHEST confidence; seller ground truth",
        "2. Photo analysis findings — MEDIUM confidence; supplemental where walkthrough is silent",
        "3. Property metadata — LOWEST confidence; gap-filler only",
        "",
        "If sources conflict, prefer walkthrough observations.",
        "",
        _SCENARIO_TIER_RULES.get(scenario, _SCENARIO_TIER_RULES["budget_15k"]),
        "",
    ]

    if confirmed:
        lines.extend(["CONFIRMED FINDINGS (multiple sources agree)", "--------------------------------------------", *confirmed, ""])
    if observed:
        lines.extend(["OBSERVED FINDINGS (single direct source)", "-----------------------------------------", *observed, ""])
    if inferred:
        lines.extend(["INFERRED FINDINGS (metadata only — low confidence)", "--------------------------------------------------", *inferred, ""])
    if dismissed:
        lines.extend([
            "DISMISSED BY SELLER (looks_fine — do not recommend)",
            "----------------------------------------------------",
            *dismissed[:40],
            "",
        ])
        if len(dismissed) > 40:
            lines.append(f"... and {len(dismissed) - 40} more dismissed items")
            lines.append("")

    lines.extend([
        "PROPERTY FACTS",
        "--------------",
        f"Built {facts.get('year_built', '?')} | {facts.get('sqft', '?')} sqft | "
        f"{facts.get('beds', '?')} bed / {facts.get('baths', '?')} bath | {facts.get('address', '')}",
        "",
    ])

    return "\n".join(lines)
