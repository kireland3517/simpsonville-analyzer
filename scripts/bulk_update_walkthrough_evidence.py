"""
Bulk-update walkthrough evidence (owner_note + include_in_report only).

Does NOT recalculate priorities, buckets, ROI fields, or regenerate reports.

Usage:
    python scripts/bulk_update_walkthrough_evidence.py              # dry-run (live DB)
    python scripts/bulk_update_walkthrough_evidence.py --simulate   # dry-run (template seeds)
    python scripts/bulk_update_walkthrough_evidence.py --apply      # write to Supabase

Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in environment or .env at project root.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Match other modules: load .env from cwd, then explicit project root paths.
load_dotenv()
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")

from supabase import create_client

from walkthrough import PROPERTY_ID, WALKTHROUGH_TABLE, seed_rows

# ---------------------------------------------------------------------------
# Observation mappings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceMapping:
    zone: str
    component: str
    lines: tuple[str, ...]
    include_in_report: bool = True


EVIDENCE_MAPPINGS: list[EvidenceMapping] = [
    # Background context — stored but excluded from ROI evidence prompt
    EvidenceMapping(
        "whole house",
        "Property context",
        (
            "Property is being prepared for resale.",
            "House was built in 1999.",
            "Many visible finishes appear original or near-original.",
        ),
        include_in_report=False,
    ),
    EvidenceMapping(
        "whole house",
        "Popcorn ceiling",
        ("Popcorn ceilings are present throughout the home.",),
    ),
    EvidenceMapping(
        "whole house",
        "Flooring (overall)",
        ("Flooring throughout the house requires evaluation.",),
    ),
    EvidenceMapping(
        "whole house",
        "Interior light fixtures",
        ("Interior light fixtures require evaluation.",),
    ),
    EvidenceMapping(
        "whole house",
        "Faucets — sinks / tubs",
        ("Faucets throughout the house require evaluation.",),
    ),
    EvidenceMapping(
        "whole house",
        "Indoor air quality",
        (
            "Strong cigarette smoke odor is present inside the home.",
            "Odor appears consistent with long-term tobacco smoke exposure.",
            "Extent of odor absorption into finishes and building materials is unknown.",
        ),
    ),
    # GREAT ROOM
    EvidenceMapping("great room", "Ceiling condition", ("Great room contains vaulted ceilings.",)),
    EvidenceMapping("great room", "Ceiling seam", ("Vaulted ceiling seam is present.",)),
    EvidenceMapping(
        "great room",
        "Ceiling fan",
        ("Existing ceiling fan/light fixture hangs unusually low.",),
    ),
    EvidenceMapping(
        "great room",
        "Fireplace",
        (
            "Fireplace is a major focal point of the room.",
            "Existing fireplace surround is present.",
        ),
    ),
    # KITCHEN
    EvidenceMapping(
        "kitchen",
        "Lighting",
        ("Kitchen has limited natural light and no window directly serving the main workspace.",),
    ),
    EvidenceMapping(
        "kitchen",
        "Countertops",
        (
            "Countertops are brown/taupe suede-style Corian, appear functionally serviceable, "
            "and no cracking or structural damage is currently known.",
        ),
    ),
    EvidenceMapping("kitchen", "Cabinets", ("Kitchen cabinets require evaluation.",)),
    EvidenceMapping("kitchen", "Sink / faucet", ("Kitchen sink area requires evaluation.",)),
    EvidenceMapping(
        "kitchen",
        "Appliances (overall)",
        ("Kitchen appliances require evaluation as a complete package.",),
    ),
    # PRIMARY SUITE
    EvidenceMapping(
        "primary bathroom",
        "Vanity cabinet",
        (
            "Primary bathroom vanity is located outside the toilet/tub/shower room.",
            "Existing vanity area requires evaluation.",
        ),
    ),
    EvidenceMapping(
        "primary bathroom",
        "Vanity mirror",
        ("Existing mirror configuration requires evaluation.",),
    ),
    EvidenceMapping(
        "primary bathroom",
        "Bath modernization",
        ("Existing jetted tub is present.", "Existing shower is present."),
    ),
    # CEILINGS / MOISTURE
    EvidenceMapping(
        "whole house",
        "Ceiling water damage",
        (
            "Water-damaged ceiling areas are present.",
            "Source of historical staining has not been fully confirmed.",
        ),
    ),
    # GARAGE
    EvidenceMapping("garage", "Garage door", ("Garage door is approximately 30 years old.",)),
    EvidenceMapping("garage", "Garage floor", ("Garage floor requires evaluation.",)),
    EvidenceMapping("garage", "Garage walls", ("Garage walls require evaluation.",)),
    EvidenceMapping(
        "garage",
        "Garage ceiling height",
        ("Garage ceiling height is approximately 10 feet.",),
    ),
    # EXTERIOR
    EvidenceMapping("exterior", "Driveway cracks", ("Driveway cracks are present.",)),
    EvidenceMapping("exterior", "Exterior lighting", ("Exterior lighting requires evaluation.",)),
    EvidenceMapping(
        "exterior",
        "Pressure wash — house / driveway / deck",
        ("Exterior surfaces may benefit from pressure washing.",),
    ),
    # DECK
    EvidenceMapping(
        "exterior",
        "Deck condition",
        (
            "Existing rear deck requires evaluation.",
            "Deck condition, safety, appearance, and remaining life should be documented.",
            "Structural condition has not yet been confirmed.",
        ),
    ),
    # SUNROOM
    EvidenceMapping(
        "sun room",
        "Room context",
        (
            "Sunroom should be treated as a distinct area.",
            "Sunroom is approximately 20 feet by 10 feet.",
            "Sunroom finishes, paint, flooring, fixtures, and lighting require evaluation.",
        ),
    ),
    # CRAWLSPACE / SYSTEMS
    EvidenceMapping(
        "structural / moisture",
        "Crawlspace",
        ("Crawlspace requires evaluation.",),
    ),
    EvidenceMapping(
        "hvac",
        "HVAC age",
        ("HVAC status, age, condition, and service history not yet confirmed.",),
    ),
    EvidenceMapping(
        "plumbing",
        "Water heater age",
        ("Water heater status, age, condition, and service history not yet confirmed.",),
    ),
    EvidenceMapping(
        "plumbing",
        "Plumbing system",
        ("Plumbing system status, age, condition, and service history not yet confirmed.",),
    ),
    EvidenceMapping(
        "electrical",
        "Electrical panel",
        ("Electrical system status, age, condition, and service history not yet confirmed.",),
    ),
    EvidenceMapping(
        "electrical",
        "Smoke detectors",
        ("Smoke detector status not yet confirmed.",),
    ),
    EvidenceMapping(
        "electrical",
        "CO detectors",
        ("Carbon monoxide detector status not yet confirmed.",),
    ),
    # INTERIOR DOORS
    EvidenceMapping(
        "interior doors",
        "Door hardware",
        ("Door hardware throughout the house requires evaluation.",),
    ),
    EvidenceMapping(
        "interior doors",
        "Hinge condition",
        ("Door hinges throughout the house require evaluation.",),
    ),
]

# Existing notes preserved verbatim — only flip include_in_report when a note exists.
@dataclass(frozen=True)
class FactualNoteReplacement:
    """Replace entire note when existing text contains a factual error trigger."""
    triggers: tuple[str, ...]
    lines: tuple[str, ...]


PRESERVE_NOTE_KEYS: frozenset[tuple[str, str]] = frozenset({
    ("whole house", "interior paint — walls"),
    ("whole house", "trim paint — baseboards + door frames"),
    ("exterior", "front porch repair / repaint"),
    ("exterior", "landscaping / yard"),
    ("exterior", "gutters / downspouts / drainage"),
    ("exterior", "exterior doors (sun room, front, garage)"),
    ("primary bedroom", "closet paint"),
    ("sun room", "outlets"),
    ("sun room", "switch plates"),
    ("sun room", "doors"),
    ("sun room", "ceiling fan / light fixture"),
})

NEW_ITEMS: list[dict] = [
    {
        "zone": "whole house",
        "component": "Property context",
        "layer": "room",
        "category": "functional",
        "buyer_visibility": "low",
        "inspection_risk": "low",
        "sort_order": 1595,
        "action": "assess",
        "source": "owner",
        "include_in_report": False,
    },
    {
        "zone": "whole house",
        "component": "Indoor air quality",
        "layer": "room",
        "category": "inspection_risk",
        "buyer_visibility": "high",
        "inspection_risk": "high",
        "sort_order": 1596,
        "action": "assess",
        "source": "owner",
    },
    {
        "zone": "garage",
        "component": "Garage ceiling height",
        "layer": "room",
        "category": "functional",
        "buyer_visibility": "low",
        "inspection_risk": "low",
        "sort_order": 1503,
        "action": "assess",
        "source": "owner",
    },
    {
        "zone": "sun room",
        "component": "Room context",
        "layer": "room",
        "category": "functional",
        "buyer_visibility": "medium",
        "inspection_risk": "low",
        "sort_order": 1201,
        "action": "assess",
        "source": "owner",
    },
    {
        "zone": "structural / moisture",
        "component": "Crawlspace",
        "layer": "systems",
        "category": "inspection_risk",
        "buyer_visibility": "low",
        "inspection_risk": "high",
        "sort_order": 2101,
        "action": "assess",
        "source": "owner",
    },
    {
        "zone": "plumbing",
        "component": "Plumbing system",
        "layer": "systems",
        "category": "functional",
        "buyer_visibility": "low",
        "inspection_risk": "medium",
        "sort_order": 2401,
        "action": "assess",
        "source": "owner",
    },
]

_EVAL_PHRASES = (
    "requires evaluation",
    "require evaluation",
)

_STRONG_MARKERS = (
    "confirmed",
    "structural crack",
    "observed",
    "gallon",
    "sqft",
    "repair",
    "needed",
    "patch",
    "repaint",
    "refresh",
    "modernization",
    "drainage solution",
    "3 exterior doors",
    "7 outlets",
    "2 switches",
    "2 doors",
    "repaint",
)


def _norm_key(zone: str, component: str) -> tuple[str, str]:
    return (zone.strip().lower(), component.strip().lower())


FACTUAL_NOTE_REPLACEMENTS: dict[tuple[str, str], FactualNoteReplacement] = {
    _norm_key("kitchen", "Countertops"): FactualNoteReplacement(
        triggers=("laminate", "assess for replacement"),
        lines=(
            "Countertops are brown/taupe suede-style Corian, appear functionally serviceable, "
            "and no cracking or structural damage is currently known.",
        ),
    ),
}


def _sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _normalize_sentence(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip().rstrip("."))


def _sentence_covered(existing: str, candidate: str) -> bool:
    cand = _normalize_sentence(candidate)
    if not cand:
        return True
    exist_norm = _normalize_sentence(existing)
    if cand in exist_norm:
        return True
    cand_tokens = set(re.findall(r"[a-z0-9]+", cand))
    if len(cand_tokens) < 3:
        return cand in exist_norm
    for sent in _sentences(existing):
        sent_tokens = set(re.findall(r"[a-z0-9]+", _normalize_sentence(sent)))
        overlap = len(cand_tokens & sent_tokens) / len(cand_tokens)
        if overlap >= 0.75:
            return True
    return False


def _is_evaluation_only(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _EVAL_PHRASES) and not any(m in t for m in _STRONG_MARKERS)


def _is_strong_note(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in _STRONG_MARKERS) and not _is_evaluation_only(text)


def _format_note_lines(lines: list[str]) -> str:
    formatted = [line if line.endswith(".") else line + "." for line in lines if line.strip()]
    return " ".join(formatted).strip()


def apply_note_update(
    existing: str | None,
    additions: list[str],
    key: tuple[str, str],
) -> tuple[str, list[str], str | None]:
    existing_stripped = (existing or "").strip() or None
    factual = FACTUAL_NOTE_REPLACEMENTS.get(key)
    if factual and existing_stripped:
        existing_lower = existing_stripped.lower()
        if any(trigger in existing_lower for trigger in factual.triggers):
            replacement_lines = [line.strip() for line in factual.lines if line.strip()]
            return _format_note_lines(replacement_lines), replacement_lines, "FACTUAL_REPLACEMENT"
    merged, new_lines = merge_notes(existing_stripped, additions)
    return merged, new_lines, None


def merge_notes(existing: str | None, additions: list[str]) -> tuple[str, list[str]]:
    existing = (existing or "").strip()
    added: list[str] = []

    for line in additions:
        line = line.strip()
        if not line:
            continue
        if existing and _sentence_covered(existing, line):
            continue
        if existing and _is_strong_note(existing) and _is_evaluation_only(line):
            continue
        if not line.endswith("."):
            line = line + "."
        added.append(line)

    if not added:
        return existing, []

    if existing:
        if not existing.endswith("."):
            existing = existing + "."
        merged = existing + " " + " ".join(added)
    else:
        merged = " ".join(added)
    return merged.strip(), added


@dataclass
class PlannedChange:
    zone: str
    component: str
    action: str
    before_note: str | None
    after_note: str
    before_include: bool | None
    after_include: bool
    new_lines: list[str] = field(default_factory=list)
    row_id: str | None = None
    skip_reason: str | None = None
    data_source: str = "live"


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print(
            "ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return create_client(url, key)


def load_rows(sb) -> list[dict]:
    return (
        sb.table(WALKTHROUGH_TABLE)
        .select("*")
        .eq("property_id", PROPERTY_ID)
        .execute()
        .data
        or []
    )


def simulate_rows() -> list[dict]:
    rows = seed_rows(PROPERTY_ID)
    for row in rows:
        row["id"] = str(uuid.uuid4())
    return rows


def index_rows(rows: list[dict]) -> dict[tuple[str, str], dict]:
    return {_norm_key(r.get("zone", ""), r.get("component", "")): r for r in rows}


def plan_changes(rows: list[dict], *, data_source: str = "live") -> list[PlannedChange]:
    by_key = index_rows(rows)
    new_item_defs = {_norm_key(d["zone"], d["component"]): d for d in NEW_ITEMS}
    changes: list[PlannedChange] = []

    for mapping in EVIDENCE_MAPPINGS:
        zone, component = mapping.zone, mapping.component
        key = _norm_key(zone, component)
        row = by_key.get(key)
        target_include = mapping.include_in_report

        if key in PRESERVE_NOTE_KEYS:
            if not row:
                continue
            before = (row.get("owner_note") or "").strip() or None
            if not before:
                continue
            inc = bool(row.get("include_in_report"))
            if inc and not target_include:
                continue
            if inc and target_include:
                continue
            changes.append(
                PlannedChange(
                    zone=zone,
                    component=component,
                    action="update",
                    before_note=before,
                    after_note=before,
                    before_include=inc,
                    after_include=target_include,
                    row_id=row.get("id"),
                    data_source=data_source,
                )
            )
            continue

        additions = list(mapping.lines)

        if row is None and key in new_item_defs:
            merged, new_lines, _ = apply_note_update(None, additions, key)
            changes.append(
                PlannedChange(
                    zone=zone,
                    component=component,
                    action="create",
                    before_note=None,
                    after_note=merged,
                    before_include=None,
                    after_include=target_include,
                    new_lines=new_lines,
                    data_source=data_source,
                )
            )
            continue

        if row is None:
            changes.append(
                PlannedChange(
                    zone=zone,
                    component=component,
                    action="update",
                    before_note=None,
                    after_note="",
                    before_include=None,
                    after_include=target_include,
                    skip_reason="NO_MATCHING_ITEM",
                    data_source=data_source,
                )
            )
            continue

        before = (row.get("owner_note") or "").strip() or None
        merged, new_lines, skip_reason = apply_note_update(before, additions, key)
        inc = bool(row.get("include_in_report"))

        if not new_lines and before:
            if inc == target_include:
                changes.append(
                    PlannedChange(
                        zone=zone,
                        component=component,
                        action="update",
                        before_note=before,
                        after_note=before,
                        before_include=inc,
                        after_include=target_include,
                        skip_reason="ALREADY_COVERED",
                        row_id=row.get("id"),
                        data_source=data_source,
                    )
                )
            else:
                changes.append(
                    PlannedChange(
                        zone=zone,
                        component=component,
                        action="update",
                        before_note=before,
                        after_note=before,
                        before_include=inc,
                        after_include=target_include,
                        row_id=row.get("id"),
                        data_source=data_source,
                    )
                )
            continue

        if not new_lines and not before:
            changes.append(
                PlannedChange(
                    zone=zone,
                    component=component,
                    action="update",
                    before_note=None,
                    after_note=merged,
                    before_include=inc,
                    after_include=target_include,
                    skip_reason="EMPTY_MERGE",
                    row_id=row.get("id"),
                    data_source=data_source,
                )
            )
            continue

        changes.append(
            PlannedChange(
                zone=zone,
                component=component,
                action="update",
                before_note=before,
                after_note=merged,
                before_include=inc,
                after_include=target_include,
                new_lines=new_lines,
                skip_reason=skip_reason,
                row_id=row.get("id"),
                data_source=data_source,
            )
        )

    return changes


def apply_changes(sb, changes: list[PlannedChange]) -> list[PlannedChange]:
    applied: list[PlannedChange] = []
    for ch in changes:
        if ch.skip_reason in ("ALREADY_COVERED", "NO_MATCHING_ITEM", "EMPTY_MERGE"):
            continue
        if ch.action == "create":
            defn = next(
                d for d in NEW_ITEMS
                if _norm_key(d["zone"], d["component"]) == _norm_key(ch.zone, ch.component)
            )
            payload = {
                **{k: v for k, v in defn.items() if k != "include_in_report"},
                "property_id": PROPERTY_ID,
                "owner_note": ch.after_note,
                "include_in_report": ch.after_include,
                "looks_fine": False,
            }
            result = sb.table(WALKTHROUGH_TABLE).insert(payload).execute()
            inserted = (result.data or [payload])[0]
            ch.row_id = inserted.get("id")
            applied.append(ch)
        elif ch.action == "update" and ch.row_id:
            sb.table(WALKTHROUGH_TABLE).update({
                "owner_note": ch.after_note,
                "include_in_report": ch.after_include,
                "looks_fine": False,
                "updated_at": "now()",
            }).eq("id", ch.row_id).execute()
            applied.append(ch)
    return applied


def print_report(rows: list[dict], changes: list[PlannedChange], *, data_source: str) -> None:
    with_notes = [r for r in rows if (r.get("owner_note") or "").strip()]

    print("=" * 72)
    print("WALKTHROUGH EVIDENCE BULK UPDATE — DRY RUN")
    print(f"Property: {PROPERTY_ID}")
    print(f"Data source: {data_source}")
    print("=" * 72)

    print(f"\n## 1. Current items with notes ({len(with_notes)})\n")
    if not with_notes:
        print("  (none)")
    for r in sorted(with_notes, key=lambda x: (x.get("zone", ""), x.get("component", ""))):
        print(f"  [{r.get('zone')}] {r.get('component')}")
        print(f"    include_in_report: {r.get('include_in_report')}")
        print(f"    note: {r.get('owner_note')}")
        print()

    actionable = [
        c for c in changes
        if c.skip_reason not in ("ALREADY_COVERED", "NO_MATCHING_ITEM", "EMPTY_MERGE")
        and (c.new_lines or c.before_include != c.after_include)
    ]
    note_changes = [c for c in actionable if c.new_lines]
    include_only = [c for c in actionable if not c.new_lines]
    creates = [c for c in actionable if c.action == "create"]
    updates = [c for c in actionable if c.action == "update"]

    print("## 2. Summary\n")
    print(f"  Total walkthrough rows:     {len(rows)}")
    print(f"  Rows with owner notes:      {len(with_notes)}")
    print(f"  Mappings:                   {len(EVIDENCE_MAPPINGS)}")
    print(f"  Note changes:               {len(note_changes)}")
    print(f"  Include-only toggles:       {len(include_only)}")
    print(f"  New items to create:        {len(creates)}")
    print(f"  Total actionable changes:   {len(actionable)}")

    print("\n## 3. Proposed note changes\n")
    if not note_changes:
        print("  (none)")
    for c in note_changes:
        print(f"  [{c.zone}] {c.component}")
        print(f"    include: {c.before_include} -> {c.after_include}")
        print(f"    BEFORE: {c.before_note or '(empty)'}")
        print(f"    AFTER:  {c.after_note}")
        print()

    print("## 4. Include-only (note preserved)\n")
    if not include_only:
        print("  (none)")
    for c in include_only:
        print(f"  [{c.zone}] {c.component}")
        print(f"    note unchanged: {c.before_note}")
        print(f"    include: {c.before_include} -> {c.after_include}")
        print()

    print("## 5. New walkthrough items\n")
    if not creates:
        print("  (none)")
    for c in creates:
        print(f"  CREATE [{c.zone}] {c.component}")
        print(f"    include_in_report: {c.after_include}")
        print(f"    note: {c.after_note}")
        print()

    blocked = [c for c in changes if c.skip_reason == "NO_MATCHING_ITEM"]
    if blocked:
        print("## 6. Blocked (no matching DB row)\n")
        for c in blocked:
            print(f"  [{c.zone}] {c.component}")
        print()

    if data_source != "live":
        print("## 7. Template vs live database\n")
        print("  This dry-run used OWNER_NOTE_SEEDS + walkthrough template rows,")
        print("  NOT the live Supabase walkthrough_items table.")
        print("  Counts and notes may differ from production until credentials are available.")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk update walkthrough evidence")
    parser.add_argument("--apply", action="store_true", help="Write changes to Supabase")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Dry-run against template seeds (no Supabase required)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON plan")
    args = parser.parse_args()

    if args.simulate:
        rows = simulate_rows()
        data_source = "template_seeds"
    else:
        sb = get_supabase()
        rows = load_rows(sb)
        data_source = "live"

    changes = plan_changes(rows, data_source=data_source)

    if args.json:
        print(json.dumps({
            "data_source": data_source,
            "property_id": PROPERTY_ID,
            "total_rows": len(rows),
            "rows_with_notes": len([r for r in rows if (r.get("owner_note") or "").strip()]),
            "changes": [
                {
                    "action": c.action,
                    "zone": c.zone,
                    "component": c.component,
                    "before_note": c.before_note,
                    "after_note": c.after_note,
                    "before_include": c.before_include,
                    "after_include": c.after_include,
                    "new_lines": c.new_lines,
                    "skip_reason": c.skip_reason,
                    "row_id": c.row_id,
                }
                for c in changes
            ],
        }, indent=2))
        return

    print_report(rows, changes, data_source=data_source)

    if args.apply:
        if args.simulate:
            print("ERROR: --apply cannot be used with --simulate", file=sys.stderr)
            sys.exit(1)
        sb = get_supabase()
        applied = apply_changes(sb, changes)
        print("=" * 72)
        print(f"APPLIED {len(applied)} changes")
        for c in applied:
            print(f"  {c.action.upper()} [{c.zone}] {c.component} (id={c.row_id})")
    else:
        print("=" * 72)
        print("DRY RUN ONLY — no changes written.")
        if data_source != "live":
            print("Re-run without --simulate once .env has Supabase credentials.")
        else:
            print("Re-run with --apply to save.")
        print("=" * 72)


if __name__ == "__main__":
    main()
