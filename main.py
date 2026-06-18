"""
main.py
-------
FastAPI backend for the house analysis web app.

Endpoints
---------
GET  /                       Serve static/index.html (or placeholder)
GET  /auth/login             Return Google OAuth URL
GET  /auth/callback?code=    Exchange code, redirect to /
GET  /auth/status            Check whether a valid token exists
GET  /auth/logout            Clear token from Supabase and memory
GET  /photos/albums          List all albums owned by the authenticated user
GET  /photos/list            List photos in a Google Photos album
GET  /photos/thumbnail       Proxy a thumbnail from Google Photos
POST /analyze                Analyze a single photo with Gemini Vision
POST /analyze/bulk           Analyze a batch of photos sequentially
GET  /analyze/results        Return all cached analysis results
POST /report                 Generate ROI report (body: {detail_level, buyer_profile})
POST /report/from-tier       Generate ROI report from listing-readiness tier
GET  /report                 Return ROI report by ?id= (default standard_general)
GET  /report/status          Cache status + prompt_version for every report slot
POST /report/invalidate      Delete cached reports (?profile=all or specific profile)
POST /report/regenerate-all  Regenerate all 3 levels for one profile (?profile=general)
GET  /report/export/csv      Download upgrades + repairs as CSV
GET  /upgrade-detail         Deep how-to detail for one upgrade (cached in Supabase)
GET  /repair-detail          Deep how-to detail for one repair (cached in Supabase)
GET  /dated-features         Aggregated dated_features across all photo analyses
GET  /inspection-flags       Top 20 inspection flags across all photo analyses
GET  /inventory              Materials shopping list with room-by-room breakdown
POST /inventory/override     Save user-edited room counts to Supabase
GET  /inventory/override     Return saved room-level overrides
GET  /decision-matrix          Current decision matrix header
GET  /decision-matrix/rows     Rows for current decision matrix (with options)
GET  /decision-matrix/health   Matrix completeness metrics
GET  /decision-matrix/scenarios/{scenario}  Scenario selection preview
GET  /decision-matrix/tiers/{tier}  Listing-readiness tier plan (cumulative)
PATCH /decision-matrix/rows/{row_id}  Seller override selected option
POST /decision-matrix/rebuild  Rebuild matrix from evidence package
GET  /walkthrough-items      List walkthrough checklist rows
POST /walkthrough-items      Create a walkthrough row
PATCH /walkthrough-items/{id} Update a walkthrough row
DELETE /walkthrough-items/{id} Delete a walkthrough row
POST /walkthrough-items/seed   Seed template rows (idempotent)
POST /walkthrough-items/recalculate  Recalculate AI/system fields for all rows

Run:
    uvicorn main:app --port 8000 --reload
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel

from analyzer import analyze_image
from attom import get_last_sale, get_property_summary
from photos import (
    exchange_code,
    get_auth_url,
    get_credentials,
    get_photo_bytes,
    list_album_photos,
    _supabase_client,
    SUPABASE_TOKEN_ID,
)
import photos as _photos_module
from roi import (
    generate_roi_report,
    levels_up_to,
    get_item_detail,
    format_item_detail_for_display,
    PROMPT_VERSION,
    BUYER_PROFILES,
    DETAIL_LEVEL_ORDER,
)
from run_roi import build_analysis_summary
from datetime import datetime, timezone

from decision_matrix import (
    apply_row_override,
    build_decision_matrix,
    compute_matrix_health,
    load_current_matrix,
    load_matrix_rows,
    load_matrix_rows_with_options,
)
from matrix_tiers import compute_tier_counts, normalize_tier
from tier_selector import select_for_tier, select_tier_from_rows
from evidence import build_evidence_package, default_property_facts, format_evidence_prompt
from report_composer import (
    TIER_TO_DETAIL_LEVEL,
    compose_for_scenario,
    compose_report_from_tier,
    format_matrix_evidence_block,
)
from walkthrough_impact import build_walkthrough_impact
from walkthrough import (
    PROPERTY_ID as WALKTHROUGH_PROPERTY_ID,
    LooksFineError,
    apply_calculated_persist_fields,
    apply_looks_fine,
    enrich_walkthrough_item,
    enrich_walkthrough_items,
    is_assessment_prompt_text,
    load_walkthrough_items,
    recalculate_all_items,
    sanitize_walkthrough_prompt_notes,
    seed_walkthrough_items,
    toggle_looks_fine,
    zone_looks_fine_remaining,
)

# ─── Module-level state ───────────────────────────────────────────────────────

# Keyed by photo_id → analysis dict from analyzer.analyze_image()
analysis_cache: dict[str, dict] = {}

# Write-through memory cache for the latest ROI report (also persisted to Supabase)
roi_cache: Optional[dict] = None

# Supabase table / row for the ROI report
ROI_TABLE = "roi_report"
ROI_ID    = "current"

REPORT_FROM_MATRIX = os.environ.get("REPORT_FROM_MATRIX", "true").lower() in ("1", "true", "yes")

# ─── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="House Analysis API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_INDEX = Path("static/index.html")

# ── Matrix cache ──────────────────────────────────────────────────────────────
# Stores the last loaded matrix payload so page reloads don't hit Supabase.
# Invalidated whenever any row is mutated (PATCH / rebuild).
_matrix_cache: dict = {}   # {"data": ..., "ts": float}
_MATRIX_CACHE_TTL = 300    # 5 minutes safety expiry

def _matrix_cache_get() -> dict | None:
    entry = _matrix_cache.get("data")
    if entry and time.time() - _matrix_cache.get("ts", 0) < _MATRIX_CACHE_TTL:
        return entry
    return None

def _matrix_cache_set(data: dict) -> None:
    _matrix_cache["data"] = data
    _matrix_cache["ts"] = time.time()

def _matrix_cache_clear() -> None:
    _matrix_cache.clear()


# ─── Supabase helper ──────────────────────────────────────────────────────────

def _load_photo_analyses(sb=None) -> list[dict]:
    client = sb or _sb()
    if not client:
        return list(analysis_cache.values())
    try:
        rows = client.table("photo_analyses").select("analysis").execute()
        return [r["analysis"] for r in (rows.data or []) if r.get("analysis")]
    except Exception:
        return list(analysis_cache.values())


def _evidence_context(sb=None, scenario: str = "budget_15k") -> tuple[dict, str, list]:
    """Build evidence package, formatted prompt, and raw walkthrough rows."""
    client = sb or _sb()
    rows = load_walkthrough_items(client, WALKTHROUGH_PROPERTY_ID) if client else []
    analyses = _load_photo_analyses(client)
    summary = build_analysis_summary(analyses) if analyses else {}
    package = build_evidence_package(rows, summary, default_property_facts())
    return package, format_evidence_prompt(package, scenario), rows


def _evidence_prompt_block(sb=None, scenario: str = "budget_15k") -> str:
    """Unified evidence package for ROI generation."""
    _, prompt, _ = _evidence_context(sb, scenario)
    return prompt


def _attach_walkthrough_impact(
    report: dict,
    package: dict,
    scenario: str,
    walkthrough_rows: list,
) -> dict:
    """Add walkthrough_impact trace and generated_at timestamp to report."""
    report["walkthrough_impact"] = build_walkthrough_impact(
        package,
        scenario,
        report.get("upgrades") or [],
        report.get("repairs") or [],
        walkthrough_rows,
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    return report


def _matrix_rows_for_property(sb, property_id: str) -> tuple[dict, list[dict]] | None:
    matrix = load_current_matrix(sb, property_id)
    if not matrix:
        return None
    rows = load_matrix_rows_with_options(sb, matrix["id"])
    return matrix, rows


def _generate_report_for_level(
    *,
    level: str,
    buyer_profile: str,
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    prior: dict | None,
    sb,
    matrix_rows: list[dict] | None,
    use_matrix: bool,
) -> dict:
    package, walkthrough_block, wt_rows = _evidence_context(sb, level)
    matrix_block = ""
    matrix_line_items = None

    if use_matrix and matrix_rows:
        _, matrix_line_items = compose_for_scenario(matrix_rows, level, buyer_profile)
        matrix_block = format_matrix_evidence_block(matrix_rows)

    report = generate_roi_report(
        summary,
        property_summary,
        last_sale,
        detail_level=level,
        buyer_profile=buyer_profile,
        prior_report=prior,
        walkthrough_block=walkthrough_block,
        matrix_block=matrix_block,
        matrix_line_items=matrix_line_items,
    )
    if report.get("error"):
        return report
    return _attach_walkthrough_impact(report, package, level, wt_rows)


def _generate_report_from_tier(
    *,
    tier: str,
    buyer_profile: str,
    property_id: str,
    summary: dict,
    property_summary: dict,
    last_sale: dict,
    sb,
) -> dict:
    """Generate ROI report from cumulative listing-readiness tier selection."""
    tier = normalize_tier(tier.strip().lower()) or ""
    if tier not in TIER_TO_DETAIL_LEVEL:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier: {tier!r}. Choose from: {sorted(TIER_TO_DETAIL_LEVEL)}",
        )

    matrix_ctx = _matrix_rows_for_property(sb, property_id) if sb else None
    if not matrix_ctx:
        raise HTTPException(
            status_code=422,
            detail=f"No decision matrix for property {property_id!r} — rebuild matrix first",
        )
    matrix, rows = matrix_ctx
    tier_selection = select_tier_from_rows(
        rows,
        tier,
        matrix_id=matrix["id"],
        property_id=property_id,
    )

    detail_level = TIER_TO_DETAIL_LEVEL[tier]
    package, walkthrough_block, wt_rows = _evidence_context(sb, detail_level)

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
        raise HTTPException(status_code=500, detail=report["error"])

    return _attach_walkthrough_impact(report, package, detail_level, wt_rows)


def _walkthrough_prompt_block(sb=None) -> str:
    """Legacy alias — evidence prompt at default scenario."""
    return _evidence_prompt_block(sb, "budget_15k")


def _sb():
    """Return a Supabase client if credentials are configured, else None."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


