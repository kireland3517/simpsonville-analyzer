"""
run_roi.py
----------
Reads all photo analyses from Supabase, pre-processes them into a
structured summary, generates an ROI report with Gemini, and saves
the result back to Supabase.

Usage:
    python run_roi.py
    python run_roi.py --all --buyer general
    python run_roi.py --detail executive --buyer first_time_buyer
    python run_roi.py --detail deep_dive --buyer relocating_professional

Arguments:
    --all      Generate all three levels in sequence (executive → standard → deep_dive)
    --detail   executive | standard | deep_dive          (default: standard)
               Generates prerequisite levels first so each tab is additive.
    --buyer    first_time_buyer | young_family | downsizer |
               investor | relocating_professional | general  (default: general)

Report is saved to Supabase with id = "{detail}_{buyer}", e.g.
    "standard_general"
    "deep_dive_relocating_professional"
    "executive_first_time_buyer"

Requires:
    GEMINI_API_KEY        -- Google Gemini API key
    SUPABASE_URL          -- Supabase project URL
    SUPABASE_SERVICE_KEY  -- Supabase service role key

Supabase table (run once):
    CREATE TABLE roi_report (
        id           TEXT PRIMARY KEY,
        report       JSONB,
        generated_at TIMESTAMPTZ DEFAULT now()
    );
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv
from supabase import create_client

from attom import get_last_sale, get_property_summary
from roi import (
    generate_roi_report,
    generate_all_roi_reports,
    levels_up_to,
    DETAIL_LEVELS,
    BUYER_PROFILES,
)

load_dotenv()

ANALYSES_TABLE = "photo_analyses"
REPORT_TABLE   = "roi_report"


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

# Words stripped when building the normalized grouping key for issues/upgrades.
# Claude writes unique verbose sentences per photo; without normalization every
# entry gets count=1 regardless of how often the same underlying issue appears.
_KEY_STOP = frozenset({
    # articles / prepositions / conjunctions
    'the','a','an','is','are','was','were','be','been',
    'to','of','and','or','in','on','at','by','for','with',
    'this','that','these','those','there','from','into',
    'has','have','may','can','will','could','would',
    'some','any','all','its','it',
    # observation qualifiers (pure filler — carry no semantic signal)
    'visible','visibly','appears','appear','apparent',
    'possible','possibly','potential','potentially','likely',
    'noted','observed','indicating','indicates','suggests',
    'suggesting','showing','shows','present','evidence',
    # location / direction words
    'near','nearby','around','along','across','above','below',
    'throughout','within','where','area','areas',
    'running','extending','spreading',
    'horizontal','horizontally','vertical','vertically',
    'diagonal','diagonally',
    # approximation / hedging
    'approximately','approx','roughly','about','estimated',
    # severity qualifiers
    'slight','minor','major','significant','small','large',
    'multiple','several','various',
    # measurement units
    'inch','inches','foot','feet','sqft','long','wide','length',
})


def _norm_key(text: str) -> str:
    """
    Collapse a verbose Claude-generated issue/upgrade sentence into a short
    content-word fingerprint so identical underlying issues across photos
    are counted together.

    Algorithm:
      1. Lowercase + strip punctuation
      2. Drop tokens that contain any digit (measurements, ranges like 18-24)
      3. Drop tokens in _KEY_STOP (filler, qualifiers, directions)
      4. Drop tokens shorter than 3 chars
      5. Return first 5 remaining words joined by space
    """
    s = re.sub(r'[^\w\s]', ' ', text.lower())
    words = [
        w for w in s.split()
        if not re.search(r'\d', w)
        and len(w) >= 3
        and w not in _KEY_STOP
    ]
    return ' '.join(words[:5])


_DEAL_RISK_WEIGHT: dict[str, float] = {
    "critical": 10.0,
    "high":     5.0,
    "medium":   2.0,
    "low":      1.0,
    "none":     0.5,
}
_DEFAULT_RISK_WEIGHT = 0.5  # unknown / missing deal_risk


def _risk_weight(deal_risk: str | None) -> float:
    """Return the frequency weight for a photo's deal_risk value."""
    risk = (deal_risk or "").strip().lower()
    return _DEAL_RISK_WEIGHT.get(risk, _DEFAULT_RISK_WEIGHT)


def _is_jetted_tub(text: str) -> bool:
    """True when a dated-feature string refers to a jetted/jacuzzi/garden tub."""
    t = text.lower()
    return "jetted" in t or "jacuzzi" in t or "garden tub" in t


