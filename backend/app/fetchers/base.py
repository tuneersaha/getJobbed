"""
Shared data types and normalization helpers for all ATS fetchers.

NormalizedJob is the canonical shape passed from fetchers to the JobFetchWorker.
strip_html, extract_experience, detect_work_type, validate_apply_url are
called by every fetcher before returning.
"""

import html as _html
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime

import bleach

logger = logging.getLogger(__name__)

# Slug validation — must match before any DB/HTTP use (path-traversal prevention)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$")

# Detect experience requirements like "3-5 years", "2+ years", "5 years experience"
_EXP_RANGE = re.compile(r"(\d+)\s*[-–]\s*(\d+)\s*(?:years?|yrs?)", re.IGNORECASE)
_EXP_PLUS  = re.compile(r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)", re.IGNORECASE)
_EXP_SINGLE = re.compile(r"(\d+)\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)", re.IGNORECASE)

# Citizenship-requirement signals (very conservative — "must be authorized" etc.)
_CITIZENSHIP_RE = re.compile(
    r"(must\s+be\s+(a\s+)?us\s+citizen|us\s+citizen(ship)?\s+required"
    r"|security\s+clearance\s+required|work\s+authoriz(?:ed|ation)\s+required"
    r"|no\s+sponsorship|not\s+eligible\s+for\s+sponsorship)",
    re.IGNORECASE,
)

# ATS slug patterns in apply_url for self-expanding slug discovery
_GREENHOUSE_URL_RE = re.compile(r"boards\.greenhouse\.io/([a-z0-9][a-z0-9\-]+[a-z0-9])/jobs/")
_LEVER_URL_RE      = re.compile(r"jobs\.lever\.co/([a-z0-9][a-z0-9\-]+[a-z0-9])/")
_ASHBY_URL_RE      = re.compile(r"jobs\.ashbyhq\.com/([a-z0-9][a-z0-9\-]+[a-z0-9])/")
_BAMBOOHR_URL_RE   = re.compile(r"([a-z0-9][a-z0-9\-]+[a-z0-9])\.bamboohr\.com", re.IGNORECASE)
_ICIMS_URL_RE      = re.compile(r"careers-([a-z0-9][a-z0-9\-]+[a-z0-9])\.icims\.com", re.IGNORECASE)
# Paylocity slugs are UUIDs (hex + hyphens) — satisfy SLUG_RE
_PAYLOCITY_URL_RE  = re.compile(
    r"recruiting\.paylocity\.com/recruiting/jobs/(?:All/)?"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
# Workday slug = "company|wd_num|site_id" — two URL shapes:
#   API:   .../wday/cxs/{company}/{site_id}/jobs
#   Apply: .../{locale?}/{site_id}/job/...
_WORKDAY_BASE_RE  = re.compile(
    r"([a-z0-9][a-z0-9\-]*)\.([a-z0-9]+)\.myworkdayjobs\.com", re.IGNORECASE
)
_WORKDAY_API_RE   = re.compile(r"/wday/cxs/[a-z0-9][a-z0-9_\-]*/([a-z0-9][a-z0-9_\-]+)", re.IGNORECASE)
_WORKDAY_APPLY_RE = re.compile(r"/(?:[a-z]{2}-[A-Z]{2}/)?([a-z0-9][a-z0-9_\-]+)/(?:job|jobs)(?:/|$)", re.IGNORECASE)
_WORKDAY_WD_RE    = re.compile(r"^wd\d+$", re.IGNORECASE)
_WORKDAY_BLOCKLIST = frozenset({"wday", "cxs", "job", "jobs", "apply", "login", "redirect", "external"})


@dataclass
class NormalizedJob:
    external_id: str
    source: str               # matches job_source enum
    company_id: str | None    # UUID string or None
    company_name: str
    title: str
    description: str          # HTML stripped
    location: str | None
    work_type: str            # 'remote'|'hybrid'|'onsite'|'unknown'
    apply_url: str
    experience_min: int | None
    experience_max: int | None
    requires_foreign_citizenship: str  # 'not_required'|'required'|'unknown'
    posted_at: datetime | None


def strip_html(raw: str) -> str:
    """Strip all HTML tags and decode entities. Returns plain text."""
    if not raw:
        return ""
    # Unescape entities first (&lt; → <) so bleach can then strip the real tags
    unescaped = _html.unescape(raw)
    return bleach.clean(unescaped, tags=[], strip=True).strip()


def extract_experience(text: str) -> tuple[int | None, int | None]:
    """
    Parse experience requirements from job description text.
    Returns (min, max). Both may be None if nothing is found.
    """
    m = _EXP_RANGE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _EXP_PLUS.search(text)
    if m:
        n = int(m.group(1))
        return n, n + 3  # "3+ years" → (3, 6)
    m = _EXP_SINGLE.search(text)
    if m:
        n = int(m.group(1))
        return n, n
    return None, None


def detect_work_type(title: str, location: str | None, description: str) -> str:
    """Classify job as remote/hybrid/onsite/unknown from title + location + description snippet."""
    text = " ".join(filter(None, [title, location, description[:600]])).lower()
    if any(w in text for w in ["fully remote", "100% remote", "remote-first", "work from home", "wfh"]):
        return "remote"
    if "remote" in text and "hybrid" not in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(w in text for w in ["on-site", "onsite", "in office", "in-office", "on site"]):
        return "onsite"
    return "unknown"


def validate_apply_url(url: str) -> bool:
    """Only http(s):// URLs are valid. Blocks javascript:, data:, file://, etc."""
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def check_citizenship_requirement(description: str) -> str:
    """Scan description for US citizenship / clearance requirements."""
    if _CITIZENSHIP_RE.search(description):
        return "required"
    return "unknown"


def extract_ats_slug_from_url(url: str) -> tuple[str, str] | None:
    """
    Try to extract (ats_type, slug) from an apply_url.
    Returns None if the URL doesn't match a known ATS pattern.
    Used for self-expanding slug discovery.
    """
    for pattern, ats_type in [
        (_GREENHOUSE_URL_RE, "greenhouse"),
        (_LEVER_URL_RE, "lever"),
        (_ASHBY_URL_RE, "ashby"),
        (_BAMBOOHR_URL_RE, "bamboohr"),
        (_ICIMS_URL_RE, "icims"),
        (_PAYLOCITY_URL_RE, "paylocity"),
    ]:
        m = pattern.search(url)
        if m:
            slug = m.group(1).lower()
            if SLUG_RE.match(slug):
                return ats_type, slug

    # Workday — composite slug "company|wd_num|site_id"
    bm = _WORKDAY_BASE_RE.search(url)
    if bm:
        company = bm.group(1).lower()
        wd_num  = bm.group(2).lower()
        if _WORKDAY_WD_RE.match(wd_num):
            path = url[bm.end():]
            site_id: str | None = None
            # Try API path first: /wday/cxs/{company}/{site_id}/
            am = _WORKDAY_API_RE.search(path)
            if am:
                site_id = am.group(1)
            else:
                # Apply URL: /[en-US/]{site_id}/job/
                pm = _WORKDAY_APPLY_RE.search(path)
                if pm:
                    site_id = pm.group(1)
            if site_id and site_id.lower() not in _WORKDAY_BLOCKLIST and len(site_id) >= 2:
                return "workday", f"{company}|{wd_num}|{site_id}"

    return None