# ─── Pydantic models ──────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    base_url: str
    photo_id: str


class BulkPhotoItem(BaseModel):
    base_url: str
    photo_id: str


class BulkAnalyzeRequest(BaseModel):
    photos: list[BulkPhotoItem]


class ReportRequest(BaseModel):
    detail_level: str = "budget_15k"
    buyer_profile: str = "general"


class TierReportRequest(BaseModel):
    tier: str
    buyer_profile: str = "general"
    property_id: str = WALKTHROUGH_PROPERTY_ID


# ─── Auth endpoints ───────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def serve_index():
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX, media_type="text/html; charset=utf-8")
    return PlainTextResponse("UI not built yet")


@app.get("/media/{filename:path}")
def serve_media(filename: str):
    """Serve files from the local media/ folder (floor plan image, etc.)."""
    path = Path("media") / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@app.get("/auth/login")
def auth_login():
    return {"auth_url": get_auth_url()}


@app.get("/auth/callback")
def auth_callback(code: str = Query(...)):
    try:
        exchange_code(code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}")
    return RedirectResponse(url="/", status_code=302)


@app.get("/auth/status")
def auth_status():
    creds = get_credentials()
    if creds and not creds.expired:
        return {"authenticated": True}
    return {"authenticated": False}


@app.get("/auth/logout")
def auth_logout():
    _photos_module._token_cache = None

    client = _supabase_client()
    if client:
        try:
            client.table("oauth_tokens").delete().eq("id", SUPABASE_TOKEN_ID).execute()
        except Exception:
            pass

    return {"status": "logged out"}


# ─── Photos endpoints ─────────────────────────────────────────────────────────

