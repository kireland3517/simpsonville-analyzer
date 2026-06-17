#!/usr/bin/env python3
"""Audit unmatched / photo-only decision matrix rows (read-only)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import _component_keys, _norm, _zone_rooms
from run_roi import build_analysis_summary
from walkthrough import enrich_walkthrough_items, load_walkthrough_items, prepare_walkthrough_row

RAILWAY = os.environ.get("RAILWAY_URL", "https://web-production-b5477.up.railway.app")

MERGE_TARGETS: list[tuple[str, str, str]] = [
    ("deck stair", "exterior", "Deck condition"),
    ("deck stairs", "exterior", "Deck condition"),
    ("deck support post", "exterior", "Deck condition"),
    ("porch column", "exterior", "Front porch repair / repaint"),
    ("porch ceiling board", "exterior", "Front porch repair / repaint"),
    ("porch beam", "exterior", "Front porch repair / repaint"),
    ("porch railing", "exterior", "Front porch repair / repaint"),
    ("porch balustrade", "exterior", "Front porch repair / repaint"),
    ("split or crack", "exterior", "Front porch repair / repaint"),
    ("exposed electrical wire", "electrical", "GFCI / AFCI protection"),
    ("electrical wire hanging", "electrical", "GFCI / AFCI protection"),
    ("return air vent", "hvac", "Filter condition"),
    ("foundation shrub", "exterior", "Landscaping / yard"),
    ("overgrown foundation", "exterior", "Landscaping / yard"),
    ("shrubs directly against siding", "exterior", "Landscaping / yard"),
    ("cardboard", "kitchen", "Cabinets"),
    ("packing material", "kitchen", "Cabinets"),
    ("cooktop surface", "kitchen", "Appliances (overall)"),
    ("glass cooktop", "kitchen", "Appliances (overall)"),
]


def fetch_matrix_rows() -> list[dict]:
    with urllib.request.urlopen(f"{RAILWAY}/decision-matrix/rows", timeout=60) as resp:
        return json.loads(resp.read())["rows"]


def find_photo_rooms(text: str, summary: dict) -> list[str]:
    rooms: list[str] = []
    for bucket in (summary.get("issues_by_room") or {}, summary.get("upgrades_by_room") or {}):
        for room, texts in bucket.items():
            if text in texts:
                rooms.append(room)
    if text in (summary.get("critical_and_high_issues") or []):
        rooms.append("critical_and_high_issues")
    if text in (summary.get("dated_features_by_frequency") or {}):
        rooms.append("dated_features")
    return rooms


def full_match_list(zone: str, component: str, summary: dict) -> list[str]:
    rooms = _zone_rooms(zone)
    comp_keys = _component_keys(component)
    matched: list[str] = []
    for room, texts in {
        **(summary.get("issues_by_room") or {}),
        **(summary.get("upgrades_by_room") or {}),
    }.items():
        room_n = _norm(room)
        if not any(alias in room_n or room_n in alias for alias in rooms):
            continue
        for text in texts:
            t = _norm(text)
            if any(k in t for k in comp_keys) and text not in matched:
                matched.append(text)
    for text in summary.get("critical_and_high_issues") or []:
        t = _norm(text)
        if any(k in t for k in comp_keys) and text not in matched:
            matched.append(text)
    return matched


def propose_merge(text: str) -> tuple[str, str] | None:
    blob = text.lower()
    for needle, zone, component in MERGE_TARGETS:
        if needle in blob:
            return zone, component
    return None


def classify_failure(text: str, summary: dict, merge: tuple[str, str] | None) -> list[str]:
    reasons: list[str] = []
    blob = text.lower()
    rooms = find_photo_rooms(text, summary)

    if blob.startswith("photo is rotated"):
        reasons.append("Meta-observation (rotation/limitation) — no domain keyword in _COMPONENT_PHOTO_KEYS")

    if merge:
        zone, component = merge
        full = full_match_list(zone, component, summary)
        if text in full:
            rank = full.index(text) + 1
            if rank > 5:
                reasons.append(
                    f"Would match [{zone}] {component} at rank {rank}/{len(full)} "
                    f"but _match_photo_texts caps at 5 per component"
                )
            else:
                reasons.append(
                    f"Matches [{zone}] {component} (rank {rank}) but never entered matched_photo_texts "
                    f"(likely consumed by another component first or rebuild drift)"
                )
        else:
            if "wire" in blob and "electrical" in zone:
                reasons.append("No open-wiring tokens in _COMPONENT_PHOTO_KEYS for electrical components")
            elif "vent" in blob and "hvac" in zone:
                reasons.append(
                    f"Return-air/HVAC tokens missing; photo rooms {rooms} don't match hvac zone aliases"
                )
            elif "porch" in blob and zone == "exterior":
                reasons.append("Porch-specific tokens missing from _COMPONENT_PHOTO_KEYS (only paint/deck keys match weakly)")
            elif "shrub" in blob or "foundation shrub" in blob:
                reasons.append("Landscaping keywords absent; false matches on generic 'condition'/'siding' keys overflow cap")
            else:
                reasons.append(f"No keyword+room path to [{zone}] {component}")
    elif not reasons:
        reasons.append("No component keyword path — text falls through to photo_only[:15] bucket")

    if rooms and merge and merge[0] == "hvac" and any("kitchen" in r.lower() for r in rooms):
        reasons.append("Photo tagged to kitchen room — hvac zone alias mismatch")

    return reasons


def main() -> None:
    rows = fetch_matrix_rows()
    unmatched = [
        r for r in rows
        if r.get("zone") == "unmatched" or str(r.get("component_id", "")).startswith("photo_only:")
    ]

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY")
    summary = {}
    if sb_url and sb_key:
        from supabase import create_client

        sb = create_client(sb_url, sb_key)
        analyses = [
            r["analysis"]
            for r in sb.table("photo_analyses").select("analysis").execute().data
            if r.get("analysis")
        ]
        summary = build_analysis_summary(analyses)

    print(f"CURRENT UNMATCHED COUNT: {len(unmatched)} / {len(rows)} matrix rows\n")

    merges: list[str] = []
    standalone: list[str] = []

    for i, row in enumerate(unmatched, 1):
        finding = row.get("current_state") or row.get("component") or "?"
        component_label = row.get("component") or "?"
        evidence = row.get("evidence_sources") or []
        ev_text = evidence[0].get("text") if evidence else finding
        ev_source = evidence[0].get("source") if evidence else "photo"

        merge = propose_merge(finding)
        reasons = classify_failure(ev_text if ev_text else finding, summary, merge)

        print(f"--- {i}. {component_label[:72]}")
        print(f"Finding: {finding[:220]}")
        print(f"Evidence: {ev_source} | confidence: {row.get('confidence_tier')} | "
              f"status: {row.get('decision_status')} | action: {row.get('recommended_action')}")
        print(f"Photo room(s): {find_photo_rooms(ev_text, summary) if summary else ['(no summary)']}")
        print("Why matching failed:")
        for r in reasons:
            print(f"  • {r}")
        if merge:
            print(f"Proposed merge -> [{merge[0]}] {merge[1]}")
            merges.append(component_label)
        else:
            print("Proposed: STANDALONE (or exclude from matrix)")
            standalone.append(component_label)
        print()

    print("=" * 60)
    print("SUMMARY")
    print(f"1. Unmatched count: {len(unmatched)}")
    print(f"2. Proposed merges: {len(merges)}")
    print(f"3. Standalone / exclude: {len(standalone)}")


if __name__ == "__main__":
    main()
