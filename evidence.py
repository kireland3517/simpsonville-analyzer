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
    "exterior": [
        "exterior", "outside", "front", "backyard", "deck", "patio",
        "exterior front", "exterior front porch", "exterior rear/side",
        "exterior rear", "exterior side", "porch",
    ],
    "laundry": ["laundry", "utility"],
    "dining room": ["dining room"],
    "guest bathroom": ["guest bathroom", "bathroom"],
    "whole house": ["whole house", "interior", "unknown"],
    "hvac": ["hvac", "utility", "mechanical", "kitchen", "hallway", "whole house"],
    "electrical": ["electrical", "kitchen", "garage", "utility", "whole house", "interior"],
}

# Component substring → keywords to match photo issue/upgrade text (legacy / profile seed)
_COMPONENT_PHOTO_KEYS: list[tuple[str, list[str]]] = [
    ("countertop", ["countertop", "counter top", "laminate counter", "granite counter"]),
    ("cabinet", ["cabinet", "cupboard", "cabinet stack"]),
    ("fireplace", ["fireplace", "mantel", "gas log"]),
    ("vanity", ["vanity", "cultured marble"]),
    ("flooring", ["flooring", "floor", "carpet", "hardwood", "tile floor", "laminate floor"]),
    ("light fixture", ["light fixture", "lighting", "chandelier", "ceiling fan"]),
    ("paint", ["repaint", "wall color"]),
    ("garage door", ["garage door"]),
    ("deck", ["deck", "deck stair", "deck stairs"]),
    ("porch", ["porch", "porch column", "porch beam", "porch ceiling", "porch railing", "balustrade"]),
    ("driveway", ["driveway"]),
    ("gutter", ["gutter", "downspout", "downspouts", "drainage", "splash block"]),
    ("crawlspace", ["crawl space", "crawlspace", "crawl space vent", "foundation vent"]),
    ("siding", ["vinyl siding", "j-channel", "j channel", "siding", "algae", "mildew", "biological growth"]),
    ("carpet", ["carpet", "carpeting"]),
    ("baluster", ["baluster", "balusters", "balustrade"]),
    ("storm door", ["storm door"]),
    ("bifold", ["bifold", "hollow core"]),
    ("door casing", ["door casing", "casing trim"]),
    ("roof", ["roof", "shingle", "roofline"]),
    ("window", ["window"]),
    ("appliance", ["appliance", "refrigerator", "range", "dishwasher", "cooktop", "glass cooktop"]),
    ("faucet", ["faucet"]),
    ("sink", ["sink"]),
    ("toilet", ["toilet"]),
    ("water heater", ["water heater"]),
    ("hvac", ["hvac", "furnace", "air handler", "return air", "return-air"]),
    ("dryer vent", ["dryer vent", "dryer"]),
    ("gfci", ["gfci", "afci"]),
    ("wiring", ["exposed wire", "electrical wire", "open wiring", "dangling wire"]),
    ("filter", ["filter", "return air", "vent cover", "air vent", "grille"]),
    ("landscaping", ["shrub", "shrubs", "foundation plant", "vegetation", "overgrown", "against siding"]),
    ("ceiling", ["ceiling", "popcorn"]),
    ("stringer", ["stringer", "footing", "post base"]),
    ("railing", ["railing", "rail", "balustrade"]),
]

# Generic tokens — must not drive assignment on their own
_GENERIC_KEY_BLOCKLIST = frozenset({
    "condition", "door", "paint", "line", "cover", "panel", "interior", "exterior",
    "damage", "repair", "assess", "functional", "cosmetic", "fixture", "outlet",
})