@app.get("/photos/albums")
def photos_albums():
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")

    albums: list[dict] = []
    page_token: Optional[str] = None
    headers = {"Authorization": f"Bearer {creds.token}"}

    while True:
        params: dict = {"pageSize": 50}
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(
                "https://photoslibrary.googleapis.com/v1/albums",
                headers=headers,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Google Photos error: {exc}")

        body = resp.json()
        for a in body.get("albums", []):
            albums.append({
                "id":    a.get("id"),
                "title": a.get("title"),
                "count": int(a.get("mediaItemsCount", 0)),
            })

        page_token = body.get("nextPageToken")
        if not page_token:
            break

    return {"albums": albums}


@app.get("/photos/list")
def photos_list(album_id: str = Query(...)):
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    photos = list_album_photos(album_id, creds)
    return {"photos": photos, "count": len(photos)}


@app.get("/photos/thumbnail")
def photos_thumbnail(
    base_url: str = Query(...),
    width: int = Query(default=400, ge=1),
):
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = get_photo_bytes(base_url, creds, width=width)
    if data is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return Response(content=data, media_type="image/jpeg")


# ─── Analysis endpoints ───────────────────────────────────────────────────────

def _download_and_analyze(base_url: str, creds) -> dict:
    """Download full-res photo, write to a temp file, run analyze_image, clean up."""
    photo_bytes = get_photo_bytes(base_url, creds, width=0)
    if photo_bytes is None:
        raise HTTPException(status_code=404, detail="Photo not found or download failed")

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(photo_bytes)
        return analyze_image(tmp_path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


def _save_photo_analysis(photo_id: str, base_url: str, result: dict, sb=None) -> None:
    """Persist an analysis result to memory and Supabase when configured."""
    analysis_cache[photo_id] = result

    client = sb or _sb()
    if not client:
        return
    try:
        client.table("photo_analyses").upsert({
            "id":       photo_id,
            "filename": photo_id,
            "base_url": base_url,
            "analysis": result,
        }).execute()
    except Exception as exc:
        print(f"WARNING: could not save analysis {photo_id} to Supabase: {exc}")


@app.post("/analyze")
def analyze_single(body: AnalyzeRequest):
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    result = _download_and_analyze(body.base_url, creds)
    _save_photo_analysis(body.photo_id, body.base_url, result)
    return result


@app.post("/analyze/bulk")
def analyze_bulk(body: BulkAnalyzeRequest):
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")

    total = len(body.photos)
    completed = 0
    sb = _sb()

    for item in body.photos:
        if item.photo_id in analysis_cache:
            continue

        if sb:
            try:
                existing = (
                    sb.table("photo_analyses")
                    .select("analysis")
                    .eq("id", item.photo_id)
                    .maybe_single()
                    .execute()
                )
                if existing and existing.data and existing.data.get("analysis"):
                    analysis_cache[item.photo_id] = existing.data["analysis"]
                    continue
            except Exception:
                pass

        photo_bytes = get_photo_bytes(item.base_url, creds, width=0)
        if photo_bytes is None:
            continue

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(photo_bytes)
            result = analyze_image(tmp_path)
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()

        _save_photo_analysis(item.photo_id, item.base_url, result, sb=sb)

        completed += 1

    return {"completed": completed, "total": total}


@app.get("/analyze/results")
def analyze_results():
    if analysis_cache:
        return analysis_cache
    sb = _sb()
    if not sb:
        return analysis_cache
    try:
        rows = sb.table("photo_analyses").select("id, analysis").execute()
        for row in rows.data or []:
            if row.get("id") and row.get("analysis"):
                analysis_cache[row["id"]] = row["analysis"]
    except Exception:
        pass
    return analysis_cache


# ─── Report endpoints ─────────────────────────────────────────────────────────

@app.post("/report")
def report_generate(body: ReportRequest):
    """Generate and persist additive report chain for the given buyer_profile."""
    global roi_cache

    detail_level  = body.detail_level
    buyer_profile = body.buyer_profile
    report_id     = f"{detail_level}_{buyer_profile}"

    analyses = list(analysis_cache.values())
    if not analyses:
        # Fall back to Supabase analyses when memory cache is cold
        sb = _sb()
        if not sb:
            raise HTTPException(status_code=503, detail="Supabase not configured — check SUPABASE_URL and SUPABASE_SERVICE_KEY env vars")
        try:
            rows = sb.table("photo_analyses").select("analysis").execute()
            analyses = [r["analysis"] for r in (rows.data or []) if r.get("analysis")]
            print(f"Loaded {len(analyses)} analyses from Supabase")
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Supabase query failed: {exc}")
    if not analyses:
        raise HTTPException(status_code=422, detail="No analyses found in Supabase photo_analyses table — run run_analysis.py first")

    summary = build_analysis_summary(analyses)
    property_summary = get_property_summary()
    last_sale = get_last_sale()

    # Generate executive → standard → deep_dive so each tab includes the prior level.
    chain = levels_up_to(detail_level)
    if not chain:
        raise HTTPException(status_code=400, detail=f"Invalid detail_level: {detail_level!r}")

    prior: dict | None = None
    report: dict | None = None
    sb = _sb()
    matrix_ctx = _matrix_rows_for_property(sb, WALKTHROUGH_PROPERTY_ID) if sb else None
    matrix_rows = matrix_ctx[1] if matrix_ctx else None
    use_matrix = REPORT_FROM_MATRIX and bool(matrix_rows)

    for level in chain:
        level_id = f"{level}_{buyer_profile}"
        report = _generate_report_for_level(
            level=level,
            buyer_profile=buyer_profile,
            summary=summary,
            property_summary=property_summary,
            last_sale=last_sale,
            prior=prior,
            sb=sb,
            matrix_rows=matrix_rows,
            use_matrix=use_matrix,
        )
        if report.get("error"):
            raise HTTPException(status_code=500, detail=report["error"])

        if sb:
            try:
                sb.table(ROI_TABLE).upsert({"id": level_id, "report": report}).execute()
            except Exception as exc:
                print(f"WARNING: could not save report {level_id} to Supabase: {exc}")

        prior = report

    assert report is not None
    roi_cache = report
    return report


@app.post("/report/from-tier")
def report_from_tier(body: TierReportRequest):
    """Generate and persist ROI report from listing-readiness tier selection."""
    global roi_cache

    buyer_profile = body.buyer_profile
    report_id = f"tier_{body.tier.strip().lower()}_{buyer_profile}"

    analyses = list(analysis_cache.values())
    sb = _sb()
    if not analyses:
        if not sb:
            raise HTTPException(
                status_code=503,
                detail="Supabase not configured — check SUPABASE_URL and SUPABASE_SERVICE_KEY env vars",
            )
        try:
            rows = sb.table("photo_analyses").select("analysis").execute()
            analyses = [r["analysis"] for r in (rows.data or []) if r.get("analysis")]
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Supabase query failed: {exc}")
    if not analyses:
        raise HTTPException(
            status_code=422,
            detail="No analyses found — run run_analysis.py first",
        )

    if not sb:
        raise HTTPException(status_code=503, detail="Supabase required for tier reports")

    summary = build_analysis_summary(analyses)
    property_summary = get_property_summary()
    last_sale = get_last_sale()

    report = _generate_report_from_tier(
        tier=body.tier,
        buyer_profile=buyer_profile,
        property_id=body.property_id,
        summary=summary,
        property_summary=property_summary,
        last_sale=last_sale,
        sb=sb,
    )

    try:
        sb.table(ROI_TABLE).upsert({"id": report_id, "report": report}).execute()
    except Exception as exc:
        print(f"WARNING: could not save tier report {report_id} to Supabase: {exc}")

    roi_cache = report
    return report


@app.get("/report")
def report_get(id: str = Query(default="standard_general")):
    """Return a saved report by id (e.g. standard_general, deep_dive_first_time_buyer)."""
    sb = _sb()
    if sb:
        try:
            row = sb.table(ROI_TABLE).select("report").eq("id", id).maybe_single().execute()
            if row and row.data:
                return row.data["report"]
        except Exception:
            pass

    # Fall back to in-memory cache (only valid for the last-generated report)
    if roi_cache is not None and id == "standard_general":
        return roi_cache

    raise HTTPException(
        status_code=404,
        detail=f"Report not generated for '{id}' yet. POST /report with detail_level and buyer_profile."
    )


@app.get("/report/status")
def report_status():
    """
    Return cache status for every report slot: whether it exists and whether it
    was generated with the current cost anchors (prompt_version).
    """
    sb = _sb()
    slots = [
        f"{level}_{profile}"
        for level in DETAIL_LEVEL_ORDER
        for profile in sorted(BUYER_PROFILES)
    ]
    result = {}
    if sb:
        try:
            rows = sb.table(ROI_TABLE).select("id, report->prompt_version").execute()
            cached = {r["id"]: r.get("prompt_version") for r in (rows.data or [])}
        except Exception:
            cached = {}
    else:
        cached = {}

    for slot in slots:
        pv = cached.get(slot)
        result[slot] = {
            "cached":       slot in cached,
            "current":      pv == PROMPT_VERSION,
            "prompt_version": pv,
        }
    result["current_prompt_version"] = PROMPT_VERSION
    return result


@app.post("/report/invalidate")
def report_invalidate(profile: str = Query(default="all")):
    """
    Delete cached roi_reports from Supabase so they are regenerated fresh on
    the next POST /report call. Pass ?profile=general to wipe one profile,
    or leave blank to wipe all profiles across all detail levels.
    """
    global roi_cache
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    deleted = []
    errors = []
    profiles = sorted(BUYER_PROFILES) if profile == "all" else [profile]

    for p in profiles:
        for level in DETAIL_LEVEL_ORDER:
            slot = f"{level}_{p}"
            try:
                sb.table(ROI_TABLE).delete().eq("id", slot).execute()
                deleted.append(slot)
            except Exception as exc:
                errors.append(f"{slot}: {exc}")

    roi_cache = None
    return {"deleted": deleted, "errors": errors}


@app.post("/report/regenerate-all")
def report_regenerate_all(profile: str = Query(default="general")):
    """
    Regenerate executive → standard → deep_dive for the given buyer profile
    (default: general) using the current cost anchors, then persist to Supabase.
    This is the one-shot command to refresh stale cached reports after a prompt change.
    """
    global roi_cache

    sb = _sb()
    analyses = list(analysis_cache.values())
    if not analyses and sb:
        try:
            rows = sb.table("photo_analyses").select("analysis").execute()
            analyses = [r["analysis"] for r in (rows.data or []) if r.get("analysis")]
        except Exception:
            pass
    if not analyses:
        raise HTTPException(status_code=422, detail="No photo analyses available")

    summary = build_analysis_summary(analyses)
    property_summary = get_property_summary()
    last_sale = get_last_sale()

    prior: dict | None = None
    reports: dict[str, dict] = {}
    matrix_ctx = _matrix_rows_for_property(sb, WALKTHROUGH_PROPERTY_ID) if sb else None
    matrix_rows = matrix_ctx[1] if matrix_ctx else None
    use_matrix = REPORT_FROM_MATRIX and bool(matrix_rows)

    for level in DETAIL_LEVEL_ORDER:
        print(f"\n=== Regenerating [{level}_{profile}] ===")
        report = _generate_report_for_level(
            level=level,
            buyer_profile=profile,
            summary=summary,
            property_summary=property_summary,
            last_sale=last_sale,
            prior=prior,
            sb=sb,
            matrix_rows=matrix_rows,
            use_matrix=use_matrix,
        )
        if report.get("error"):
            raise HTTPException(status_code=500, detail=f"[{level}] {report['error']}")

        level_id = f"{level}_{profile}"
        if sb:
            try:
                sb.table(ROI_TABLE).upsert({"id": level_id, "report": report}).execute()
            except Exception as exc:
                print(f"WARNING: could not save {level_id} to Supabase: {exc}")

        reports[level_id] = report
        prior = report

    roi_cache = reports.get(f"budget_15k_{profile}")
    return {
        "regenerated": list(reports.keys()),
        "prompt_version": PROMPT_VERSION,
    }


@app.get("/report/export/csv")
def report_export_csv():
    # Use memory cache if available, else hit Supabase
    report = roi_cache
    if report is None:
        sb = _sb()
        if sb:
            try:
                row = sb.table(ROI_TABLE).select("report").eq("id", ROI_ID).maybe_single().execute()
                if row and row.data:
                    report = row.data["report"]
            except Exception:
                pass

    if report is None:
        raise HTTPException(status_code=404, detail="No report generated yet")

    upgrades: list[dict] = report.get("upgrades") or []
    repairs:  list[dict] = report.get("repairs")  or []

    upgrade_fields = list(dict.fromkeys(k for u in upgrades for k in u))
    repair_fields  = list(dict.fromkeys(k for r in repairs  for k in r))
    all_fields     = ["type"] + list(dict.fromkeys(upgrade_fields + repair_fields))

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=all_fields, extrasaction="ignore")
    writer.writeheader()
    for u in upgrades:
        writer.writerow({"type": "upgrade", **u})
    for r in repairs:
        writer.writerow({"type": "repair", **r})
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=roi_report.csv"},
    )


