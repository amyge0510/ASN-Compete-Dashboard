# Runbook — operating the Compete Dashboard

Everything you need to own this after the original author. You do not need to
write code.

**Dashboard:** https://amyge0510-tech.github.io/Compete-Dashboard/
**Repository:** `amyge0510-tech/Compete-Dashboard`

1. [The one rule](#the-one-rule)
2. [How it works](#how-it-works)
3. [Running the workflows](#running-the-workflows)
4. [Adding a competitor or a source](#adding-a-competitor-or-a-source)
5. [Checking whether it is still working](#checking-whether-it-is-still-working)
6. [When something fails](#when-something-fails)
7. [Honest limitations](#honest-limitations)
8. [Changing what counts as important](#changing-what-counts-as-important)
9. [What needs an engineer](#what-needs-an-engineer)
10. [Facts worth keeping](#facts-worth-keeping)

---

## The one rule

**Only edit `config/sources.yaml` and `config/analysis.yaml`. Never edit
anything in `scraper/`.**

Those two files are designed to be changed. Everything in `scraper/` is
machinery that works regardless of who the competitors are — changing it is
how you break the pipeline in ways that are hard to diagnose.

- **`config/sources.yaml`** — *where do I look?* Feeds, catalogs, search
  queries.
- **`config/analysis.yaml`** — *how do I judge what I find?* Analyst persona,
  categories, significance rules, competitor tiers.

Mistakes in `sources.yaml` fail loudly and get caught. Mistakes in
`analysis.yaml` fail **silently** — the run goes green and the insights are
just quietly worse. Change that file deliberately.

---

## How it works

Three phases, once per run.

**Phase 1 — Ingest.** Collect everything the competitors published.

- **RSS/Atom feeds** — the reliable case. Any valid feed works.
- **Course catalogs** — the list of courses is snapshotted each run and
  compared against last run's snapshot, producing adds, removals and totals.
  This catches things nobody announces: the dashboard's Google Workspace
  removal insight came from here, not from any blog post.
- **Search-based discovery** — competitors post updates in inconsistent
  places, so each run also runs a web search per competitor and feeds the
  results into the same pipeline. This needs no parsing, which makes it the
  fallback for sites that cannot be scraped.
- A broken source never kills the run. The others carry on.

**Phase 2 — Interpret.** Decide what is genuinely new, then explain it.

- **Cross-reference the knowledge base** (`knowledge_base.db`). Every item and
  course ever seen is recorded. Only never-seen items go forward, so nothing
  is ever re-reported as this week's news.
- **Recency guard.** "Have I seen this?" is not "is this still news?" Items
  older than the window (the gap since the last run, plus a few days' slack)
  are recorded but not analyzed.
- **Full-article crawl.** New items get their full body fetched, best-effort,
  so the analysis sees more than a one-line teaser.
- **LLM analysis.** Produces the headline, what changed, the "so what",
  significance, category and audience. Irrelevant items are dropped rather
  than force-fitted.
- **Deterministic backstops.** Categories in `drop_categories` are deleted
  outright; anyone outside `high_priority` is capped at `medium` significance.
  These are code, not prompt instructions, because models do not reliably
  follow rules.
- Items are marked seen **only after analysis succeeds**, so an API outage
  retries next run instead of silently losing a batch.

**Phase 3 — Report.** The `site/` folder is rebuilt from the knowledge base
every run and published to GitHub Pages. Overview, one page per competitor,
source health, and a filterable table of every update.

---

## Running the workflows

There are two, both under the **Actions** tab.

### `competitor-monitor` — the pipeline

Runs automatically **every Monday at 13:00 UTC**. Nobody has to do anything.

To run it manually: **Actions → competitor-monitor → Run workflow**. Leave
mode on `monitor` and click the green button.

- Takes **15–45 minutes**.
- `backfill` mode is a one-time history load, not a testing tool. It ignores
  the "is this new?" check on purpose. You should almost never need it.

### `config-check` — validation and the URL tester

Runs automatically whenever anything in `config/` changes. You can also run it
by hand, which is how you test a URL before adding it:

**Actions → config-check → Run workflow → paste a URL into "Optional: a URL to
test" → Run workflow.**

Takes a few minutes, mostly spent checking existing sources. The result
appears **on the run's Summary page** — you never need to open a log.

---

## Adding a competitor or a source

### Step 0 — test the URL first

Run `config-check` with the URL, as above. You get one of:

| Verdict | Meaning | What you get |
|---|---|---|
| `READY` | It is a feed or sitemap | A prompt to paste |
| `NEEDS A SELECTOR` | A readable list page | A prompt with a verified selector |
| `NEEDS CUSTOM CODE` | Cannot be scraped | A prompt to add a **search query** instead |
| `BLOCKED` | The site refuses bots | A prompt to add a **search query** instead |
| `ALREADY CONFIGURED` | Already a source | Nothing — it is already covered |
| `UNREACHABLE` | Could not connect | Nothing — try again |

**A selector is only suggested when the evidence is strong** — a group of at
least 8 links with title-like text and almost no call-to-action labels. A page
whose only link groups look like navigation ("View full catalog", "All
Courses") is refused, with the rejected groups shown. **If there is no prompt,
there is nothing to add.** That silence is deliberate.

### Step 1 — paste the prompt into Copilot

Copy the block under *"Copy this into GitHub Copilot Chat"*, replace the
`<REPLACE WITH THE COMPETITOR'S NAME>` placeholder, and paste it into GitHub
Copilot Chat with this repository open.

The prompt already carries everything: which files to edit, that the
competitor also goes in `analysis.yaml`'s tier list, that `scraper/` is
off-limits, and to show you a diff first.

### Step 2 — check the examples yourself

For `NEEDS A SELECTOR`, the summary lists example link texts. **Read them.**
If they look like course names, good. If they say "Contact Us" or "Pricing",
stop — no automated check downstream can catch that, and a bad selector
permanently pollutes the knowledge base.

**If you cannot get a source working in one attempt, delete it and move on.**
A missing source is far better than one reporting junk.

### Step 3 — let `config-check` confirm it

Committing triggers it automatically. Green means the config is valid and the
source returns data. Red on *"Validate config files"* means a structural
mistake — the log says exactly what, and reverting the commit is always safe.

### Step 4 — read the first real run

After the next monitor run, open the dashboard and look at the new source's
items. This is the last chance to catch a wrong selector.

---

## Checking whether it is still working

The dashboard has a **Source health** tab. Every source, with a verdict:

- **Working** — read fine on the last run.
- **Returns nothing** — reached, but empty. The URL moved, the page now needs
  JavaScript, or the selector stopped matching.
- **Cannot be read** — the fetch failed; the detail column says why.

Each row shows **when it last worked**. A source that has not worked in weeks
is rot, not weather.

**Glance at this monthly.** It is the only place silent decay becomes visible.

---

## When something fails

Actions → the red run → click the failed step.

| Symptom | Meaning | Fix |
|---|---|---|
| `Validate config` failed | A config file is broken | Read the error; revert the last `config/` commit if unsure |
| `401` / `invalid_api_key` | Key expired or revoked | Settings → Secrets and variables → Actions → update `OPENAI_API_KEY` |
| `429` / `insufficient_quota` | API account out of credit | Top up the account |
| `[some-source] ingestion failed` | One source broke | Not fatal. Fix or delete that source |
| `Cache not found` | Knowledge base lost | Re-seeds itself. One quiet week, then normal |
| Run took 45 minutes | Normal | Nothing — the Trailhead catalog alone is ~18 MB |
| No new insights | Normal | Nothing was published. A quiet week is not a failure |

**Reverting is always safe.** Open the commit on GitHub, click `···` → Revert.
No pipeline state can be corrupted by a config revert.

---

## Honest limitations

Read this section before promising anything to anyone.

### What it can and cannot read

| Source type | Effort | Reliability |
|---|---|---|
| RSS/Atom feed | Minutes, config only | High |
| XML sitemap | Minutes, config only | High |
| Server-rendered list page | Needs a verified selector | Medium — breaks on redesigns |
| JavaScript app / API-backed catalog | Custom code, hours | Only if someone writes it |
| Anything behind a login | Not supported | — |
| Paywalled content, PDFs, video | Not supported | — |
| LinkedIn | Deliberately out of scope (their terms ban it) | — |

The AWS and Google catalog readers are each a few hundred lines of
hand-written, site-specific code. That is what "needs an engineer" costs.

### Specific things that will bite you

**`render: true` does not work in the hosted run.** The setting exists for
JavaScript-rendered pages, but the workflow does not install the browser it
needs. A source using it will fail every week. `validate` warns about this.

**Some sites block datacenter IPs.** GitHub's runners are datacenter IPs, so a
source can work when tested from a laptop and fail every week in the hosted
run. Check Source health after adding anything.

**Feed URLs move.** It has already happened twice here — Google's training feed
changed paths and Salesforce retired one entirely. Sources need re-checking
every few months. Nothing warns you except Source health.

**A wrong selector is invisible.** If it matches navigation links instead of
courses, nothing errors. The probe's confidence check and your reading of the
example titles are the only defences.

**Junk in the knowledge base is permanent.** There is no supported way to
remove a bad source's items once recorded. This is why "delete the source
rather than guess" matters.

### Coverage is not complete, by design

**Discovery is best-effort and non-deterministic.** It is a web search, so
results vary run to run, often arrive without publication dates, and depend
heavily on how the query is phrased. A query naming a brand tends to return
evergreen product pages; a query naming an *event* ("announces", "launches")
returns news. Coverage via discovery is real but looser than a feed.

**Catalog history cannot be recovered.** Diffing is run-over-run, so a
competitor's catalog changes before their source was added are invisible
forever. Only feed items can be backfilled.

**The knowledge base lives in the Actions cache.** GitHub evicts caches after
a period of inactivity. Losing it is survivable — the next run re-seeds — but
the run history and accumulated insights are gone.

### The analysis layer is a judgement, not a fact

The LLM decides what matters, and it is wrong sometimes. It inflates
significance, which is why the cap on non-priority competitors exists as code
rather than instruction. It can also miss things, drop something you would
have wanted, or phrase the same story two different ways so the duplicate
check misses it.

Dedup catches the same URL and the same headline. It does **not** catch the
same story reported by two outlets in different words.

**Treat the dashboard as a well-informed first pass, not a system of record.**

### Cost and timing

Every run spends API credit, including runs that produce nothing — discovery
searches and analyzes before dedup rejects the results. A weekly cadence is
cheap; running it repeatedly to test is not.

### Publication

If the repository is not on a GitHub Enterprise plan, **the Pages site is
publicly readable by anyone with the URL**, even when the repository is
private. This is competitive research under a Microsoft-affiliated account.
Confirm that is acceptable, or ask an admin about enterprise-private Pages.

---

## Changing what counts as important

`config/analysis.yaml` controls the judgement. The parts worth knowing:

- **`persona`** — who the analyst is. Shapes output quality more than anything
  else in the file.
- **`significance_rules`** — the actual rubric. Concrete examples work far
  better than abstract rules.
- **`drop_categories`** — categories deleted outright. Currently `partnership`
  and `pricing`, because this team tracks skilling products rather than company
  news. Set to `[]` to keep everything.
- **`non_priority_max_significance: medium`** — nobody outside
  `high_priority` can be rated `high`. This is why ServiceNow cannot outrank
  AWS.
- **`competitors`** — the tiers, and the canonical names every insight must
  use. New competitors go here as well as in `sources.yaml`.

After any change, run the monitor once and read the output before trusting it.
Mistakes here do not raise errors.

---

## What needs an engineer

Say "this needs help" for:

- A source that needs `render: true` (JavaScript-rendered)
- A competitor whose catalog is a web app with no feed or sitemap
- Any change inside `scraper/`
- Removing bad data from the knowledge base
- Enabling private Pages, or moving the dashboard somewhere access-controlled

---

## Facts worth keeping

- **Repository:** `amyge0510-tech/Compete-Dashboard`
- **Dashboard:** GitHub Pages, served from the `gh-pages` branch
- **Schedule:** Mondays 13:00 UTC
- **Secret:** `OPENAI_API_KEY` in Actions secrets — the only credential. If
  the account it belongs to lapses, the pipeline stops. Know who owns it.
- **Knowledge base:** `knowledge_base.db`, carried between runs in the Actions
  cache. It is the pipeline's entire memory.
- **Sources last verified live:** 2026-07-28.

### Commands, if you ever have the repo checked out

```bash
python -m scraper validate              # config structure; instant, no network
python -m scraper check-sources         # does every source return data?
python -m scraper probe --url <URL>     # can this page be monitored?
python -m scraper                       # a full run
python -m scraper site                  # rebuild the site from the database
```