# Photo finding substring → tag, specificity (higher = more specific)
_PHOTO_FINDING_PATTERNS: list[tuple[str, str, int]] = [
    # Deck
    ("deck stair", "deck_stairs", 10),
    ("deck stairs", "deck_stairs", 10),
    ("deck support post", "deck_post", 9),
    ("stringer", "stringer", 9),
    ("footing", "footing", 9),
    ("post base", "post_base", 9),
    ("post bases", "post_base", 9),
    ("wood-to-soil", "footing", 8),
    ("weathering", "weathered", 7),
    ("graying", "graying", 7),
    ("deck rail", "deck_rail", 8),
    # Porch
    ("porch column", "porch_column", 10),
    ("porch ceiling board", "ceiling_board", 10),
    ("porch ceiling", "ceiling_board", 9),
    ("porch beam", "beam", 10),
    ("porch railing", "porch_railing", 10),
    ("balustrade", "balustrade", 9),
    ("chalking", "chalking", 8),
    ("peeling paint", "peeling", 8),
    ("paint peeling", "peeling", 8),
    ("porch", "porch", 7),
    # Electrical / wiring
    ("exposed electrical wire", "exposed_wire", 11),
    ("exposed wire", "exposed_wire", 10),
    ("dangling wire", "exposed_wire", 10),
    ("loose wire", "exposed_wire", 10),
    ("unsecured wire", "exposed_wire", 10),
    ("open wiring", "exposed_wire", 10),
    ("electrical wire", "exposed_wire", 10),
    ("plug end", "exposed_wire", 9),
    # HVAC / return air
    ("return air", "return_air", 10),
    ("return-air", "return_air", 10),
    ("vent cover", "vent_cover", 9),
    ("air vent", "vent_cover", 8),
    ("grille", "grille", 7),
    ("dust buildup", "dust_buildup", 9),
    ("clogged", "clogged", 8),
    # Landscaping
    ("foundation shrub", "landscaping", 10),
    ("shrubs directly", "landscaping", 10),
    ("against siding", "against_siding", 9),
    ("overgrown", "overgrown", 8),
    ("vegetation", "vegetation", 7),
    ("shrub", "landscaping", 7),
    # Appliances
    ("glass cooktop", "cooktop", 10),
    ("cooktop", "cooktop", 9),
    ("range surface", "cooktop", 8),
    ("glass top", "cooktop", 8),
    # Other useful
    ("garage door", "garage_door", 10),
    ("cardboard", "cabinet_shim", 9),
    ("packing material", "cabinet_shim", 9),
    ("popcorn", "popcorn", 9),
    ("water damage", "water_damage", 10),
    ("water stain", "water_damage", 9),
    ("smoke odor", "smoke", 10),
    ("garage door panel", "garage_door", 9),
    # Gutters / drainage
    ("downspout", "downspout", 10),
    ("splash block", "downspout", 9),
    # Crawlspace
    ("crawl space vent", "crawlspace_vent", 11),
    ("crawlspace vent", "crawlspace_vent", 11),
    # Siding
    ("vinyl siding", "vinyl_siding", 10),
    ("j-channel", "siding_trim", 10),
    ("j channel", "siding_trim", 10),
    ("biological growth", "siding_bio", 11),
    ("algae", "siding_bio", 9),
    ("mildew", "siding_bio", 9),
    # Flooring / carpet
    ("carpet", "carpet", 9),
    # Balusters / railing
    ("baluster", "balustrade", 10),
    ("balusters", "balustrade", 10),
    # Doors
    ("storm door", "storm_door", 10),
    ("bifold", "bifold_door", 9),
    ("hollow core", "interior_door", 8),
    ("door casing", "door_trim", 9),
    # Stairs / structure
    ("stair tread", "exterior_stairs", 10),
    ("ground-contact", "footing", 10),
    ("loose brick", "structural_brick", 8),
    # Paint upgrades (dated_features bucket)
    ("repaint all walls", "interior_paint", 10),
    ("agreeable gray", "interior_paint", 8),
]

