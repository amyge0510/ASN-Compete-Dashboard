"""Orchestrates one pipeline run: ingest -> discover -> dedup -> analyze -> export."""
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from scraper.analyze import analyze
from scraper.config import SOURCES_PATH, load_analysis, load_sources
from scraper.export import deploy, site
from scraper.sources.article import enrich_items
from scraper.sources.course_catalog import ingest_catalog
from scraper.sources.discovery import discover
from scraper.sources.google_skills import ingest_google_skills
from scraper.sources.rss import ingest_feed
from scraper.sources.sitemap import ingest_sitemap
from scraper.sources.skill_builder import ingest_skillbuilder
from scraper.store import Store, now_iso

logger = logging.getLogger(__name__)

# Catalog-style sources: snapshot a list of courses/pages, diff run-over-run.
_CATALOG_INGESTERS = {
    "course_catalog": ingest_catalog,          # CSS-selector scraping (+ optional render)
    "sitemap": ingest_sitemap,                 # XML sitemap URL-set diffing
    "google_skills_catalog": ingest_google_skills,  # skills.google embedded JSON
    "skillbuilder_catalog": ingest_skillbuilder,    # skillbuilder.aws public GraphQL
}


def load_config(path: str | Path = SOURCES_PATH) -> dict:
    """Back-compat alias — sources config. See scraper/config.py."""
    return load_sources(path)


def days_since_last_run(store: Store, floor: int = 1, ceiling: int = 90) -> int | None:
    """Whole days since the previous run, or None before the first run.

    The lookback window should track how often the pipeline actually runs: a
    weekly schedule wants 7 days, a run 4 days later wants 4. Clamped so a
    same-day re-run still asks for a usable window, and so a long outage
    (expired cache, paused schedule) cannot request a flood.
    """
    run = store.latest_run()
    if not run or not run[0]:
        return None
    last = _parse_published(run[0])
    if last is None:
        return None
    gap = (datetime.now(timezone.utc) - last).total_seconds() / 86400
    return max(floor, min(ceiling, int(gap) or floor))


def _publish_site() -> None:
    """Push site/ to the static host if configured; never fails the run."""
    try:
        deploy.publish(site.SITE_DIR)
    except Exception as e:
        logger.error("Site publish failed (site/ is still updated locally): %s", e)


def _finish(store: Store, run_at: str, run_started_at: str, items_scanned: int,
            net_new: int, insights: list[dict], summary: str) -> None:
    """Shared run epilogue — every exit path writes every sink, so the DB
    and site can't diverge based on which branch a run took."""
    store.record_run(items_scanned, net_new, len(insights), summary,
                     run_at=run_at)
    store.record_course_counts(run_at)
    site.export_site(store)
    _publish_site()


