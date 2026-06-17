#!/usr/bin/env python3
"""Before/after verification for photo matching cleanup."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
load_dotenv(".env")
load_dotenv(".env.local")

from supabase import create_client

from decision_matrix import build_decision_matrix, build_decision_matrix_dry_run, load_matrix_rows_with_options, load_current_matrix
from evidence import build_evidence_package, default_property_facts
from run_roi import build_analysis_summary
from walkthrough import load_walkthrough_items


PROPERTY_ID = "130_kingfisher"
RAILWAY = os.environ.get("RAILWAY_URL", "https://web-production-b5477.up.railway.app")


def fetch_live_rows() -> list[dict]:
    try:
        with urllib.request.urlopen(f"{RAILWAY}/decision-matrix/rows", timeout=60) as resp:
            return json.loads(resp.read()).get("rows", [])
    except Exception:
        return []


def matrix_stats(rows: list[dict]) -> dict:
    unmatched = [
        r for r in rows
        if r.get("zone") == "unmatched" or str(r.get("component_id", "")).startswith("photo_only:")
    ]
    return {
        "total_rows": len(rows),
        "unmatched_count": len(unmatched),
        "photo_only_count": len(unmatched),
        "unmatched_labels": [r.get("component", "")[:70] for r in unmatched],
    }


def row_photo_count(rows: list[dict], zone: str, component: str) -> int:
    for r in rows:
        if r.get("zone") == zone and r.get("component") == component:
            return len(r.get("photo_evidence") or r.get("photo_observations") or [])
    return 0


def find_row(rows: list[dict], zone: str, component: str) -> dict | None:
    for r in rows:
        if r.get("zone") == zone and r.get("component") == component:
            return r
    return None


def evidence_targets(sb) -> dict:
    wt = load_walkthrough_items(sb, PROPERTY_ID)
    analyses = [
        r["analysis"]
        for r in sb.table("photo_analyses").select("analysis").execute().data
        if r.get("analysis")
    ]
    summary = build_analysis_summary(analyses)
    pkg = build_evidence_package(wt, summary, default_property_facts())
    stats = pkg.get("matching_stats") or {}

    def photos(zone: str, comp: str) -> list[str]:
        for c in pkg.get("components") or []:
            if c.get("zone") == zone and c.get("component") == comp:
                return c.get("photo_observations") or []
        return []

    def find_assignment(keyword: str) -> tuple[str, str, list[str]] | None:
        for c in pkg.get("components") or []:
            for p in c.get("photo_observations") or []:
                if keyword.lower() in p.lower():
                    return c.get("zone"), c.get("component"), c.get("photo_observations") or []
        for p in pkg.get("photo_only_findings") or []:
            if keyword.lower() in p.lower():
                return "unmatched", "photo_only", [p]
        return None

    wire = find_assignment("exposed electrical wire") or find_assignment("electrical wire")
    vent = find_assignment("return air vent") or find_assignment("return air")

    deck_photos = photos("exterior", "Deck condition")
    porch_photos = photos("exterior", "Front porch repair / repaint")

    deck_merged = sum(
        1 for p in deck_photos
        if any(k in p.lower() for k in ("deck stair", "stringer", "footing", "weathering", "graying"))
    )
    porch_merged = sum(
        1 for p in porch_photos
        if any(k in p.lower() for k in ("porch column", "porch beam", "porch ceiling", "porch railing", "balustrade"))
    )

    return {
        "meta_excluded": stats.get("meta_observations_excluded", 0),
        "photo_only_evidence_count": len(pkg.get("photo_only_findings") or []),
        "photos_assigned": stats.get("photos_assigned", 0),
        "deck_photo_count": len(deck_photos),
        "porch_photo_count": len(porch_photos),
        "deck_merged_keywords": deck_merged,
        "porch_merged_keywords": porch_merged,
        "wire_target": f"{wire[0]} / {wire[1]}" if wire else None,
        "vent_target": f"{vent[0]} / {vent[1]}" if vent else None,
        "photo_only_findings": pkg.get("photo_only_findings") or [],
    }


def dry_run_stats(sb) -> dict:
    result = build_decision_matrix_dry_run(property_id=PROPERTY_ID, sb=sb)
    return {
        "total_rows": result["total_rows"],
        "walkthrough_rows": result["walkthrough_rows"],
        "photo_only_rows": result["photo_only_rows"],
    }


def main() -> int:
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    print("=" * 60)
    print("BEFORE (current production matrix via API)")
    print("=" * 60)
    before_live = matrix_stats(fetch_live_rows())
    print(json.dumps(before_live, indent=2))

    print("\n" + "=" * 60)
    print("AFTER DRY-RUN (new matching, no persist)")
    print("=" * 60)
    dry = dry_run_stats(sb)
    ev = evidence_targets(sb)
    print(json.dumps({**dry, **ev}, indent=2))

    print("\n" + "=" * 60)
    print("REBUILDING MATRIX...")
    print("=" * 60)
    result = build_decision_matrix(property_id=PROPERTY_ID, sb=sb)
    print(f"matrix_id={result['matrix_id']} version={result['version']}")
    print(f"total_rows={result['total_rows']} photo_only_rows={result['photo_only_rows']}")

    matrix = load_current_matrix(sb, PROPERTY_ID)
    rows = load_matrix_rows_with_options(sb, matrix["id"]) if matrix else []
    after = matrix_stats(rows)
    after["deck_photos"] = row_photo_count(rows, "exterior", "Deck condition")
    after["porch_photos"] = row_photo_count(rows, "exterior", "Front porch repair / repaint")

    gfci = find_row(rows, "electrical", "GFCI / AFCI protection")
    filt = find_row(rows, "hvac", "Filter condition")
    after["gfci_in_matrix"] = gfci is not None
    after["filter_in_matrix"] = filt is not None
    if gfci:
        after["wire_in_gfci"] = any("wire" in (p.get("observation") or "").lower() for p in (gfci.get("photo_evidence") or []))
    if filt:
        after["vent_in_filter"] = any("return air" in (p.get("observation") or "").lower() for p in (filt.get("photo_evidence") or []))

    print("\n" + "=" * 60)
    print("AFTER REBUILD (persisted)")
    print("=" * 60)
    print(json.dumps(after, indent=2))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  total rows:        {before_live['total_rows']} -> {after['total_rows']}")
    print(f"  unmatched/photo:   {before_live['unmatched_count']} -> {after['unmatched_count']}")
    print(f"  meta excluded:     {ev['meta_excluded']}")
    print(f"  deck photos:       -> {after.get('deck_photos', ev['deck_photo_count'])} ({ev['deck_merged_keywords']} deck-keyword merges)")
    print(f"  porch photos:      -> {after.get('porch_photos', ev['porch_photo_count'])} ({ev['porch_merged_keywords']} porch-keyword merges)")
    print(f"  wire target:       {ev['wire_target']}")
    print(f"  vent target:       {ev['vent_target']}")
    print(f"  GFCI row exists:   {after.get('gfci_in_matrix')}")
    print(f"  Filter row exists: {after.get('filter_in_matrix')}")

    if after["unmatched_count"] > 3:
        print("\nRemaining unmatched:")
        for label in after.get("unmatched_labels", []):
            print(f"  - {label}")

    return 0 if after["unmatched_count"] <= 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
