"""LLM interpretation layer: net-new items -> Compete insights.

Sends the run's net-new blog posts and catalog changes to the LLM and gets
back structured insights (guaranteed-valid JSON via schema-constrained
output). Large runs are analyzed in batches so no single request overruns
its output budget; a final rollup call produces the run summary.

Provider-agnostic: scraper/llm.py routes to Anthropic or OpenAI based on
LLM_PROVIDER / which API key is set.
"""
import logging

from scraper import llm

logger = logging.getLogger(__name__)

ITEMS_PER_BATCH = 25

SYSTEM_PROMPT = """\
You are a competitive-intelligence analyst on the Microsoft AI Skills Navigator
Compete team. AI Skills Navigator is Microsoft's AI-powered skilling platform
for developers and business users. You monitor competing learning platforms —
AWS Skill Builder, Google Cloud Skills Boost, Salesforce Trailhead, and
similar — for product updates, new certifications, new courses, pricing
changes, and strategy shifts.

You will receive only NET-NEW items observed since the previous pipeline run —
never re-report old news as fresh. For each item that matters, produce an
insight: what changed, and what it means for AI Skills Navigator's competitive
position. Skip items with no compete relevance (marketing fluff, event recaps
with no product content) rather than forcing an insight.

Theme detection on catalog changes: when the newly-appearing-courses list
contains several courses that cluster around a shared topic, ALSO produce one
"strategy" insight naming the theme — e.g. headline "AWS is building out an
agentic-AI track (6 of 11 new courses)", with the so_what saying what focus
area this suggests the competitor is investing in and what it means for AI
Skills Navigator. One theme insight per clear cluster (don't force one if the
additions are scattered); significance "high" if the cluster suggests a new
strategic focus area, otherwise "medium". Routine one-off course adds still
get their normal individual treatment.

Significance calibration — significance ranks PRODUCT SIGNAL about the
competitor's skilling platform, never business magnitude:

- "high": any product- or feature-level update to the skilling platform
  itself — new platform capabilities or modes, new certification/badging
  programs, new learning modalities or paths, a catalog pivot. Every platform
  feature a competitor ships reveals where their product is heading, so
  feature announcements are high even when the feature itself looks small.
  Examples: "AWS introduces SimuLearn learning plan badges as verifiable
  proof of hands-on skills" (a bet on verifiable hands-on credentials) and
  "Skill Builder adds Self-Paced Trivia and Polls" (a bet on asynchronous,
  team-based engagement) are BOTH high. Official platform announcements from
  high-priority competitors (e.g. AWS Skill Builder announcements on the AWS
  Training and Certification blog) default to high — just confirm the item
  genuinely is a product/feature-level update before surfacing it as one.
- "medium": individual course or content additions worth noting, incremental
  content refreshes, and notable community signal.
- "low": routine course churn and minor content updates.

DO NOT EMIT INSIGHTS for company-level business news at all: partnerships,
customer/training deals ("X to train 20,000 engineers on Y"), company pricing
or free-access grants, funding, executive changes, model/API releases,
hardware — anything where no learning-platform feature or program changed.
The Compete team tracks skilling PRODUCTS; company news is noise here, so
skip those items entirely rather than rating them low. The one exception: if
a deal/announcement directly changes the skilling platform itself (e.g. a
partnership that adds a certification track to the platform), treat it as the
platform change it is, categorized by what changed rather than as
"partnership".

Insight fields: headline is a concise title. what_changed is ONE factual
sentence stating what the update actually is. so_what is ONE sentence on what
the move says about the competitor's strategic direction — it renders under
"What this means:" on the dashboard, so write it as strategy interpretation,
not a restatement of the change.
"""

INSIGHT_PROPERTIES = {
    "competitor": {"type": "string"},
    "category": {
        "type": "string",
        "enum": [
            "new_course", "removed_course", "certification", "product_update",
            "pricing", "partnership", "strategy", "other",
        ],
    },
    "headline": {"type": "string"},
    "what_changed": {
        "type": "string",
        "description": "One factual sentence stating what the update is",
    },
    "so_what": {
        "type": "string",
        "description": "One sentence on what this says about the "
                       "competitor's strategic direction — shown as 'What "
                       "this means' on the dashboard",
    },
    "significance": {
        "type": "string", "enum": ["high", "medium", "low"],
        "description": "Product signal about the competitor's skilling "
                       "platform — company-level news (partnerships, pricing, "
                       "deals) is always low",
    },
    "audience": {
        "type": "string",
        "enum": ["developers", "business_users", "both", "unknown"],
        "description": "Who the competitor move targets — AI Skills Navigator serves both",
    },
    "ai_related": {
        "type": "boolean",
        "description": "True if this is an AI-skilling move (AI courses, AI certs, AI features)",
    },
    "url": {"type": "string"},
}