# ─── Analysis aggregation endpoints ──────────────────────────────────────────

def _load_all_analyses() -> list[dict]:
    """Load all photo analyses from Supabase."""
    sb = _sb()
    if not sb:
        return []
    try:
        rows = sb.table("photo_analyses").select("analysis").execute()
        return [r["analysis"] for r in (rows.data or []) if r.get("analysis")]
    except Exception:
        return []


@app.get("/dated-features")
def dated_features_get():
    """Aggregate dated_features across all photo analyses, sorted by frequency."""
    from collections import Counter
    from run_roi import _norm_key

    analyses = _load_all_analyses()
    if not analyses:
        raise HTTPException(status_code=404, detail="No photo analyses found in Supabase")

    counts:  Counter = Counter()
    display: dict[str, str] = {}

    for a in analyses:
        for text in (a.get("dated_features") or []):
            text = text.strip()
            if not text:
                continue
            key = _norm_key(text)
            if not key:
                continue
            counts[key] += 1
            if key not in display or len(text) > len(display[key]):
                display[key] = text

    features = [
        {"name": display[k], "count": c}
        for k, c in counts.most_common()
    ]
    return {"features": features}


@app.get("/inspection-flags")
def inspection_flags_get():
    """Aggregate inspection_flags across all photo analyses, return top 20 by frequency."""
    from collections import Counter
    from run_roi import _norm_key

    analyses = _load_all_analyses()
    if not analyses:
        raise HTTPException(status_code=404, detail="No photo analyses found in Supabase")

    counts:  Counter = Counter()
    display: dict[str, str] = {}
    severity: dict[str, str] = {}

    for a in analyses:
        for flag in (a.get("inspection_flags") or []):
            if isinstance(flag, dict):
                text  = (flag.get("description") or flag.get("text") or str(flag)).strip()
                sev   = flag.get("severity") or flag.get("priority") or "medium"
            else:
                text = str(flag).strip()
                sev  = "medium"
            if not text:
                continue
            key = _norm_key(text)
            if not key:
                continue
            counts[key] += 1
            if key not in display or len(text) > len(display[key]):
                display[key] = text
                severity[key] = sev

    flags = [
        {"description": display[k], "count": c, "severity": severity.get(k, "medium")}
        for k, c in counts.most_common(20)
    ]
    return {"flags": flags}


