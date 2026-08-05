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

# microsoft-web-scraper

Competitive-intelligence pipeline for the **AI Skills Navigator Compete team**.
Continuously monitors competitor learning platforms (AWS Skill Builder, Google
Skills, Salesforce Trailhead, Anthropic Academy, OpenAI Academy, …), has an
LLM interpret what changed and why it matters for Compete, and publishes a
**static dashboard site** — surfacing only **net-new** changes since the
previous run.

## Architecture

```
Layer 1 — Ingest             Layer 2 — Interpret           Layer 3 — Report
─────────────────────        ────────────────────          ─────────────────
RSS/Atom feeds        ─┐
Course catalogs        ├─► Knowledge base (SQLite) ─► LLM ──► site/  (the dashboard)
  (4 catalog flavors)  │   dedup: net-new only     analysis
Web-search discovery  ─┘        │
                          full-article crawl of net-new items
```

- **Configured sources** (`config/sources.yaml`) — RSS/Atom feeds parsed with
  the standard library, plus catalog snapshots diffed run-over-run (new
  courses, removed courses, active counts). Catalogs come in four flavors:
  CSS-selector scraping (`course_catalog`, with optional Playwright
  `render: true`), XML sitemap URL-set diffing (`sitemap` — OpenAI Academy,
  Trailhead's ~8,600-module content sitemap), skills.google's server-rendered
  embedded JSON (`google_skills_catalog`), and AWS Skill Builder's public
  GraphQL search backend (`skillbuilder_catalog` — full ~2,250-item catalog,
  no browser needed).
- **LinkedIn is deliberately out of scope** — LinkedIn's User Agreement bans
  scraping (and binds visitors), pages authwall automated fetchers, and the
  official API only reads pages you administer. Sanctioned coverage instead:
  subscribe to the Trailhead Dispatch newsletter by email, and note most
  Trailhead LinkedIn posts mirror Salesforce's own blogs (monitored here).
- **Search-based discovery** — competitors post platform updates in
  inconsistent places, so each run also searches per competitor (via Claude's
  server-side `web_search` tool), surfaces the ~5 most relevant recent results
  with summaries, and feeds them into the same pipeline.
- **Knowledge base** (`knowledge_base.db`) — every item and course ever
  observed is recorded; only never-seen items go to analysis. Product updates
  might land once a month while the pipeline runs weekly or daily; old
  updates are never re-surfaced as "this week's news."
- **Full-article crawl** — net-new items get their full post body fetched
  (best-effort) so analysis sees more than a one-line feed teaser.
- **Analysis** (`claude-opus-4-8`, structured JSON output) — batched insight
  extraction (competitor / category / headline / what-changed / so-what /
  significance) plus
  a run-level rollup summary. Irrelevant items are dropped, not force-fitted.
- **The site** (`scraper/export/site.py`) — every run regenerates `site/`:
  an overview page (run summary, What's-New cards, catalog changes, KPIs),
  one page per competitor (insight history, adds/removals, catalog trend
  chart), and a trends page. The overview's all-updates table filters by
  competitor, category, significance, and date range. Self-contained
  HTML/CSS/SVG plus inline JS for the filters — no server, no CDN, no
  external dependencies. Open it, serve it, or publish it anywhere.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...        # required: analysis + discovery

# Only for JS-rendered catalogs (sources with `render: true`) — none of the
# default sources need this anymore (Skill Builder now uses its GraphQL API):
pip install playwright && playwright install chromium
```

## Running

**Non-technical (recommended):**

- **Mac:** double-click `Run Monitor.command`. **Windows:** double-click
  `run.bat`. Both self-install on first use, read the API key from `.env`,
  run the pipeline, and open the dashboard when done (~10 minutes).
- **Fully hosted (zero-touch):** `.github/workflows/monitor.yml` runs the
  monitor every Monday in GitHub Actions and publishes the site to GitHub
  Pages — viewers just bookmark the Pages URL. Setup steps are in the
  workflow file's header comment

**Command line:**

```bash
python -m scraper check-sources    # verify every configured source works
python -m scraper --no-analyze     # first run: seed the knowledge base without
                                   # reporting all of feed history as "new"
python -m scraper                  # full run: ingest -> discover -> dedup ->
                                   # analyze -> export
python -m scraper --no-discovery   # skip the web-search discovery layer
python -m scraper site             # regenerate site/ from the knowledge base
python -m scraper -v               # debug logging
```

## The site

`site/index.html` is the dashboard. Every pipeline run rewrites the whole
folder from the knowledge base, so it is always consistent with the latest
run; `python -m scraper site` rebuilds it on demand (no network needed).

Viewing/hosting options, simplest first:

- **Open the file** — everything is self-contained, `file://` works.
- **Serve locally** — `python -m http.server -d site 8080`.
- **Auto-publish every run (recommended for sharing)** — set
  `NETLIFY_SITE_ID` + `NETLIFY_AUTH_TOKEN` (setup steps in
  `scraper/export/deploy.py`) and each pipeline run pushes `site/` to a
  stable Netlify URL viewers just bookmark. Free tier is plenty.
- **Other static hosts** — the folder is plain static files, so anything
  works: GitHub Pages (`.github/workflows/monitor.yml` does this), Cloudflare
  Pages (free access control via Cloudflare Access for up to 50 users —
  worth it if the intel shouldn't be public), Azure Static Web Apps (Entra
  ID auth, the Microsoft-native choice), or an internal SharePoint share.

## LLM provider (Anthropic or OpenAI)

All LLM access goes through `scraper/llm.py` — two functions (structured
output + web research), each implemented for both providers. Selection is
automatic from whichever API key is set, or forced with `LLM_PROVIDER`.

- **Anthropic (default):** `ANTHROPIC_API_KEY`; optional `ANTHROPIC_MODEL`
  (e.g. `claude-sonnet-4-6` for ~1/3 the cost) and `ANTHROPIC_BASE_URL`
  (centralized gateway).
- **OpenAI credits:** `pip install openai`, set `OPENAI_API_KEY`; optional
  `OPENAI_MODEL` (default `gpt-5`) and `OPENAI_BASE_URL` (Azure OpenAI or a
  centralized gateway — for Azure, use an endpoint exposing the
  OpenAI-compatible `/v1` API). Discovery uses OpenAI's built-in `web_search`
  tool; if the chosen model/endpoint doesn't support it, set
  `discovery.enabled: false` — the feeds and catalogs don't need an LLM to
  ingest.

## Configuring sources

Edit `config/sources.yaml`, then run `python -m scraper check-sources` — it
fetches every source and reports item/course counts or failures (feed URLs
move around — the Google Cloud training feed already migrated from
`/products/…` to `/topics/…`, and Salesforce's learning-category feed was
retired outright). `check-sources` exits non-zero on failures, so it can
gate CI. All configured URLs were last verified live on 2026-07-28.

`course_catalog` sources take a CSS `item_selector` for course links and an
optional `render: true` for JavaScript-rendered pages. `sitemap` sources
take an optional `url_filter` (substring or list) to keep only catalog paths.
The `skillbuilder_catalog` and `google_skills_catalog` types are
self-contained — see `scraper/sources/skill_builder.py` and
`scraper/sources/google_skills.py` for how their endpoints were derived.

The `discovery:` section defines per-competitor search targets and a
`lookback_days` window; it's independent of the configured feeds and covers
the gaps between them.

## Insight fields

Field notes:
- **`significance`** (`high`/`medium`/`low`) ranks product signal about the
  competitor's skilling platform, not business magnitude: feature/program
  level platform changes land `high`, while company-level news (partnerships,
  pricing/free-access moves, deals) is capped at `low` in `analyze.py`
  regardless of what the model returns. The What's New section is just
  `insights` filtered to `significance = "high"`.
- **`category`** separates program/certification launches from routine course
  adds, pricing moves, partnerships, and strategy shifts.
- **`audience`** (`developers`/`business_users`/`both`) matters because AI
  Skills Navigator serves both — it shows which of your segments each
  competitor move targets.
- **`ai_related`** isolates the AI-skilling race from generic catalog churn.

## Scheduling

Run on any cadence — dedup makes frequent runs safe:

```cron
# weekly, Mondays 8am
0 8 * * 1  cd /path/to/microsoft-web-scraper && .venv/bin/python -m scraper
```

A GitHub Actions `schedule:` workflow works too. Persist `knowledge_base.db`
between runs (cache/artifact or committed file) — it *is* the pipeline's
memory; losing it re-reports history as new.

## Tests

```bash
python -m unittest discover -s tests
```

Covers the knowledge-base dedup guarantees (including that items are only
marked seen *after* analysis succeeds, so an API failure mid-run can't
swallow a batch), course-catalog diffing, the RSS/Atom and sitemap parsers,
the skills.google and Skill Builder response parsers, and article-text
extraction — no network or API key needed.

## Design notes

- `feedparser` is deliberately not used (its `sgmllib3k` dependency fails to
  build on some Python 3.11+ setups); RSS/Atom parsing is stdlib.
- Per-source failures are isolated — one broken feed never kills the run.
- Discovery costs a few LLM calls per run; disable with `--no-discovery` or
  `discovery.enabled: false` if running very frequently.
- Skill Builder's anonymous GraphQL access is limited to a 100-result window
  per query; `scraper/sources/skill_builder.py` slices the catalog by facet
  filters (type → level → duration → domain) to enumerate ~99% of it, and
  every slice is fetched newest-first so new courses always surface even in
  slices that can't be subdivided below the window. The full enumeration
  takes ~4 minutes at the polite 0.5 s request spacing.
- Items are marked "seen" only after analysis succeeds (`mark_items_seen`),
  so an Anthropic API outage mid-run means the batch is retried next run
  instead of silently lost.
