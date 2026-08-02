"""Google Skills (skills.google) catalog — the platform that absorbed
Google Cloud Skills Boost in 2025 (cloudskillsboost.google now redirects here).

The catalog page is server-rendered: each page embeds its results as JSON in
the pagedSearchResults attribute of a <ql-search-result-container> element,
including title, path, duration, level, and credential type — no headless
browser needed. The JSON also carries resultsCount/itemsPerPage, which bounds
the pagination loop (?page=N).
"""
import html
import json
import logging
import math
import re
from urllib.parse import urljoin, urlparse

from scraper.fetch import fetch
from scraper.sources.course_catalog import Course

logger = logging.getLogger(__name__)

# The attribute is single-quoted with HTML-entity-encoded JSON inside.
_ATTR_RE = re.compile(r"pagedSearchResults='([^']*)'", re.S)

MAX_PAGES = 1000  # runaway backstop only; the real bound is resultsCount
                  # (the catalog passed 180 pages in mid-2026 — do not lower)


def parse_catalog_page(raw_html: str) -> dict:
    """Extract the embedded search-results JSON from one catalog page.

    Returns the decoded object: {"searchResults": [...], "resultsCount": N,
    "itemsPerPage": N, ...}. Raises ValueError if the attribute is missing
    (layout change — surfaced by check-sources).
    """
    m = _ATTR_RE.search(raw_html)
    if not m:
        raise ValueError(
            "pagedSearchResults attribute not found — skills.google catalog "
            "layout may have changed")
    return json.loads(html.unescape(m.group(1)))


def _course_id(base_url: str, path: str) -> str:
    """Stable key: the course path with tracking query params stripped
    (path arrives as e.g. '/course_templates/630?catalog_rank=...')."""
    parsed = urlparse(path)
    return urljoin(base_url, parsed.path)


def ingest_google_skills(source: dict) -> list[Course]:
    """Fetch the full paginated catalog and return it as Course entries."""
    base_url = source["url"]
    seen_ids = set()
    courses = []
    total_pages = 1
    page = 1
    count = 0
    while page <= min(total_pages, source.get("max_pages", MAX_PAGES)):
        data = parse_catalog_page(fetch(f"{base_url}?page={page}"))
        results = data.get("searchResults") or []
        count = data.get("resultsCount") or 0
        # Fall back to the observed page size so a missing itemsPerPage field
        # degrades to full pagination instead of a silent single page.
        per_page = data.get("itemsPerPage") or len(results)
        if per_page:
            total_pages = math.ceil(count / per_page)
        if not results:
            break
        for r in results:
            path = r.get("path")
            title = r.get("title")
            if not path or not title:
                continue
            cid = _course_id(base_url, path)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            courses.append(
                Course(
                    source=source["name"],
                    competitor=source.get("competitor", ""),
                    course_id=cid,
                    title=title,
                )
            )
        page += 1

    if not courses:
        logger.warning("[%s] Google Skills catalog yielded 0 courses", source["name"])
    elif count and len(courses) < count * 0.9:
        logger.warning("[%s] collected %d of %d reported courses — pagination "
                       "may be capped or the layout changed",
                       source["name"], len(courses), count)
    else:
        logger.debug("[%s] %d courses across %d pages",
                     source["name"], len(courses), page - 1)
    return courses
