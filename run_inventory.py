"""
run_inventory.py
────────────────
Backfill inventory counts for photos already in Supabase photo_analyses.

Reads all rows where inventory IS NULL, fetches each photo via its stored
base_url, runs the lightweight inventory vision pass, and saves only the
inventory column — the existing analysis column is never touched.

Usage:
    python run_inventory.py

Requires:
    ANTHROPIC_API_KEY     — Anthropic API key
    SUPABASE_URL          — Supabase project URL
    SUPABASE_SERVICE_KEY  — Supabase service role key
    GOOGLE_CREDENTIALS_JSON or google_credentials.json  — Google OAuth config
    A valid Google token (google_token.json or GOOGLE_TOKEN_JSON env var)

Supabase schema prerequisites (run once in Supabase SQL editor):
    ALTER TABLE photo_analyses ADD COLUMN IF NOT EXISTS inventory JSONB;
    ALTER TABLE photo_analyses ADD COLUMN IF NOT EXISTS base_url TEXT;
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

from analyzer import analyze_image_inventory
from claude_client import get_api_key
from photos import get_credentials, get_photo_bytes

TABLE = "photo_analyses"
FULL_RES_WIDTH = 0  # width=0 → full-resolution download


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def load_pending_rows(client) -> list[dict]:
    """Return rows that have analysis but no inventory yet."""
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


def main() -> None:
    if not get_api_key():
        print("ERROR: ANTHROPIC_API_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    client = get_supabase()
    rows = load_pending_rows(client)

    if not rows:
        print("Nothing to do — all analyzed photos already have inventory data.")
        return

    print(f"{len(rows)} photos need inventory analysis.")

    creds = get_credentials()
    if not creds:
        print(
            "ERROR: No Google credentials found. Authenticate via the web app first "
            "(GET /auth/login), or set GOOGLE_TOKEN_JSON in your environment.",
            file=sys.stderr,
        )
        sys.exit(1)

    succeeded = 0
    skipped = 0
    failed = 0

    for i, row in enumerate(rows, start=1):
        row_id   = row.get("id") or row.get("filename")
        base_url = row.get("base_url")

        if not base_url:
            print(f"  SKIP  [{i}/{len(rows)}] {row_id} — no base_url stored (re-run bulk analysis to populate)")
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
    print(f"Done. {succeeded} succeeded, {skipped} skipped (no base_url), {failed} failed out of {len(rows)}.")
    if skipped:
        print("To fill skipped rows: re-run the bulk analysis from the web app with Google Photos authenticated.")


if __name__ == "__main__":
    main()
