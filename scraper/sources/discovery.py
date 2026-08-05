"""Search-based source discovery.

Tracked organizations post updates in inconsistent places (subdomains, blog
subcategories, newsrooms, ...). This layer runs a web search per target,
surfaces the top relevant results with generated summaries, and returns them
as candidate items. Candidates flow through the same knowledge-base dedup as
feed items, so re-discovered pages are never re-reported.

The researcher persona and the noun for what is being tracked come from
config/analysis.yaml; the queries come from the `discovery` block of
config/sources.yaml.

Two API calls per target:
  1. web-search call — the model searches and writes up its findings
     (structured output can't be combined with search citations, so prose)
  2. extraction call — structured output turns the findings into clean JSON
"""
import logging

from scraper import llm
from scraper.config import load_analysis
from scraper.sources.rss import FeedItem

logger = logging.getLogger(__name__)

MAX_SEARCHES_PER_TARGET = 8
RESULTS_PER_TARGET = 8

# Syndication mirrors: they rehost (and often 404) other outlets' articles.
# The prompt asks for the original URL instead; anything that still points at
# these is dropped.
DEFAULT_BLOCKED_DOMAINS = [
    "aol.com", "msn.com", "news.yahoo.com", "finance.yahoo.com",
]

CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "published": {
                        "type": "string",
                        "description": "Publication date if known, else empty string",
                    },
                    "summary": {"type": "string"},
                    "source_name": {
                        "type": "string",
                        "description": "Publisher, e.g. 'AWS Blog', 'TechCrunch'",
                    },
                    "source_quality": {
                        "type": "string",
                        "enum": ["official", "major_outlet", "trade_press", "other"],
                        "description": "official = the competitor's own site/blog; "
                                       "major_outlet = established news org; "
                                       "trade_press = credible industry publication; "
                                       "other = blogs, aggregators, unknown",
                    },
                },
                "required": ["url", "title", "published", "summary",
                             "source_name", "source_quality"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def _domain_blocked(url: str, blocked: list[str]) -> bool:
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return True
    return any(host == d or host.endswith("." + d) for d in blocked)


def _search(competitor: str, query: str, lookback_days: int,
            results_per_target: int, max_searches: int,
            persona: str, subject: str) -> str:
    """Build the research prompt and run one web-search-backed call.

    `persona` and `subject` come from config/analysis.yaml — this module
    carries no assumptions about what is being researched.
    """
    prompt = (
            f"{persona.strip()}\n\n"
            f"Search for updates about the {competitor} {subject} "
            f"from roughly the last {lookback_days} days: {query}\n\n"
            f"Surface up to {results_per_target} relevant results — product "
            "updates, launches, pricing changes, strategy "
            "shifts. For each, give the exact URL, title, publication date if "
            "visible, the publisher's name, and a 2-3 sentence summary of what "
            "it announces.\n\n"
            "Source quality matters more than volume:\n"
            "- Strongly prefer PRIMARY sources (the competitor's own blog, "
            "newsroom, docs, or product pages) and established outlets "
            "(Reuters, CNBC, TechCrunch, The Verge, ZDNet, credible trade "
            "press).\n"
            "- If a result is a syndicated copy (AOL, MSN, Yahoo rehosts), "
            "track down and report the ORIGINAL article's URL instead.\n"
            "- Skip SEO content farms, press-release aggregators, unsourced "
            "blogs, and anything you cannot attribute to a real publisher.\n"
            "- Skip marketing fluff and anything clearly older than the window."
    )
    return llm.web_research(prompt, max_searches=max_searches)


def _extract(findings: str) -> list[dict]:
    """Structured-output call: findings prose -> validated candidate JSON."""
    result = llm.structured(
        "Extract every distinct update candidate (with its exact URL) from "
        "these research findings:\n\n" + findings,
        CANDIDATES_SCHEMA, max_tokens=8000,
    )
    return result["candidates"]


def discover(config: dict, analysis: dict | None = None) -> list[FeedItem]:
    """Run discovery for every target in the config's `discovery` section.

    `analysis` is the loaded analysis.yaml; it supplies the researcher persona
    and the noun for what is being tracked.
    """
    settings = config.get("discovery") or {}
    if not settings.get("enabled"):
        return []

    analysis = analysis or load_analysis()
    persona = analysis["persona"]
    subject = analysis.get("subject", "product")

    lookback = settings.get("lookback_days", 45)
    results_per_target = settings.get("results_per_target", RESULTS_PER_TARGET)
    max_searches = settings.get("max_searches", MAX_SEARCHES_PER_TARGET)
    blocked = [d.lower() for d in
               settings.get("blocked_domains", DEFAULT_BLOCKED_DOMAINS)]
    dropped = 0
    items = []
    for target in settings.get("targets", []):
        competitor = target["competitor"]
        try:
            findings = _search(competitor, target["query"], lookback,
                               results_per_target, max_searches,
                               persona, subject)
            if not findings:
                continue
            for c in _extract(findings):
                if not c["url"]:
                    continue
                if _domain_blocked(c["url"], blocked):
                    dropped += 1
                    logger.debug("[discovery:%s] dropped blocked-domain "
                                 "candidate %s", competitor, c["url"])
                    continue
                # Carry provenance into the analysis layer via the summary.
                provenance = (f"[source: {c['source_name']} "
                              f"({c['source_quality']})] ")
                items.append(FeedItem(
                    source=f"discovery:{competitor.lower().replace(' ', '-')}",
                    competitor=competitor,
                    url=c["url"],
                    title=c["title"],
                    published=c["published"],
                    summary=provenance + c["summary"],
                ))
        except Exception as e:
            logger.error("[discovery:%s] failed: %s (cause: %r)",
                         competitor, e, getattr(e, "__cause__", None))
    logger.info("Discovery surfaced %d candidate items (%d dropped by "
                "domain blocklist)", len(items), dropped)
    return items
