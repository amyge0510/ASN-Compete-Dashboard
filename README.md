# ASN-Compete-Dashboard

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

## Architecture 🛠️

- **Phase 1 — Ingest.**
  - **RSS/Atom feeds** parsed with the standard library.
  - **Course catalogs** snapshotted each run and diffed run-over-run: new
    courses, removed courses, active counts.
    - `course_catalog`(CSS-selector scraping, optional Playwright via `render: true`)
    - `sitemap` (XML URL-set diffing — OpenAI Academy, Trailhead's ~8,600-module content sitemap)
    - `google_skills_catalog` (skills.google's server-rendered embedded JSON)
    - `skillbuilder_catalog` (AWS Skill Builder's public GraphQL search backend, ~2,250 items, no browser)
  - **Search-based discovery.** Competitors post updates in inconsistent
    places, so each run also web-searches per competitor and surfaces the ~5
    most relevant recent results with summaries.
  - *Per-source failures are isolated; one broken feed never kills the run.*

- **Phase 2 — Interpret.**
  - **Cross-reference the knowledge base** (`knowledge_base.db`, SQLite).
    Every item and course ever observed is recorded. Incoming items are
    checked cross referenced and only net-new insights proceed. Product updates may
    land monthly while the pipeline runs weekly; old updates are never
    re-surfaced as "this week's news."
  - **Full-article crawl.** Net-new items get their full post body fetched
    (best-effort), so analysis sees more than a one-line feed teaser.
  - **LLM analysis.** Batched, structured JSON output: competitor, category,
    headline, what-changed, so-what, significance, audience. A run-level rollup summary
    is generated on top.
  - Items are marked seen **only after analysis succeeds**, so an API outage
    mid-run retries the batch next run.
    
- **Phase 3 — Report.** 
  - `site/` is rebuilt from the knowledge base every run: an overview page
    (run summary, Net-New cards, catalog changes, KPIs), one page per
    competitor (insight history, adds/removals, catalog trend chart), and a
    trends page.
  - The overview's all-updates table **filters by competitor, category,
    significance, and date range**.
  - Self-contained HTML/CSS/SVG plus inline JS.

## How insights are categorized 🗂️

<table>
<tr><td>

<b>significance</b> — <code>high</code> / <code>medium</code> / <code>low</code>.
Ranks product signal about the competitor's skilling platform. Feature- and program-level platform changes land <code>high</code>;
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

> **Taking this over?** Read [RUNBOOK.md](RUNBOOK.md) — how to add a source,
> how to tell when something broke, and what to do about it.

## Configuring sources (**Non-Technical 💼**)

**`config/sources.yaml` is the only file you should
touch.** 

Everything else is infrastructure that **does NOT care** which sources it runs on.

Three steps:

**Step 1 — Decide what you want to track.** Write down the websites, blogs, articles, etc. you monitors by
hand today. 

**Step 2 — Pick the right mechanism for each source.**

- **You know the exact page and URL.** Product blogs, announcement pages, release
  notes, course catalogs — anything at a stable URL. Collect the URLs, paste
  them into GitHub Copilot in this repo, and ask it to add
  them to **`config/sources.yaml`**. It will pick the right source type and
  selectors.
- **Insights are scattered.** No specific page(s) reliably carries what you need.
  Use **search-based discovery**.
  - Define the queries you would type into a search engine yourself, and each run the system runs them, takes the top results, and feeds those pages into the same pipeline.
  - Queries live in the `discovery` block of `sources.yaml`.
- **Social listening matters.** Reddit, forums, practitioner chatter. This is
  NOT built in. Aadd a custom Reddit integration (Reddit's official
  read-only API via `praw`) or a third-party scraper such as Apify. Note that
  **LinkedIn is deliberately out of scope**: its User Agreement bans scraping
  and binds visitors, pages authwall automated fetchers, and the official API
  only reads pages you administer.

**Step 3 — Verify before you rely on it.** Committing to `config/` triggers
the `config-check` workflow automatically — check the Actions tab. Or run it
yourself:

```bash
python -m scraper validate        # structure: instant, no network
python -m scraper check-sources   # reachability: does each source return data?
```

Fetches every configured source and reports item/course counts or failures.
Exits non-zero on failure, so it can gate CI. **Feed URLs move.**
