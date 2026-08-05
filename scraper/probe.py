"""Can this URL be monitored with the pipeline as it stands?

Answers the question a non-engineer actually has when they find a competitor
page: "do I just paste this in, or does someone need to write code?"

Fetches the URL once and reports one of:
  READY     — drop it straight into sources.yaml, with the type to use
  SELECTOR  — server-rendered list; needs a CSS item_selector chosen by hand
  CUSTOM    — JavaScript-rendered or API-backed; needs an engineer
  BLOCKED   — the site refuses automated access; no config will fix it

It also looks for a declared RSS feed and a sitemap, because the best outcome
is usually "don't scrape that page, use the feed it links to."
"""
import logging
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from scraper.fetch import fetch

logger = logging.getLogger(__name__)

# Below this, a page is a landing page rather than a catalog listing.
LIST_LINK_THRESHOLD = 25
# A page whose body has almost no text but plenty of script tags is a shell
# that assembles itself in the browser.
SPA_TEXT_FLOOR = 600


def _looks_like_feed(body: str) -> str | None:
    head = body[:2000].lower()
    if "<rss" in head or "<feed" in head:
        return "rss"
    if "<urlset" in head or "<sitemapindex" in head:
        return "sitemap"
    return None


def _declared_feeds(soup: BeautifulSoup, base: str) -> list[str]:
    return [urljoin(base, link["href"]) for link in soup.find_all("link", href=True)
            if "rss" in (link.get("type") or "").lower()
            or "atom" in (link.get("type") or "").lower()]


def probe(url: str) -> tuple[str, list[str]]:
    """Return (verdict, human-readable notes)."""
    notes: list[str] = []
    try:
        body = fetch(url)
    except Exception as e:
        msg = str(e)
        # Distinguish "the site said no" from "we could not get there at all".
        # A proxy or DNS failure can contain a status code in its text, so
        # match the requests-style phrasing rather than a bare number.
        refused = re.search(r"\b(40[13]|429) Client Error", msg)
        if refused:
            return "BLOCKED", [
                f"The site refused the request (HTTP {refused.group(1)}).",
                "It blocks automated access. No sources.yaml setting fixes this.",
                "Look for an RSS feed, a sitemap, or an official API instead.",
            ]
        return "UNREACHABLE", [
            f"Could not reach it: {msg[:200]}",
            "This may be the network you are running from rather than the "
            "site. Check the URL opens in a browser, then try again.",
        ]

    kind = _looks_like_feed(body)
    if kind == "rss":
        count = len(re.findall(r"<item[ >]|<entry[ >]", body, re.I))
        return "READY", [
            f"This is an RSS/Atom feed with about {count} items.",
            "Add it with `type: rss`. Nothing else needed.",
        ]
    if kind == "sitemap":
        count = body.lower().count("<loc>")
        return "READY", [
            f"This is an XML sitemap with about {count} URLs.",
            "Add it with `type: sitemap`. Consider `url_filter` to keep only "
            "the catalog paths.",
        ]

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(("script", "style")):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    links = [a for a in soup.find_all("a", href=True)]
    scripts = len(re.findall(r"<script", body, re.I))

    feeds = _declared_feeds(BeautifulSoup(body, "html.parser"), url)
    if feeds:
        notes.append("This page declares a feed — prefer it over scraping:")
        notes += [f"    {f}  (add with `type: rss`)" for f in feeds[:3]]

    host = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    try:
        sitemap = fetch(f"{host}/sitemap.xml")
        if _looks_like_feed(sitemap) == "sitemap":
            notes.append(f"A sitemap exists at {host}/sitemap.xml — often a "
                         "better catalog surface than the page itself.")
    except Exception:
        pass

    if feeds:
        return "READY", notes

    if len(text) < SPA_TEXT_FLOOR and scripts > 5:
        return "CUSTOM", notes + [
            f"The page has almost no text without JavaScript "
            f"({len(text)} characters, {scripts} scripts).",
            "It builds itself in the browser, so the pipeline sees an empty "
            "shell. This needs a custom reader — the AWS and Google catalog "
            "readers are examples, and each was a few hundred lines.",
        ]

    if len(links) < LIST_LINK_THRESHOLD:
        return "CUSTOM", notes + [
            f"Only {len(links)} links found — this looks like a marketing or "
            "landing page, not a catalog listing.",
            "Find the page that actually lists the courses and probe that "
            "instead.",
        ]

    return "SELECTOR", notes + [
        f"Server-rendered with {len(links)} links, so the content is readable.",
        "Add it with `type: course_catalog` plus an `item_selector` that "
        "matches ONLY course links.",
        "Without a good selector you will capture navigation and footer links "
        "and report them as new courses.",
        "After adding it, run the monitor once and read the first few item "
        "titles. If they say things like 'Contact Us', the selector is wrong.",
    ]


def report(url: str) -> int:
    """Print a plain-English verdict. Returns 0 if usable as-is."""
    verdict, notes = probe(url)
    headline = {
        "READY": "READY — works with the pipeline as it stands",
        "SELECTOR": "NEEDS A SELECTOR — usable, but someone must pick a CSS selector",
        "CUSTOM": "NEEDS CUSTOM CODE — not usable from config alone",
        "BLOCKED": "BLOCKED — the site refuses automated access",
        "UNREACHABLE": "UNREACHABLE — could not connect (may be your network)",
    }[verdict]
    print(f"\n{url}\n{headline}\n")
    for note in notes:
        print(f"  {note}")
    print()
    return 0 if verdict == "READY" else 1
