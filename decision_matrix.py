"""
decision_matrix.py
------------------
Build and persist a Decision Matrix from the unified evidence package.
Deterministic rules only — no LLM option scoring in MVP.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from evidence import build_evidence_package, default_property_facts, _norm
from run_roi import build_analysis_summary
from walkthrough import (
    _derive_buyer_impact,
    load_walkthrough_items,
)

MATRICES_TABLE = "decision_matrices"
ROWS_TABLE = "decision_matrix_rows"
STATE_TABLE = "property_decision_state"
PHOTO_TABLE = "photo_analyses"

DECISION_STATUSES = frozenset({
    "required_action", "decision_required", "monitor", "informational",
})
RECOMMENDED_ACTIONS = frozenset({
    "leave_as_is", "clean", "repair", "refresh", "replace", "further_inspect",
})

_REPLACE_SIGNALS = (
    "full replacement", "replacement required", "replace full",
    "missing panel", "missing entirely", "structural crack",
    "spider-web", "panel separation", "off-track", "spring failure",
    "exposing the full garage interior",
)
_SAFETY_SIGNALS = (
    "exposed wire", "electrical wire", "safety hazard", "unsecured",
    "dangling", "gfci",
)
_ODOR_SIGNALS = ("smoke odor", "cigarette", "tobacco", "odor")
_WATER_SIGNALS = (
    "water damage", "water stain", "water-damaged", "water-damaged ceiling",
    "active leak", "active water stain", "staining has not", "source of historical",
    "moisture intrusion", "moisture damage",
)
_STRUCTURAL_UNKNOWN = (
    "structural condition has not", "not yet been confirmed",
    "requires further evaluation", "requires evaluation by",
)
_SYSTEM_COMPONENTS = (
    "hvac age", "electrical panel", "plumbing system", "water heater age",
    "smoke detector", "co detector", "carbon monoxide",
)
_INFORMATIONAL_SIGNALS = (
    "approximately", "observed", "sqft", "feet by", "ceiling height",
    "outlets observed", "switches observed", "doors:", "contains vaulted",
)
_DATED_REFRESH_COMPONENTS = (
    "countertop", "popcorn ceiling", "cabinet", "vanity", "light fixture",
    "hardware", "trim paint", "interior paint",
)


def _text_blob(*parts: str | None) -> str:
    return _norm(" ".join(p for p in parts if p))


def _photo_hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode()).hexdigest()[:16]


def _canonical_evidence_hash(package: dict[str, Any]) -> str:
    """Stable hash of evidence inputs that drive matrix rows."""
    components = []
    for c in package.get("components") or []:
        components.append({
            "zone": c.get("zone"),
            "component": c.get("component"),
            "include_in_report": c.get("include_in_report"),
            "walkthrough_note": c.get("walkthrough_note"),
            "photo_observations": sorted(c.get("photo_observations") or []),
            "confidence_tier": c.get("confidence_tier"),
            "looks_fine": c.get("looks_fine"),
        })
    payload = {
        "components": sorted(
            components,
            key=lambda x: (_norm(x.get("zone") or ""), _norm(x.get("component") or "")),
        ),
        "photo_only_findings": sorted(package.get("photo_only_findings") or []),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_photo_records(sb) -> list[dict[str, Any]]:
    if not sb:
        return []
    try:
        result = sb.table(PHOTO_TABLE).select("id, filename, analysis").execute()
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for row in result.data or []:
        raw = row.get("analysis")
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if raw.get("error") and not raw.get("room_type"):
            continue
        records.append({
            "id": row.get("id") or row.get("filename"),
            "filename": row.get("filename") or row.get("id"),
            "analysis": raw,
        })
    return records


def _photo_texts(analysis: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for issue in analysis.get("issues") or []:
        if isinstance(issue, str) and issue.strip():
            texts.append(issue.strip())
    for upgrade in analysis.get("upgrades") or []:
        if isinstance(upgrade, str) and upgrade.strip():
            texts.append(upgrade.strip())
    for dated in analysis.get("dated_features") or []:
        if isinstance(dated, str) and dated.strip():
            texts.append(dated.strip())
    for flag in analysis.get("inspection_flags") or []:
        if isinstance(flag, str) and flag.strip():
            texts.append(flag.strip())
    return texts


def _observation_matches_photo(observation: str, photo_text: str) -> bool:
    obs = _norm(observation)
    pt = _norm(photo_text)
    if not obs or not pt:
        return False
    if obs in pt or pt in obs:
        return True
    obs_words = [w for w in obs.split() if len(w) >= 4]
    if len(obs_words) >= 3:
        hits = sum(1 for w in obs_words if w in pt)
        if hits >= max(3, len(obs_words) // 2):
            return True
    key = " ".join(obs_words[:5])
    if len(key) >= 12 and key in pt:
        return True
    return False


def _match_photos_to_observation(
    observation: str,
    photo_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in photo_records:
        analysis = rec.get("analysis") or {}
        for text in _photo_texts(analysis):
            if _observation_matches_photo(observation, text):
                fn = rec.get("filename") or rec.get("id") or "unknown"
                if fn in seen:
                    continue
                seen.add(fn)
                matched.append({
                    "filename": fn,
                    "observation": text,
                    "room_type": analysis.get("room_type"),
                })
                break
    return matched[:5]


def _build_photo_evidence(
    observations: list[str],
    photo_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for obs in observations:
        for item in _match_photos_to_observation(obs, photo_records):
            key = item["filename"]
            if key in seen:
                continue
            seen.add(key)
            evidence.append(item)
    return evidence


def _walkthrough_lookup(wt_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in wt_rows:
        key = (_norm(row.get("zone") or ""), _norm(row.get("component") or ""))
        lookup[key] = row
    return lookup


def _derive_marketability_risk(
    category: str | None,
    buyer_visibility: str | None,
    blob: str,
) -> str:
    if category == "dated" and buyer_visibility == "high":
        return "high"
    if category == "cosmetic" and buyer_visibility == "high":
        return "high"
    if category == "dated" or "dated" in blob or "builder grade" in blob:
        return "medium"
    if category == "cosmetic":
        return "medium"
    return "low"


def _escalate_inspection_risk(template_risk: str | None, blob: str) -> str:
    high_signals = (
        "structural", "safety hazard", "exposed wire", "crack penetrating",
        "moisture intrusion", "active leak", "missing panel", "deal killer",
    )
    medium_signals = ("crack", "damage", "leak", "inspect", "evaluation")
    if any(s in blob for s in high_signals):
        return "high"
    if template_risk == "high":
        return "high"
    if any(s in blob for s in medium_signals) or template_risk == "medium":
        return "medium"
    return template_risk or "low"


def _build_current_state(note: str | None, photos: list[str], component: str) -> str:
    if note and photos:
        return f"{note.strip()} Photo analysis corroborates: {photos[0][:180]}."
    if note:
        return note.strip()[:400]
    if photos:
        return f"{component}: {photos[0][:350]}"
    return f"{component}: condition documented; no seller note on file."


def _build_evidence_sources(
    walkthrough_item_id: str | None,
    note: str | None,
    photo_evidence: list[dict[str, Any]],
    property_context: list[str],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if note and walkthrough_item_id:
        sources.append({
            "source": "walkthrough",
            "ref": walkthrough_item_id,
            "text": note,
            "weight": "highest",
        })
    elif note:
        sources.append({
            "source": "walkthrough",
            "ref": None,
            "text": note,
            "weight": "highest",
        })
    for pe in photo_evidence:
        sources.append({
            "source": "photo",
            "ref": pe.get("filename"),
            "text": pe.get("observation"),
            "weight": "medium",
        })
    for ctx in property_context or []:
        sources.append({
            "source": "metadata",
            "ref": "property_facts",
            "text": ctx,
            "weight": "low",
        })
    return sources


def _is_informational(component: str, note: str | None, category: str | None) -> bool:
    comp = _norm(component)
    text = _text_blob(component, note)
    if category == "inspection_risk":
        return False
    if any(s in comp for s in ("outlet", "switch", "door", "room context", "ceiling height")):
        if note and any(s in _norm(note) for s in _INFORMATIONAL_SIGNALS):
            if "requires evaluation" not in text and "not yet confirmed" not in text:
                return True
    if note and "approximately" in _norm(note) and "requires evaluation" not in text:
        return True
    if note and "vaulted ceilings" in _norm(note) and "assessment" not in text:
        return True
    return False


def _decision_blob(
    component: str,
    note: str | None,
    photos: list[str],
    confidence_tier: str | None,
) -> str:
    """Evidence text for classification — walkthrough note takes precedence."""
    if note and confidence_tier in ("confirmed", "observed"):
        return _text_blob(component, note)
    return _text_blob(component, note, " ".join(photos))


def _classify_decision(
    *,
    zone: str,
    component: str,
    note: str | None,
    photos: list[str],
    category: str | None,
    template_risk: str | None,
    buyer_impact: str,
    condition_label: str | None,
    blob: str,
) -> tuple[str, str]:
    """Return (decision_status, recommended_action) using deterministic rules."""
    comp = _norm(component)

    # ── Required action: safety / replacement / odor ─────────────────────
    if "garage door" in comp:
        if any(s in blob for s in _REPLACE_SIGNALS) or "structural crack" in blob:
            return "required_action", "replace"
        if "crack" in blob or "30 years" in blob:
            return "decision_required", "further_inspect"

    if "crawlspace" in comp:
        return "required_action", "further_inspect"

    # ── Monitor: systems with unknown status ─────────────────────────────
    if any(sys_name in comp for sys_name in _SYSTEM_COMPONENTS):
        if "not yet confirmed" in blob or "requires evaluation" in blob or not note:
            return "monitor", "further_inspect"

    if any(s in blob for s in _ODOR_SIGNALS):
        return "required_action", "clean"

    if any(s in blob for s in _SAFETY_SIGNALS):
        return "required_action", "repair"

    if "water damage" in comp or "ceiling water" in comp or any(s in blob for s in _WATER_SIGNALS):
        if "source" in blob or "not confirmed" in blob or "unknown" in blob:
            return "required_action", "further_inspect"
        return "required_action", "repair"

    # ── Informational: context-only rows ───────────────────────────────
    if _is_informational(component, note, category):
        return "informational", "leave_as_is"

    # ── Cosmetic refresh (before substring deck/driveway false positives) ─
    if category == "cosmetic":
        if "pressure wash" in comp or "landscaping" in comp or "front porch" in comp:
            return "decision_required", "clean"
        if buyer_impact == "high":
            return "decision_required", "refresh"
        return "decision_required", "clean"

    # ── Deck / structural unknown ────────────────────────────────────────
    if comp == "deck" or "deck condition" in comp:
        if any(s in blob for s in _STRUCTURAL_UNKNOWN):
            return "decision_required", "further_inspect"
        return "decision_required", "repair"

    if "fireplace" in comp:
        if "assess" in blob or "?" in (note or ""):
            return "decision_required", "further_inspect"
        return "decision_required", "repair"

    if "driveway" in comp and "pressure wash" not in comp:
        if "assess" in blob or "structural" in blob:
            return "decision_required", "further_inspect"
        return "decision_required", "repair"

    if "popcorn ceiling" in comp:
        return "decision_required", "refresh"

    if "exterior lighting" in comp:
        if "requires evaluation" in blob:
            return "monitor", "further_inspect"
        return "decision_required", "refresh"

    # ── Dated choices ────────────────────────────────────────────────────
    if category == "dated" and any(k in comp for k in _DATED_REFRESH_COMPONENTS):
        if "serviceable" in blob or "no cracking" in blob or "functionally" in blob:
            return "decision_required", "leave_as_is"
        return "decision_required", "refresh"

    if "smoke detector" in comp or "co detector" in comp or "carbon monoxide" in comp:
        if "test" in blob or "review" in blob or "not yet confirmed" in blob:
            return "monitor", "further_inspect"

    if category == "functional":
        if condition_label in ("poor", "replace") and "smoke" not in comp:
            return "required_action", "repair"
        if "requires evaluation" in blob or "assess" in blob or "not yet confirmed" in blob:
            return "decision_required", "further_inspect"
        if template_risk == "high" and ("not yet confirmed" in blob or "requires evaluation" in blob):
            return "monitor", "further_inspect"
        return "decision_required", "further_inspect"

    if category == "inspection_risk":
        if "not yet confirmed" in blob or "requires evaluation" in blob:
            return "monitor", "further_inspect"
        if template_risk == "high":
            return "required_action", "further_inspect"
        return "monitor", "further_inspect"

    # ── Defaults ─────────────────────────────────────────────────────────
    if "requires evaluation" in blob or "assess" in blob:
        return "decision_required", "further_inspect"

    if buyer_impact == "high" and category in ("dated", "cosmetic", None):
        return "decision_required", "refresh"

    return "decision_required", "leave_as_is"


def _is_actionable_component(c: dict[str, Any]) -> bool:
    return bool(
        c.get("include_in_report")
        and c.get("confidence_tier") in ("confirmed", "observed")
        and not c.get("looks_fine")
        and (c.get("walkthrough_note") or c.get("photo_observations"))
    )


def _build_row_from_component(
    *,
    matrix_id: str,
    component_id: str,
    walkthrough_item_id: str | None,
    zone: str,
    component: str,
    evidence_entry: dict[str, Any],
    wt_row: dict[str, Any] | None,
    photo_records: list[dict[str, Any]],
) -> dict[str, Any]:
    note = evidence_entry.get("walkthrough_note")
    photo_obs = evidence_entry.get("photo_observations") or []
    photo_evidence = _build_photo_evidence(photo_obs, photo_records)
    if not photo_evidence and photo_obs:
        photo_evidence = [{"filename": None, "observation": t, "room_type": None} for t in photo_obs]

    category = evidence_entry.get("template_category") or (wt_row or {}).get("category")
    template_risk = evidence_entry.get("template_risk") or (wt_row or {}).get("inspection_risk")
    condition_label = (wt_row or {}).get("condition_label")

    buyer_impact = _derive_buyer_impact(wt_row or {
        "buyer_visibility": evidence_entry.get("buyer_visibility"),
        "zone": zone,
    })

    blob = _decision_blob(
        component,
        note,
        photo_obs,
        evidence_entry.get("confidence_tier"),
    )
    risk_blob = _text_blob(component, note, " ".join(photo_obs))
    inspection_risk = _escalate_inspection_risk(template_risk, risk_blob)
    marketability_risk = _derive_marketability_risk(
        category,
        (wt_row or {}).get("buyer_visibility"),
        risk_blob,
    )

    decision_status, recommended_action = _classify_decision(
        zone=zone,
        component=component,
        note=note,
        photos=photo_obs,
        category=category,
        template_risk=template_risk,
        buyer_impact=buyer_impact,
        condition_label=condition_label,
        blob=blob,
    )

    return {
        "matrix_id": matrix_id,
        "component_id": component_id,
        "walkthrough_item_id": walkthrough_item_id,
        "zone": zone,
        "component": component,
        "confidence_tier": evidence_entry.get("confidence_tier"),
        "evidence_sources": _build_evidence_sources(
            walkthrough_item_id,
            note,
            photo_evidence,
            evidence_entry.get("property_context") or [],
        ),
        "walkthrough_notes": note,
        "photo_evidence": photo_evidence,
        "current_state": _build_current_state(note, photo_obs, component),
        "buyer_impact": buyer_impact,
        "inspection_risk": inspection_risk,
        "marketability_risk": marketability_risk,
        "decision_status": decision_status,
        "recommended_action": recommended_action,
    }


def _build_photo_only_row(
    *,
    matrix_id: str,
    observation: str,
    photo_records: list[dict[str, Any]],
) -> dict[str, Any]:
    component_id = f"photo_only:{_photo_hash(observation)}"
    photo_evidence = _match_photos_to_observation(observation, photo_records)
    if not photo_evidence:
        photo_evidence = [{"filename": None, "observation": observation, "room_type": None}]

    label = observation[:80].strip()
    if len(observation) > 80:
        label += "…"

    blob = _text_blob(observation)
    category = "functional"
    if any(s in blob for s in _SAFETY_SIGNALS + _REPLACE_SIGNALS):
        category = "inspection_risk"

    buyer_impact = "medium"
    if any(s in blob for s in ("garage", "structural", "electrical", "deck")):
        buyer_impact = "high"

    inspection_risk = _escalate_inspection_risk("medium", blob)
    marketability_risk = _derive_marketability_risk(category, "medium", blob)

    decision_status, recommended_action = _classify_decision(
        zone="unmatched",
        component=label,
        note=None,
        photos=[observation],
        category=category,
        template_risk=inspection_risk,
        buyer_impact=buyer_impact,
        condition_label=None,
        blob=blob,
    )

    if "garage door" in blob and any(s in blob for s in _REPLACE_SIGNALS):
        decision_status, recommended_action = "required_action", "replace"
    elif any(s in blob for s in _SAFETY_SIGNALS):
        decision_status, recommended_action = "required_action", "repair"

    return {
        "matrix_id": matrix_id,
        "component_id": component_id,
        "walkthrough_item_id": None,
        "zone": "unmatched",
        "component": label,
        "confidence_tier": "observed",
        "evidence_sources": _build_evidence_sources(None, None, photo_evidence, []),
        "walkthrough_notes": None,
        "photo_evidence": photo_evidence,
        "current_state": observation[:400],
        "buyer_impact": buyer_impact,
        "inspection_risk": inspection_risk,
        "marketability_risk": marketability_risk,
        "decision_status": decision_status,
        "recommended_action": recommended_action,
    }


def _next_matrix_version(sb, property_id: str) -> int:
    try:
        result = (
            sb.table(MATRICES_TABLE)
            .select("version")
            .eq("property_id", property_id)
            .order("version", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows:
            return int(rows[0]["version"]) + 1
    except Exception:
        pass
    return 1


def _mark_prior_matrices_stale(sb, property_id: str, keep_matrix_id: str) -> int:
    try:
        result = (
            sb.table(MATRICES_TABLE)
            .update({"status": "stale"})
            .eq("property_id", property_id)
            .neq("id", keep_matrix_id)
            .neq("status", "stale")
            .execute()
        )
        return len(result.data or [])
    except Exception:
        return 0


def build_decision_matrix_dry_run(
    property_id: str = "130_kingfisher",
    sb=None,
) -> dict[str, Any]:
    """Build matrix in memory without persisting (no migration required)."""
    if sb is None:
        raise ValueError("Supabase client required for loading evidence")

    wt_rows = load_walkthrough_items(sb, property_id)
    photo_records = _load_photo_records(sb)
    analyses = [r["analysis"] for r in photo_records]
    summary = build_analysis_summary(analyses) if analyses else {}
    package = build_evidence_package(wt_rows, summary, default_property_facts())
    evidence_hash = _canonical_evidence_hash(package)

    wt_lookup = _walkthrough_lookup(wt_rows)
    matrix_id = "dry-run"

    walkthrough_components = [
        c for c in package.get("components") or []
        if _is_actionable_component(c)
    ]
    photo_only_findings = package.get("photo_only_findings") or []

    rows_to_insert: list[dict[str, Any]] = []

    for comp in walkthrough_components:
        zone = comp.get("zone") or ""
        component = comp.get("component") or ""
        wt = wt_lookup.get((_norm(zone), _norm(component)))
        wt_id = str(wt["id"]) if wt and wt.get("id") else None
        component_id = wt_id or f"wt:{_norm(zone)}:{_norm(component)}"

        rows_to_insert.append(_build_row_from_component(
            matrix_id=matrix_id,
            component_id=component_id,
            walkthrough_item_id=wt_id,
            zone=zone,
            component=component,
            evidence_entry=comp,
            wt_row=wt,
            photo_records=photo_records,
        ))

    for obs in photo_only_findings:
        rows_to_insert.append(_build_photo_only_row(
            matrix_id=matrix_id,
            observation=obs,
            photo_records=photo_records,
        ))

    status_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    for row in rows_to_insert:
        status_counts[row["decision_status"]] = status_counts.get(row["decision_status"], 0) + 1
        action_counts[row["recommended_action"]] = action_counts.get(row["recommended_action"], 0) + 1

    preview = [
        {
            "zone": r["zone"],
            "component": r["component"],
            "decision_status": r["decision_status"],
            "recommended_action": r["recommended_action"],
        }
        for r in sorted(
            rows_to_insert,
            key=lambda x: (
                {"required_action": 0, "decision_required": 1, "monitor": 2, "informational": 3}
                .get(x["decision_status"], 9),
                x["zone"],
                x["component"],
            ),
        )[:20]
    ]

    return {
        "matrix_id": matrix_id,
        "property_id": property_id,
        "version": 0,
        "evidence_hash": evidence_hash,
        "total_rows": len(rows_to_insert),
        "walkthrough_rows": len(walkthrough_components),
        "photo_only_rows": len(photo_only_findings),
        "prior_matrices_marked_stale": 0,
        "counts_by_decision_status": status_counts,
        "counts_by_recommended_action": action_counts,
        "preview_top_20": preview,
        "dry_run": True,
    }


def build_decision_matrix(
    property_id: str = "130_kingfisher",
    sb=None,
) -> dict[str, Any]:
    """
    Load evidence, build matrix rows, persist to Supabase.
    Returns summary dict with matrix_id, counts, and row previews.
    """
    if sb is None:
        raise ValueError("Supabase client required")

    wt_rows = load_walkthrough_items(sb, property_id)
    photo_records = _load_photo_records(sb)
    analyses = [r["analysis"] for r in photo_records]
    summary = build_analysis_summary(analyses) if analyses else {}
    package = build_evidence_package(wt_rows, summary, default_property_facts())
    evidence_hash = _canonical_evidence_hash(package)

    wt_lookup = _walkthrough_lookup(wt_rows)
    version = _next_matrix_version(sb, property_id)

    walkthrough_components = [
        c for c in package.get("components") or []
        if _is_actionable_component(c)
    ]
    photo_only_findings = package.get("photo_only_findings") or []

    header = {
        "property_id": property_id,
        "version": version,
        "status": "finalized",
        "evidence_snapshot": package,
        "evidence_hash": evidence_hash,
        "actionable_count": len(walkthrough_components) + len(photo_only_findings),
        "walkthrough_count": len(walkthrough_components),
        "photo_only_count": len(photo_only_findings),
    }

    insert_result = sb.table(MATRICES_TABLE).insert(header).execute()
    matrix_row = (insert_result.data or [None])[0]
    if not matrix_row:
        raise RuntimeError("Failed to insert decision_matrices header")
    matrix_id = matrix_row["id"]

    rows_to_insert: list[dict[str, Any]] = []

    for comp in walkthrough_components:
        zone = comp.get("zone") or ""
        component = comp.get("component") or ""
        wt = wt_lookup.get((_norm(zone), _norm(component)))
        wt_id = str(wt["id"]) if wt and wt.get("id") else None
        component_id = wt_id or f"wt:{_norm(zone)}:{_norm(component)}"

        rows_to_insert.append(_build_row_from_component(
            matrix_id=matrix_id,
            component_id=component_id,
            walkthrough_item_id=wt_id,
            zone=zone,
            component=component,
            evidence_entry=comp,
            wt_row=wt,
            photo_records=photo_records,
        ))

    for obs in photo_only_findings:
        rows_to_insert.append(_build_photo_only_row(
            matrix_id=matrix_id,
            observation=obs,
            photo_records=photo_records,
        ))

    if rows_to_insert:
        sb.table(ROWS_TABLE).insert(rows_to_insert).execute()

    stale_count = _mark_prior_matrices_stale(sb, property_id, matrix_id)

    state_payload = {
        "property_id": property_id,
        "current_matrix_id": matrix_id,
        "current_evidence_hash": evidence_hash,
        "updated_at": "now()",
    }
    sb.table(STATE_TABLE).upsert(state_payload, on_conflict="property_id").execute()

    status_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    for row in rows_to_insert:
        status_counts[row["decision_status"]] = status_counts.get(row["decision_status"], 0) + 1
        action_counts[row["recommended_action"]] = action_counts.get(row["recommended_action"], 0) + 1

    preview = [
        {
            "zone": r["zone"],
            "component": r["component"],
            "decision_status": r["decision_status"],
            "recommended_action": r["recommended_action"],
        }
        for r in sorted(
            rows_to_insert,
            key=lambda x: (
                {"required_action": 0, "decision_required": 1, "monitor": 2, "informational": 3}
                .get(x["decision_status"], 9),
                x["zone"],
                x["component"],
            ),
        )[:20]
    ]

    return {
        "matrix_id": matrix_id,
        "property_id": property_id,
        "version": version,
        "evidence_hash": evidence_hash,
        "total_rows": len(rows_to_insert),
        "walkthrough_rows": len(walkthrough_components),
        "photo_only_rows": len(photo_only_findings),
        "prior_matrices_marked_stale": stale_count,
        "counts_by_decision_status": status_counts,
        "counts_by_recommended_action": action_counts,
        "preview_top_20": preview,
    }


def load_current_matrix(sb, property_id: str) -> dict[str, Any] | None:
    """Return current matrix header or None."""
    try:
        state = (
            sb.table(STATE_TABLE)
            .select("current_matrix_id")
            .eq("property_id", property_id)
            .maybe_single()
            .execute()
        )
        matrix_id = (state.data or {}).get("current_matrix_id") if state else None
        if not matrix_id:
            return None
        result = (
            sb.table(MATRICES_TABLE)
            .select("*")
            .eq("id", matrix_id)
            .maybe_single()
            .execute()
        )
        return result.data if result else None
    except Exception:
        return None


def load_matrix_rows(sb, matrix_id: str) -> list[dict[str, Any]]:
    try:
        result = (
            sb.table(ROWS_TABLE)
            .select("*")
            .eq("matrix_id", matrix_id)
            .order("zone")
            .order("component")
            .execute()
        )
        return result.data or []
    except Exception:
        return []
