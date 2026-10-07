"""
nuke_data.py — wipe all transient data, keep companies + users + profiles + resumes.

Safe to run during testing. Companies table untouched so slug discovery persists.
Run: python scripts/nuke_data.py [--yes]
"""

import argparse
import os
import sys

import psycopg


def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe test data from GetJobbed DB")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL_TEST") or os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: set DATABASE_URL_TEST or DATABASE_URL")

    if not args.yes:
        print("This will DELETE (in order):")
        print("  tailored_resumes, user_job_matches, user_excluded_jobs")
        print("  jobs, job_queue, fetch_runs")
        print("Kept: users, user_profiles, user_resumes, companies")
        ans = input("Type YES to continue: ").strip()
        if ans != "YES":
            sys.exit("Aborted.")

    with psycopg.connect(db_url, autocommit=True) as conn:
        tables = [
            "tailored_resumes",
            "user_job_matches",
            "user_excluded_jobs",
            "jobs",
            "job_queue",
            "fetch_runs",
        ]
        for table in tables:
            conn.execute(f"TRUNCATE TABLE {table} CASCADE")
            print(f"  truncated {table}")

    print("Done. companies / users / profiles / resumes untouched.")


if __name__ == "__main__":
    main()
