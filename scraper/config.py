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
