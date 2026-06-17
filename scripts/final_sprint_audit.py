#!/usr/bin/env python3
"""Step 7 — final Decision Matrix Finalization Sprint audit."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("RAILWAY_URL", "https://web-production-b5477.up.railway.app")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as resp:
        return json.loads(resp.read())


def run_script(name: str) -> tuple[int, str]:
    path = os.path.join(ROOT, "scripts", name)
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, path],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def main() -> int:
    errors: list[str] = []
    print("=" * 60)
    print("STEP 7 — FINAL SPRINT AUDIT")
    print("=" * 60)

    # Production matrix shape
    rows = get("/decision-matrix/rows")["rows"]
    unmatched = sum(
        1 for r in rows
        if r.get("zone") == "unmatched" or str(r.get("component_id", "")).startswith("photo_only:")
    )
    health = get("/decision-matrix/health")["health"]
    print(f"\nMatrix: {len(rows)} rows, {unmatched} unmatched photo-only")
    print(f"Health: missing_options={health.get('missing_options')} "
          f"missing_tiers={health.get('missing_minimum_tier')} "
          f"manual_review={health.get('manual_review_count')}")

    if len(rows) > 80:
        errors.append(f"unexpected row count {len(rows)} (expected ~64)")
    if unmatched > 20:
        errors.append(f"too many unmatched rows: {unmatched}")
    if health.get("missing_options"):
        errors.append("rows missing options")
    if health.get("missing_minimum_tier"):
        errors.append("rows missing minimum_tier")

    tier_counts = health.get("tier_counts", {}).get("by_minimum_tier", {})
    print(f"Tier distribution (min): {tier_counts}")

    # Tier endpoints
    for tier in ("must_do", "should_do", "nice_to_do", "aspirational"):
        data = get(f"/decision-matrix/tiers/{tier}")
        print(f"  {tier}: {data['selected_count']} rows, ${data['cost_low_total']}-${data['cost_high_total']}")

    # Cached tier reports
    for tier in ("must_do", "should_do", "nice_to_do", "aspirational"):
        rid = f"tier_{tier}_general"
        try:
            report = get(f"/report?id={urllib.parse.quote(rid)}")
            items = (report.get("upgrades") or []) + (report.get("repairs") or [])
            traced = sum(
                1 for i in items
                if i.get("matrix_row_id") and (i.get("matrix_option_id") or i.get("option_id"))
            )
            src = report.get("projection_source") or report.get("report_source")
            print(f"  report {rid}: {len(items)} items, traced {traced}/{len(items)}, source={src}")
            if src not in ("matrix_tier", "matrix"):
                errors.append(f"{rid} unexpected source {src}")
            if items and traced != len(items):
                errors.append(f"{rid} incomplete traceability")
        except Exception as exc:
            errors.append(f"missing cached report {rid}: {exc}")

    # UI cutover — no budget tabs in static HTML
    index_path = os.path.join(ROOT, "static", "index.html")
    html = open(index_path, encoding="utf-8").read()
    if 'data-level="budget_15k"' in html or "Spend Nothing" in html and "detail-tab" in html:
        if 'data-tier="must_do"' not in html:
            errors.append("ROI tabs not migrated to tier tabs")
    if 'id="dm-scenario-select"' in html:
        errors.append("budget scenario select still in Decision Matrix UI")

    print("\nVerification scripts:")
    for script in (
        "verify_tier_validation_step2.py",
        "verify_tiers_live.py",
        "verify_tier_report.py",
    ):
        code, out = run_script(script)
        status = "OK" if code == 0 else "FAIL"
        print(f"  {script}: {status}")
        if code != 0:
            errors.append(f"{script} failed")
            if out:
                print(out[-800:])

    print("\n" + "=" * 60)
    if errors:
        print("FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("OK: Final sprint audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
