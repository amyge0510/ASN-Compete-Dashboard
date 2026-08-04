"""Fetch and extract the main text of announcement pages.

Feed summaries are often a one-line teaser; the analysis layer produces much
better insights when it sees the full post body. Extraction is best-effort —
a failed fetch leaves the item's feed summary in place.
"""
import logging
import re

from bs4 import BeautifulSoup

from scraper.fetch import fetch

logger = logging.getLogger(__name__)

MAX_ARTICLE_CHARS = 6000
_NOISE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form")

# Discovery prepends "[source: name (quality)] " to its summaries; enrichment
# must not destroy that provenance when it swaps in the article body.
_PROVENANCE_RE = re.compile(r"^\[source:[^\]]*\]\s*")


def extract_main_text(html: str, max_chars: int = MAX_ARTICLE_CHARS) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    node = soup.find("article") or soup.find("main") or soup.body or soup
    text = " ".join(node.get_text(" ", strip=True).split())
    return text[:max_chars]


def enrich_items(items: list) -> None:
    """Replace each item's summary with the full article text, best-effort."""
    for item in items:
        try:
            text = extract_main_text(fetch(item.url))
        except Exception as e:
            logger.warning("Could not fetch article body for %s: %s", item.url, e)
            continue
        # Keep the feed summary if extraction produced nothing substantial.
        if len(text) > max(200, len(item.summary or "")):
            provenance = _PROVENANCE_RE.match(item.summary or "")
            item.summary = (provenance.group(0) if provenance else "") + text
