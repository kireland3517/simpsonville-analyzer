#!/usr/bin/env python3
"""Re-persist matrix tiers and regenerate listing-readiness tier reports."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv()
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")

from attom import get_last_sale, get_property_summary
from decision_matrix import (
    _persist_tiers_for_matrix,
    load_current_matrix,
    load_matrix_rows_with_options,
)
from evidence import build_evidence_package, default_property_facts, format_evidence_prompt
from matrix_tiers import normalize_tier
from report_composer import TIER_TO_DETAIL_LEVEL, compose_report_from_tier
from run_roi import build_analysis_summary
from tier_selector import select_tier_from_rows
from walkthrough import load_walkthrough_items
from walkthrough_impact import build_walkthrough_impact

PROPERTY_ID = os.environ.get("PROPERTY_ID", "130_kingfisher")
BUYER_PROFILE = os.environ.get("BUYER_PROFILE", "general")
TIERS = ("must_do", "should_do", "nice_to_do")
ROI_TABLE = "roi_report"


def get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def evidence_context(sb, scenario: str) -> tuple[dict, str, list]:
    rows = load_walkthrough_items(sb, PROPERTY_ID)
    analyses = [
        r["analysis"]
        for r in (sb.table("photo_analyses").select("analysis").execute().data or [])
        if r.get("analysis")
    ]
    summary = build_analysis_summary(analyses) if analyses else {}
    package = build_evidence_package(rows, summary, default_property_facts())
    return package, format_evidence_prompt(package, scenario), rows


def generate_report_from_tier(
    *,
    tier: str,
    buyer_profile: str,
    property_id: str,
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    sb,
) -> dict:
    tier = normalize_tier(tier.strip().lower()) or ""
    if tier not in TIER_TO_DETAIL_LEVEL:
        raise ValueError(f"Invalid tier: {tier!r}. Choose from: {sorted(TIER_TO_DETAIL_LEVEL)}")

    matrix = load_current_matrix(sb, property_id)
    if not matrix:
        raise ValueError(f"No decision matrix for property {property_id!r}")

    rows = load_matrix_rows_with_options(sb, matrix["id"])
    tier_selection = select_tier_from_rows(
        rows,
        tier,
        matrix_id=matrix["id"],
        property_id=property_id,
    )

    detail_level = TIER_TO_DETAIL_LEVEL[tier]
    package, walkthrough_block, wt_rows = evidence_context(sb, detail_level)

    report = compose_report_from_tier(
        rows,
        tier_selection,
        summary=summary,
        property_summary=property_summary,
        last_sale=last_sale,
        buyer_profile=buyer_profile,
        walkthrough_block=walkthrough_block,
    )
    if report.get("error"):
        raise RuntimeError(report["error"])

    report["walkthrough_impact"] = build_walkthrough_impact(
        package,
        detail_level,
        report.get("upgrades") or [],
        report.get("repairs") or [],
        wt_rows,
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> int:
    sb = get_supabase()
    if not sb:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY required", file=sys.stderr)
        return 1

    matrix = load_current_matrix(sb, PROPERTY_ID)
    if not matrix:
        print(f"ERROR: no decision matrix for {PROPERTY_ID!r}", file=sys.stderr)
        return 1

    print(f"Re-persisting tiers for matrix {matrix['id']} ...")
    tier_summary = _persist_tiers_for_matrix(sb, matrix["id"])
    print(json.dumps(tier_summary, indent=2))

    by_min = tier_summary.get("by_minimum_tier") or {}
    by_rec = tier_summary.get("by_recommended_tier") or {}
    if by_min.get("aspirational") or by_rec.get("aspirational"):
        print("WARNING: aspirational counts remain after re-persist", file=sys.stderr)

    analyses = [
        r["analysis"]
        for r in (sb.table("photo_analyses").select("analysis").execute().data or [])
        if r.get("analysis")
    ]
    if not analyses:
        print("ERROR: no photo analyses — run run_analysis.py first", file=sys.stderr)
        return 1

    summary = build_analysis_summary(analyses)
    property_summary = get_property_summary()
    last_sale = get_last_sale()

    try:
        sb.table(ROI_TABLE).delete().eq("id", f"tier_aspirational_{BUYER_PROFILE}").execute()
        print("Removed stale tier_aspirational report cache")
    except Exception:
        pass

    errors: list[str] = []
    for tier in TIERS:
        print(f"\nGenerating tier report: {tier} ...", flush=True)
        try:
            report = generate_report_from_tier(
                tier=tier,
                buyer_profile=BUYER_PROFILE,
                property_id=PROPERTY_ID,
                summary=summary,
                property_summary=property_summary,
                last_sale=last_sale,
                sb=sb,
            )
        except Exception as exc:
            errors.append(f"{tier}: {exc}")
            print(f"  FAIL: {exc}", file=sys.stderr)
            continue

        report_id = f"tier_{tier}_{BUYER_PROFILE}"
        sb.table(ROI_TABLE).upsert({"id": report_id, "report": report}).execute()
        upgrades = len(report.get("upgrades") or [])
        repairs = len(report.get("repairs") or [])
        detail = report.get("detail_level") or "?"
        print(
            f"  OK: saved {report_id} "
            f"(detail_level={detail}, items={upgrades + repairs}, "
            f"projection_source={report.get('projection_source')})"
        )

    if errors:
        print("\nFAILURES:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("\nOK: tiers re-persisted and tier reports regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
