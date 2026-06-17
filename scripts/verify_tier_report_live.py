#!/usr/bin/env python3
"""Verify Phase 12 tier reports on deployed Railway API."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

BASE = os.environ.get("RAILWAY_URL", "https://web-production-b5477.up.railway.app")


def wait_deploy(max_wait: int = 180) -> bool:
    for i in range(max_wait // 10):
        try:
            req = urllib.request.Request(f"{BASE}/report/status", method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    print(f"App reachable at {BASE}")
                    return True
        except Exception as exc:
            print(f"wait {i * 10}s: {exc}")
        time.sleep(10)
    return False


def post_tier(tier: str) -> dict:
    body = json.dumps({
        "tier": tier,
        "buyer_profile": "general",
        "property_id": "130_kingfisher",
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/report/from-tier",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode())


def get_report(report_id: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/report?id={urllib.parse.quote(report_id)}",
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def component_match(items: list[dict], component: str) -> bool:
    needle = component.lower()
    for item in items:
        head = (item.get("name") or "").split("—")[0].strip().lower()
        if needle in head:
            return True
    return False


def find_blob(items: list[dict], *parts: str) -> bool:
    blob = " ".join(
        (i.get("name") or "") + " " + (i.get("description") or "") for i in items
    ).lower()
    return all(p.lower() in blob for p in parts)


def traceable(items: list[dict]) -> tuple[int, int]:
    ok = sum(
        1
        for i in items
        if i.get("matrix_row_id") and (i.get("matrix_option_id") or i.get("option_id"))
    )
    return ok, len(items)


def main() -> int:
    if not wait_deploy():
        print("FAIL: deploy wait timeout")
        return 1

    results: dict[str, dict] = {}
    errors: list[str] = []

    for tier in ("must_do", "should_do", "nice_to_do", "aspirational"):
        print(f"\nPOST /report/from-tier tier={tier} ...", flush=True)
        try:
            report = post_tier(tier)
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code}: {exc.read().decode()[:500]}")
            return 1

        results[tier] = report
        all_items = (report.get("upgrades") or []) + (report.get("repairs") or [])
        traced, total = traceable(all_items)
        print(
            f"  projection_source={report.get('projection_source')} "
            f"tier={report.get('tier')} items={total} traced={traced}/{total}"
        )

        if report.get("projection_source") != "matrix_tier":
            errors.append(f"{tier}: projection_source not matrix_tier")
        if total and traced != total:
            errors.append(f"{tier}: {total - traced} items missing matrix traceability")

    must = (results["must_do"].get("upgrades") or []) + (results["must_do"].get("repairs") or [])
    if not find_blob(must, "garage", "door"):
        errors.append("must_do missing garage door")
    if not find_blob(must, "smoke"):
        errors.append("must_do missing smoke remediation")
    if not find_blob(must, "water"):
        errors.append("must_do missing ceiling water damage")
    if not find_blob(must, "popcorn"):
        errors.append("must_do missing popcorn ceiling")

    should = (results["should_do"].get("upgrades") or []) + (results["should_do"].get("repairs") or [])

    nice = (results["nice_to_do"].get("upgrades") or []) + (results["nice_to_do"].get("repairs") or [])
    if not component_match(nice, "countertops"):
        errors.append("nice_to_do missing countertops")

    print("\nBudget fallback checks:")
    budget_ok = 0
    for rid in ("spend_nothing_general", "budget_5k_general", "budget_15k_general", "maximize_general"):
        try:
            report = get_report(rid)
            src = report.get("report_source") or report.get("projection_source") or "unknown"
            print(f"  GET {rid}: OK (source={src})")
            budget_ok += 1
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                print(f"  GET {rid}: 404 not cached")
            else:
                errors.append(f"budget report {rid}: HTTP {exc.code}")

    if budget_ok == 0:
        errors.append("no cached budget scenario reports found")

    if errors:
        print("\nFAILURES:")
        for err in errors:
            print(" -", err)
        return 1

    print("\nOK: Railway Phase 12 verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
