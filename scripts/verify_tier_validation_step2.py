#!/usr/bin/env python3
"""Step 2 — tier validation on rebuilt decision matrix."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("RAILWAY_URL", "https://web-production-b5477.up.railway.app")


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as resp:
        return json.loads(resp.read())


def find_rows(rows: list[dict], needle: str) -> list[dict]:
    n = needle.lower()
    return [r for r in rows if n in (r.get("component") or "").lower()]


def main() -> int:
    errors: list[str] = []
    health = get("/decision-matrix/health")
    matrix = get("/decision-matrix")
    rows = get("/decision-matrix/rows")["rows"]

    print("=== MATRIX SUMMARY ===")
    print(f"version: {matrix.get('matrix', {}).get('version')}")
    print(f"total rows: {len(rows)}")
    print(f"options total: {sum(len(r.get('options') or []) for r in rows)}")

    print("\n=== HEALTH ===")
    for key in sorted(k for k in health if k != "rows"):
        print(f"  {key}: {health[key]}")

    missing_tiers = health.get("missing_minimum_tier", 0) + health.get("missing_recommended_tier", 0)
    if missing_tiers:
        errors.append(f"missing tiers: {missing_tiers}")

    print("\n=== BY minimum_tier ===")
    for tier, count in sorted(Counter(r.get("minimum_tier") or "MISSING" for r in rows).items()):
        print(f"  {tier}: {count}")

    print("\n=== BY decision_status ===")
    for status, count in sorted(Counter(r.get("decision_status") for r in rows).items()):
        print(f"  {status}: {count}")

    print("\n=== BY recommended_action ===")
    for action, count in sorted(Counter(r.get("recommended_action") for r in rows).items()):
        print(f"  {action}: {count}")

    print("\n=== KEY COMPONENT TIERS ===")
    expectations = [
        ("garage door", "must_do", None),
        ("indoor air quality", "must_do", None),
        ("ceiling water damage", "must_do", None),
        ("popcorn ceiling", "must_do", None),
        ("countertops", None, {"nice_to_do", "should_do"}),
        ("deck condition", None, {"must_do", "should_do"}),
        ("crawlspace", None, {"must_do", "should_do"}),
    ]
    for needle, expected_min, allowed_mins in expectations:
        matches = find_rows(rows, needle)
        if not matches:
            print(f"  {needle}: NOT FOUND")
            errors.append(f"{needle} not in matrix")
            continue
        for row in matches:
            mn = row.get("minimum_tier")
            rc = row.get("recommended_tier")
            print(
                f"  {row.get('component', '')[:55]}: "
                f"min={mn} rec={rc} status={row.get('decision_status')} action={row.get('recommended_action')}"
            )
            if expected_min and mn != expected_min:
                errors.append(f"{needle}: expected min={expected_min}, got {mn}")
            if allowed_mins and mn not in allowed_mins:
                errors.append(f"{needle}: min={mn} not in {allowed_mins}")

    print("\n=== EXPOSED WIRE (merged photo evidence) ===")
    wire_rows = []
    for row in rows:
        blob = " ".join(
            (pe.get("observation") or "") for pe in (row.get("photo_evidence") or [])
        ).lower()
        if "exposed" in blob and "wire" in blob:
            wire_rows.append(row)
    if not wire_rows:
        print("  none found")
        errors.append("exposed wire not found in any row photo_evidence")
    else:
        for row in wire_rows:
            print(f"  {row.get('zone')} / {row.get('component')}: min={row.get('minimum_tier')}")
            if row.get("minimum_tier") != "must_do":
                errors.append(
                    f"exposed wire row {row.get('component')} has min={row.get('minimum_tier')}, expected must_do"
                )

    print("\n=== TIER ENDPOINTS ===")
    for tier in ("must_do", "should_do", "nice_to_do"):
        data = get(f"/decision-matrix/tiers/{tier}")
        print(
            f"  {tier}: selected={data['selected_count']} "
            f"cost={data['cost_low_total']}-{data['cost_high_total']}"
        )

    print("\n=== CHECKS ===")
    must = get("/decision-matrix/tiers/must_do")
    should = get("/decision-matrix/tiers/should_do")
    nice = get("/decision-matrix/tiers/nice_to_do")

    def has_comp(data: dict, component: str) -> bool:
        return any(r["component"] == component for r in data.get("selected_rows", []))

    spot_checks = [
        ("must_do includes Garage door", has_comp(must, "Garage door")),
        ("must_do includes Indoor air quality", has_comp(must, "Indoor air quality")),
        ("must_do includes Ceiling water damage", has_comp(must, "Ceiling water damage")),
        ("must_do includes Popcorn ceiling", has_comp(must, "Popcorn ceiling")),
        ("should_do includes Garage door (cumulative)", has_comp(should, "Garage door")),
        ("nice_to_do includes Countertops", has_comp(nice, "Countertops")),
    ]
    for label, ok in spot_checks:
        status = "OK" if ok else "FAIL"
        print(f"  {label}: {status}")
        if not ok:
            errors.append(label)

    print("\n=== RESULT ===")
    if errors:
        for err in errors:
            print(f"  FAIL: {err}")
        return 1

    print("  OK: Step 2 tier validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