@app.get("/inventory")
def inventory_get():
    """
    Aggregate materials inventory across all photo analyses and gap-fill
    unobserved rooms using known River Ridge property layout data.
    Returns summary totals (for shopping list) and per-room breakdown.
    """
    from run_roi import aggregate_inventory
    from attom import get_property_summary

    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        rows = sb.table("photo_analyses") \
                 .select("analysis, inventory") \
                 .not_.is_("inventory", "null") \
                 .execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase query failed: {exc}")

    if not rows.data:
        raise HTTPException(
            status_code=404,
            detail="No inventory data found. Run the inventory analysis pass first via POST /analyze/bulk or python run_inventory.py",
        )

    combined: list[dict] = []
    for row in rows.data:
        inv = row.get("inventory") or {}
        analysis = row.get("analysis") or {}
        # Use room_type from inventory if present, fall back to analysis
        if not inv.get("room_type") and analysis.get("room_type"):
            inv = {**inv, "room_type": analysis["room_type"]}
        combined.append(inv)

    try:
        property_summary = get_property_summary()
    except Exception:
        property_summary = {}

    result = aggregate_inventory(combined, property_summary=property_summary)

    # Apply saved user overrides on top of AI-aggregated + gap-filled values
    try:
        ov_result = sb.table(INVENTORY_OVERRIDE_TABLE) \
                      .select("overrides") \
                      .eq("id", INVENTORY_OVERRIDE_ID) \
                      .execute()
        overrides = {}
        if ov_result.data:
            overrides = ov_result.data[0].get("overrides") or {}
    except Exception:
        overrides = {}

    if overrides:
        _FIELD_MAP = {
            "doors": "doors", "outlets": "outlets", "switch_plates": "switch_plates",
            "light_fixtures": "light_fixtures", "ceiling_fans": "ceiling_fans",
            "windows": "windows", "cabinet_doors": "cabinet_doors", "sqft": "sqft",
        }
        for room_row in result["rooms"]:
            room_key = room_row["room"]
            if room_key in overrides:
                for field, val in overrides[room_key].items():
                    if field in _FIELD_MAP and isinstance(val, (int, float)):
                        room_row[field] = int(val)
                        room_row["source"] = "edited"  # mark as user-edited

        # Recalculate summary totals from the (now-overridden) room rows
        rooms = result["rooms"]
        total_doors          = sum(r.get("doors", 0)          for r in rooms)
        total_outlets        = sum(r.get("outlets", 0)        for r in rooms)
        total_switch_plates  = sum(r.get("switch_plates", 0)  for r in rooms)
        total_light_fixtures = sum(r.get("light_fixtures", 0) for r in rooms)
        total_ceiling_fans   = sum(r.get("ceiling_fans", 0)   for r in rooms)
        total_windows        = sum(r.get("windows", 0)        for r in rooms)
        total_cabinet_doors  = sum(r.get("cabinet_doors", 0)  for r in rooms)
        total_sqft           = sum(r.get("sqft") or 0         for r in rooms)
        result["summary"].update({
            "total_doors":            total_doors,
            "total_hinges":           total_doors * 3,
            "total_outlets":          total_outlets,
            "total_switch_plates":    total_switch_plates,
            "total_light_fixtures":   total_light_fixtures,
            "total_ceiling_fans":     total_ceiling_fans,
            "total_windows":          total_windows,
            "total_cabinet_doors":    total_cabinet_doors,
            "total_cabinet_hardware": total_cabinet_doors,
            "estimated_total_sqft":   total_sqft,
            "estimated_paint_gallons": round(total_sqft / 175) if total_sqft else 0,
        })

    result["overrides"] = overrides  # send to frontend so it knows which fields are edited
    return result


# ─── Inventory override endpoints ────────────────────────────────────────────
# Supabase table (run once):
#   CREATE TABLE inventory_overrides (
#       id         TEXT PRIMARY KEY,   -- always "130_kingfisher"
#       overrides  JSONB NOT NULL,     -- {room_name: {field: value, ...}, ...}
#       updated_at TIMESTAMPTZ DEFAULT now()
#   );

INVENTORY_OVERRIDE_TABLE = "inventory_overrides"
INVENTORY_OVERRIDE_ID    = "130_kingfisher"


class InventoryOverrideRequest(BaseModel):
    overrides: dict  # {room_name: {field: value, ...}, ...}