def run(skip_analysis: bool = False, skip_discovery: bool = False) -> None:
    store = Store()
    config = load_config()
    run_started_at = now_iso()

    # Layer 1a: configured sources (feeds + catalog snapshots). Catalog diffs
    # are computed read-only here and persisted only after analysis succeeds
    # (same crash-retry contract as filter_new_items/mark_items_seen).
    all_items = []
    added_courses = []
    removed_titles: dict[str, list[str]] = {}
    catalog_snapshots: list[tuple[str, list]] = []

    for source in config["sources"]:
        name, stype = source["name"], source["type"]
        try:
            if stype == "rss":
                items = ingest_feed(source)
                logger.info("[%s] %d feed items", name, len(items))
                all_items.extend(items)
                store.record_source_health(
                    name, stype, "ok" if items else "empty", len(items),
                    "" if items else "feed returned 0 items")
            elif stype in _CATALOG_INGESTERS:
                courses = _CATALOG_INGESTERS[stype](source)
                added, removed = store.diff_courses(name, courses, record=False)
                logger.info("[%s] %d courses (+%d new, -%d removed)",
                            name, len(courses), len(added), len(removed))
                catalog_snapshots.append((name, courses))
                added_courses.extend(added)
                if removed:
                    removed_titles[name] = removed
                store.record_source_health(
                    name, stype, "ok" if courses else "empty", len(courses),
                    "" if courses else "catalog returned 0 items")
            else:
                logger.warning("[%s] unknown source type %r", name, stype)
                store.record_source_health(name, stype, "failed", 0,
                                           f"unknown source type {stype!r}")
        except Exception as e:
            logger.error("[%s] ingestion failed: %s", name, e)
            store.record_source_health(name, stype, "failed", 0, str(e))
    store.prune_source_health({s["name"] for s in config["sources"]})

    # How far back this run should look: the gap since the previous run, so
    # the window tracks the actual cadence instead of a fixed guess.
    gap_days = days_since_last_run(store)
    if gap_days is not None:
        logger.info("Lookback window: %d day(s) since the previous run", gap_days)

    # Layer 1b: search-based discovery — finds updates posted outside the
    # configured feeds. Failures here never block the configured sources.
    if not skip_discovery:
        try:
            all_items.extend(discover(config, lookback_override=gap_days))
        except Exception as e:
            logger.error("Discovery failed: %s", e)

    # Knowledge-base dedup: this is what stops old news being reported as new.
    new_items = store.filter_new_items(all_items)
    logger.info("Net-new items since last run: %d", len(new_items))

    def apply_snapshots() -> None:
        for name, courses in catalog_snapshots:
            store.diff_courses(name, courses)  # record=True persists

    if not (new_items or added_courses or removed_titles):
        logger.info("No changes since last run — nothing to analyze.")
        apply_snapshots()  # refreshes last_seen_at; no change rows by definition
        _finish(store, now_iso(), run_started_at, len(all_items), 0, [],
                "No changes since last run.")
        return

    if skip_analysis:
        logger.info("Skipping analysis (--no-analyze); knowledge base updated.")
        apply_snapshots()
        store.mark_items_seen(new_items)
        _finish(store, now_iso(), run_started_at, len(all_items),
                len(new_items), [], "Analysis skipped (--no-analyze).")
        return

    # Recency guard: dedup answers "have I seen this?", not "is this still
    # news?". A feed that back-fills old posts, or a discovery result from
    # last year, is net-new to the knowledge base but stale to a reader.
    # Stale items are still marked seen below, so they never come back.
    analysis_config = load_analysis()
    max_age = analysis_config.get("max_age_days")
    if max_age == "auto":
        # Track the run cadence, plus a grace buffer: feeds sometimes publish
        # an item days after its stated date, and a window with no slack
        # would silently drop those.
        grace = analysis_config.get("max_age_grace_days", 3)
        max_age = (gap_days + grace) if gap_days is not None else None
        if max_age:
            logger.info("Recency window: %d day(s) (run gap + %d grace)",
                        max_age, grace)
    items_to_analyze = new_items
    if max_age:
        items_to_analyze = [i for i in new_items if not is_stale(i.published, max_age)]
        skipped = len(new_items) - len(items_to_analyze)
        if skipped:
            logger.info("Recency filter: %d of %d net-new items are older than "
                        "%d days — recorded but not analyzed",
                        skipped, len(new_items), max_age)

    # Fetch full article bodies for better analysis (best-effort).
    enrich_items(items_to_analyze)

    # Layer 2: LLM interpretation of net-new changes only.
    result = analyze(items_to_analyze, added_courses, removed_titles, analysis_config)
    insights = result["insights"]

    # Analysis succeeded — only now persist this run's state, insights first:
    # if a write fails midway, nothing has been marked consumed yet, so the
    # next run re-analyzes instead of silently losing the batch (the unique
    # indexes make re-recorded insights idempotent).
    run_at = store.record_insights(insights)
    apply_snapshots()
    store.mark_items_seen(new_items)

    print(f"\nRun summary: {result['weekly_summary']}\n")
    for i in insights:
        print(f"  [{i['significance'].upper()}] {i['competitor']}: {i['headline']}")

    # Layer 3: site (primary output).
    _finish(store, run_at, run_started_at, len(all_items), len(new_items),
            insights, result["weekly_summary"])
    logger.info("Exported %d insights — site at %s/index.html.",
                len(insights), site.SITE_DIR)


