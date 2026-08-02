"""Orchestrates one pipeline run: ingest -> discover -> dedup -> analyze -> export."""
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

from scraper.analyze import analyze
from scraper.export import deploy, powerbi, site
from scraper.sources.article import enrich_items
from scraper.sources.course_catalog import ingest_catalog
from scraper.sources.discovery import discover
from scraper.sources.google_skills import ingest_google_skills
from scraper.sources.reddit import listen as reddit_listen
from scraper.sources.rss import ingest_feed
from scraper.sources.sitemap import ingest_sitemap
from scraper.sources.skillbuilder import ingest_skillbuilder
from scraper.store import Store, now_iso

logger = logging.getLogger(__name__)

CONFIG_PATH = "config/sources.yaml"

# Catalog-style sources: snapshot a list of courses/pages, diff run-over-run.
_CATALOG_INGESTERS = {
    "course_catalog": ingest_catalog,          # CSS-selector scraping (+ optional render)
    "sitemap": ingest_sitemap,                 # XML sitemap URL-set diffing
    "google_skills_catalog": ingest_google_skills,  # skills.google embedded JSON
    "skillbuilder_catalog": ingest_skillbuilder,    # skillbuilder.aws public GraphQL
}


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _publish_site() -> None:
    """Push site/ to the static host if configured; never fails the run."""
    try:
        deploy.publish(site.SITE_DIR)
    except Exception as e:
        logger.error("Site publish failed (site/ is still updated locally): %s", e)


def _finish(store: Store, run_at: str, run_started_at: str, items_scanned: int,
            net_new: int, insights: list[dict], summary: str) -> None:
    """Shared run epilogue — every exit path writes every sink, so the DB,
    site, and Power BI CSVs can't diverge based on which branch a run took."""
    store.record_run(items_scanned, net_new, len(insights), summary,
                     run_at=run_at)
    store.record_course_counts(run_at)
    site.export_site(store)
    _publish_site()
    if insights:
        powerbi.export_insights(insights, run_at)
        powerbi.push_to_powerbi(insights, run_at)
    powerbi.export_course_changes(store.course_changes_since(run_started_at))
    powerbi.export_course_counts(store.course_counts())
    powerbi.export_run_log(items_scanned, net_new, len(insights), summary)


def run(skip_analysis: bool = False, skip_discovery: bool = False,
        skip_reddit: bool = False) -> None:
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
        try:
            if source["type"] == "rss":
                items = ingest_feed(source)
                logger.info("[%s] %d feed items", source["name"], len(items))
                all_items.extend(items)
            elif source["type"] in _CATALOG_INGESTERS:
                courses = _CATALOG_INGESTERS[source["type"]](source)
                added, removed = store.diff_courses(source["name"], courses,
                                                    record=False)
                logger.info("[%s] %d courses (+%d new, -%d removed)",
                            source["name"], len(courses), len(added), len(removed))
                catalog_snapshots.append((source["name"], courses))
                added_courses.extend(added)
                if removed:
                    removed_titles[source["name"]] = removed
            else:
                logger.warning("[%s] unknown source type %r",
                               source["name"], source["type"])
        except Exception as e:
            logger.error("[%s] ingestion failed: %s", source["name"], e)

    # Layer 1b: search-based discovery — finds updates posted outside the
    # configured feeds. Failures here never block the configured sources.
    if not skip_discovery:
        try:
            all_items.extend(discover(config))
        except Exception as e:
            logger.error("Discovery failed: %s", e)

    # Layer 1c: Reddit social listening — what practitioners are actually
    # saying, independent of what competitors publish themselves. Failures
    # here never block the other sources.
    if not skip_reddit:
        try:
            all_items.extend(reddit_listen(config))
        except Exception as e:
            logger.error("Reddit listening failed: %s", e)

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

    # Fetch full article bodies for better analysis (best-effort).
    enrich_items(new_items)

    # Layer 2: LLM interpretation of net-new changes only.
    result = analyze(new_items, added_courses, removed_titles,
                     config.get("competitors"))
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

    # Layer 3: site (primary output) + optional Power BI CSV/push exports.
    _finish(store, run_at, run_started_at, len(all_items), len(new_items),
            insights, result["weekly_summary"])
    logger.info("Exported %d insights — site at %s/index.html (plus output/ CSVs).",
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


def backfill(days: int = 45) -> None:
    """One-time cold-start fix: analyze the last N days of feed items that
    the seed marked as baseline, so competitor pages open with history
    instead of starting empty. Idempotent-ish: re-running re-analyzes the
    same window (insight unique keys dedupe exact repeats per run)."""
    store = Store()
    config = load_config()
    run_started_at = now_iso()

    items = []
    for source in config["sources"]:
        if source["type"] != "rss":
            continue
        try:
            recent = [i for i in ingest_feed(source)
                      if published_within(i.published, days)]
            logger.info("[backfill:%s] %d items within %d days",
                        source["name"], len(recent), days)
            items.extend(recent)
        except Exception as e:
            logger.error("[backfill:%s] failed: %s", source["name"], e)

    if not items:
        logger.info("Backfill found no dated items in the window.")
        return

    logger.info("Backfill: analyzing %d items from the last %d days",
                len(items), days)
    enrich_items(items)
    result = analyze(items, [], {}, config.get("competitors"))
    insights = result["insights"]
    run_at = store.record_insights(insights)
    store.mark_items_seen(items)

    print(f"\nBackfill summary: {result['weekly_summary']}\n")
    for i in insights:
        print(f"  [{i['significance'].upper()}] {i['competitor']}: {i['headline']}")

    _finish(store, run_at, run_started_at, len(items), len(items), insights,
            f"Backfill of the last {days} days: " + result["weekly_summary"])
