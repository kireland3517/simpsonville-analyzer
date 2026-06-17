#!/usr/bin/env python3
"""Verify Phase 12 tier-driven report line items (no LLM)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decision_matrix import load_current_matrix, load_matrix_rows_with_options
from report_composer import compose_line_items_from_tier
from tier_selector import select_tier_from_rows


def _names(items: list[dict]) -> str:
    return " | ".join(i.get("name", "?") for i in items)


def _find_component(items: list[dict], component: str) -> bool:
    needle = component.lower()
    for i in items:
        head = (i.get("name") or "").split("—")[0].strip().lower()
        if needle in head:
            return True
    return False


def _find(items: list[dict], *needles: str) -> bool:
    blob = " ".join((i.get("name") or "") + " " + (i.get("description") or "") for i in items).lower()
    return all(n.lower() in blob for n in needles)


def _traceable(items: list[dict]) -> tuple[int, int]:
    ok = sum(1 for i in items if i.get("matrix_row_id") and (i.get("matrix_option_id") or i.get("option_id")))
    return ok, len(items)


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("SKIP: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        return 0

    from supabase import create_client

    sb = create_client(url, key)
    property_id = os.environ.get("PROPERTY_ID", "130_kingfisher")
    matrix = load_current_matrix(sb, property_id)
    if not matrix:
        print("FAIL: no decision matrix")
        return 1

    rows = load_matrix_rows_with_options(sb, matrix["id"])
    errors: list[str] = []

    for tier in ("must_do", "should_do", "nice_to_do"):
        sel = select_tier_from_rows(rows, tier, matrix_id=matrix["id"], property_id=property_id)
        items = compose_line_items_from_tier(rows, sel)
        all_lines = (items.get("upgrades") or []) + (items.get("repairs") or [])
        traced, total = _traceable(all_lines)
        print(f"\n=== {tier} ({sel['selected_count']} rows, {total} spend lines, {traced} traced) ===")

        if total and traced != total:
            errors.append(f"{tier}: {total - traced} items missing matrix traceability")

        if tier == "must_do":
            for label, needles in (
                ("garage door", ("garage", "door")),
                ("smoke remediation", ("smoke",)),
                ("ceiling water damage", ("water",)),
                ("popcorn ceiling", ("popcorn",)),
            ):
                if not _find(all_lines, *needles):
                    errors.append(f"must_do missing {label}: {_names(all_lines[:8])}…")

        if tier in ("must_do", "should_do"):
            if _find_component(all_lines, "countertops"):
                errors.append(f"{tier} should not include countertops")

        if tier == "nice_to_do":
            if not _find_component(all_lines, "countertops"):
                errors.append(f"{tier} missing countertops")

    if errors:
        print("\nFAILURES:")
        for e in errors:
            print("  -", e)
        return 1

    print("\nOK: tier report projection checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