def check_sources() -> int:
    """Fetch every configured source and report what it yields.

    Run this after editing sources.yaml (feed URLs move around). Returns the
    number of failing sources, so it can gate CI.
    """
    config = load_config()
    failures = 0
    for source in config["sources"]:
        try:
            if source["type"] == "rss":
                count = len(ingest_feed(source))
                status = f"OK — {count} items" if count else "EMPTY — verify URL"
            elif source["type"] in _CATALOG_INGESTERS:
                count = len(_CATALOG_INGESTERS[source["type"]](source))
                status = f"OK — {count} courses" if count else \
                    "EMPTY — verify URL/selector (JS-rendered? set render: true)"
            else:
                status = f"UNKNOWN TYPE {source['type']!r}"
            if "EMPTY" in status or "UNKNOWN" in status:
                failures += 1
        except Exception as e:
            status = f"FAILED — {e}"
            failures += 1
        print(f"  {source['name']:<32} {status}")
    print(f"\n{len(config['sources']) - failures}/{len(config['sources'])} sources healthy")
    return failures


def _parse_published(published: str):
    """Best-effort feed-date parse (RFC 2822 or ISO); None if unparseable."""
    if not published:
        return None
    try:
        parsed = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_stale(published: str, days: int) -> bool:
    """True only when the date parses AND is older than `days`.

    Deliberately lenient: undated items are kept, because we cannot prove
    they are old and dropping them would silently lose discovery results,
    which frequently carry no publication date.
    """
    parsed = _parse_published(published)
    return parsed is not None and parsed < datetime.now(timezone.utc) - timedelta(days=days)


def published_within(published: str, days: int) -> bool:
    """Best-effort feed-date parse (RFC 2822 or ISO); unknown dates excluded."""
    if not published:
        return False
    parsed = None
    try:
        parsed = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= datetime.now(timezone.utc) - timedelta(days=days)


def backfill(days: int = 45, force: bool = False) -> None:
    """Cold-start fix: analyze the last N days of feed items that the seed
    marked as baseline, so competitor pages open with history instead of
    starting empty.

    Unlike a normal run this ignores the seen-items flag — that is the whole
    point, since the seed marks items seen without analyzing them. It does
    skip items that have already produced an insight, so re-running is
    idempotent and never re-reports the same window as fresh news.

    `force=True` re-analyzes everything in the window regardless. Use it when
    deliberately re-testing an analysis.yaml change; expect duplicate
    insights on the dashboard afterwards."""
    store = Store()
    config = load_config()
    run_started_at = now_iso()

    already_reported = set() if force else store.urls_with_insights()
    items = []
    for source in config["sources"]:
        if source["type"] != "rss":
            continue
        try:
            recent = [i for i in ingest_feed(source)
                      if published_within(i.published, days)
                      and i.url not in already_reported]
            logger.info("[backfill:%s] %d items within %d days",
                        source["name"], len(recent), days)
            items.extend(recent)
        except Exception as e:
            logger.error("[backfill:%s] failed: %s", source["name"], e)

    if not items:
        logger.info("Backfill found nothing new in the window — every dated "
                    "item has already been reported. Use --force to "
                    "re-analyze anyway.")
        return

    logger.info("Backfill: analyzing %d items from the last %d days",
                len(items), days)
    enrich_items(items)
    result = analyze(items, [], {}, load_analysis())
    insights = result["insights"]
    run_at = store.record_insights(insights)
    store.mark_items_seen(items)

    print(f"\nBackfill summary: {result['weekly_summary']}\n")
    for i in insights:
        print(f"  [{i['significance'].upper()}] {i['competitor']}: {i['headline']}")

    _finish(store, run_at, run_started_at, len(items), len(items), insights,
            f"Backfill of the last {days} days: " + result["weekly_summary"])