def build_analysis_summary(analyses: list[dict]) -> dict:
    """
    Aggregate raw analysis dicts into a structured summary for the ROI prompt.
    Uses normalized key deduplication so the same issue appearing across many
    photos accumulates a weighted score (previously all issues showed count=1
    because Claude writes unique verbose sentences each time).

    Issue weighting by photo deal_risk:
      critical: 10 | high: 5 | medium: 2 | low: 1 | none/unknown: 0.5

    Canonical text = the longest seen string for that key (most descriptive).

    Limits applied to cap token usage:
      - issues_by_frequency:        top 30 by weighted score
      - critical_and_high_issues:   ALL issues from critical/high photos (uncapped)
      - upgrades_by_frequency:      top 30 by raw count
      - dated_features_by_frequency: top 20 by count + always include jetted tub
      - issues_by_room:             top 5 per room, max 10 rooms
      - upgrades_by_room:             top 5 per room, max 10 rooms
    """
    issue_weights:         defaultdict[str, float] = defaultdict(float)
    issue_counts:          Counter = Counter()
    upgrade_counts:        Counter = Counter()
    dated_counts:          Counter = Counter()
    flag_counts:           Counter = Counter()
    condition_counts:      Counter = Counter()
    finish_quality_counts: Counter = Counter()
    deal_risk_counts:      Counter = Counter()

    # normalized_key → longest canonical display text
    issue_canon:   dict[str, str] = {}
    upgrade_canon: dict[str, str] = {}
    dated_canon:   dict[str, str] = {}
    flag_canon:    dict[str, str] = {}

    # flag keys seen in critical/high photos — always include in summary
    critical_high_flag_keys: set[str] = set()

    # issue keys seen in at least one critical/high deal_risk photo
    critical_high_keys: set[str] = set()
    # dated-feature keys for jetted/jacuzzi tub — always included in summary
    pinned_dated_keys: set[str] = set()

    # room → list of canonical display texts (first-seen-wins per room)
    issues_by_room:   defaultdict[str, list[str]] = defaultdict(list)
    upgrades_by_room: defaultdict[str, list[str]] = defaultdict(list)

    for a in analyses:
        room = (a.get("room_type") or "unknown").strip().lower()
        risk = (a.get("deal_risk") or "").strip().lower()
        weight = _risk_weight(risk)

        if cond := (a.get("condition") or "").strip().lower():
            condition_counts[cond] += 1

        if finish := (a.get("finish_quality") or "").strip().lower():
            finish_quality_counts[finish] += 1

        if risk:
            deal_risk_counts[risk] += 1

        for text in (a.get("dated_features") or []):
            text = text.strip()
            if not text:
                continue
            key = _norm_key(text)
            if not key:
                continue
            dated_counts[key] += 1
            if key not in dated_canon or len(text) > len(dated_canon[key]):
                dated_canon[key] = text
            if _is_jetted_tub(text):
                pinned_dated_keys.add(key)

        for text in (a.get("issues") or []):
            text = text.strip()
            if not text:
                continue
            key = _norm_key(text)
            if not key:
                continue
            issue_weights[key] += weight
            issue_counts[key] += 1
            if risk in ("critical", "high"):
                critical_high_keys.add(key)
            if key not in issue_canon or len(text) > len(issue_canon[key]):
                issue_canon[key] = text
            canonical = issue_canon[key]
            if canonical not in issues_by_room[room]:
                issues_by_room[room].append(canonical)

        for text in (a.get("upgrades") or []):
            text = text.strip()
            if not text:
                continue
            key = _norm_key(text)
            if not key:
                continue
            upgrade_counts[key] += 1
            if key not in upgrade_canon or len(text) > len(upgrade_canon[key]):
                upgrade_canon[key] = text
            canonical = upgrade_canon[key]
            if canonical not in upgrades_by_room[room]:
                upgrades_by_room[room].append(canonical)

        for text in (a.get("inspection_flags") or []):
            if isinstance(text, dict):
                text = text.get("flag") or text.get("text") or str(text)
            text = text.strip()
            if not text:
                continue
            key = _norm_key(text)
            if not key:
                continue
            flag_counts[key] += 1
            if key not in flag_canon or len(text) > len(flag_canon[key]):
                flag_canon[key] = text
            if risk in ("critical", "high"):
                critical_high_flag_keys.add(key)

    total_unique_issues   = len(issue_weights)
    total_unique_upgrades = len(upgrade_counts)

    # ── Cap frequency dicts ────────────────────────────────────────
    issues_by_freq = {
        issue_canon[k]: round(score, 1)
        for k, score in sorted(issue_weights.items(), key=lambda kv: kv[1], reverse=True)[:30]
    }
    upgrades_by_freq = {upgrade_canon[k]: c for k, c in upgrade_counts.most_common(30)}

    dated_by_freq = {dated_canon[k]: c for k, c in dated_counts.most_common(20)}
    for key in pinned_dated_keys:
        text = dated_canon.get(key)
        if text and text not in dated_by_freq:
            dated_by_freq[text] = dated_counts[key]

    critical_and_high_issues = sorted(
        (issue_canon[k] for k in critical_high_keys if k in issue_canon),
        key=lambda t: issue_weights[_norm_key(t)],
        reverse=True,
    )

    # ── Cap room dicts: top-5 items per room, max 10 rooms ─────────
    def cap_room_dict(
        room_dict: defaultdict[str, list[str]],
        score_lookup: dict[str, float] | Counter,
        max_rooms: int = 10,
        max_per_room: int = 5,
    ) -> dict[str, list[str]]:
        def room_score(items: list[str]) -> float:
            return sum(float(score_lookup.get(_norm_key(t), 0)) for t in items)

        ranked = sorted(room_dict.items(), key=lambda kv: room_score(kv[1]), reverse=True)
        result: dict[str, list[str]] = {}
        for room, items in ranked[:max_rooms]:
            sorted_items = sorted(
                items,
                key=lambda t: float(score_lookup.get(_norm_key(t), 0)),
                reverse=True,
            )
            result[room] = sorted_items[:max_per_room]
        return result

    capped_issues_by_room   = cap_room_dict(issues_by_room,   issue_weights)
    capped_upgrades_by_room = cap_room_dict(upgrades_by_room, upgrade_counts)

    # Top inspection flags — all from critical/high photos, plus top 20 by frequency
    top_flags_by_freq = {flag_canon[k]: c for k, c in flag_counts.most_common(20)}
    critical_high_flags = sorted(
        (flag_canon[k] for k in critical_high_flag_keys if k in flag_canon),
        key=lambda t: flag_counts[_norm_key(t)],
        reverse=True,
    )

    return {
        "total_photos":                len(analyses),
        "total_unique_issues":         total_unique_issues,
        "total_unique_upgrades":       total_unique_upgrades,
        "condition_summary":           dict(condition_counts.most_common()),
        "finish_quality_summary":      dict(finish_quality_counts.most_common()),
        "deal_risk_summary":           dict(deal_risk_counts.most_common()),
        "dated_features_by_frequency": dated_by_freq,
        "issues_by_frequency":         issues_by_freq,
        "critical_and_high_issues":    critical_and_high_issues,
        "inspection_flags_by_frequency": top_flags_by_freq,
        "critical_and_high_flags":     critical_high_flags,
        "upgrades_by_frequency":       upgrades_by_freq,
        "issues_by_room":              capped_issues_by_room,
        "upgrades_by_room":            capped_upgrades_by_room,
    }


