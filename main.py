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
POST /analyze                Analyze a single photo with Claude Vision
POST /analyze/bulk           Analyze a batch of photos sequentially
GET  /analyze/results        Return all cached analysis results
POST /report                 Generate ROI report (body: {detail_level, buyer_profile})
GET  /report                 Return ROI report by ?id= (default standard_general)
GET  /report/export/csv      Download upgrades + repairs as CSV
GET  /upgrade-detail         Deep how-to detail for one upgrade (cached in Supabase)
GET  /repair-detail          Deep how-to detail for one repair (cached in Supabase)
GET  /dated-features         Aggregated dated_features across all photo analyses
GET  /inspection-flags       Top 20 inspection flags across all photo analyses

Run:
    uvicorn main:app --port 8000 --reload
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path
from typing import Optional

import requests

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
from roi import generate_roi_report, get_item_detail
from run_roi import build_analysis_summary

# ─── Module-level state ───────────────────────────────────────────────────────

# Keyed by photo_id → analysis dict from analyzer.analyze_image()
analysis_cache: dict[str, dict] = {}

# Write-through memory cache for the latest ROI report (also persisted to Supabase)
roi_cache: Optional[dict] = None

# Supabase table / row for the ROI report
ROI_TABLE = "roi_report"
ROI_ID    = "current"

# ─── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="House Analysis API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_INDEX = Path("static/index.html")


# ─── Supabase helper ──────────────────────────────────────────────────────────

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


class ReportRequest(BaseModel):
    detail_level: str = "standard"
    buyer_profile: str = "general"


# ─── Auth endpoints ───────────────────────────────────────────────────────────

@app.get("/")
def serve_index():
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    return PlainTextResponse("UI not built yet")


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


@app.post("/analyze")
def analyze_single(body: AnalyzeRequest):
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _download_and_analyze(body.base_url, creds)


@app.post("/analyze/bulk")
def analyze_bulk(body: BulkAnalyzeRequest):
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")

    total = len(body.photos)
    completed = 0

    for item in body.photos:
        if item.photo_id in analysis_cache:
            continue

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

        analysis_cache[item.photo_id] = result
        completed += 1

    return {"completed": completed, "total": total}


@app.get("/analyze/results")
def analyze_results():
    return analysis_cache


# ─── Report endpoints ─────────────────────────────────────────────────────────

@app.post("/report")
def report_generate(body: ReportRequest):
    """Generate and persist a report for the given detail_level + buyer_profile."""
    global roi_cache

    detail_level  = body.detail_level
    buyer_profile = body.buyer_profile
    report_id     = f"{detail_level}_{buyer_profile}"

    analyses = list(analysis_cache.values())
    if not analyses:
        # Fall back to Supabase analyses when memory cache is cold
        sb = _sb()
        if sb:
            try:
                rows = sb.table("photo_analyses").select("analysis").execute()
                analyses = [r["analysis"] for r in (rows.data or []) if r.get("analysis")]
            except Exception:
                pass
    if not analyses:
        raise HTTPException(status_code=422, detail="No analyses available — run run_analysis.py first")

    summary = build_analysis_summary(analyses)
    report  = generate_roi_report(
        summary,
        get_property_summary(),
        get_last_sale(),
        detail_level=detail_level,
        buyer_profile=buyer_profile,
    )

    if report.get("error"):
        raise HTTPException(status_code=500, detail=report["error"])

    sb = _sb()
    if sb:
        try:
            sb.table(ROI_TABLE).upsert({"id": report_id, "report": report}).execute()
        except Exception as exc:
            print(f"WARNING: could not save report to Supabase: {exc}")

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


# ─── Item detail endpoints ────────────────────────────────────────────────────

# Supabase table for caching on-demand deep detail per item
DETAIL_TABLE = "upgrade_details"


def _get_or_generate_detail(name: str, item_type: str) -> dict:
    """
    Return cached detail from Supabase if available, otherwise call Claude
    via roi.get_item_detail() and persist the result.
    item_type: "upgrade" | "repair"
    """
    if not name or not name.strip():
        raise HTTPException(status_code=422, detail="name parameter is required")

    row_id = name.strip()

    # Check Supabase cache first
    sb = _sb()
    if sb:
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
                return row.data["detail"]
        except Exception:
            pass

    # Cache miss — call Claude
    result = get_item_detail(row_id, item_type)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    # Persist to Supabase (non-fatal if it fails)
    if sb:
        try:
            sb.table(DETAIL_TABLE).upsert({
                "id":        row_id,
                "item_type": item_type,
                "detail":    result,
            }).execute()
        except Exception as exc:
            print(f"WARNING: could not cache item detail to Supabase: {exc}")

    return result


@app.get("/upgrade-detail")
def upgrade_detail(name: str = Query(..., description="Upgrade name from the ROI report")):
    """Return deep how-to detail for a single upgrade item (cached in Supabase)."""
    return _get_or_generate_detail(name, "upgrade")


@app.get("/repair-detail")
def repair_detail(name: str = Query(..., description="Repair name from the ROI report")):
    """Return deep how-to detail for a single repair item (cached in Supabase)."""
    return _get_or_generate_detail(name, "repair")


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