# Explicit tag weights per walkthrough component (zone, component)
_COMPONENT_TAG_OVERRIDES: dict[tuple[str, str], dict[str, int]] = {
    ("exterior", "deck condition"): {
        "deck": 6, "deck_stairs": 12, "stringer": 12, "footing": 12, "post_base": 11,
        "deck_post": 10, "deck_rail": 8, "railing": 7, "balustrade": 10, "weathered": 6, "graying": 6,
        "exterior_stairs": 11,
    },
    ("exterior", "front porch repair / repaint"): {
        "porch": 10, "porch_column": 12, "column": 6, "balustrade": 12, "porch_railing": 12,
        "railing": 8, "beam": 11, "ceiling_board": 11, "peeling": 9, "chalking": 9,
        "structural_brick": 8,
    },
    ("exterior", "landscaping / yard"): {
        "landscaping": 12, "vegetation": 9, "overgrown": 10, "against_siding": 11,
        "shrub": 8,
    },
    ("exterior", "pressure wash — house / driveway / deck"): {
        "deck": 5, "deck_stairs": 6, "weathered": 4, "porch": 4,
    },
    ("exterior", "exterior paint"): {
        "peeling": 8, "chalking": 8, "porch": 6, "paint": 4,
    },
    ("exterior", "rotten trim"): {
        "peeling": 7, "porch": 6, "beam": 6, "against_siding": 5,
    },
    ("electrical", "gfci / afci protection"): {
        "exposed_wire": 14, "gfci": 8, "wiring": 10,
    },
    ("hvac", "filter condition"): {
        "return_air": 14, "vent_cover": 12, "grille": 10, "filter": 8,
        "clogged": 11, "dust_buildup": 12,
    },
    ("hvac", "hvac age"): {
        "return_air": 8, "hvac": 6, "clogged": 6, "dust_buildup": 7,
    },
    ("kitchen", "appliances (overall)"): {
        "cooktop": 12, "appliance": 8,
    },
    ("kitchen", "cabinets"): {
        "cabinet": 10, "cabinet_shim": 12,
    },
    ("kitchen", "cabinet hardware"): {
        "cabinet": 8,
    },
    ("garage", "garage door"): {
        "garage_door": 14, "door": 3,
    },
    ("structural / moisture", "crawlspace"): {
        "crawlspace_vent": 14, "crawlspace": 10,
    },
    ("structural / moisture", "water intrusion"): {
        "crawlspace_vent": 10, "water_damage": 8,
    },
    ("exterior", "gutters / downspouts / drainage"): {
        "downspout": 14, "gutter": 10,
    },
    ("exterior", "siding damage"): {
        "vinyl_siding": 12, "siding_bio": 13, "siding_trim": 11,
    },
    ("exterior", "walkway trip hazards"): {
        "exterior_stairs": 10, "footing": 8,
    },
    ("whole house", "interior paint — walls"): {
        "interior_paint": 12, "paint": 5,
    },
    ("interior doors", "door assessment (all doors)"): {
        "bifold_door": 12, "interior_door": 10, "door_trim": 9,
    },
    ("entry foyer", "front door"): {
        "storm_door": 14, "door": 4,
    },
    ("entry foyer", "walls / paint"): {
        "interior_paint": 10, "paint": 6,
    },
    ("entry foyer", "flooring"): {
        "carpet": 12, "flooring": 8,
    },
    ("primary bedroom", "flooring"): {
        "carpet": 12, "flooring": 8,
    },
    ("great room", "flooring"): {
        "carpet": 12, "flooring": 8,
    },
    ("guest bedroom", "flooring"): {
        "carpet": 12, "flooring": 8,
    },
}

# Cross-zone: (zone, component fragment) → tags that waive room mismatch
_CROSS_ZONE_TAG_RULES: list[tuple[str, str, frozenset[str]]] = [
    ("hvac", "filter", frozenset({"return_air", "vent_cover", "grille", "clogged", "dust_buildup"})),
    ("electrical", "gfci", frozenset({"exposed_wire"})),
    ("kitchen", "appliances", frozenset({"cooktop"})),
]

_MIN_ASSIGNMENT_SCORE = 7.0

_META_ONLY_RE = re.compile(
    r"^photo\s+(?:is\s+)?rotated|^photo\s+orientation\s+is\s+rotated",
    re.IGNORECASE,
)

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


