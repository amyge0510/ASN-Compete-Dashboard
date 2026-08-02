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
RSS/Atom feeds        ─┐                                   site/  (the dashboard)
Course catalogs        ├─► Knowledge base (SQLite) ─► Claude ─► output/*.csv (optional
  (4 catalog flavors)  │   dedup: net-new only     analysis     Power BI feeds)
Web-search discovery  ─┤        │
Reddit social listening┘  full-article crawl of net-new items
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
- **Reddit social listening** (`scraper/sources/reddit.py`) — searches
  configured subreddits (r/aws, r/AWSCertifications, r/googlecloud,
  r/salesforce, r/csMajors, ...) for configured keywords (e.g. "skill
  builder", "trailhead") and tags each match with the competitor its keyword
  maps to. This is practitioner sentiment, not official announcements — the
  analysis layer tags it `community_sentiment` rather than treating it as a
  product update. Uses Reddit's official read-only API via `praw`; see
  `scraper/sources/reddit.py` for the app-registration steps.
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
- **Power BI (optional)** — append-only CSVs and an optional push-dataset
  POST, for teams that also want the data in a Power BI model.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...        # required: analysis + discovery
export POWERBI_PUSH_URL=https://api.powerbi.com/beta/.../rows?key=...   # optional

# Only for JS-rendered catalogs (sources with `render: true`) — none of the
# default sources need this anymore (Skill Builder now uses its GraphQL API):
pip install playwright && playwright install chromium

# Only for Reddit social listening (reddit.enabled: true in sources.yaml):
pip install praw
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
export REDDIT_USER_AGENT="compete-monitor/1.0 by u/yourname"
# Get credentials: reddit.com/prefs/apps -> create app -> type "script"
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
python -m scraper --no-reddit      # skip the Reddit social-listening layer
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
self-contained — see `scraper/sources/skillbuilder.py` and
`scraper/sources/google_skills.py` for how their endpoints were derived.

The `discovery:` section defines per-competitor search targets and a
`lookback_days` window; it's independent of the configured feeds and covers
the gaps between them.

The `reddit:` section lists subreddits to search and keywords mapped to the
competitor each implies (e.g. `trailhead` → Salesforce Trailhead). Every
subreddit is searched with all keywords combined into one query per run;
`time_filter` and `limit_per_subreddit` bound how far back and how much each
search pulls. Set `reddit.enabled: false` (or pass `--no-reddit`) to turn
this off — it requires a Reddit API app (see Setup above).

## Power BI (optional)

The CSVs under `output/` remain available for teams that want the same data
in a Power BI model; nothing below is required for the site.

### Data model (four tables, emitted every run)

| Table | Grain | Key fields | Dashboard use |
|---|---|---|---|
| `insights.csv` | one row per detected update | `run_at`, `competitor`, `category`, `headline`, `so_what`, `significance`, `audience`, `ai_related`, `url` | "What's New" section, category mix, AI-move tracking |
| `course_changes.csv` | one row per course add/remove | `run_at`, `competitor`, `title`, `change_type` | "New courses they shipped" list |
| `course_counts.csv` | competitor × source × run | `run_at`, `competitor`, `active_courses` | catalog-size trend lines |
| `run_log.csv` | one row per pipeline run | `run_at`, `items_scanned`, `net_new_items`, `insight_count`, `summary` | freshness card, narrative text |

Field notes:
- **`significance`** (`high`/`medium`/`low`) ranks product signal about the
  competitor's skilling platform, not business magnitude: feature/program
  level platform changes land `high`, while company-level news (partnerships,
  pricing/free-access moves, deals) is capped at `low` in `analyze.py`
  regardless of what the model returns. The What's New section is just
  `insights` filtered to `significance = "high"`.
- **`category`** separates program/certification launches from routine course
  adds, pricing moves, partnerships, and strategy shifts. `community_sentiment`
  marks Reddit-sourced items — practitioner reaction rather than an official
  update — so it can be filtered separately from competitor-published news.
- **`audience`** (`developers`/`business_users`/`both`) matters because AI
  Skills Navigator serves both — it shows which of your segments each
  competitor move targets.
- **`ai_related`** isolates the AI-skilling race from generic catalog churn.

### Suggested dashboard layout

1. **What's New** (landing page) — card visuals from `insights` where
   `significance = "high"`, newest first: headline + so-what + link. Below
   it, a table of medium/low items. Slicers: competitor, category, audience,
   ai_related.
2. **Competitor deep-dive** — same tables filtered to one competitor, plus
   their catalog trend from `course_counts` and recent `course_changes`.
3. **Catalog trends** — line chart of `active_courses` over `run_at` per
   competitor; bar chart of adds/removes per month from `course_changes`.
4. **Activity cadence** — insight count per competitor per week (how fast is
   each competitor shipping), % `ai_related` over time.
5. **Narrative card** — latest `run_log.summary` in a text visual, plus
   `run_at` for data freshness.

### Connecting (two paths, both in `scraper/export/powerbi.py`)

1. **CSV + scheduled refresh (primary)** — the CSVs are append-only run
   logs. Put `output/` in OneDrive/SharePoint, then in Power BI Desktop:
   Get Data → Text/CSV (one connection per table) → build the model (relate
   tables on `competitor`; mark `run_at` as date/time) → publish to the
   Service → set scheduled refresh (e.g. daily). Every pipeline run appends
   rows; every refresh picks them up. Fully automatic.
2. **Push dataset (instant "What's New")** — in powerbi.com: workspace →
   New → Streaming dataset → API → define fields matching the `insights`
   columns above (enable *Historic data analysis*) → copy the Push URL →
   `export POWERBI_PUSH_URL=<push url>`. Each run then POSTs its insight
   rows and tiles update the moment the pipeline finishes. Push datasets
   only carry the insights table — use path 1 for the trend pages.

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
- Reddit listening costs API calls to Reddit, not Claude, but respect
  Reddit's rate limits on frequent schedules; disable with `--no-reddit` or
  `reddit.enabled: false` if needed.
- Skill Builder's anonymous GraphQL access is limited to a 100-result window
  per query; `scraper/sources/skillbuilder.py` slices the catalog by facet
  filters (type → level → duration → domain) to enumerate ~99% of it, and
  every slice is fetched newest-first so new courses always surface even in
  slices that can't be subdivided below the window. The full enumeration
  takes ~4 minutes at the polite 0.5 s request spacing.
- Items are marked "seen" only after analysis succeeds (`mark_items_seen`),
  so an Anthropic API outage mid-run means the batch is retried next run
  instead of silently lost.
