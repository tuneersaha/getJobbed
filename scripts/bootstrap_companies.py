#!/usr/bin/env python3
"""
Bootstrap the companies table from:
  1. data/india_companies.json          — curated India ATS slugs (hot/warm)
  2. Feashliaa/job-board-aggregator     — 7 ATS sources:
       greenhouse, lever, ashby, bamboohr, icims, paylocity, workday
  3. remoteintech/remote-jobs           — remote company career URLs (parse slugs)
  4. poteto/hiring-without-whiteboards  — career page URLs (parse slugs)

Safe to rerun — all inserts use ON CONFLICT (ats_type, ats_slug) DO NOTHING.

Workday slug format: "company|wd_num|site_id" (pipe-delimited) stored verbatim.
Fetcher parses at runtime: company.wd_num.myworkdayjobs.com/wday/cxs/company/site_id/jobs

Usage:
    python scripts/bootstrap_companies.py

Requires DATABASE_URL in env or .env file.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psycopg

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ─── Config ───────────────────────────────────────────────────────────────────

DB_URL = os.environ["DATABASE_URL"]
DATA_DIR = Path(__file__).parent.parent / "data"

FEASHLIAA_BASE = "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data"
FEASHLIAA_FILES = {
    "greenhouse": f"{FEASHLIAA_BASE}/greenhouse_companies.json",
    "lever":      f"{FEASHLIAA_BASE}/lever_companies.json",
    "ashby":      f"{FEASHLIAA_BASE}/ashby_companies.json",
    "bamboohr":   f"{FEASHLIAA_BASE}/bamboohr_companies.json",
    "icims":      f"{FEASHLIAA_BASE}/icims_companies.json",
    "paylocity":  f"{FEASHLIAA_BASE}/paylocity_companies_clean.json",
    "workday":    f"{FEASHLIAA_BASE}/workday_companies.json",
}

REMOTEINTECH_URL = "https://raw.githubusercontent.com/remoteintech/remote-jobs/main/data/remote-companies.json"
HIRING_WITHOUT_WB_URL = "https://raw.githubusercontent.com/poteto/hiring-without-whiteboards/main/data/companies.json"

# Standard slug validation: alphanumeric + hyphens, no leading/trailing dash
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$')

# Workday: "company|wd_num|site_id" — each component validated separately
WORKDAY_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]*$')   # company + site_id parts
WORKDAY_WD_RE   = re.compile(r'^wd\d+$')                  # wd1, wd3, wd503, etc.
WORKDAY_SITE_RE = re.compile(r'^[a-z0-9][a-z0-9_\-]*$')  # site_id allows underscores

# Paylocity: UUID slug (32-hex + dashes)
PAYLOCITY_GUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)

# Career URL domain → (ats_type, slug_extractor)
ATS_DOMAIN_MAP = {
    "boards.greenhouse.io": ("greenhouse", lambda p, _: p.strip("/").split("/")[0] if p else None),
    "jobs.lever.co":        ("lever",      lambda p, _: p.strip("/").split("/")[0] if p else None),
    "jobs.ashbyhq.com":     ("ashby",      lambda p, _: p.strip("/").split("/")[0] if p else None),
}
# Subdomain-based ATSes (handled separately in extract_ats_from_url)
BAMBOOHR_RE = re.compile(r'^([a-z0-9][a-z0-9\-]*)\.bamboohr\.com$')
ICIMS_RE    = re.compile(r'^careers-([a-z0-9][a-z0-9\-]*)\.icims\.com$')


# ─── Helpers ──────────────────────────────────────────────────────────────────

def valid_slug(slug: str | None) -> str | None:
    """Validate standard ATS slug (greenhouse/lever/ashby/bamboohr/icims)."""
    if not slug:
        return None
    slug = slug.lower().strip()
    if len(slug) < 2:
        return None
    return slug if SLUG_RE.match(slug) else None


def valid_workday_slug(raw: str) -> str | None:
    """
    Validate and normalise a Workday pipe-slug.
    Input: "company|wd_num|site_id"
    Returns the normalised slug or None if invalid.
    """
    parts = raw.strip().split("|")
    if len(parts) != 3:
        return None
    company, wd_num, site_id = parts
    company = company.lower()
    wd_num  = wd_num.lower()
    site_id = site_id.lower()
    if not WORKDAY_SLUG_RE.match(company):
        return None
    if not WORKDAY_WD_RE.match(wd_num):
        return None
    if not WORKDAY_SITE_RE.match(site_id):
        return None
    return f"{company}|{wd_num}|{site_id}"


def valid_paylocity_guid(guid: str | None) -> str | None:
    if not guid:
        return None
    guid = guid.lower().strip()
    return guid if PAYLOCITY_GUID_RE.match(guid) else None


def extract_ats_from_url(url: str) -> tuple[str, str] | None:
    """
    Given a career page URL, return (ats_type, slug) or None.
    Handles: greenhouse, lever, ashby (path-based)
             bamboohr, icims (subdomain-based)
    """
    try:
        parsed = urlparse(url.lower())
        netloc = parsed.netloc

        # Path-based ATSes
        for domain, (ats_type, extract) in ATS_DOMAIN_MAP.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                slug = extract(parsed.path, netloc)
                slug = valid_slug(slug)
                if slug:
                    return (ats_type, slug)

        # BambooHR: {slug}.bamboohr.com
        m = BAMBOOHR_RE.match(netloc)
        if m:
            slug = valid_slug(m.group(1))
            if slug:
                return ("bamboohr", slug)

        # iCIMS: careers-{slug}.icims.com
        m = ICIMS_RE.match(netloc)
        if m:
            slug = valid_slug(m.group(1))
            if slug:
                return ("icims", slug)

    except Exception:
        pass
    return None


def fetch_json(url: str, retries: int = 3) -> list | dict | None:
    for attempt in range(retries):
        try:
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  WARN: failed to fetch {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
    return None


def upsert_companies(conn, rows: list[dict]) -> tuple[int, int]:
    """
    Upsert rows into companies table. Returns (inserted, skipped).
    Each row: {ats_type, ats_slug, name?, priority?}
    """
    inserted = 0
    skipped = 0
    for row in rows:
        slug = row.get("ats_slug")
        ats_type = row.get("ats_type")
        if not slug or not ats_type:
            skipped += 1
            continue

        result = conn.execute(
            """
            INSERT INTO companies (name, ats_type, ats_slug, priority)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (ats_type, ats_slug) DO NOTHING
            """,
            (
                row.get("name"),
                ats_type,
                slug,
                row.get("priority", "cold"),
            ),
        )
        if result.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    return inserted, skipped


# ─── Sources ──────────────────────────────────────────────────────────────────

def load_india_companies() -> list[dict]:
    path = DATA_DIR / "india_companies.json"
    with open(path) as f:
        return json.load(f)


def fetch_feashliaa(ats_type: str, url: str) -> list[dict]:
    data = fetch_json(url)
    if not data:
        return []

    rows = []
    for item in data:
        if ats_type == "workday":
            # "company|wd_num|site_id" pipe-delimited string
            if isinstance(item, str):
                slug = valid_workday_slug(item)
                if slug:
                    rows.append({"ats_type": ats_type, "ats_slug": slug, "priority": "cold"})

        elif ats_type == "paylocity":
            # {"guid": "uuid", "name": "...", "jobs": N}
            if isinstance(item, dict):
                guid = valid_paylocity_guid(item.get("guid"))
                if guid:
                    rows.append({
                        "ats_type": ats_type,
                        "ats_slug": guid,
                        "name": item.get("name"),
                        "priority": "cold",
                    })

        elif isinstance(item, str):
            # Standard: greenhouse, lever, ashby, bamboohr, icims — array of slugs
            slug = valid_slug(item)
            if slug:
                rows.append({"ats_type": ats_type, "ats_slug": slug, "priority": "cold"})

        elif isinstance(item, dict):
            # Fallback dict format
            slug = item.get("slug") or item.get("id") or item.get("name", "").lower()
            name = item.get("name") or item.get("company")
            slug = valid_slug(slug)
            if slug:
                rows.append({"ats_type": ats_type, "ats_slug": slug, "name": name, "priority": "cold"})

    return rows


def fetch_remoteintech() -> list[dict]:
    data = fetch_json(REMOTEINTECH_URL)
    if not data:
        return []

    rows = []
    items = data if isinstance(data, list) else data.get("companies", [])
    for item in items:
        career_url = item.get("careerPage") or item.get("career_page") or ""
        name = item.get("company") or item.get("name") or ""
        result = extract_ats_from_url(career_url)
        if result:
            ats_type, slug = result
            rows.append({"ats_type": ats_type, "ats_slug": slug, "name": name, "priority": "cold"})
    return rows


def fetch_hiring_without_whiteboards() -> list[dict]:
    data = fetch_json(HIRING_WITHOUT_WB_URL)
    if not data:
        return []

    rows = []
    items = data if isinstance(data, list) else []
    for item in items:
        career_url = item.get("careerPage") or item.get("url") or ""
        name = item.get("name") or item.get("company") or ""
        result = extract_ats_from_url(career_url)
        if result:
            ats_type, slug = result
            rows.append({"ats_type": ats_type, "ats_slug": slug, "name": name, "priority": "cold"})
    return rows


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("GetJobbed — company bootstrap")
    print(f"DB: {DB_URL.split('@')[-1]}")  # host/db only, no credentials
    print()

    with psycopg.connect(DB_URL, prepare_threshold=0) as conn:
        conn.autocommit = False

        # 1. India companies (hot/warm — highest priority)
        print("Loading data/india_companies.json...", end=" ", flush=True)
        rows = load_india_companies()
        ins, skip = upsert_companies(conn, rows)
        print(f"{ins} inserted, {skip} skipped")

        # 2. Feashliaa — 7 ATS sources
        for ats_type, url in FEASHLIAA_FILES.items():
            print(f"Fetching feashliaa/{ats_type}...", end=" ", flush=True)
            rows = fetch_feashliaa(ats_type, url)
            ins, skip = upsert_companies(conn, rows)
            print(f"{ins} inserted, {skip} skipped")

        # 3. remoteintech (greenhouse/lever/ashby/bamboohr/icims career URLs)
        print("Fetching remoteintech/remote-jobs...", end=" ", flush=True)
        rows = fetch_remoteintech()
        ins, skip = upsert_companies(conn, rows)
        print(f"{ins} inserted, {skip} skipped")

        # 4. hiring-without-whiteboards
        print("Fetching poteto/hiring-without-whiteboards...", end=" ", flush=True)
        rows = fetch_hiring_without_whiteboards()
        ins, skip = upsert_companies(conn, rows)
        print(f"{ins} inserted, {skip} skipped")

        conn.commit()

    # Summary count
    with psycopg.connect(DB_URL, prepare_threshold=0) as conn:
        total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        by_type = conn.execute(
            "SELECT ats_type, COUNT(*) FROM companies GROUP BY ats_type ORDER BY COUNT(*) DESC"
        ).fetchall()

    print(f"\nDone. Total companies: {total}")
    for ats_type, count in by_type:
        print(f"  {ats_type:<12} {count:>5}")


if __name__ == "__main__":
    main()
