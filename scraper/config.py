"""Configuration loading.

Two config files, deliberately split by how often you touch them and how
loudly they fail:

  config/sources.yaml   — WHERE to look. Feeds, catalogs, discovery queries.
                          Breaks loudly: `check-sources` names the bad entry.
  config/analysis.yaml  — HOW to judge what you find. Analyst persona,
                          categories, significance rules, competitor tiers.
                          Breaks quietly: the run still goes green, the
                          insights are just wrong. Edit with more care.

`analysis.yaml` is optional. Without it the defaults below apply — a generic,
domain-neutral analyst. Everything specific to a particular beat (which
industry, which competitors, what counts as significant, what to ignore)
belongs in the YAML, never in the Python.
"""
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SOURCES_PATH = "config/sources.yaml"
ANALYSIS_PATH = "config/analysis.yaml"

# Domain-neutral fallbacks. These keep the pipeline runnable with no
# analysis.yaml at all; they are not meant to produce good insights for any
# particular beat — write an analysis.yaml for that.
DEFAULT_ANALYSIS: dict = {
    "persona": (
        "You are a competitive-intelligence analyst. You monitor a set of "
        "tracked organizations for product updates, launches, and strategy "
        "shifts, and report what changed and why it matters."
    ),
    "subject": "product",
    "brand": "Change Monitor",
    "categories": [
        "new_item", "removed_item", "product_update", "pricing",
        "partnership", "strategy", "other",
    ],
    "drop_categories": [],
    "audiences": ["unknown"],
    "significance_rules": (
        '- "high": material product- or program-level changes that reveal '
        "where the organization is heading.\n"
        '- "medium": incremental additions and notable signal.\n'
        '- "low": routine churn and minor updates.'
    ),
    "flags": {},
    "extra_guidance": "",
    "competitors": {},
}


def load_sources(path: str | Path = SOURCES_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_analysis(path: str | Path = ANALYSIS_PATH) -> dict:
    """Analysis config with defaults filled in for anything unspecified."""
    config = dict(DEFAULT_ANALYSIS)
    try:
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.info("No %s — using domain-neutral analysis defaults.", path)
        return config
    config.update({k: v for k, v in loaded.items() if v is not None})
    return config


# ── Validation ────────────────────────────────────────────────────────────
# Config edits are the main way a non-author breaks this pipeline: a stray
# indent in YAML, a source type that does not exist, a competitor named in
# sources.yaml but not tiered in analysis.yaml. All of those either crash the
# weekly run or, worse, degrade it silently. `python -m scraper validate`
# catches them in seconds with no network and no API spend.

KNOWN_SOURCE_TYPES = {
    "rss", "course_catalog", "sitemap",
    "google_skills_catalog", "skillbuilder_catalog",
}
SIGNIFICANCE_LEVELS = {"low", "medium", "high"}


def validate(sources_path: str | Path = SOURCES_PATH,
             analysis_path: str | Path = ANALYSIS_PATH) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors mean the pipeline will misbehave."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        sources = load_sources(sources_path)
    except FileNotFoundError:
        return ([f"{sources_path} not found"], [])
    except yaml.YAMLError as e:
        return ([f"{sources_path} is not valid YAML: {e}"], [])
    if not isinstance(sources, dict):
        return ([f"{sources_path} should be a mapping, got {type(sources).__name__}"], [])

    try:
        analysis = load_analysis(analysis_path)
    except yaml.YAMLError as e:
        return ([f"{analysis_path} is not valid YAML: {e}"], [])

    entries = sources.get("sources")
    if not entries:
        errors.append("sources.yaml has no `sources:` entries")
        entries = []

    seen_names = set()
    seen_urls: dict[str, str] = {}
    competitors_used = set()
    for n, src in enumerate(entries, 1):
        if not isinstance(src, dict):
            errors.append(f"source #{n} is not a mapping")
            continue
        label = src.get("name", f"#{n}")
        for key in ("name", "type", "url", "competitor"):
            if not src.get(key):
                errors.append(f"source {label}: missing `{key}`")
        if src.get("name") in seen_names:
            errors.append(f"source {label}: duplicate name")
        seen_names.add(src.get("name"))
        if src.get("url"):
            from scraper.store import normalize_url
            key = normalize_url(src["url"])
            if key in seen_urls:
                errors.append(f"source {label}: same URL as {seen_urls[key]!r} "
                              "— two sources snapshotting one page double-report "
                              "catalogue changes")
            seen_urls[key] = src.get("name")
        if src.get("competitor"):
            competitors_used.add(src["competitor"])
        stype = src.get("type")
        if stype and stype not in KNOWN_SOURCE_TYPES:
            errors.append(f"source {label}: unknown type {stype!r} "
                          f"(valid: {', '.join(sorted(KNOWN_SOURCE_TYPES))})")
        if stype == "course_catalog" and not src.get("item_selector"):
            warnings.append(f"source {label}: course_catalog without `item_selector` "
                            "matches every link on the page — expect junk items")
        if src.get("render"):
            warnings.append(f"source {label}: `render: true` needs Playwright, which "
                            "the GitHub Actions workflow does not install — this "
                            "source will fail in the hosted run")

    discovery = sources.get("discovery") or {}
    if discovery.get("enabled"):
        for n, target in enumerate(discovery.get("targets") or [], 1):
            if not isinstance(target, dict) or not target.get("competitor") \
                    or not target.get("query"):
                errors.append(f"discovery target #{n}: needs `competitor` and `query`")
            elif target.get("competitor"):
                competitors_used.add(target["competitor"])

    for key in ("persona", "categories", "significance_rules"):
        if not analysis.get(key):
            errors.append(f"analysis.yaml: `{key}` is required")

    categories = set(analysis.get("categories") or [])
    for cat in analysis.get("drop_categories") or []:
        if cat not in categories:
            errors.append(f"analysis.yaml: drop_categories has {cat!r}, which is not "
                          "in `categories` — it can never match anything")

    tiers = analysis.get("competitors") or {}
    tiered = set(tiers.get("high_priority") or []) | set(tiers.get("medium_priority") or [])
    cap = tiers.get("non_priority_max_significance")
    if cap and cap not in SIGNIFICANCE_LEVELS:
        errors.append(f"analysis.yaml: non_priority_max_significance {cap!r} must be "
                      f"one of {', '.join(sorted(SIGNIFICANCE_LEVELS))}")
    other = tiers.get("other_label", "Industry")
    for name in sorted(competitors_used - tiered - {other}):
        warnings.append(f"competitor {name!r} is used in sources.yaml but not listed in "
                        "analysis.yaml `competitors` — its insights will be untiered")

    max_age = analysis.get("max_age_days")
    if max_age not in (None, "auto") and not isinstance(max_age, int):
        errors.append(f"analysis.yaml: max_age_days should be a number or \"auto\", "
                      f"got {max_age!r}")
    return errors, warnings
