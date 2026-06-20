"""
run_inventory.py
----------------
Backfill inventory counts for local photos in Supabase photo_analyses.

This script scans local image/video files, runs the inventory vision pass, and
updates Supabase rows that already have analysis data. External photo import has
been removed from the active app.

Usage:
    python run_inventory.py
    python run_inventory.py --local

Requires:
    ANTHROPIC_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY

Supabase schema prerequisite:
    ALTER TABLE photo_analyses ADD COLUMN IF NOT EXISTS inventory JSONB;
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

from analyzer import analyze_image_inventory, extract_video_frames
from claude_client import get_api_key

TABLE = "photo_analyses"

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS = {".mp4"}
VIDEO_FRAMES_DIR = Path(".video_frames")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".video_frames", "static"}


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def load_analyzed_ids(client) -> set[str]:
    """Return filenames that already have inventory data."""
    try:
        result = (
            client.table(TABLE)
            .select("id, inventory")
            .not_.is_("inventory", "null")
            .execute()
        )
        return {r["id"] for r in (result.data or [])}
    except Exception as exc:
        print(f"WARNING: could not check existing inventory: {exc}")
        return set()


def scan_local_files() -> list[tuple[Path, str]]:
    """Return (path_to_analyze, row_id) pairs from media/ or the repo root."""
    media_dir = Path("media")
    search_root = media_dir if media_dir.is_dir() else Path(".")
    work: list[tuple[Path, str]] = []

    for path in sorted(search_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue

        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            work.append((path, path.name))
        elif ext in VIDEO_EXTS:
            frames_dir = VIDEO_FRAMES_DIR / path.stem
            frames = extract_video_frames(path, frames_dir, every_n_seconds=5)
            if not frames:
                print(f"  SKIP  {path.name} - ffmpeg not found or no frames extracted")
                continue
            for frame in frames:
                work.append((frame, frame.name))

    return work


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run inventory vision pass on local house photos and save to Supabase."
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Deprecated no-op; local media is the only supported mode.",
    )
    parser.parse_args()

    if not get_api_key():
        print("ERROR: ANTHROPIC_API_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    client = get_supabase()
    all_work = scan_local_files()
    if not all_work:
        print("No image or video files found.")
        return

    already_done = load_analyzed_ids(client)
    to_run = [(path, row_id) for path, row_id in all_work if row_id not in already_done]

    print(f"{len(all_work)} files found on disk (images + video frames).")
    print(f"{len(already_done)} already have inventory in Supabase.")
    print(f"{len(to_run)} to analyze.")

    if not to_run:
        print("Nothing to do.")
        return

    succeeded = failed = 0

    for index, (path, row_id) in enumerate(to_run, start=1):
        print(f"  [{index}/{len(to_run)}] {row_id}", end="", flush=True)

        result = analyze_image_inventory(path)

        if result.get("error"):
            print(f" - FAIL: {result['error']}")
            failed += 1
            continue

        try:
            client.table(TABLE).update({"inventory": result}).eq("id", row_id).execute()
            print(f" - OK ({result.get('room_type', '?')})")
            succeeded += 1
        except Exception as exc:
            print(f" - WARN: could not save to Supabase: {exc}")
            failed += 1

    print()
    print(f"Done. {succeeded} succeeded, {failed} failed out of {len(to_run)}.")


if __name__ == "__main__":
    main()
