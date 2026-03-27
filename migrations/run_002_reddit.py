#!/usr/bin/env python3
"""
Execute migration 002_reddit_tables.sql against Supabase via the Management API.
"""
import httpx
import sys

SUPABASE_PAT = "sbp_5f99c50af8ce78795b14e941795c0c5dcf20d9d0"
PROJECT_REF = "osdbckbblcdtwtnjqmii"
MANAGEMENT_API = f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query"
MIGRATION_FILE = "D:/tricellworks/meridian-api/migrations/002_reddit_tables.sql"

def main():
    # Read the migration SQL
    with open(MIGRATION_FILE, "r") as f:
        sql = f.read()

    headers = {
        "Authorization": f"Bearer {SUPABASE_PAT}",
        "Content-Type": "application/json",
    }

    payload = {"query": sql}

    print("=" * 60)
    print("EXECUTING: 002_reddit_tables.sql")
    print(f"TARGET: {PROJECT_REF}")
    print("=" * 60)

    with httpx.Client(timeout=60.0) as client:
        response = client.post(MANAGEMENT_API, headers=headers, json=payload)

    print(f"\nHTTP Status: {response.status_code}")

    if response.status_code in (200, 201):
        print("RESULT: SUCCESS")
        try:
            data = response.json()
            if data:
                print(f"Response data: {data}")
            else:
                print("Response: (empty, as expected for DDL)")
        except Exception:
            print(f"Response text: {response.text[:500]}")
    else:
        print("RESULT: FAILED")
        print(f"Response: {response.text}")
        sys.exit(1)

    # Verify tables exist
    print("\n" + "=" * 60)
    print("VERIFICATION: Checking tables via information_schema")
    print("=" * 60)

    verify_sql = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name IN ('reddit_ad_signals', 'reddit_organic_signals')
    ORDER BY table_name;
    """

    verify_payload = {"query": verify_sql}

    with httpx.Client(timeout=60.0) as client:
        verify_response = client.post(MANAGEMENT_API, headers=headers, json=verify_payload)

    print(f"Verification HTTP Status: {verify_response.status_code}")

    if verify_response.status_code in (200, 201):
        try:
            tables = verify_response.json()
            if isinstance(tables, list):
                table_names = [t.get("table_name", "") for t in tables]
                print(f"Tables found: {table_names}")

                expected = {"reddit_ad_signals", "reddit_organic_signals"}
                found = set(table_names)
                if expected.issubset(found):
                    print("\nVERIFICATION: PASSED — Both tables exist.")
                else:
                    missing = expected - found
                    print(f"\nVERIFICATION: PARTIAL — Missing tables: {missing}")
                    sys.exit(1)
            else:
                print(f"Unexpected response format: {tables}")
        except Exception as e:
            print(f"Failed to parse response: {e}")
            print(f"Raw text: {verify_response.text[:500]}")
    else:
        print(f"Verification FAILED: {verify_response.text}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("ALL DONE")
    print("=" * 60)

if __name__ == "__main__":
    main()
