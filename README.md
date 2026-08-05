# microsoft-web-scraper

<div align="center">
  <table>
    <tr>
      <td align="center">
        <h1>
          <a href="https://amyge0510-tech.github.io/Compete-Dashboard/index.html">
            Link to live dashboard
          </a>
        </h1>
      </td>
    </tr>
  </table>
</div>

Competitive-intelligence pipeline for the **AI Skills Navigator Compete team**.
Monitors competitor learning platforms (AWS Skill Builder, Google Skills,
Salesforce Trailhead, Anthropic Academy, OpenAI Academy, …), has an LLM
interpret what changed and why it matters, and publishes a **static dashboard
site** — surfacing only **net-new** changes since the previous run.

## Architecture

- **Phase 1 — Ingest.** Pull everything competitors published.
  - **RSS/Atom feeds** parsed with the standard library (`feedparser` is
    deliberately unused — its `sgmllib3k` dependency fails to build on some
    Python 3.11+ setups).
  - **Course catalogs** snapshotted each run and diffed run-over-run: new
    courses, removed courses, active counts. Four flavors — `course_catalog`
    (CSS-selector scraping, optional Playwright via `render: true`), `sitemap`
    (XML URL-set diffing — OpenAI Academy, Trailhead's ~8,600-module content
    sitemap), `google_skills_catalog` (skills.google's server-rendered
    embedded JSON), `skillbuilder_catalog` (AWS Skill Builder's public GraphQL
    search backend, ~2,250 items, no browser).
  - **Search-based discovery** — competitors post updates in inconsistent
    places, so each run also web-searches per competitor and surfaces the ~5
    most relevant recent results with summaries.
  - Per-source failures are isolated; one broken feed never kills the run.

- **Phase 2 — Interpret.** Decide what is actually new, then explain it.
  - **Cross-reference the knowledge base** (`knowledge_base.db`, SQLite) —
    every item and course ever observed is recorded. Incoming items are
    checked against it and only never-seen ones proceed. Product updates may
    land monthly while the pipeline runs weekly; old updates are never
    re-surfaced as "this week's news."
  - **Full-article crawl** — net-new items get their full post body fetched
    (best-effort), so analysis sees more than a one-line feed teaser.
  - **LLM analysis** — batched, structured JSON output: competitor, category,
    headline, what-changed, so-what, significance, audience, `ai_related`.
    Irrelevant items are dropped, not force-fitted. A run-level rollup summary
    is generated alongside.
  - Items are marked seen **only after analysis succeeds**, so an API outage
    mid-run retries the batch next run instead of silently swallowing it.

- **Phase 3 — Report.** Regenerate the deliverable.
  - `site/` is rebuilt from the knowledge base every run: an overview page
    (run summary, What's-New cards, catalog changes, KPIs), one page per
    competitor (insight history, adds/removals, catalog trend chart), and a
    trends page.
  - The overview's all-updates table filters by competitor, category,
    significance, and date range.
  - Self-contained HTML/CSS/SVG plus inline JS — no server, no CDN, no
    external dependencies. Open it, serve it, or publish it anywhere.

## How insights are categorized

<table>
<tr><td>

<b>significance</b> — <code>high</code> / <code>medium</code> / <code>low</code>.
Ranks product signal about the competitor's skilling platform, not business
magnitude. Feature- and program-level platform changes land <code>high</code>;
company-level news (partnerships, pricing or free-access moves, deals) is
capped at <code>low</code> in <code>analyze.py</code> regardless of what the
model returns. The What's New section is just insights filtered to
<code>significance = "high"</code>.

<b>category</b> — separates program and certification launches from routine
course adds, pricing moves, partnerships, and strategy shifts.

<b>audience</b> — <code>developers</code> / <code>business_users</code> /
<code>both</code>. AI Skills Navigator serves both, so this shows which segment
each competitor move targets.

<b>ai_related</b> — isolates the AI-skilling race from generic catalog churn.

</td></tr>
</table>

## Configuring sources

**If you are not technical, `config/sources.yaml` is the only file you should
touch.** Everything else — fetching, deduping, diffing, analyzing, publishing —
is machinery that does not care whose sources it runs on. Adapting this
pipeline to a different beat is a config exercise, not an engineering one.

Three steps:

**Step 1 — Decide what you want to track.** Write down the places you check by
hand today. That list *is* your config file.

**Step 2 — Pick the right mechanism for each source.**

- **You know the exact page.** Product blogs, announcement pages, release
  notes, course catalogs — anything at a stable URL. Collect the URLs, paste
  them into GitHub Copilot (or Claude Code) in this repo, and ask it to add
  them to `config/sources.yaml`. It will pick the right source type and
  selectors.
- **The news moves around.** No single page reliably carries what you need.
  Use **search-based discovery**: define the queries you would type into a
  search engine yourself, and each run the system runs them, takes the top
  results, and feeds those pages into the same pipeline. Queries live in the
  `discovery` block of `sources.yaml`.
- **Social listening matters.** Reddit, forums, practitioner chatter. This is
  not built in — you would add a custom Reddit integration (Reddit's official
  read-only API via `praw`) or a third-party scraper such as Apify. Note that
  **LinkedIn is deliberately out of scope**: its User Agreement bans scraping
  and binds visitors, pages authwall automated fetchers, and the official API
  only reads pages you administer.

**Step 3 — Verify before you rely on it.**

```bash
python -m scraper check-sources
```

Fetches every configured source and reports item/course counts or failures.
Exits non-zero on failure, so it can gate CI. Feed URLs move — the Google Cloud
training feed already migrated from `/products/…` to `/topics/…`, and
Salesforce's learning-category feed was retired outright. All configured URLs
were last verified live on 2026-07-28.

Reference for hand-editing: `course_catalog` sources take a CSS `item_selector`
for course links and an optional `render: true` for JavaScript-rendered pages.
`sitemap` sources take an optional `url_filter` (substring or list) to keep only
catalog paths. `skillbuilder_catalog` and `google_skills_catalog` are
self-contained — see `scraper/sources/skill_builder.py` and
`scraper/sources/google_skills.py` for how their endpoints were derived.
