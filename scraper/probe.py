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
import os
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from scraper.fetch import fetch

logger = logging.getLogger(__name__)

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


def _already_configured(url: str) -> tuple[str, str] | None:
    """(name, type) of an existing source with the same URL, if any."""
    try:
        from scraper.store import normalize_url
        from scraper.config import load_sources
        target = normalize_url(url)
        for src in load_sources().get("sources") or []:
            if src.get("url") and normalize_url(src["url"]) == target:
                return src.get("name", "?"), src.get("type", "?")
    except Exception:
        pass  # probing must still work outside a configured checkout
    return None


def probe(url: str) -> tuple[str, list[str]]:
    """Return (verdict, human-readable notes)."""
    notes: list[str] = []

    # Cheapest possible answer: it is already being monitored. Adding it twice
    # gives two sources snapshotting one page, which double-reports catalogue
    # changes and is tedious to unpick later.
    existing = _already_configured(url)
    if existing:
        name, stype = existing
        return "ALREADY CONFIGURED", [
            f"This URL is already a source: `{name}` (type: {stype}).",
            "Nothing to do. Check the dashboard's Source health tab to see "
            "whether it is working.",
        ]

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

    # Whether a page is a catalogue is decided by finding a coherent group of
    # item links, not by counting links overall — a short but real catalogue
    # (a dozen courses) would otherwise be dismissed as a landing page.
    candidates = selector_candidates(body, url)
    if not any(c["confident"] for c in candidates):
        detail = [
            f"{len(links)} links found, but none form a group that is clearly "
            "a list of courses rather than navigation.",
            "This is probably a marketing or landing page. Find the page that "
            "actually lists the courses and test that instead.",
        ]
        if candidates:
            detail.append("The closest groups, rejected as too small or too "
                          "navigation-like:")
            for c in candidates[:2]:
                detail.append(f"    {c['selector']}  ({c['matches']} links)")
                for sample in c["samples"][:3]:
                    detail.append(f"        e.g. {sample}")
        return "CUSTOM", notes + detail

    notes.append(f"Server-rendered with {len(links)} links, so the content is "
                 "readable. Add it with `type: course_catalog` plus an "
                 "`item_selector`.")
    if candidates:
        notes.append("Suggested selectors, best first — CHECK THE EXAMPLES. "
                     "If they are menu items rather than courses, the "
                     "selector is wrong:")
        for c in candidates:
            if not c["confident"]:
                continue
            notes.append(f"    {c['selector']}  ({c['matches']} links)")
            for sample in c["samples"]:
                notes.append(f"        e.g. {sample}")
    else:
        notes.append("No obvious selector found — the course links do not "
                     "share a URL pattern. Someone will need to inspect the "
                     "page by hand.")
    return "SELECTOR", notes


# Chrome around the content. Links inside these are navigation, not items,
# and dropping them removes most of the noise before any scoring happens.
_CHROME_TAGS = ("nav", "header", "footer", "aside", "form")
# Course titles read like sentences; nav labels are one or two words.
_MIN_AVERAGE_TITLE_CHARS = 14
# Five links is not enough to distinguish a catalogue from a row of buttons.
_MIN_GROUP_SIZE = 8

# Calls to action and filter labels. These survive the chrome strip because
# sites put them in the body — "View full catalog", "All Courses", "Benefits"
# read like content but are navigation. A group full of them is not a
# catalogue, however long the strings are.
_NAV_TEXT = re.compile(
    r"^(view|see|browse|explore|discover|find|shop|start|get started|learn more"
    r"|read more|show|more|all|free|new|popular|featured|filter|sort|next"
    r"|previous|back|home|overview|benefits|pricing|contact|about|sign in"
    r"|sign up|log in|register|subscribe|download|watch|why|how|what|who)\b",
    re.I)
# A group is only trustworthy if few of its labels look navigational.
_MAX_NAV_FRACTION = 0.25
# Confident enough to hand someone a ready-made prompt. A little nav-looking
# text is tolerated: real course titles legitimately start with "How to" or
# "What is", and demanding zero would reject good catalogues.
_CONFIDENT_MIN_SIZE = 8
_CONFIDENT_MIN_TITLE_CHARS = 20
_CONFIDENT_MAX_NAV_FRACTION = 0.15


