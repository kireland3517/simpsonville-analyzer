"""Force re-analyze every image in media/ and upsert to Supabase."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from analyzer import analyze_image
from run_analysis import IMAGE_EXTS, get_supabase, save_result


def _is_usable(row: dict | None) -> bool:
    if not row:
        return False
    analysis = row.get("analysis") or {}
    if isinstance(analysis, str):
        analysis = json.loads(analysis)
    return not (analysis.get("error") and not analysis.get("room_type"))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resume-missing",
        action="store_true",
        help="Only analyze media files without a usable Supabase row",
    )
    args = parser.parse_args()

    client = get_supabase()
    media_files = sorted(
        p for p in (ROOT / "media").rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    existing = {
        r["id"]: r
        for r in (client.table("photo_analyses").select("id, analysis").execute().data or [])
    }

    if args.resume_missing:
        work = [p for p in media_files if not _is_usable(existing.get(p.name))]
    else:
        work = media_files

    total = len(work)
    skipped = len(media_files) - len(work)
    print(
        f"Analyzing {total} media photos"
        + (f" ({skipped} skipped — already usable)" if skipped else "")
        + "...",
        flush=True,
    )
    succeeded = failed = 0
    errors: list[tuple[str, str]] = []

    for i, path in enumerate(work, 1):
        print(f"[{i}/{total}] {path.name}", flush=True)
        result = analyze_image(path)
        if result.get("error"):
            failed += 1
            errors.append((path.name, result["error"]))
            print(f"  FAIL: {result['error']}", flush=True)
        else:
            succeeded += 1
        try:
            save_result(client, path.name, result)
        except Exception as exc:
            print(f"  WARN save: {exc}", flush=True)

    print(
        f"DONE found={len(media_files)} analyzed={total} succeeded={succeeded} "
        f"skipped={skipped} failed={failed}",
        flush=True,
    )
    if errors:
        print("ERRORS:", json.dumps(errors), flush=True)


if __name__ == "__main__":
    main()
