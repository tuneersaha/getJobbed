"""
Shared data types and normalization helpers for all ATS fetchers.

NormalizedJob is the canonical shape passed from fetchers to the JobFetchWorker.
strip_html, extract_experience, detect_work_type, validate_apply_url are
called by every fetcher before returning.
"""

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


def strip_html(html: str) -> str:
    """Strip all HTML tags and decode entities. Returns plain text."""
    if not html:
        return ""
    return bleach.clean(html, tags=[], strip=True).strip()


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
    ]:
        m = pattern.search(url)
        if m:
            slug = m.group(1)
            if SLUG_RE.match(slug):
                return ats_type, slug
    return None
