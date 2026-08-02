"""Ingest RSS 2.0 / Atom feeds using only the standard library.

(feedparser is avoided deliberately: its sgmllib3k dependency fails to build
on some Python 3.11+ environments, and RSS/Atom are simple enough to parse
directly.)
"""
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from scraper.fetch import fetch

logger = logging.getLogger(__name__)


@dataclass
class FeedItem:
    source: str
    competitor: str
    url: str
    title: str
    published: str  # as given by the feed; may be empty
    summary: str


def _local(tag: str) -> str:
    """Strip an XML namespace: '{http://...}entry' -> 'entry'."""
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, name: str) -> str:
    for child in elem:
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _atom_link(entry: ET.Element) -> str:
    fallback = ""
    for child in entry:
        if _local(child.tag) == "link":
            href = child.get("href", "")
            if child.get("rel") in (None, "alternate"):
                return href
            fallback = fallback or href
    return fallback


def parse_feed(raw_xml: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into [{url, title, published, summary}, ...]."""
    root = ET.fromstring(raw_xml)
    kind = _local(root.tag)

    entries = []
    if kind == "rss":
        for item in root.iter():
            if _local(item.tag) != "item":
                continue
            entries.append({
                "url": _child_text(item, "link"),
                "title": _child_text(item, "title"),
                "published": _child_text(item, "pubDate") or _child_text(item, "date"),
                "summary": _child_text(item, "description"),
            })
    elif kind == "feed":  # Atom
        for entry in root.iter():
            if _local(entry.tag) != "entry":
                continue
            entries.append({
                "url": _atom_link(entry),
                "title": _child_text(entry, "title"),
                "published": _child_text(entry, "published") or _child_text(entry, "updated"),
                "summary": _child_text(entry, "summary") or _child_text(entry, "content"),
            })
    else:
        raise ValueError(f"Not an RSS/Atom document (root element: {kind})")
    return entries


def ingest_feed(source: dict) -> list[FeedItem]:
    """Fetch one RSS source (dict from sources.yaml) and return its items."""
    raw = fetch(source["url"])
    entries = parse_feed(raw)
    if not entries:
        logger.warning("[%s] feed parsed but contained no entries — "
                       "check the URL with `python -m scraper check-sources`",
                       source["name"])
    return [
        FeedItem(
            source=source["name"],
            competitor=source.get("competitor", ""),
            url=e["url"],
            title=e["title"],
            published=e["published"],
            summary=e["summary"],
        )
        for e in entries
        if e["url"] and e["title"]
    ]