def _strip_meta_prefix(text: str) -> tuple[str | None, bool]:
    """
    Return (matchable_text, was_meta_stripped).
    None = pure photo-quality observation with no domain finding — exclude.
    """
    original = (text or "").strip()
    if not original:
        return None, False

    lowered = _norm(original)
    if not _META_ONLY_RE.search(lowered):
        return original, False

    stripped = original
    for pattern in (
        r"^photo\s+is\s+rotated[^,.]*[.,]\s*(?:but\s+)?",
        r"^photo\s+is\s+rotated[^—]*—\s*(?:but\s+)?",
        r"^photo\s+is\s+rotated[^,]*,\s*(?:but\s+)?",
        r"^photo\s+orientation\s+is\s+rotated[^,.]*[.,]\s*(?:but\s+)?",
        r"^photo\s+is\s+rotated\s+\d+\s+degrees[^,.]*[.,]\s*(?:but\s+)?",
        r"^photo\s+is\s+rotated\s+\d+\s+degrees[^—]*—\s*(?:but\s+)?",
    ):
        stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE).strip()

    if not stripped or _META_ONLY_RE.search(_norm(stripped)):
        return None, True

    return stripped, True


def _tags_in_photo(text: str) -> list[tuple[str, int]]:
    """Return (tag, specificity) for patterns found in photo text."""
    t = _norm(text)
    found: dict[str, int] = {}
    for pattern, tag, specificity in sorted(
        _PHOTO_FINDING_PATTERNS, key=lambda x: len(x[0]), reverse=True,
    ):
        if pattern in t:
            found[tag] = max(found.get(tag, 0), specificity)
    return [(tag, score) for tag, score in found.items()]


def _component_tag_profile(zone: str, component: str) -> dict[str, int]:
    key = (_norm(zone), _norm(component))
    if key in _COMPONENT_TAG_OVERRIDES:
        return dict(_COMPONENT_TAG_OVERRIDES[key])

    profile: dict[str, int] = {}
    comp_n = _norm(component)
    for pattern_key, patterns in _COMPONENT_PHOTO_KEYS:
        tag = pattern_key.replace(" ", "_")
        if pattern_key in comp_n or any(p in comp_n for p in patterns):
            profile[tag] = max(profile.get(tag, 0), 5)
            for p in patterns:
                pt = p.replace(" ", "_")
                if pt not in _GENERIC_KEY_BLOCKLIST:
                    profile[pt] = max(profile.get(pt, 0), 4)
    return profile


def _component_keys(component: str) -> list[str]:
    """Return match tokens for a component (key + expanded patterns)."""
    comp = _norm(component)
    keys: list[str] = []
    for key, patterns in _COMPONENT_PHOTO_KEYS:
        if key in comp or any(p in comp for p in patterns):
            keys.append(key)
            keys.extend(patterns)
    if keys:
        return list(dict.fromkeys(keys))
    return [comp]


def _zone_rooms(zone: str) -> list[str]:
    z = _norm(zone)
    return _ZONE_ROOM_ALIASES.get(z, [z])


def _room_matches_zone(photo_rooms: list[str], zone: str) -> bool:
    if not photo_rooms:
        return False
    aliases = _zone_rooms(zone)
    for room in photo_rooms:
        if room in ("critical_and_high_issues", "dated_features"):
            continue
        room_n = _norm(room)
        if any(alias in room_n or room_n in alias for alias in aliases):
            return True
    return False


def _cross_zone_match(zone: str, component: str, tags: set[str]) -> bool:
    comp_n = _norm(component)
    zone_n = _norm(zone)
    for rule_zone, comp_fragment, required_tags in _CROSS_ZONE_TAG_RULES:
        if rule_zone in zone_n and comp_fragment in comp_n and tags & required_tags:
            return True
    return False


def _score_photo_to_component(
    photo_text: str,
    photo_rooms: list[str],
    zone: str,
    component: str,
) -> float:
    tags = _tags_in_photo(photo_text)
    if not tags:
        return 0.0

    profile = _component_tag_profile(zone, component)
    if not profile:
        return 0.0

    tag_set = {tag for tag, _ in tags}
    score = 0.0
    specific_hit = False

    for tag, specificity in tags:
        weight = profile.get(tag, 0)
        if weight <= 0:
            continue
        score += weight * (specificity / 10.0)
        if tag not in _GENERIC_KEY_BLOCKLIST and weight >= 6:
            specific_hit = True

    if score <= 0:
        return 0.0

    if _room_matches_zone(photo_rooms, zone):
        score += 12.0
    elif _cross_zone_match(zone, component, tag_set):
        score += 9.0
    elif "critical_and_high_issues" in photo_rooms and specific_hit:
        score += 4.0
    else:
        score *= 0.25

    if not specific_hit:
        score *= 0.4

    return score


