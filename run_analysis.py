"""
run_analysis.py
───────────────
Batch-analyzes house photos and video frames using Claude Vision,
saving results to Supabase.

Usage:
    python run_analysis.py

Requires:
    ANTHROPIC_API_KEY     — Claude API key
    SUPABASE_URL          — Supabase project URL
    SUPABASE_SERVICE_KEY  — Supabase service role key

Supabase table (run once):
    CREATE TABLE photo_analyses (
        id         TEXT PRIMARY KEY,   -- filename used as key
        filename   TEXT NOT NULL,
        analysis   JSONB,
        created_at TIMESTAMPTZ DEFAULT now()
    );
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

from analyzer import analyze_image, extract_video_frames

# ─── Config ───────────────────────────────────────────────────────────────────

IMAGE_EXTS  = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS  = {".mp4"}
SKIP_DIRS   = {".git", "node_modules", "__pycache__", ".venv", "venv"}

VIDEO_FRAMES_DIR = Path(".video_frames")
TABLE = "photo_analyses"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def load_existing_ids(client, reanalyze_outdated: bool = False) -> set[str]:
    """
    Return the set of filename IDs already in the table.
    If reanalyze_outdated is True, exclude rows that are missing the new
    'dated_features' field (written by the previous prompt) so they get
    re-analyzed with the updated prompt.
    """
    try:
        result = client.table(TABLE).select("id, analysis").execute()
        ids: set[str] = set()
        for row in (result.data or []):
            analysis = row.get("analysis") or {}
            if reanalyze_outdated and "dated_features" not in analysis:
                continue  # will be re-analyzed
            ids.add(row["id"])
        return ids
    except Exception as exc:
        print(f"WARNING: Could not fetch existing rows: {exc}")
        return set()


def scan_files() -> list[Path]:
    """Return all image and video files in the current folder tree."""
    results: list[Path] = []
    for p in sorted(Path(".").rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(".")
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        if p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
            results.append(p)
    return results


def save_result(client, filename: str, analysis: dict) -> None:
    client.table(TABLE).upsert({
        "id":       filename,
        "filename": filename,
        "analysis": analysis,
    }).execute()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reanalyze-outdated", action="store_true",
        help="Re-analyze photos that are missing the new schema fields (dated_features etc.)"
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    client = get_supabase()
    existing = load_existing_ids(client, reanalyze_outdated=args.reanalyze_outdated)
    label = "using updated schema" if args.reanalyze_outdated else "already analyzed"
    print(f"{len(existing)} files {label} in Supabase — will skip them.")

    # Expand video files into their frame paths
    Work = list[tuple[Path, str]]  # (path_to_analyze, row_id_key)
    work: list[tuple[Path, str]] = []

    for p in scan_files():
        ext = p.suffix.lower()
        if ext in VIDEO_EXTS:
            frames_dir = VIDEO_FRAMES_DIR / p.stem
            frames = extract_video_frames(p, frames_dir, every_n_seconds=5)
            if not frames:
                print(f"  SKIP  {p} — ffmpeg not found or no frames extracted")
                continue
            for frame in frames:
                row_id = frame.name
                if row_id not in existing:
                    work.append((frame, row_id))
        else:
            row_id = p.name
            if row_id not in existing:
                work.append((p, row_id))

    total = len(work)
    if total == 0:
        print("Nothing to analyze — all files are already in Supabase.")
        return

    succeeded = 0
    failed = 0

    for i, (path, row_id) in enumerate(work, start=1):
        print(f"Analyzing {i}/{total}: {path.name}", flush=True)
        result = analyze_image(path)

        if result.get("error"):
            print(f"  FAIL  {path.name}: {result['error']}")
            failed += 1
        else:
            succeeded += 1

        # Save regardless — error results are stored so we don't retry them
        try:
            save_result(client, row_id, result)
        except Exception as exc:
            print(f"  WARN  Could not save {row_id} to Supabase: {exc}")

    print()
    print(f"Done. {succeeded} succeeded, {failed} failed out of {total} analyzed.")


if __name__ == "__main__":
    main()
