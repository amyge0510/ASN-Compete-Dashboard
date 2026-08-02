"""Snapshot competitor course catalogs so the pipeline can diff them over time."""
import logging
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.fetch import fetch
from scraper.sources.render import fetch_rendered

logger = logging.getLogger(__name__)


@dataclass
class Course:
    source: str
    competitor: str
    course_id: str  # stable key — the course URL
    title: str


def ingest_catalog(source: dict) -> list[Course]:
    """Fetch a catalog page and extract its course list.

    Sources with `render: true` are fetched through a headless browser
    (needed for JS-rendered catalogs like AWS Skill Builder); the rest use
    the plain HTTP fetcher. Course links are located with the source's
    `item_selector` (CSS).
    """
    if source.get("render"):
        html = fetch_rendered(source["url"])
    else:
        html = fetch(source["url"])

    soup = BeautifulSoup(html, "html.parser")
    seen_ids = set()
    courses = []
    for a in soup.select(source.get("item_selector", "a")):
        href = a.get("href")
        title = a.get_text(strip=True)
        if not href or not title:
            continue
        course_id = urljoin(source["url"], href)
        if course_id in seen_ids:  # same course linked twice on the page
            continue
        seen_ids.add(course_id)
        courses.append(
            Course(
                source=source["name"],
                competitor=source.get("competitor", ""),
                course_id=course_id,
                title=title,
            )
        )
    if not courses:
        logger.warning(
            "[%s] catalog returned 0 courses — if this page is JS-rendered, "
            "set `render: true` on the source (requires Playwright)",
            source["name"],
        )
    return courses
