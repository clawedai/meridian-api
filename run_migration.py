"""
Run SQL migrations against Supabase via the Management API.

Usage: python run_migration.py
Requires: SUPABASE_PAT, SUPABASE_URL in environment or .env
"""
import os
import sys
from pathlib import Path
import httpx
from dotenv import load_dotenv

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    print("WARNING: .env not found - relying on environment variables")

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://osdbckbblcdtwtnjqmii.supabase.co")
PROJECT_REF = SUPABASE_URL.replace("https://", "").replace(".supabase.co", "")

# Management API requires a Personal Access Token (PAT), not the service role key.
# Generate one at: https://supabase.com/dashboard/project/{ref}/settings/api
MGMT_ENDPOINT = f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query"
MGMT_TOKEN = os.getenv("SUPABASE_PAT")
if not MGMT_TOKEN:
    raise ValueError(
        "SUPABASE_PAT env var is not set. "
        "Generate a Personal Access Token at: "
        "https://supabase.com/dashboard/project/{ref}/settings/api"
    )

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# ─────────────────────────────────────────────────────────────────────────────


def run_sql_via_management_api(sql: str) -> dict:
    """Execute raw SQL via the Supabase Management API."""
    headers = {
        "Authorization": f"Bearer {MGMT_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"query": sql}

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(MGMT_ENDPOINT, headers=headers, json=payload)
        return {"status_code": resp.status_code, "body": resp.text}


def verify_tables_exist() -> dict:
    """Query information_schema to confirm tables were created."""
    check_sql = """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('meta_ad_signals', 'meta_ads');
    """
    headers = {
        "Authorization": f"Bearer {MGMT_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    payload = {"query": check_sql}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(MGMT_ENDPOINT, headers=headers, json=payload)
        return {"status_code": resp.status_code, "body": resp.text}


def verify_intent_scores_columns() -> dict:
    """Check that intent_scores has the new meta_ad columns."""
    check_sql = """
        SELECT column_name, data_type, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'intent_scores'
          AND column_name LIKE 'meta_ad%';
    """
    headers = {
        "Authorization": f"Bearer {MGMT_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"query": check_sql}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(MGMT_ENDPOINT, headers=headers, json=payload)
        return {"status_code": resp.status_code, "body": resp.text}


def main():
    print(f"Supabase Project Ref : {PROJECT_REF}")
    print(f"Mgmt API endpoint    : {MGMT_ENDPOINT}")
    print()

    # Discover all .sql migration files sorted by name
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print("No migration files found in migrations/ - nothing to do.")
        return

    print(f"Found {len(migration_files)} migration file(s):")
    for f in migration_files:
        print(f"  {f.name}")
    print()

    for mf in migration_files:
        print(f"-- Running migration: {mf.name} --")
        sql = mf.read_text(encoding="utf-8")

        result = run_sql_via_management_api(sql)
        print(f"  Status : {result['status_code']}")
        print(f"  Response: {result['body'][:500]}")

        if result["status_code"] not in (200, 201):
            print(f"\nFAILED - stopping. Fix the error above before continuing.")
            sys.exit(1)

        print("  OK\n")

    # ── Verification ──────────────────────────────────────────────────────────
    print("-- Verification ------------------------------------------------------")
    tables_result = verify_tables_exist()
    print(f"Tables query status : {tables_result['status_code']}")
    print(f"Tables result        : {tables_result['body'][:800]}")

    cols_result = verify_intent_scores_columns()
    print(f"Columns query status : {cols_result['status_code']}")
    print(f"Columns result       : {cols_result['body'][:800]}")
    print()
    print("Migration complete.")


if __name__ == "__main__":
    main()
