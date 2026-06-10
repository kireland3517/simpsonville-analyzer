"""
walkthrough_impact.py
---------------------
Build observation → recommendation traceability for ROI reports.
"""
from __future__ import annotations

from typing import Any

from evidence import _component_keys, _norm

_LEVEL_LABELS: dict[str, str] = {
    "spend_nothing": "Spend Nothing",
    "budget_5k": "$5,000 Budget",
    "budget_15k": "$15,000 Budget",
    "maximize": "Maximize Sale Price",
    "executive": "Spend Nothing",
    "standard": "$15,000 Budget",
    "deep_dive": "Maximize Sale Price",
}

_SCENARIO_INFERRED_EXCLUDED = frozenset({"spend_nothing", "executive"})


def _normalize_scenario(scenario: str) -> str:
    from roi import normalize_detail_level

    return normalize_detail_level(scenario)


def _prompt_section(component: dict[str, Any]) -> str | None:
    """Which format_evidence_prompt section this component occupies."""
    if not component.get("include_in_report"):
        return None
    if component.get("looks_fine"):
        return "seller_ok" if component.get("walkthrough_note") else "dismissed"
    tier = component.get("confidence_tier") or "unknown"
    if tier == "confirmed":
        return "confirmed"
    if tier == "observed":
        if component.get("walkthrough_note"):
            return "observed"
        return None
    if tier == "inferred":
        return "inferred"
    return None


def _is_analyzed(prompt_section: str | None) -> bool:
    return prompt_section in ("confirmed", "observed", "inferred", "seller_ok")


def _note_excerpt(note: str, max_len: int = 80) -> str:
    text = (note or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _note_matches_evidence(note: str, evidence_text: str) -> bool:
    note = (note or "").strip()
    ev = (evidence_text or "").strip()
    if not note or not ev:
        return False
    n, e = _norm(note), _norm(ev)
    if n in e or e in n:
        return True
    if len(n) >= 12 and n[:12] in e:
        return True
    threshold = max(12, int(len(n) * 0.5))
    for start in range(len(n) - threshold + 1):
        if n[start : start + threshold] in e:
            return True
    return False


def _component_in_text(component: str, *texts: str | None) -> bool:
    keys = _component_keys(component)
    for text in texts:
        if not text:
            continue
        t = _norm(text)
        if any(k in t for k in keys):
            return True
    return False


def _collect_walkthrough_citations(
    upgrades: list[dict[str, Any]],
    repairs: list[dict[str, Any]],
) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for rec_type, items in (("upgrade", upgrades), ("repair", repairs)):
        for item in items or []:
            name = item.get("name") or ""
            desc = item.get("description") or ""
            rationale = item.get("rationale") or {}
            for ev in rationale.get("evidence") or []:
                if _norm(ev.get("source") or "") != "walkthrough":
                    continue
                citations.append({
                    "name": name,
                    "type": rec_type,
                    "text": ev.get("text") or "",
                    "description": desc,
                })
    return citations


def _match_influenced(
    zone: str,
    component: str,
    note: str,
    citations: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    influenced: list[dict[str, str]] = []

    for cite in citations:
        key = (cite["name"], cite["type"])
        if key in seen:
            continue
        comp_hit = _component_in_text(component, cite["text"], cite["name"], cite.get("description"))
        note_hit = _note_matches_evidence(note, cite["text"])
        if comp_hit or note_hit:
            seen.add(key)
            influenced.append({"name": cite["name"], "type": cite["type"]})

    return influenced


def _not_selected_reason(
    *,
    prompt_section: str | None,
    scenario: str,
    looks_fine: bool,
    has_photos: bool,
    influenced: list[dict[str, str]],
) -> str | None:
    if influenced:
        return None
    if not _is_analyzed(prompt_section):
        return None
    if looks_fine and prompt_section == "seller_ok":
        return "You noted no concerns — excluded from recommendations"
    if prompt_section == "inferred" and _normalize_scenario(scenario) in _SCENARIO_INFERRED_EXCLUDED:
        return "Inferred finding — excluded at Spend Nothing budget"
    if not has_photos and prompt_section in ("confirmed", "observed"):
        return "Walkthrough-only — no matching recommendation at this budget"
    return "Not selected at this budget level"


def _row_lookup(walkthrough_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in walkthrough_rows:
        key = (_norm(row.get("zone") or ""), _norm(row.get("component") or ""))
        out[key] = row
    return out


def build_walkthrough_impact(
    package: dict[str, Any],
    scenario: str,
    upgrades: list[dict[str, Any]],
    repairs: list[dict[str, Any]],
    walkthrough_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build traceability payload for included seller observations.

    Only rows with owner_note + include_in_report appear. Sorted: influenced first.
    """
    scenario = _normalize_scenario(scenario)
    scenario_label = _LEVEL_LABELS.get(scenario, scenario)
    row_by_key = _row_lookup(walkthrough_rows)
    citations = _collect_walkthrough_citations(upgrades, repairs)
    items: list[dict[str, Any]] = []

    for comp in package.get("components") or []:
        note = (comp.get("walkthrough_note") or "").strip()
        if not note or not comp.get("include_in_report"):
            continue

        zone = comp.get("zone") or ""
        component = comp.get("component") or ""
        row = row_by_key.get((_norm(zone), _norm(component)), {})
        item_id = row.get("id")
        prompt_section = _prompt_section(comp)
        analyzed = _is_analyzed(prompt_section)
        photos = comp.get("photo_observations") or []
        tier = comp.get("confidence_tier") or "unknown"
        if comp.get("looks_fine"):
            tier = "unknown"

        influenced = _match_influenced(zone, component, note, citations) if analyzed else []
        not_selected = _not_selected_reason(
            prompt_section=prompt_section,
            scenario=scenario,
            looks_fine=bool(comp.get("looks_fine")),
            has_photos=bool(photos),
            influenced=influenced,
        )

        items.append({
            "walkthrough_item_id": item_id,
            "zone": zone,
            "component": component,
            "note_excerpt": _note_excerpt(note),
            "evidence_tier": tier if tier in ("confirmed", "observed", "inferred") else "observed",
            "analyzed": analyzed,
            "prompt_section": prompt_section,
            "influenced": influenced,
            "not_selected_reason": not_selected,
            "corroboration": "photo" if photos else None,
        })

    items.sort(key=lambda i: (
        0 if i.get("influenced") else 1,
        (i.get("zone") or "").lower(),
        (i.get("component") or "").lower(),
    ))

    influenced_count = sum(1 for i in items if i.get("influenced"))
    analyzed_not_selected = sum(
        1 for i in items
        if i.get("analyzed") and not i.get("influenced")
    )

    return {
        "summary": {
            "included": len(items),
            "influenced": influenced_count,
            "analyzed_not_selected": analyzed_not_selected,
            "scenario": scenario,
            "scenario_label": scenario_label,
        },
        "items": items,
    }