def _nav_fraction(titles: list[str]) -> float:
    return sum(1 for t in titles if _NAV_TEXT.match(t)) / len(titles)


def selector_candidates(body: str, page_url: str, top: int = 3) -> list[dict]:
    """Propose item_selector values, each with evidence.

    The probe has the page in hand, so it can do the selector analysis
    itself rather than asking a downstream assistant to re-fetch and guess.
    Links are grouped by the shape of their URL — the thing that stays
    stable across redesigns — and scored on whether the group looks like a
    list of items rather than a menu.
    """
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(_CHROME_TAGS):
        tag.decompose()

    page_host = (urlsplit(page_url).hostname or "").lower().removeprefix("www.")
    groups: dict[str, list[tuple[str, str]]] = {}
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        if not text:
            continue
        href = urljoin(page_url, a["href"])
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https"):
            continue
        host = (parts.hostname or "").lower().removeprefix("www.")
        segments = [seg for seg in parts.path.split("/") if seg]
        keys = []
        if host and host != page_host:
            # Courses hosted elsewhere (Skilljar, a learn. subdomain) — the
            # host itself is the cleanest possible selector.
            keys.append(host)
        for depth in (1, 2):
            if len(segments) > depth:
                keys.append("/" + "/".join(segments[:depth]) + "/")
        for key in keys:
            groups.setdefault(key, []).append((text, href))

    candidates = []
    for key, entries in groups.items():
        titles = list(dict.fromkeys(t for t, _ in entries))
        if len(titles) < _MIN_GROUP_SIZE:
            continue
        avg = sum(len(t) for t in titles) / len(titles)
        if avg < _MIN_AVERAGE_TITLE_CHARS:
            continue  # short labels: a menu, not a catalogue
        nav = _nav_fraction(titles)
        if nav > _MAX_NAV_FRACTION:
            continue  # mostly calls to action: a navigation cluster
        candidates.append({
            "selector": f"a[href*='{key}']",
            "matches": len(titles),
            "avg_title_chars": round(avg),
            "nav_fraction": round(nav, 2),
            "confident": (len(titles) >= _CONFIDENT_MIN_SIZE
                          and avg >= _CONFIDENT_MIN_TITLE_CHARS
                          and nav <= _CONFIDENT_MAX_NAV_FRACTION),
            "samples": titles[:5],
        })
    # Prefer longer titles, then bigger groups — both point at real content.
    candidates.sort(key=lambda c: (c["avg_title_chars"], c["matches"],
                                   len(c["selector"])), reverse=True)
    return candidates[:top]


