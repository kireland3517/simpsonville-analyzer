"""
Optional utility: apply decision_matrix_v1.sql via direct Postgres connection.

Normal workflow: run migrations/decision_matrix_v1.sql in the Supabase SQL Editor.
The app persists the decision matrix via Supabase REST (SUPABASE_URL + SUPABASE_SERVICE_KEY).

This script is only for environments that provide DATABASE_URL or SUPABASE_DB_PASSWORD.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv()
load_dotenv(ROOT / ".env")

MIGRATION = ROOT / "migrations" / "decision_matrix_v1.sql"


def get_database_url() -> str | None:
    import os
    import re

    direct = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DATABASE_URL")
    if direct:
        return direct
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    url = os.environ.get("SUPABASE_URL", "")
    match = re.search(r"https://([^.]+)\.supabase\.co", url)
    if password and match:
        ref = match.group(1)
        return f"postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres"
    return None


def main() -> None:
    db_url = get_database_url()
    if not db_url:
        print("DATABASE_URL not set. Run this SQL in the Supabase SQL Editor instead:")
        print()
        print(MIGRATION.read_text(encoding="utf-8"))
        sys.exit(1)

    try:
        import psycopg2
    except ImportError:
        print("Install psycopg2-binary: pip install psycopg2-binary", file=sys.stderr)
        sys.exit(1)

    sql = MIGRATION.read_text(encoding="utf-8")
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print("Migration applied successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