def _collect_photo_findings(photo_summary: dict[str, Any]) -> list[tuple[str, list[str], str]]:
    """Return list of (original_text, rooms, source_bucket)."""
    findings: list[tuple[str, list[str], str]] = []
    seen: set[str] = set()

    def add(text: str, room: str, source: str) -> None:
        if not text or text in seen:
            return
        seen.add(text)
        findings.append((text, [room], source))

    for room, texts in (photo_summary.get("issues_by_room") or {}).items():
        for text in texts:
            add(text, room, "issues_by_room")

    for room, texts in (photo_summary.get("upgrades_by_room") or {}).items():
        for text in texts:
            # merge room lists if duplicate text from upgrades
            existing = next((f for f in findings if f[0] == text), None)
            if existing:
                if room not in existing[1]:
                    existing[1].append(room)
            else:
                add(text, room, "upgrades_by_room")

    for text in photo_summary.get("critical_and_high_issues") or []:
        existing = next((f for f in findings if f[0] == text), None)
        if existing:
            if "critical_and_high_issues" not in existing[1]:
                existing[1].append("critical_and_high_issues")
        else:
            seen.add(text)
            findings.append((text, ["critical_and_high_issues"], "critical"))

    for text in (photo_summary.get("dated_features_by_frequency") or {}):
        if text not in seen:
            seen.add(text)
            findings.append((text, ["dated_features"], "dated"))

    return findings


def _legacy_keyword_match(
    matchable: str,
    rooms: list[str],
    zone: str,
    component: str,
) -> bool:
    """Room-scoped keyword match without per-component cap (secondary pass)."""
    tags = _tags_in_photo(matchable)
    profile = _component_tag_profile(zone, component)
    tag_hit = any(profile.get(tag, 0) > 0 for tag, _ in tags)

    keys = _component_keys(component)
    specific = [k for k in keys if k not in _GENERIC_KEY_BLOCKLIST]
    text = _norm(matchable)
    key_hit = any(k in text for k in specific) if specific else False

    if not tag_hit and not key_hit:
        return False

    if _room_matches_zone(rooms, zone):
        return True
    tag_set = {tag for tag, _ in tags}
    if _cross_zone_match(zone, component, tag_set):
        return True
    if "critical_and_high_issues" in rooms and (tag_hit or key_hit):
        return True
    if "dated_features" in rooms and (tag_hit or key_hit):
        return True
    return False


def _assign_photo_findings(
    enriched_rows: list[dict[str, Any]],
    photo_summary: dict[str, Any],
) -> tuple[dict[tuple[str, str], list[str]], list[str], int]:
    """
    Two-pass photo assignment:
    1) Scored best-match for high-specificity domain findings (exclusive).
    2) Legacy room+keyword match without cap for remaining photos.
    Returns (assignments, photo_only, meta_excluded_count).
    """
    assignments: dict[tuple[str, str], list[str]] = {}
    photo_only: list[str] = []
    meta_excluded = 0
    primary_assigned: set[str] = set()

    candidates: list[tuple[str, str, list[str]]] = []
    for original, rooms, _source in _collect_photo_findings(photo_summary):
        matchable, was_meta = _strip_meta_prefix(original)
        if matchable is None:
            meta_excluded += 1
            continue
        if was_meta and not _tags_in_photo(matchable):
            meta_excluded += 1
            continue
        candidates.append((original, matchable, rooms))

    component_rows = [
        (row.get("zone") or "", row.get("component") or "")
        for row in enriched_rows
    ]

    # Pass 1 — scored exclusive assignment for domain-specific findings
    for original, matchable, rooms in candidates:
        best_key: tuple[str, str] | None = None
        best_score = 0.0

        for zone, component in component_rows:
            if not zone or not component:
                continue
            score = _score_photo_to_component(matchable, rooms, zone, component)
            if score > best_score:
                best_score = score
                best_key = (_norm(zone), _norm(component))

        if best_key and best_score >= _MIN_ASSIGNMENT_SCORE:
            assignments.setdefault(best_key, []).append(original)
            primary_assigned.add(original)

    # Pass 2 — legacy match without cap (may attach to multiple components)
    for original, matchable, rooms in candidates:
        if original in primary_assigned:
            continue
        matched_any = False
        for zone, component in component_rows:
            if not zone or not component:
                continue
            if _legacy_keyword_match(matchable, rooms, zone, component):
                key = (_norm(zone), _norm(component))
                bucket = assignments.setdefault(key, [])
                if original not in bucket:
                    bucket.append(original)
                matched_any = True
        if not matched_any:
            photo_only.append(original)

    return assignments, photo_only, meta_excluded


