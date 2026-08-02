"""Diff competitor catalogs published as XML sitemaps.

Some platforms expose no feed but publish every course/module page in a
sitemap with lastmod dates (OpenAI Academy's content sitemap, Trailhead's
content_sitemap.xml with ~8,600 module/trail URLs). Snapshotting the URL set
and diffing it run-over-run detects added and removed content the same way
course_catalog does — each sitemap URL becomes a Course keyed on its address.

Handles both <urlset> documents and one level of <sitemapindex> indirection
(Trailhead intermittently serves its index where a child sitemap is expected).
"""
import logging
import xml.etree.ElementTree as ET

from scraper.fetch import fetch
from scraper.sources.course_catalog import Course

logger = logging.getLogger(__name__)

# Trailhead's content sitemap alone is ~18 MB.
MAX_SITEMAP_BYTES = 40 * 1024 * 1024
MAX_CHILD_SITEMAPS = 10


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(raw_xml: str) -> tuple[str, list[str]]:
    """Parse a sitemap document.

    Returns ("urlset", [page urls]) or ("index", [child sitemap urls]).
    """
    root = ET.fromstring(raw_xml)
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise ValueError(f"Not a sitemap document (root element: {kind})")

    locs = []
    for elem in root.iter():
        if _local(elem.tag) != "loc":
            continue
        if elem.text and elem.text.strip():
            locs.append(elem.text.strip())
    return ("urlset" if kind == "urlset" else "index"), locs


def title_from_url(url: str) -> str:
    """Humanize a URL slug: .../modules/quip-for-service-cloud -> 'Quip For Service Cloud'."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    words = slug.replace("-", " ").replace("_", " ").split()
    return " ".join(w.capitalize() for w in words) or url


def ingest_sitemap(source: dict) -> list[Course]:
    """Fetch a sitemap source (dict from sources.yaml) and return its pages
    as Course entries.

    Optional keys: `url_filter` — substring (or list of substrings) a page URL
    must contain, e.g. "/content/learn/" to keep Trailhead modules/trails and
    drop marketing pages.
    """
    kind, locs = parse_sitemap(fetch(source["url"], max_bytes=MAX_SITEMAP_BYTES))

    if kind == "index":
        pages = []
        for child_url in locs[:MAX_CHILD_SITEMAPS]:
            if child_url.rstrip("/") == source["url"].rstrip("/"):
                continue  # some indexes list themselves
            try:
                child_kind, child_locs = parse_sitemap(
                    fetch(child_url, max_bytes=MAX_SITEMAP_BYTES))
                if child_kind == "urlset":
                    pages.extend(child_locs)
            except Exception as e:
                logger.warning("[%s] child sitemap %s failed: %s",
                               source["name"], child_url, e)
        locs = pages

    url_filter = source.get("url_filter") or []
    if isinstance(url_filter, str):
        url_filter = [url_filter]
    if url_filter:
        locs = [u for u in locs if any(f in u for f in url_filter)]

    seen = set()
    courses = []
    for u in locs:
        if u in seen:
            continue
        seen.add(u)
        courses.append(
            Course(
                source=source["name"],
                competitor=source.get("competitor", ""),
                course_id=u,
                title=title_from_url(u),
            )
        )
    if not courses:
        logger.warning("[%s] sitemap yielded 0 pages — verify the URL "
                       "and url_filter", source["name"])
    return courses
