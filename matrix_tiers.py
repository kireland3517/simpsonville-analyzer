"""
matrix_tiers.py
---------------
Deterministic listing-readiness tier assignment per decision matrix row.
"""
from __future__ import annotations

from typing import Any

from evidence import _norm

READINESS_TIERS = frozenset({"must_do", "should_do", "nice_to_do", "aspirational"})

TIER_ORDER = ("must_do", "should_do", "nice_to_do", "aspirational")

_TIER_INDEX = {t: i for i, t in enumerate(TIER_ORDER)}

_MUST_DO_SIGNALS = (
    "safety hazard", "exposed wire", "electrical wire", "electrical hazard",
    "smoke odor", "cigarette", "tobacco", "odor",
    "water damage", "water stain", "active leak", "moisture intrusion",
    "missing panel", "structural crack", "spider-web", "off-track",
    "deal killer", "gfci", "unsecured", "dangling",
)

_NICE_TO_DO_COMPONENTS = (
    "hardware", "light fixture", "interior lighting", "cabinet hardware",
    "door hardware", "faucet", "vanity", "trim paint", "switch plate",
    "outlet cover",
)

_SHOULD_DO_COMPONENTS = (
    "interior paint", "landscaping", "pressure wash", "front porch",
    "garage walls", "garage floor",
)


def _row_blob(row: dict[str, Any]) -> str:
    note = row.get("walkthrough_notes") or ""
    state = row.get("current_state") or ""
    photos = " ".join(
        pe.get("observation") or ""
        for pe in (row.get("photo_evidence") or [])
    )
    return _norm(f"{row.get('component') or ''} {note} {state} {photos}")


def _component_match(comp: str, *needles: str) -> bool:
    return any(n in comp for n in needles)


def _deck_safety_issue(blob: str) -> bool:
    return any(s in blob for s in (
        "structural", "safety", "rot", "railing", "rail loose", "post crack",
        "split", "failure", "collapse", "unsafe",
    ))


def _crawlspace_inspection_required(row: dict[str, Any], blob: str) -> bool:
    if row.get("decision_status") == "required_action":
        if row.get("recommended_action") == "further_inspect":
            return True
    return any(s in blob for s in (
        "unknown", "not confirmed", "requires evaluation", "requires further",
        "moisture", "vapor barrier", "not yet confirmed",
    ))


def assign_readiness_tiers(row: dict[str, Any]) -> tuple[str, str]:
    """Return (minimum_tier, recommended_tier) for a matrix row."""
    comp = _norm(row.get("component") or "")
    blob = _row_blob(row)
    status = row.get("decision_status") or ""
    rec_action = row.get("recommended_action") or ""
    insp = row.get("inspection_risk") or "low"
    buyer = row.get("buyer_impact") or "medium"

    # ── Hardcoded Kingfisher component rules ─────────────────────────────
    if _component_match(comp, "garage door"):
        return "must_do", "must_do"

    if _component_match(comp, "indoor air quality") or any(s in blob for s in ("smoke odor", "cigarette", "tobacco odor")):
        return "must_do", "must_do"

    if _component_match(comp, "ceiling water", "water damage"):
        return "must_do", "must_do"

    if "popcorn ceiling" in comp:
        return "should_do", "should_do"

    if comp == "deck" or "deck condition" in comp:
        if _deck_safety_issue(blob) or insp == "high":
            return "must_do", "must_do"
        return "should_do", "should_do"

    if "countertop" in comp:
        return "nice_to_do", "should_do"

    if "crawlspace" in comp:
        if _crawlspace_inspection_required(row, blob):
            return "must_do", "must_do"
        return "should_do", "should_do"

    if "fireplace" in comp:
        return "nice_to_do", "aspirational"

    if "exterior lighting" in comp:
        return "nice_to_do", "nice_to_do"

    if "driveway" in comp and "pressure wash" not in comp:
        return "should_do", "should_do"

    # ── General must_do signals ────────────────────────────────────────
    if any(s in blob for s in _MUST_DO_SIGNALS):
        return "must_do", "must_do"

    if _component_match(comp, "electrical", "smoke detector", "co detector", "carbon monoxide"):
        if status == "required_action" or insp == "high":
            return "must_do", "must_do"

    if status == "required_action":
        if rec_action in ("replace", "clean", "repair") or insp == "high":
            return "must_do", "must_do"
        if rec_action == "further_inspect" and insp == "high":
            return "must_do", "must_do"

    if status == "required_action":
        return "must_do", "should_do"

    # ── should_do ──────────────────────────────────────────────────────
    if any(k in comp for k in _SHOULD_DO_COMPONENTS):
        return "should_do", "should_do"

    if "gutter" in comp or "drainage" in comp or "downspout" in comp:
        return "should_do", "should_do"

    if status == "decision_required" and buyer == "high" and rec_action in ("refresh", "repair", "clean"):
        return "should_do", "should_do"

    if status == "decision_required" and rec_action == "refresh":
        return "should_do", "should_do"

    if status == "monitor" and rec_action == "further_inspect":
        return "should_do", "should_do"

    # ── nice_to_do ─────────────────────────────────────────────────────
    if any(k in comp for k in _NICE_TO_DO_COMPONENTS):
        return "nice_to_do", "nice_to_do"

    if status == "decision_required" and rec_action in ("leave_as_is", "clean", "refresh"):
        if buyer in ("medium", "low"):
            return "nice_to_do", "should_do" if rec_action == "refresh" else "nice_to_do"
        return "nice_to_do", "should_do"

    if status == "informational":
        return "nice_to_do", "nice_to_do"

    if status == "monitor":
        return "nice_to_do", "should_do"

    # ── aspirational ───────────────────────────────────────────────────
    if rec_action == "replace" and buyer != "high" and insp == "low":
        return "nice_to_do", "aspirational"

    if status == "decision_required" and rec_action == "replace":
        return "should_do", "aspirational"

    # ── Default ────────────────────────────────────────────────────────
    return "nice_to_do", "should_do"


def apply_tiers_to_row(row: dict[str, Any]) -> dict[str, Any]:
    minimum_tier, recommended_tier = assign_readiness_tiers(row)
    row["minimum_tier"] = minimum_tier
    row["recommended_tier"] = recommended_tier
    return row


def compute_tier_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count rows by minimum_tier and recommended_tier."""
    by_minimum: dict[str, int] = {t: 0 for t in TIER_ORDER}
    by_recommended: dict[str, int] = {t: 0 for t in TIER_ORDER}
    missing_minimum = 0
    missing_recommended = 0

    for row in rows:
        min_t = row.get("minimum_tier")
        rec_t = row.get("recommended_tier")
        if min_t in by_minimum:
            by_minimum[min_t] += 1
        else:
            missing_minimum += 1
        if rec_t in by_recommended:
            by_recommended[rec_t] += 1
        else:
            missing_recommended += 1

    return {
        "by_minimum_tier": by_minimum,
        "by_recommended_tier": by_recommended,
        "missing_minimum_tier": missing_minimum,
        "missing_recommended_tier": missing_recommended,
        "total_rows": len(rows),
    }
