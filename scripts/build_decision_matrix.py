"""
Build and persist the Decision Matrix for a property.

Usage:
    python scripts/build_decision_matrix.py --property-id 130_kingfisher

Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in environment or .env at project root.
Run migrations/decision_matrix_v1.sql in Supabase before first use.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv()
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")

from supabase import create_client

from decision_matrix import build_decision_matrix


def get_supabase():
    import os

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Decision Matrix from evidence package")
    parser.add_argument(
        "--property-id",
        default="130_kingfisher",
        help="Property ID (default: 130_kingfisher)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build matrix in memory without writing to Supabase",
    )
    args = parser.parse_args()

    sb = get_supabase()
    print(f"Building decision matrix for {args.property_id}...")
    if args.dry_run:
        from decision_matrix import build_decision_matrix_dry_run
        result = build_decision_matrix_dry_run(property_id=args.property_id, sb=sb)
    else:
        try:
            result = build_decision_matrix(property_id=args.property_id, sb=sb)
        except Exception as exc:
            err = str(exc)
            if "decision_matrices" in err and "schema cache" in err:
                print(
                    "ERROR: Decision matrix tables not found.\n"
                    "Apply migrations/decision_matrix_v1.sql in the Supabase SQL Editor.\n",
                    file=sys.stderr,
                )
            raise

    print()
    print("=" * 60)
    print("DECISION MATRIX BUILT")
    print("=" * 60)
    print(f"  matrix_id:              {result['matrix_id']}")
    print(f"  version:                {result['version']}")
    print(f"  evidence_hash:          {result['evidence_hash'][:16]}...")
    print(f"  total rows:             {result['total_rows']}")
    print(f"  walkthrough rows:       {result['walkthrough_rows']}")
    print(f"  photo-only rows:        {result['photo_only_rows']}")
    print(f"  prior matrices stale:   {result['prior_matrices_marked_stale']}")
    print()
    print("Counts by decision_status:")
    for status, count in sorted(result["counts_by_decision_status"].items()):
        print(f"  {status:20s} {count}")
    print()
    print("Counts by recommended_action:")
    for action, count in sorted(result["counts_by_recommended_action"].items()):
        print(f"  {action:20s} {count}")
    print()
    print("Top 20 rows:")
    print(f"  {'Zone':<22} {'Component':<36} {'Status':<18} Action")
    print("  " + "-" * 95)
    for row in result["preview_top_20"]:
        zone = (row["zone"] or "")[:21]
        comp = (row["component"] or "")[:35]
        print(
            f"  {zone:<22} {comp:<36} {row['decision_status']:<18} {row['recommended_action']}"
        )
    print()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