def _print_report_summary(report: dict, detail_level: str, buyer_profile: str) -> None:
    """Print executive summary block for one generated report."""
    print("\n" + "-" * 60)
    print(f"EXECUTIVE SUMMARY  [{detail_level.upper()} / {buyer_profile.upper()}]")
    print("-" * 60)

    ex  = report.get("executive_summary") or {}
    arv = ex.get("estimated_arv")
    if arv:
        print(f"Estimated ARV:      ${float(arv):,.0f}")

    upgrades = report.get("upgrades") or []
    repairs  = report.get("repairs")  or []

    total_upgrade_cost = sum(float(u.get("estimated_cost") or 0) for u in upgrades)
    critical_count     = sum(1 for r in repairs if r.get("priority") == "critical")

    print(f"Total upgrade cost: ${total_upgrade_cost:,.0f}")
    print(f"Critical repairs:   {critical_count}")
    print(f"Upgrades returned:  {len(upgrades)}")
    print(f"Repairs returned:   {len(repairs)}")

    if ex.get("recommendation"):
        print(f"\n{ex['recommendation']}")

    profile_notes = report.get("buyer_profile_notes") or []
    if profile_notes:
        print(f"\nBuyer profile notes ({buyer_profile}):")
        for note in profile_notes[:3]:
            print(f"  - {note}")

    if upgrades:
        print("\nTop upgrades by ROI:")
        for u in upgrades[:5]:
            roi  = u.get("roi_percent", 0)
            name = u.get("name", "-")
            cost = u.get("estimated_cost", 0)
            print(f"  {roi:>6.1f}%  {name}  (${cost:,.0f})")

    print("-" * 60)


