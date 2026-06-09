"""
run_inventory.py
────────────────
Backfill inventory counts for photos in Supabase photo_analyses.

Two modes:

  --local   Scan image files on disk, run inventory vision pass, update
            Supabase rows that already have analysis data.
            Use this when photos are in the repo and base_url was never stored.

  (default) Fetch each photo via its stored base_url from Google Photos,
            run inventory vision pass, save to Supabase.
            Use this when photos came through the web app OAuth flow.

Usage:
    python run_inventory.py --local
    python run_inventory.py

Requires:
    ANTHROPIC_API_KEY     — Anthropic API key
    SUPABASE_URL          — Supabase project URL
    SUPABASE_SERVICE_KEY  — Supabase service role key

For default (Google Photos) mode, also requires:
    GOOGLE_CREDENTIALS_JSON or google_credentials.json
    A valid Google token (google_token.json or GOOGLE_TOKEN_JSON env var)

Supabase schema prerequisites (run once in Supabase SQL editor):
    ALTER TABLE photo_analyses ADD COLUMN IF NOT EXISTS inventory JSONB;
    ALTER TABLE photo_analyses ADD COLUMN IF NOT EXISTS base_url TEXT;
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

from analyzer import analyze_image_inventory
from claude_client import get_api_key

TABLE = "photo_analyses"
FULL_RES_WIDTH = 0  # width=0 → full-resolution download

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
SKIP_DIRS  = {".git", "node_modules", "__pycache__", ".venv", "venv", ".video_frames", "static"}


# ─── Supabase helpers ─────────────────────────────────────────────────────────

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def load_pending_rows(client) -> list[dict]:
    """Return rows that have analysis but no inventory yet (Google Photos mode)."""
    try:
        result = (
            client.table(TABLE)
            .select("id, filename, base_url")
            .is_("inventory", "null")
            .not_.is_("analysis", "null")
            .execute()
        )
        return result.data or []
    except Exception as exc:
        print(f"ERROR: Could not fetch pending rows: {exc}", file=sys.stderr)
        sys.exit(1)


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


# ─── Local file scan ──────────────────────────────────────────────────────────

def scan_local_files() -> list[Path]:
    """Return all image files under the media/ folder (or repo root if absent)."""
    media_dir = Path("media")
    search_root = media_dir if media_dir.is_dir() else Path(".")
    results = []
    for p in sorted(search_root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in IMAGE_EXTS:
            results.append(p)
    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run inventory vision pass on house photos and save to Supabase."
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Scan local image files instead of fetching from Google Photos",
    )
    args = parser.parse_args()

    if not get_api_key():
        print("ERROR: ANTHROPIC_API_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    client = get_supabase()

    # ── Local mode ────────────────────────────────────────────────────────────
    if args.local:
        files = scan_local_files()
        if not files:
            print("No image files found in current directory.")
            return

        already_done = load_analyzed_ids(client)
        to_run = [f for f in files if f.name not in already_done]

        print(f"{len(files)} images found on disk.")
        print(f"{len(already_done)} already have inventory in Supabase.")
        print(f"{len(to_run)} to analyze.")

        if not to_run:
            print("Nothing to do.")
            return

        succeeded = failed = skipped = 0

        for i, path in enumerate(to_run, start=1):
            print(f"  [{i}/{len(to_run)}] {path.name}", end="", flush=True)

            result = analyze_image_inventory(path)

            if result.get("error"):
                print(f" — FAIL: {result['error']}")
                failed += 1
                continue

            # Update only the inventory column on the matching row (keyed by filename)
            try:
                client.table(TABLE).update({"inventory": result}).eq("id", path.name).execute()
                print(f" — OK ({result.get('room_type', '?')})")
                succeeded += 1
            except Exception as exc:
                print(f" — WARN: could not save to Supabase: {exc}")
                failed += 1

        print()
        print(f"Done. {succeeded} succeeded, {failed} failed out of {len(to_run)}.")
        return

    # ── Google Photos mode (default) ──────────────────────────────────────────
    from photos import get_credentials, get_photo_bytes

    rows = load_pending_rows(client)

    if not rows:
        print("Nothing to do — all analyzed photos already have inventory data.")
        return

    print(f"{len(rows)} photos need inventory analysis.")

    creds = get_credentials()
    if not creds:
        print(
            "ERROR: No Google credentials found. Authenticate via the web app first "
            "(GET /auth/login), or set GOOGLE_TOKEN_JSON in your environment.\n"
            "Tip: if photos are stored locally, run with --local instead.",
            file=sys.stderr,
        )
        sys.exit(1)

    succeeded = skipped = failed = 0

    for i, row in enumerate(rows, start=1):
        row_id   = row.get("id") or row.get("filename")
        base_url = row.get("base_url")

        if not base_url:
            print(f"  SKIP  [{i}/{len(rows)}] {row_id} — no base_url stored")
            print("        Tip: run with --local if photos are in the repo.")
            skipped += 1
            continue

        print(f"  [{i}/{len(rows)}] {row_id}", end="", flush=True)

        photo_bytes = get_photo_bytes(base_url, creds, width=FULL_RES_WIDTH)
        if photo_bytes is None:
            print(" — FAIL: could not download photo (token expired or URL stale)")
            failed += 1
            continue

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(photo_bytes)
            result = analyze_image_inventory(tmp_path)
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()

        if result.get("error"):
            print(f" — FAIL: {result['error']}")
            failed += 1
            continue

        try:
            client.table(TABLE).update({"inventory": result}).eq("id", row_id).execute()
            print(f" — OK ({result.get('room_type', '?')})")
            succeeded += 1
        except Exception as exc:
            print(f" — WARN: could not save to Supabase: {exc}")
            failed += 1

    print()
    print(f"Done. {succeeded} succeeded, {skipped} skipped (no base_url), {failed} failed.")
    if skipped:
        print("Tip: re-run with --local to process photos stored on disk.")


if __name__ == "__main__":
    main()