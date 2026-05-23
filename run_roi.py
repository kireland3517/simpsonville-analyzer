"""
run_roi.py
----------
Reads all photo analyses from Supabase, pre-processes them into a
structured summary, generates an ROI report with Claude, and saves
the result back to Supabase.

Usage:
    python run_roi.py

Requires:
    ANTHROPIC_API_KEY     -- Claude API key
    SUPABASE_URL          -- Supabase project URL
    SUPABASE_SERVICE_KEY  -- Supabase service role key

Supabase table (run once):
    CREATE TABLE roi_report (
        id           TEXT PRIMARY KEY,   -- always "current"
        report       JSONB,
        generated_at TIMESTAMPTZ DEFAULT now()
    );
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv
from supabase import create_client

from attom import get_last_sale, get_property_summary
from roi import generate_roi_report

load_dotenv()

ANALYSES_TABLE = "photo_analyses"
REPORT_TABLE   = "roi_report"
REPORT_ID      = "current"


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def load_analyses(client) -> list[dict]:
    """Fetch all rows from photo_analyses and return the analysis dicts."""
    try:
        result = client.table(ANALYSES_TABLE).select("id, filename, analysis").execute()
    except Exception as exc:
        print(f"ERROR: Could not read {ANALYSES_TABLE}: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = result.data or []
    analyses: list[dict] = []
    skipped = 0

    for row in rows:
        raw = row.get("analysis")
        if not raw:
            skipped += 1
            continue
        if isinstance(raw, str):
            raw = json.loads(raw)
        # Skip rows that only contain an error
        if raw.get("error") and not raw.get("room_type"):
            skipped += 1
            continue
        analyses.append(raw)

    print(f"Loaded {len(analyses)} analyses ({skipped} skipped - empty or error-only).")
    return analyses


# ---------------------------------------------------------------------------
# Pre-processing
# ---------------------------------------------------------------------------

def build_analysis_summary(analyses: list[dict]) -> dict:
    """
    Aggregate raw analysis dicts into a structured summary suitable for
    the ROI prompt. Reduces token usage and gives Claude richer context.
    """
    issue_counts: Counter   = Counter()
    upgrade_counts: Counter = Counter()
    condition_counts: Counter      = Counter()
    finish_quality_counts: Counter = Counter()

    issues_by_room:   defaultdict[str, list[str]] = defaultdict(list)
    upgrades_by_room: defaultdict[str, list[str]] = defaultdict(list)

    # Track canonical casing for display
    issue_canonical:   dict[str, str] = {}
    upgrade_canonical: dict[str, str] = {}

    for a in analyses:
        room = (a.get("room_type") or "unknown").strip().lower()

        condition = (a.get("condition") or "").strip().lower()
        if condition:
            condition_counts[condition] += 1

        finish = (a.get("finish_quality") or "").strip().lower()
        if finish:
            finish_quality_counts[finish] += 1

        for issue in (a.get("issues") or []):
            text = issue.strip()
            if not text:
                continue
            key = text.lower()
            issue_counts[key] += 1
            if key not in issue_canonical:
                issue_canonical[key] = text
            # Add to room list (deduplicated per room)
            if text not in issues_by_room[room]:
                issues_by_room[room].append(text)

        for upgrade in (a.get("upgrades") or []):
            text = upgrade.strip()
            if not text:
                continue
            key = text.lower()
            upgrade_counts[key] += 1
            if key not in upgrade_canonical:
                upgrade_canonical[key] = text
            if text not in upgrades_by_room[room]:
                upgrades_by_room[room].append(text)

    # Build frequency dicts using canonical display text, sorted by count desc
    issues_by_freq   = {issue_canonical[k]:   c for k, c in issue_counts.most_common()}
    upgrades_by_freq = {upgrade_canonical[k]: c for k, c in upgrade_counts.most_common()}

    return {
        "total_photos":          len(analyses),
        "condition_summary":     dict(condition_counts.most_common()),
        "finish_quality_summary": dict(finish_quality_counts.most_common()),
        "issues_by_frequency":   issues_by_freq,
        "upgrades_by_frequency": upgrades_by_freq,
        "issues_by_room":        dict(issues_by_room),
        "upgrades_by_room":      dict(upgrades_by_room),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    client = get_supabase()

    # 1. Load raw analyses
    analyses = load_analyses(client)
    if not analyses:
        print("No usable analyses found. Run run_analysis.py first.")
        sys.exit(0)

    # 2. Pre-process into structured summary
    summary = build_analysis_summary(analyses)
    print(f"Summary: {summary['total_photos']} photos | "
          f"condition: {summary['condition_summary']} | "
          f"{len(summary['issues_by_frequency'])} unique issues | "
          f"{len(summary['upgrades_by_frequency'])} unique upgrades")

    # 3. Load property data
    property_summary = get_property_summary()
    last_sale        = get_last_sale()
    print(f"Property: {property_summary.get('address')}")
    print(
        f"Market value: ${property_summary.get('market_value'):,}  |  "
        f"Last sale: ${last_sale.get('sale_amount'):,} ({last_sale.get('sale_date')})"
    )

    # 4. Generate ROI report
    print(f"\nGenerating ROI report from {summary['total_photos']} photo analyses with Claude...")
    report = generate_roi_report(summary, property_summary, last_sale)

    if report.get("error"):
        print(f"ERROR generating report: {report['error']}", file=sys.stderr)
        sys.exit(1)

    # 5. Save to Supabase
    try:
        client.table(REPORT_TABLE).upsert({
            "id":     REPORT_ID,
            "report": report,
        }).execute()
        print(f"Report saved to Supabase table '{REPORT_TABLE}' (id='{REPORT_ID}').")
    except Exception as exc:
        print(f"WARNING: Could not save report to Supabase: {exc}")

    # 6. Print executive summary
    print("\n" + "-" * 60)
    print("EXECUTIVE SUMMARY")
    print("-" * 60)

    ex = report.get("executive_summary") or {}
    arv = ex.get("estimated_arv") or report.get("estimated_arv")
    if arv:
        print(f"Estimated ARV:      ${float(arv):,.0f}")

    upgrades = report.get("upgrades") or []
    repairs  = report.get("repairs")  or []

    total_upgrade_cost = sum(float(u.get("estimated_cost") or 0) for u in upgrades)
    critical_count     = sum(1 for r in repairs if r.get("priority") == "critical")

    print(f"Total upgrade cost: ${total_upgrade_cost:,.0f}")
    print(f"Critical repairs:   {critical_count}")
    print(f"Upgrades found:     {len(upgrades)}")
    print(f"Repairs found:      {len(repairs)}")

    if ex.get("recommendation"):
        print(f"\n{ex['recommendation']}")

    if upgrades:
        print("\nTop upgrades by ROI:")
        for u in upgrades[:5]:
            roi  = u.get("roi_percent", 0)
            name = u.get("name", "-")
            cost = u.get("estimated_cost", 0)
            print(f"  {roi:>6.1f}%  {name}  (${cost:,.0f})")

    print("-" * 60)


if __name__ == "__main__":
    main()
