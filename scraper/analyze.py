"""LLM interpretation layer: net-new items -> structured insights.

Sends the run's net-new items and catalog changes to the LLM and gets back
structured insights (guaranteed-valid JSON via schema-constrained output).
Large runs are analyzed in batches so no single request overruns its output
budget; a final rollup call produces the run summary.

Nothing in this file knows what beat it is covering. The analyst persona,
insight vocabulary, significance rubric, drop rules, and competitor tiers all
come from config/analysis.yaml — see scraper/config.py. Adapting the pipeline
to a different subject means editing that YAML, not this module.

Provider-agnostic: scraper/llm.py routes to Anthropic or OpenAI based on
LLM_PROVIDER / which API key is set.
"""
import logging

from scraper import llm
from scraper.config import load_analysis

logger = logging.getLogger(__name__)

ITEMS_PER_BATCH = 25

# Structural scaffolding only — every domain-specific sentence is injected
# from analysis.yaml. Keep this generic.
PROMPT_TEMPLATE = """\
{persona}

You will receive only NET-NEW items observed since the previous pipeline run —
never re-report old news as fresh. For each item that matters, produce an
insight: what changed, and what it means competitively. Skip items with no
relevance (marketing fluff, event recaps with no substance) rather than
forcing an insight.

Significance calibration:
{significance_rules}
{extra_guidance}{tier_guidance}

Insight fields: headline is a concise title. what_changed is ONE factual
sentence stating what the update actually is. so_what is ONE sentence on what
the move says about the organization's strategic direction — it renders under
"What this means:" on the dashboard, so write it as interpretation, not a
restatement of the change.
"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "weekly_summary": {
            "type": "string",
            "description": "2-3 sentence rollup of this run's landscape movement",
        },
    },
    "required": ["weekly_summary"],
    "additionalProperties": False,
}


def _insight_properties(config: dict) -> dict:
    """Insight field schema, with enums and flags driven by analysis.yaml."""
    properties = {
        "competitor": {"type": "string"},
        "category": {"type": "string", "enum": list(config["categories"])},
        "headline": {"type": "string"},
        "what_changed": {
            "type": "string",
            "description": "One factual sentence stating what the update is",
        },
        "so_what": {
            "type": "string",
            "description": "One sentence on what this says about the "
                           "organization's strategic direction — shown as "
                           "'What this means' on the dashboard",
        },
        "significance": {
            "type": "string", "enum": ["high", "medium", "low"],
            "description": "Follow the significance calibration in the system prompt",
        },
        "audience": {
            "type": "string", "enum": list(config["audiences"]),
            "description": "Who this move targets",
        },
    }
    for name, description in (config.get("flags") or {}).items():
        properties[name] = {"type": "boolean",
                            "description": str(description).strip()}
    properties["url"] = {"type": "string"}
    return properties


def _insights_schema(config: dict) -> dict:
    properties = _insight_properties(config)
    return {
        "type": "object",
        "properties": {
            "insights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            },
        },
        "required": ["insights"],
        "additionalProperties": False,
    }


def _apply_drop_categories(insights: list[dict], drop: set[str]) -> list[dict]:
    """Deterministic backstop for the prompt's rules: categories listed in
    analysis.yaml's `drop_categories` are removed outright, so a mis-rated
    item can never claim space on the dashboard no matter what the model
    returns. Empty by default — nothing is dropped unless configured."""
    if not drop:
        return insights
    kept = []
    for i in insights:
        if i.get("category") in drop:
            logger.info("Dropping insight in dropped category (%s): %s",
                        i["category"], i.get("headline", ""))
            continue
        kept.append(i)
    return kept


def _catalog_sections(added_courses: list, removed_titles: dict) -> list[str]:
    sections = []
    if added_courses:
        sections.append("## Items newly appearing in tracked catalogs\n" + "\n".join(
            f"- [{c.competitor}] {c.title} ({c.course_id})" for c in added_courses
        ))
    if removed_titles:
        sections.append("## Items removed from tracked catalogs\n" + "\n".join(
            f"- [{source}] {title}"
            for source, titles in removed_titles.items()
            for title in titles
        ))
    return sections


def _tier_guidance(tiers: dict | None) -> str:
    """Competitor priority tiers -> canonical-name + calibration prompt text."""
    if not tiers:
        return ""
    high = tiers.get("high_priority") or []
    medium = tiers.get("medium_priority") or []
    if not (high or medium):
        return ""
    other = tiers.get("other_label", "Industry")
    mappings = tiers.get("name_mappings") or []
    mapping_text = (" Map variants onto the canonical name (e.g. "
                    + "; ".join(mappings) + ").") if mappings else ""

    text = ("\n\nNaming: every insight's competitor field MUST be exactly one "
            f"of: {', '.join(high + medium)} — or \"{other}\" for market-wide "
            f"moves by anyone else.{mapping_text}")
    if high:
        text += (f"\n\nPriority calibration: {', '.join(high)} are "
                 "HIGH-PRIORITY — their official product/feature announcements "
                 "are automatically 'high', and other meaningful moves by them "
                 "deserve 'high' significance readily.")
    if medium:
        text += (f" {', '.join(medium)} are MEDIUM-PRIORITY: reserve 'high' "
                 "for truly major moves; their routine additions and "
                 "incremental updates should be 'medium' or 'low'.")
    return text


def build_system_prompt(config: dict) -> str:
    """Compose the analyst prompt from analysis.yaml."""
    extra = (config.get("extra_guidance") or "").strip()
    return PROMPT_TEMPLATE.format(
        persona=config["persona"].strip(),
        significance_rules=config["significance_rules"].strip(),
        extra_guidance=("\n\n" + extra) if extra else "",
        tier_guidance=_tier_guidance(config.get("competitors")),
    )


def analyze(new_items: list, added_courses: list, removed_titles: dict,
            config: dict | None = None) -> dict:
    """Return {"insights": [...], "weekly_summary": "..."} for this run's changes.

    `config` is the loaded analysis.yaml; loaded on demand if not supplied.
    """
    config = config or load_analysis()
    system_prompt = build_system_prompt(config)
    schema = _insights_schema(config)
    drop = set(config.get("drop_categories") or [])

    batches = [new_items[i:i + ITEMS_PER_BATCH]
               for i in range(0, len(new_items), ITEMS_PER_BATCH)] or [[]]

    insights: list[dict] = []
    for n, batch in enumerate(batches):
        sections = []
        if batch:
            sections.append("## New blog/announcement items\n" + "\n".join(
                f"- [{i.competitor}] {i.title} ({i.url})\n  {(i.summary or '')[:2000]}"
                for i in batch
            ))
        if n == 0:  # catalog changes ride with the first batch only
            sections.extend(_catalog_sections(added_courses, removed_titles))
        if not sections:
            continue
        logger.info("Analyzing batch %d/%d (%d items)", n + 1, len(batches), len(batch))
        result = llm.structured(
            "Net-new observations since the last pipeline run:\n\n"
            + "\n\n".join(sections),
            schema, system=system_prompt,
        )
        insights.extend(result["insights"])

    insights = _apply_drop_categories(insights, drop)
    if not insights:
        return {"insights": [], "weekly_summary": "No relevant changes this run."}

    headlines = "\n".join(
        f"- [{i['significance']}] {i['competitor']}: {i['headline']}" for i in insights
    )
    summary = llm.structured(
        "Write the run-level rollup summary for these insights:\n\n" + headlines,
        SUMMARY_SCHEMA, system=system_prompt, max_tokens=2000,
    )
    return {"insights": insights, "weekly_summary": summary["weekly_summary"]}