def _match_photo_texts(
    zone: str,
    component: str,
    photo_summary: dict[str, Any],
    *,
    assignments: dict[tuple[str, str], list[str]] | None = None,
) -> list[str]:
    """Return photo observations assigned to a component (no cap)."""
    if assignments is not None:
        return list(assignments.get((_norm(zone), _norm(component)), []))

    prepared = [{"zone": zone, "component": component}]
    enriched = enrich_walkthrough_items([prepare_walkthrough_row(r) for r in prepared])
    assign, _, _ = _assign_photo_findings(enriched, photo_summary)
    return list(assign.get((_norm(zone), _norm(component)), []))


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

    assignments, photo_only, meta_excluded = _assign_photo_findings(enriched, photo_summary)
    matched_photo_texts: set[str] = set()
    for photos in assignments.values():
        matched_photo_texts.update(photos)

    components: list[dict[str, Any]] = []
    dismissed: list[str] = []
    walkthrough_only: list[str] = []

    for row in enriched:
        included = bool(row.get("include_in_report"))
        note = (row.get("owner_note") or "").strip() or None
        zone = row.get("zone") or ""
        component = row.get("component") or ""
        photos = list(assignments.get((_norm(zone), _norm(component)), []))
        prop_ctx = _property_context_for_row(row, facts)
        tier = _classify_tier(note, photos, bool(row.get("looks_fine")), prop_ctx)

        entry = {
            "zone": zone,
            "component": component,
            "layer": row.get("layer"),
            "walkthrough_note": note,
            "looks_fine": bool(row.get("looks_fine")),
            "photo_observations": photos,
            "property_context": prop_ctx,
            "confidence_tier": tier,
            "template_category": row.get("category"),
            "template_risk": row.get("inspection_risk"),
            "include_in_report": included,
        }
        components.append(entry)

        if not included:
            continue
        if row.get("looks_fine"):
            dismissed.append(f"[{zone.title()}] {component}")
        elif note and not photos:
            walkthrough_only.append(f"[{zone.title()}] {component}: \"{note}\"")
        elif photos and not note and not row.get("looks_fine"):
            pass  # photo fills gap on component entry

    actionable = sum(
        1 for c in components
        if c.get("include_in_report")
        and c["confidence_tier"] in ("confirmed", "observed")
        and not c["looks_fine"]
        and (c["walkthrough_note"] or c["photo_observations"])
    )
    needs_input = sum(
        1 for c in components
        if c.get("include_in_report")
        and not c["looks_fine"]
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
        "matching_stats": {
            "meta_observations_excluded": meta_excluded,
            "photos_assigned": len(matched_photo_texts),
            "photo_only_count": len(photo_only),
        },
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
    seller_ok: list[str] = []

    for c in components:
        if not c.get("include_in_report"):
            continue

        zone = (c.get("zone") or "").title()
        comp = c.get("component") or ""
        note = c.get("walkthrough_note")
        photos = c.get("photo_observations") or []
        tier = c.get("confidence_tier") or "unknown"

        if c.get("looks_fine"):
            if note:
                seller_ok.append(f"- [{zone}] {comp}: \"{note}\" (seller reports no concerns)")
            else:
                seller_ok.append(f"- [{zone}] {comp} (seller reports no concerns)")
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
    if seller_ok:
        lines.extend([
            "SELLER CONFIRMED OK (no concerns — include as negative evidence)",
            "----------------------------------------------------------------",
            *seller_ok[:40],
            "",
        ])
    if dismissed:
        lines.extend([
            "DISMISSED BY SELLER (no concerns — do not recommend)",
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