@app.post("/inventory/override")
def inventory_override_save(body: InventoryOverrideRequest):
    """Save user-edited room counts to Supabase. Merges with any existing overrides."""
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        # Load existing overrides so we merge rather than replace
        existing = {}
        result = sb.table(INVENTORY_OVERRIDE_TABLE) \
                   .select("overrides") \
                   .eq("id", INVENTORY_OVERRIDE_ID) \
                   .execute()
        if result.data:
            existing = result.data[0].get("overrides") or {}

        merged = {**existing, **body.overrides}
        sb.table(INVENTORY_OVERRIDE_TABLE).upsert({
            "id":        INVENTORY_OVERRIDE_ID,
            "overrides": merged,
        }).execute()
        return {"saved": True, "rooms_overridden": len(merged)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase error: {exc}")


@app.get("/inventory/override")
def inventory_override_get():
    """Return the saved room-level overrides."""
    sb = _sb()
    if not sb:
        return {"overrides": {}}
    try:
        result = sb.table(INVENTORY_OVERRIDE_TABLE) \
                   .select("overrides") \
                   .eq("id", INVENTORY_OVERRIDE_ID) \
                   .execute()
        if result.data:
            return {"overrides": result.data[0].get("overrides") or {}}
        return {"overrides": {}}
    except Exception:
        return {"overrides": {}}


# ─── Item detail endpoints ────────────────────────────────────────────────────

# Supabase table for caching on-demand deep detail per item
DETAIL_TABLE = "upgrade_details"


def _get_or_generate_detail(
    name: str,
    item_type: str,
    description: str = "",
    issues: str = "",
) -> dict:
    """
    Return cached detail from Supabase if available, otherwise call Gemini
    via roi.get_item_detail() and persist the result.
    item_type: "upgrade" | "repair"
    description / issues: grounding context forwarded from the frontend.
    """
    if not name or not name.strip():
        raise HTTPException(status_code=422, detail="name parameter is required")

    row_id = name.strip()

    # Check Supabase cache first (keyed by name; context params bypass cache so
    # grounded results always reflect what was actually observed)
    sb = _sb()
    if sb and not description and not issues:
        try:
            row = (
                sb.table(DETAIL_TABLE)
                .select("detail")
                .eq("id", row_id)
                .eq("item_type", item_type)
                .maybe_single()
                .execute()
            )
            if row and row.data:
                return format_item_detail_for_display(row.data["detail"])
        except Exception:
            pass

    # Cache miss — call Gemini with observed context
    result = get_item_detail(row_id, item_type, description=description, issues=issues)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    formatted = format_item_detail_for_display(result)

    # Persist to Supabase only when we have grounding context (so cached results
    # are the context-aware versions, not the hallucinated ones)
    if sb and (description or issues):
        try:
            sb.table(DETAIL_TABLE).upsert({
                "id":        row_id,
                "item_type": item_type,
                "detail":    result,
            }).execute()
        except Exception as exc:
            print(f"WARNING: could not cache item detail to Supabase: {exc}")

    return formatted


@app.get("/upgrade-detail")
def upgrade_detail(
    name: str = Query(..., description="Upgrade name from the ROI report"),
    description: str = Query("", description="Item description from the report"),
    issues: str = Query("", description="Observed issues from photo analysis"),
):
    """Return deep how-to detail for a single upgrade item (cached in Supabase)."""
    return _get_or_generate_detail(name, "upgrade", description=description, issues=issues)


@app.get("/repair-detail")
def repair_detail(
    name: str = Query(..., description="Repair name from the ROI report"),
    description: str = Query("", description="Item description from the report"),
    issues: str = Query("", description="Observed issues from photo analysis"),
):
    """Return deep how-to detail for a single repair item (cached in Supabase)."""
    return _get_or_generate_detail(name, "repair", description=description, issues=issues)


# ─── Notes endpoints ─────────────────────────────────────────────────────────
# Supabase table (run once):
#   CREATE TABLE notes (
#       id         TEXT PRIMARY KEY,   -- always "130_kingfisher"
#       content    TEXT NOT NULL DEFAULT '',
#       updated_at TIMESTAMPTZ DEFAULT now()
#   );

NOTES_TABLE = "notes"
NOTES_ID    = "130_kingfisher"


class NotesSaveRequest(BaseModel):
    content: str


@app.get("/notes")
def notes_get():
    sb = _sb()
    if not sb:
        return {"content": "", "updated_at": None}
    try:
        result = sb.table(NOTES_TABLE).select("content, updated_at").eq("id", NOTES_ID).execute()
        if result.data:
            return {"content": result.data[0].get("content") or "", "updated_at": result.data[0].get("updated_at")}
        return {"content": "", "updated_at": None}
    except Exception:
        return {"content": "", "updated_at": None}


# ─── Walkthrough checklist endpoints ─────────────────────────────────────────
# Supabase table: see migrations/walkthrough_items.sql

class WalkthroughItemCreate(BaseModel):
    zone: str
    component: str
    layer: str = "room"
    category: str | None = None
    condition_label: str | None = None
    action: str = "assess"
    owner_note: str | None = None
    buyer_visibility: str | None = None
    inspection_risk: str | None = None
    estimated_cost_low: int | None = None
    estimated_cost_high: int | None = None
    priority_score: int | None = None
    sort_order: int = 0
    include_in_report: bool = False
    looks_fine: bool = False
    source: str = "user"


class WalkthroughItemPatch(BaseModel):
    zone: str | None = None
    component: str | None = None
    layer: str | None = None
    category: str | None = None
    condition_label: str | None = None
    action: str | None = None
    owner_note: str | None = None
    buyer_visibility: str | None = None
    inspection_risk: str | None = None
    estimated_cost_low: int | None = None
    estimated_cost_high: int | None = None
    priority_score: int | None = None
    sort_order: int | None = None
    include_in_report: bool | None = None
    looks_fine: bool | None = None
    source: str | None = None


class ZoneLooksFineRequest(BaseModel):
    zone: str
    property_id: str = WALKTHROUGH_PROPERTY_ID


@app.get("/walkthrough-items")
def walkthrough_items_list(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    sb = _sb()
    if not sb:
        return {"items": [], "property_id": property_id}
    items = enrich_walkthrough_items(load_walkthrough_items(sb, property_id))
    return {"items": items, "property_id": property_id, "count": len(items)}


@app.post("/walkthrough-items/seed")
def walkthrough_items_seed(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
    force: bool = Query(default=False),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    return seed_walkthrough_items(sb, property_id, force=force)


@app.post("/walkthrough-items")
def walkthrough_items_create(
    body: WalkthroughItemCreate,
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    row = {"property_id": property_id, **body.model_dump()}
    try:
        result = sb.table("walkthrough_items").insert(row).execute()
        inserted = (result.data or [row])[0]
        calc_fields = apply_calculated_persist_fields(inserted)
        sb.table("walkthrough_items").update({**calc_fields, "updated_at": "now()"}).eq("id", inserted["id"]).execute()
        fresh = sb.table("walkthrough_items").select("*").eq("id", inserted["id"]).maybe_single().execute()
        item = fresh.data if fresh and fresh.data else inserted
        return {"item": enrich_walkthrough_item(item)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase error: {exc}")


@app.get("/decision-matrix")
def get_decision_matrix(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    matrix = load_current_matrix(sb, property_id)
    if not matrix:
        raise HTTPException(status_code=404, detail="No decision matrix for this property")
    rows = load_matrix_rows(sb, matrix["id"])
    tier_counts = compute_tier_counts(rows)
    return {
        "property_id": property_id,
        "matrix": matrix,
        "tier_counts": tier_counts,
    }


@app.get("/decision-matrix/rows")
def get_decision_matrix_rows(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
    include_options: bool = Query(default=True),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    matrix = load_current_matrix(sb, property_id)
    if not matrix:
        raise HTTPException(status_code=404, detail="No decision matrix for this property")
    if include_options:
        rows = load_matrix_rows_with_options(sb, matrix["id"])
    else:
        rows = load_matrix_rows(sb, matrix["id"])
    return {
        "property_id": property_id,
        "matrix_id": matrix["id"],
        "row_count": len(rows),
        "rows": rows,
    }


@app.get("/decision-matrix/health")
def get_decision_matrix_health(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    matrix = load_current_matrix(sb, property_id)
    if not matrix:
        raise HTTPException(status_code=404, detail="No decision matrix for this property")
    rows = load_matrix_rows_with_options(sb, matrix["id"])
    health = compute_matrix_health(rows)
    return {
        "property_id": property_id,
        "matrix_id": matrix["id"],
        "health": health,
    }


@app.get("/decision-matrix/load")
def get_decision_matrix_load(
    tier: str = Query(default="should_do"),
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    """Combined endpoint: returns all rows + tier plan in one request, cached in memory."""
    cached = _matrix_cache_get()
    if cached and cached.get("property_id") == property_id:
        rows = cached["rows"]
        matrix_id = cached["matrix_id"]
    else:
        sb = _sb()
        if not sb:
            raise HTTPException(status_code=503, detail="Supabase not configured")
        matrix = load_current_matrix(sb, property_id)
        if not matrix:
            raise HTTPException(status_code=404, detail="No decision matrix for this property")
        matrix_id = matrix["id"]
        rows = load_matrix_rows_with_options(sb, matrix_id)
        _matrix_cache_set({"property_id": property_id, "matrix_id": matrix_id, "rows": rows})

    tier_plan = select_tier_from_rows(rows, tier, matrix_id=matrix_id, property_id=property_id)
    return {
        "property_id": property_id,
        "matrix_id": matrix_id,
        "rows": rows,
        "tier_plan": tier_plan,
    }


@app.get("/decision-matrix/tiers/{tier}")
def get_decision_matrix_tier(
    tier: str,
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        result = select_for_tier(tier, sb=sb, property_id=property_id)
    except ValueError as exc:
        msg = str(exc)
        if "No decision matrix" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return result


@app.get("/decision-matrix/scenarios/{scenario}")
def get_decision_matrix_scenario(
    scenario: str,
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
    buyer_profile: str = Query(default="general"),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    matrix = load_current_matrix(sb, property_id)
    if not matrix:
        raise HTTPException(status_code=404, detail="No decision matrix for this property")
    rows = load_matrix_rows_with_options(sb, matrix["id"])
    try:
        selection, line_items = compose_for_scenario(rows, scenario, buyer_profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "property_id": property_id,
        "matrix_id": matrix["id"],
        "scenario": scenario,
        "buyer_profile": buyer_profile,
        "selection": selection,
        "line_item_counts": {
            "upgrades": len(line_items.get("upgrades") or []),
            "repairs": len(line_items.get("repairs") or []),
        },
    }


class DecisionMatrixRowOverride(BaseModel):
    selected_option_key: str
    note: str | None = None


@app.patch("/decision-matrix/rows/{row_id}")
def patch_decision_matrix_row(
    row_id: str,
    body: DecisionMatrixRowOverride,
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        updated = apply_row_override(
            sb,
            row_id,
            selected_option_key=body.selected_option_key,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Row not found or update failed")
    _matrix_cache_clear()
    return {"row": updated}


class DecisionMatrixRowMeta(BaseModel):
    zone: str | None = None
    component: str | None = None
    current_state: str | None = None
    walkthrough_notes: str | None = None
    minimum_tier: str | None = None
    cost_low: float | None = None
    cost_high: float | None = None
    forecasted_spend: float | None = None
    selected_option_key: str | None = None


@app.patch("/decision-matrix/rows/{row_id}/meta")
def patch_decision_matrix_row_meta(row_id: str, body: DecisionMatrixRowMeta):
    """Update zone, tier, cost, or decision override for a decision matrix row."""
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    update: dict = {}
    if body.zone is not None:
        update["zone"] = body.zone.strip()
    if body.minimum_tier is not None:
        valid = {"must_do", "should_do", "nice_to_do", "not_doing"}
        if body.minimum_tier not in valid:
            raise HTTPException(status_code=400, detail=f"Invalid tier: {body.minimum_tier!r}")
        update["minimum_tier"] = body.minimum_tier
    if body.selected_option_key is not None:
        update["selected_option_key"] = body.selected_option_key
        update["seller_override"] = True
    if body.component is not None:
        update["component"] = body.component.strip()
    if body.current_state is not None:
        update["current_state"] = body.current_state.strip()
    if body.walkthrough_notes is not None:
        update["walkthrough_notes"] = body.walkthrough_notes.strip()
    cost_update: dict = {}
    if body.cost_low is not None:
        cost_update["cost_low"] = body.cost_low
        update["cost_low"] = body.cost_low  # save on row directly
    if body.cost_high is not None:
        cost_update["cost_high"] = body.cost_high
        update["cost_high"] = body.cost_high  # save on row directly
    if body.forecasted_spend is not None:
        update["forecasted_spend"] = body.forecasted_spend

    if not update and not cost_update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    try:
        if update:
            sb.table("decision_matrix_rows").update(update).eq("id", row_id).execute()
        if cost_update:
            # Also try to update matching option record if one exists
            row_result = (
                sb.table("decision_matrix_rows")
                .select("selected_option_key")
                .eq("id", row_id)
                .maybe_single()
                .execute()
            )
            selected_key = (row_result.data or {}).get("selected_option_key")
            if selected_key:
                sb.table("decision_matrix_options").update(cost_update).eq("row_id", row_id).eq("option_key", selected_key).execute()
            else:
                sb.table("decision_matrix_options").update(cost_update).eq("row_id", row_id).execute()
        result = (
            sb.table("decision_matrix_rows")
            .select("*")
            .eq("id", row_id)
            .maybe_single()
            .execute()
        )
        _matrix_cache_clear()
        return {"row": result.data}
    except Exception as exc:
        message = str(exc)
        if "decision_matrix_rows_minimum_tier_check" in message or "decision_matrix_rows_recommended_tier_check" in message:
            raise HTTPException(
                status_code=409,
                detail="Supabase tier constraint does not allow not_doing yet; run migrations/decision_matrix_v5_not_doing_tier.sql",
            )
        if "cost_low" in message or "cost_high" in message:
            raise HTTPException(
                status_code=409,
                detail="Supabase decision_matrix_rows is missing cost columns; run migrations/decision_matrix_v6_row_costs.sql",
            )
        if "forecasted_spend" in message:
            raise HTTPException(
                status_code=409,
                detail="Supabase decision_matrix_rows is missing forecasted_spend; run migrations/decision_matrix_v8_forecasted_spend.sql",
            )
        raise HTTPException(status_code=503, detail=message)


@app.delete("/decision-matrix/rows/{row_id}")
def delete_decision_matrix_row(row_id: str):
    """Delete a decision matrix row (and its options)."""
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    sb.table("decision_matrix_options").delete().eq("row_id", row_id).execute()
    sb.table("decision_matrix_rows").delete().eq("id", row_id).execute()
    _matrix_cache_clear()
    return {"deleted": row_id}


class DecisionMatrixCustomRow(BaseModel):
    zone: str
    component: str
    minimum_tier: str = "must_do"
    selected_option_key: str | None = None
    cost_low: float = 0
    cost_high: float = 0
    forecasted_spend: float = 0
    property_id: str = WALKTHROUGH_PROPERTY_ID


@app.post("/decision-matrix/rows/custom")
def add_custom_decision_row(body: DecisionMatrixCustomRow):
    """Add a manually created line item to the decision matrix."""
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    valid_tiers = {"must_do", "should_do", "nice_to_do", "not_doing"}
    if body.minimum_tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {body.minimum_tier!r}")
    valid_actions = {"leave_as_is", "clean", "repair", "refresh", "replace", "further_inspect", "paint", "needs_assessment", "not_doing"}
    selected_option_key = body.selected_option_key or "repair"
    if selected_option_key not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid decision: {selected_option_key!r}")
    matrix = load_current_matrix(sb, body.property_id)
    if not matrix:
        raise HTTPException(status_code=404, detail="No decision matrix for this property")
    decision_status = "required_action" if body.minimum_tier == "must_do" else "decision_required"
    row_data = {
        "matrix_id": matrix["id"],
        "component_id": f"custom:{uuid.uuid4().hex}",
        "walkthrough_item_id": None,
        "zone": body.zone.strip(),
        "component": body.component.strip(),
        "confidence_tier": "seller_added",
        "evidence_sources": [{"source": "seller", "text": "Manual decision matrix line item"}],
        "walkthrough_notes": "Manual decision matrix line item",
        "photo_evidence": [],
        "current_state": f"Seller-added item: {body.component.strip()}",
        "buyer_impact": "medium",
        "inspection_risk": "medium",
        "marketability_risk": "medium",
        "decision_status": decision_status,
        "minimum_tier": body.minimum_tier,
        "recommended_tier": body.minimum_tier,
        "selected_option_key": selected_option_key,
        "recommended_action": selected_option_key,
        "seller_override": True,
        "forecasted_spend": body.forecasted_spend,
    }
    try:
        result = sb.table("decision_matrix_rows").insert(row_data).execute()
    except Exception as exc:
        msg = str(exc)
        if "recommended_action_check" in msg:
            raise HTTPException(
                status_code=409,
                detail="DB constraint doesn't allow this decision type yet — run migrations/decision_matrix_v7_extended_actions.sql in Supabase",
            )
        raise HTTPException(status_code=500, detail=f"Row insert failed: {exc}")
    row = (result.data or [{}])[0]
    row_id = row.get("id")
    if row_id:
        try:
            sb.table("decision_matrix_options").insert({
                "row_id": row_id,
                "option_key": selected_option_key,
                "cost_low": body.cost_low,
                "cost_high": body.cost_high,
                "buyer_impact": "medium",
                "inspection_risk_impact": "reduces",
                "marketability_impact": "medium",
                "roi_quality": "manual",
                "feasibility": "recommended",
                "is_recommended": True,
                "rationale": {"source": "seller_custom_row"},
            }).execute()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Option insert failed: {exc}")
    _matrix_cache_clear()
    return {"row": row}


@app.post("/decision-matrix/rebuild")
def rebuild_decision_matrix(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        result = build_decision_matrix(property_id=property_id, sb=sb)
        _matrix_cache_clear()
        return result
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Decision matrix build failed: {exc}")


@app.get("/walkthrough-items/evidence-package")
def walkthrough_evidence_package(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
    scenario: str = Query(default="budget_15k"),
):
    sb = _sb()
    rows = load_walkthrough_items(sb, property_id) if sb else []
    analyses = _load_photo_analyses(sb)
    summary = build_analysis_summary(analyses) if analyses else {}
    package = build_evidence_package(rows, summary, default_property_facts())
    return {
        "package": package,
        "prompt": format_evidence_prompt(package, scenario),
        "property_id": property_id,
        "scenario": scenario,
    }


@app.post("/walkthrough-items/{item_id}/looks-fine")
def walkthrough_item_looks_fine(item_id: str):
    """Toggle No Concerns (looks_fine). Never clears owner_note."""
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        fresh = sb.table("walkthrough_items").select("*").eq("id", item_id).maybe_single().execute()
        if not fresh or not fresh.data:
            raise HTTPException(status_code=404, detail="Item not found")
        row = toggle_looks_fine(fresh.data)
        sb.table("walkthrough_items").update({
            **{k: row[k] for k in ("looks_fine", "include_in_report") if k in row},
            "updated_at": "now()",
        }).eq("id", item_id).execute()
        calc_fields = apply_calculated_persist_fields(row)
        sb.table("walkthrough_items").update({**calc_fields, "updated_at": "now()"}).eq("id", item_id).execute()
        fresh = sb.table("walkthrough_items").select("*").eq("id", item_id).maybe_single().execute()
        item = fresh.data if fresh and fresh.data else row
        return {"item": enrich_walkthrough_item(item)}
    except LooksFineError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase error: {exc}")


@app.post("/walkthrough-items/zone-looks-fine")
def walkthrough_zone_looks_fine(body: ZoneLooksFineRequest):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    rows = load_walkthrough_items(sb, body.property_id)
    to_mark = zone_looks_fine_remaining(rows, body.zone)
    marked = 0
    for row in to_mark:
        if not row.get("id"):
            continue
        updated = apply_looks_fine(row)
        try:
            sb.table("walkthrough_items").update({
                "looks_fine": True,
                "include_in_report": False,
                "updated_at": "now()",
            }).eq("id", row["id"]).execute()
            calc_fields = apply_calculated_persist_fields(updated)
            sb.table("walkthrough_items").update({**calc_fields, "updated_at": "now()"}).eq("id", row["id"]).execute()
            marked += 1
        except Exception:
            pass
    return {"marked": marked, "skipped": len(rows) - marked, "zone": body.zone}


@app.patch("/walkthrough-items/{item_id}")
def walkthrough_items_patch(item_id: str, body: WalkthroughItemPatch):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    existing = None
    if "owner_note" in updates:
        existing_row = (
            sb.table("walkthrough_items").select("component", "category")
            .eq("id", item_id).maybe_single().execute()
        )
        existing = existing_row.data if existing_row and existing_row.data else {}
        raw = updates["owner_note"]
        note = (raw or "").strip() if raw else ""
        if note and is_assessment_prompt_text(
            note,
            existing.get("component"),
            existing.get("category"),
        ):
            note = ""
        if note:
            updates["owner_note"] = note
            updates["looks_fine"] = False
            updates["include_in_report"] = True
        else:
            updates["owner_note"] = None
            updates["include_in_report"] = False
    if "estimated_cost_low" in updates or "estimated_cost_high" in updates:
        updates["cost_overridden"] = True
    if "priority_score" in updates:
        updates["priority_overridden"] = True
    if "action" in updates:
        updates["action_overridden"] = True
    if "condition_label" in updates:
        updates["condition_overridden"] = True
        from walkthrough import CONDITION_LABEL_TO_SCORE
        updates["condition_score"] = CONDITION_LABEL_TO_SCORE.get(updates["condition_label"])
    if "category" in updates:
        updates["category_overridden"] = True
    if "buyer_visibility" in updates:
        updates["visibility_overridden"] = True
    if "inspection_risk" in updates:
        updates["risk_overridden"] = True
    updates["updated_at"] = "now()"
    try:
        result = (
            sb.table("walkthrough_items")
            .update(updates)
            .eq("id", item_id)
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Item not found")
        row = result.data[0]
        calc_fields = apply_calculated_persist_fields(row)
        sb.table("walkthrough_items").update({**calc_fields, "updated_at": "now()"}).eq("id", item_id).execute()
        fresh = (
            sb.table("walkthrough_items").select("*").eq("id", item_id).maybe_single().execute()
        )
        row = fresh.data if fresh and fresh.data else row
        return {"item": enrich_walkthrough_item(row)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase error: {exc}")


@app.post("/walkthrough-items/sanitize-prompts")
def walkthrough_sanitize_prompts(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    """Clear assessment prompt text mistakenly stored as owner_note."""
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    return sanitize_walkthrough_prompt_notes(sb, property_id)


@app.post("/walkthrough-items/recalculate")
def walkthrough_items_recalculate(
    property_id: str = Query(default=WALKTHROUGH_PROPERTY_ID),
):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    return recalculate_all_items(sb, property_id)


@app.delete("/walkthrough-items/{item_id}")
def walkthrough_items_delete(item_id: str):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        sb.table("walkthrough_items").delete().eq("id", item_id).execute()
        return {"deleted": True, "id": item_id}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase error: {exc}")


@app.post("/notes")
def notes_save(body: NotesSaveRequest):
    sb = _sb()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        sb.table(NOTES_TABLE).upsert({
            "id":         NOTES_ID,
            "content":    body.content,
            "updated_at": "now()",
        }).execute()
        return {"saved": True}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase error: {exc}")


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