def _suggested_name(url: str) -> str:
    """A plausible source name, e.g. nvidia-training, from the URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "source").replace("www.", "").split(".")[0]
    tail = [p for p in parts.path.strip("/").split("/") if p and "." not in p]
    slug = f"{host}-{tail[-1]}" if tail else host
    return re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")


def copilot_prompt(verdict: str, url: str, source_type: str | None = None,
                   feed_url: str | None = None,
                   selector: str | None = None) -> str | None:
    """A self-contained prompt to paste into GitHub Copilot Chat.

    The point is that the person adding a source should never have to know
    the YAML shape, which file the tiers live in, or that scraper/ is
    off-limits. The prompt carries all of that.
    """
    name = _suggested_name(url)
    target = feed_url or url
    common = (
        "Rules:\n"
        "- Match the formatting, indentation and comment style of the "
        "existing entries.\n"
        "- Also add the competitor's name to `medium_priority` under "
        "`competitors:` in config/analysis.yaml (use `high_priority` only for "
        "a direct compete target). analysis.yaml is the file the analyst "
        "prompt actually reads.\n"
        "- Do NOT modify anything under scraper/.\n"
        "- Show me the diff before applying it."
    )

    if verdict == "READY":
        stype = source_type or "rss"
        return (
            f"Add a new source to config/sources.yaml in this repo.\n\n"
            f"  name: {name}\n"
            f"  type: {stype}\n"
            f"  url: {target}\n"
            f"  competitor: <REPLACE WITH THE COMPETITOR'S NAME>\n\n"
            f"Add a short `notes:` line saying what this source covers.\n\n"
            f"{common}"
        )

    if verdict == "SELECTOR":
        selector = selector or "<CHOOSE A SELECTOR — see the suggestions above>"
        return (
            f"Add a new course-catalog source to config/sources.yaml in this "
            f"repo.\n\n"
            f"  name: {name}\n"
            f"  type: course_catalog\n"
            f"  url: {url}\n"
            f"  competitor: <REPLACE WITH THE COMPETITOR'S NAME>\n"
            f"  item_selector: \"{selector}\"\n\n"
            f"The selector was already worked out against the live page, so "
            f"use it as given — do not re-derive it.\n\n"
            f"Add a short `notes:` line saying what this source covers.\n\n"
            f"{common}"
        )
    return None


def _step_summary(url: str, headline: str, notes: list[str],
                  prompt: str | None, next_step: str) -> None:
    """Also write the verdict to the GitHub Actions run summary.

    Buried log output is no use to a non-technical owner — this puts the
    verdict and the copy-paste prompt on the run's front page.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## Can the pipeline read this URL?", "",
             f"`{url}`", "", f"### {headline}", ""]
    lines += [f"- {n.strip()}" for n in notes]
    lines += ["", f"**Next step:** {next_step}", ""]
    if prompt:
        lines += ["<details open><summary>Copy this into GitHub Copilot Chat"
                  "</summary>", "", "```text", prompt, "```", "", "</details>"]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def report(url: str) -> int:
    """Print a plain-English verdict. Returns 0 if usable as-is."""
    verdict, notes = probe(url)
    headline = {
        "READY": "READY — works with the pipeline as it stands",
        "SELECTOR": "NEEDS A SELECTOR — usable, but someone must pick a CSS selector",
        "CUSTOM": "NEEDS CUSTOM CODE — not usable from config alone",
        "BLOCKED": "BLOCKED — the site refuses automated access",
        "UNREACHABLE": "UNREACHABLE — could not connect (may be your network)",
        "ALREADY CONFIGURED": "ALREADY CONFIGURED — this URL is already being "
                              "monitored",
    }[verdict]
    print(f"\n{url}\n{headline}\n")
    for note in notes:
        print(f"  {note}")

    # Next step, spelled out. For the two workable verdicts that is a
    # ready-to-paste Copilot prompt; for the rest it is "stop here".
    stype = None
    feed = None
    for note in notes:
        if "RSS/Atom feed" in note:
            stype = "rss"
        elif "XML sitemap" in note:
            stype = "sitemap"
        elif note.strip().endswith("(add with `type: rss`)"):
            stype, feed = "rss", note.strip().split()[0]

    selector = None
    for note in notes:
        stripped = note.strip()
        if stripped.startswith("a[href*=") and selector is None:
            selector = stripped.split("  (")[0]
    prompt = copilot_prompt(verdict, url, stype, feed, selector)
    next_step = {
        "READY": "paste the prompt below into GitHub Copilot Chat.",
        "SELECTOR": "paste the prompt below into GitHub Copilot Chat.",
        "CUSTOM": "stop here — this page cannot be added from config alone. "
                  "Look for an RSS feed or sitemap on the same site and test "
                  "that instead.",
        "BLOCKED": "stop here — the site refuses automated access and no "
                   "config change will help. Look for an RSS feed or sitemap "
                   "instead.",
        "UNREACHABLE": "check the URL opens in a browser, then run this again.",
        "ALREADY CONFIGURED": "nothing to do — this source already exists.",
    }[verdict]
    _step_summary(url, headline, notes, prompt, next_step)

    print(f"\n  NEXT STEP: {next_step}\n")
    if prompt:
        print("=" * 70)
        print("Paste everything between the lines into GitHub Copilot Chat,")
        print("with this repository open. Replace the <REPLACE WITH ...>")
        print("placeholder first.")
        print("=" * 70)
        print(prompt)
        print("=" * 70 + "\n")
    return 0 if verdict == "READY" else 1
