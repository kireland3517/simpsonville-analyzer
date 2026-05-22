"""
main.py
───────
FastAPI backend for the house analysis web app.

Endpoints
─────────
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
POST /report                 Generate ROI report from cached analyses
GET  /report                 Return cached ROI report
GET  /report/export/csv      Download upgrades + repairs as CSV

Run:
    uvicorn main:app --port 8000 --reload
"""
from __future__ import annotations

import csv
import io
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
from roi import generate_roi_report

# ─── Module-level state ───────────────────────────────────────────────────────

# Keyed by photo_id → analysis dict from analyzer.analyze_image()
analysis_cache: dict[str, dict] = {}

# Latest generated ROI report
roi_cache: Optional[dict] = None

# ─── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="House Analysis API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_INDEX = Path("static/index.html")


# ─── Pydantic models ──────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    base_url: str
    photo_id: str


class BulkPhotoItem(BaseModel):
    base_url: str
    photo_id: str


class BulkAnalyzeRequest(BaseModel):
    photos: list[BulkPhotoItem]


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
    # Clear in-memory cache
    _photos_module._token_cache = None

    # Delete from Supabase if configured
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
def report_generate():
    global roi_cache
    analyses = list(analysis_cache.values())
    roi_cache = generate_roi_report(
        analyses,
        get_property_summary(),
        get_last_sale(),
    )
    return roi_cache


@app.get("/report")
def report_get():
    if roi_cache is None:
        raise HTTPException(status_code=404, detail="No report generated yet")
    return roi_cache


@app.get("/report/export/csv")
def report_export_csv():
    if roi_cache is None:
        raise HTTPException(status_code=404, detail="No report generated yet")

    upgrades: list[dict] = roi_cache.get("upgrades") or []
    repairs: list[dict] = roi_cache.get("repairs") or []

    # Collect all field names across both lists
    upgrade_fields = list(dict.fromkeys(k for u in upgrades for k in u))
    repair_fields = list(dict.fromkeys(k for r in repairs for k in r))
    all_fields = ["type"] + list(dict.fromkeys(upgrade_fields + repair_fields))

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


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