def _save_report(client, report_id: str, report: dict) -> None:
    try:
        client.table(REPORT_TABLE).upsert({
            "id":     report_id,
            "report": report,
        }).execute()
        print(f"Report saved to Supabase table '{REPORT_TABLE}' (id='{report_id}').")
    except Exception as exc:
        print(f"WARNING: Could not save report to Supabase: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a pre-sale ROI report from Supabase photo analyses."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all three levels in sequence (executive → standard → deep_dive)",
    )
    parser.add_argument(
        "--detail",
        choices=sorted(DETAIL_LEVELS),
        default="standard",
        metavar="LEVEL",
        help="executive | standard | deep_dive  (default: standard)",
    )
    parser.add_argument(
        "--buyer",
        choices=sorted(BUYER_PROFILES),
        default="general",
        metavar="PROFILE",
        help="first_time_buyer | young_family | downsizer | investor | "
             "relocating_professional | general  (default: general)",
    )
    args = parser.parse_args()

    detail_level  = args.detail
    buyer_profile = args.buyer
    generate_all  = args.all

    if generate_all:
        detail_level = "deep_dive"

    report_id = f"{detail_level}_{buyer_profile}"

    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GEMINI_API_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    if generate_all:
        print("Mode:          all three levels (additive chain)")
    else:
        chain = levels_up_to(detail_level)
        print(f"Mode:          chain through {detail_level} ({' → '.join(chain)})")
    print(f"Buyer profile: {buyer_profile}")
    print(f"Final report:  {report_id}")
    print()

    client = get_supabase()

    # 1. Load raw analyses
    analyses = load_analyses(client)
    if not analyses:
        print("No usable analyses found. Run run_analysis.py first.")
        sys.exit(0)

    # 2. Pre-process into structured summary
    summary = build_analysis_summary(analyses)
    print(f"Summary: {summary['total_photos']} photos | "
          f"{summary['total_unique_issues']} unique issues (showing top 30) | "
          f"{summary['total_unique_upgrades']} unique upgrades (showing top 30)")

    # Diagnostic: show top 10 issues by weighted score
    print("\nTop 10 issues by weighted score:")
    for text, score in list(summary["issues_by_frequency"].items())[:10]:
        print(f"  [{score:>5.1f} pts]  {text[:80]}")
    crit_high = summary.get("critical_and_high_issues") or []
    print(f"\nCritical/high deal-risk issues (always sent to repairs prompt): {len(crit_high)}")
    for text in crit_high[:5]:
        print(f"  - {text[:80]}")
    if len(crit_high) > 5:
        print(f"  ... and {len(crit_high) - 5} more")
    print("Top 5 upgrades by frequency:")
    for text, count in list(summary["upgrades_by_frequency"].items())[:5]:
        print(f"  [{count:>3}x]  {text[:80]}")

    # 3. Load property data
    property_summary = get_property_summary()
    last_sale        = get_last_sale()
    print(f"Property: {property_summary.get('address')}")
    print(
        f"Market value: ${property_summary.get('market_value'):,}  |  "
        f"Last sale: ${last_sale.get('sale_amount'):,} ({last_sale.get('sale_date')})"
    )

    # 4. Generate ROI report(s) in additive sequence
    property_summary = get_property_summary()
    last_sale        = get_last_sale()

    if generate_all:
        print(f"\nGenerating all ROI reports for [{buyer_profile}] buyer with Gemini...")
        result = generate_all_roi_reports(
            summary, property_summary, last_sale, buyer_profile=buyer_profile,
        )
        if result.get("error"):
            print(f"ERROR generating reports: {result['error']}", file=sys.stderr)
            sys.exit(1)
        reports = {k: v for k, v in result.items() if k != "error"}
        for level, report in reports.items():
            rid = f"{level}_{buyer_profile}"
            _save_report(client, rid, report)
            _print_report_summary(report, level, buyer_profile)
        return

    chain = levels_up_to(detail_level)
    prior: dict | None = None
    report: dict | None = None

    for level in chain:
        level_report_id = f"{level}_{buyer_profile}"
        print(f"\nGenerating [{level}] ROI report for [{buyer_profile}] buyer with Gemini...")
        report = generate_roi_report(
            summary, property_summary, last_sale,
            detail_level=level,
            buyer_profile=buyer_profile,
            prior_report=prior,
        )
        if report.get("error"):
            print(f"ERROR generating report: {report['error']}", file=sys.stderr)
            sys.exit(1)
        _save_report(client, level_report_id, report)
        prior = report

    assert report is not None
    _print_report_summary(report, detail_level, buyer_profile)


if __name__ == "__main__":
    main()