INSIGHTS_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": INSIGHT_PROPERTIES,
                "required": list(INSIGHT_PROPERTIES),
                "additionalProperties": False,
            },
        },
    },
    "required": ["insights"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "weekly_summary": {
            "type": "string",
            "description": "2-3 sentence rollup of this run's competitive landscape movement",
        },
    },
    "required": ["weekly_summary"],
    "additionalProperties": False,
}


# Company-level business news, not skilling-product changes. The prompt
# steers these to "low"; the cap makes it a guarantee, so the dashboard's
# What's-New section (significance == "high") can never be claimed by a
# partnership or pricing headline no matter what the model returns.
COMPANY_NEWS_CATEGORIES = {"partnership", "pricing"}


def _recalibrate(insights: list[dict]) -> list[dict]:
    """Deterministic backstop for the prompt's rules: company-level news
    (partnerships, pricing) is dropped outright — Compete tracks skilling
    products, and a mis-rated deal headline must never crowd the dashboard."""
    kept = []
    for i in insights:
        if i.get("category") in COMPANY_NEWS_CATEGORIES:
            logger.info("Dropping company-news insight (%s): %s",
                        i["category"], i.get("headline", ""))
            continue
        kept.append(i)
    return kept


def _catalog_sections(added_courses: list, removed_titles: dict) -> list[str]:
    sections = []
    if added_courses:
        sections.append("## Courses newly appearing in competitor catalogs\n" + "\n".join(
            f"- [{c.competitor}] {c.title} ({c.course_id})" for c in added_courses
        ))
    if removed_titles:
        sections.append("## Courses removed from competitor catalogs\n" + "\n".join(
            f"- [{source}] {title}"
            for source, titles in removed_titles.items()
            for title in titles
        ))
    return sections


def _tier_guidance(tiers: dict | None) -> str:
    """Competitor priority tiers -> canonical-name + calibration prompt text."""
    if not tiers:
        return ""
    high = tiers.get("high_priority", [])
    medium = tiers.get("medium_priority", [])
    names = ", ".join(high + medium)
    return (
        "\n\nCompetitor naming: every insight's competitor field MUST be "
        f"exactly one of: {names} — or \"Industry\" for market-wide moves by "
        "anyone else. Map variants onto the canonical name (e.g. 'OpenAI "
        "Academy' -> OpenAI, 'Google Cloud Skills Boost'/'Google Skills' -> "
        "Google, 'Trailhead' -> Salesforce Trailhead).\n\n"
        f"Priority calibration: {', '.join(high)} are the HIGH-PRIORITY "
        "direct competitors — their official platform product/feature "
        "announcements are automatically 'high', and other meaningful moves "
        "by them deserve 'high' "
        f"significance readily. {', '.join(medium)} are MEDIUM-PRIORITY: "
        "reserve 'high' for truly major product moves (major platform "
        "launches, new certification programs at scale, significant new "
        "learning modalities); their routine course additions and incremental "
        "updates should be 'medium' or 'low'. Company-level news stays 'low' "
        "for every tier."
    )


def analyze(new_items: list, added_courses: list, removed_titles: dict,
            tiers: dict | None = None) -> dict:
    """Return {"insights": [...], "weekly_summary": "..."} for this run's changes."""
    system_prompt = SYSTEM_PROMPT + _tier_guidance(tiers)
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
            "Net-new observations since the last pipeline run:\n\n" + "\n\n".join(sections),
            INSIGHTS_SCHEMA, system=system_prompt,
        )
        insights.extend(result["insights"])

    insights = _recalibrate(insights)
    if not insights:
        return {"insights": [], "weekly_summary": "No compete-relevant changes this run."}

    headlines = "\n".join(
        f"- [{i['significance']}] {i['competitor']}: {i['headline']}" for i in insights
    )
    summary = llm.structured(
        "Write the run-level rollup summary for these insights:\n\n" + headlines,
        SUMMARY_SCHEMA, system=system_prompt, max_tokens=2000,
    )
    return {"insights": insights, "weekly_summary": summary["weekly_summary"]}
