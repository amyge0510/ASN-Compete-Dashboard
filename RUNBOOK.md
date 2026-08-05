# Runbook — operating the Compete Dashboard

For whoever owns this after the original author. You do not need to write
code. You need to know four things: how to read it, how to add a source, how
to tell when something broke, and what to do about it.

**Dashboard:** https://amyge0510-tech.github.io/Compete-Dashboard/

---

## The one rule

**Only edit `config/sources.yaml` and `config/analysis.yaml`. Never edit
anything in `scraper/`.**

Those two files are designed to be changed. Everything in `scraper/` is
machinery that works regardless of who the competitors are — changing it is
how you break the pipeline in ways that are hard to diagnose.

---

## What runs, and when

A GitHub Action (`competitor-monitor`) runs every Monday at 13:00 UTC. It
reads the sources, finds what is new since the last run, has an LLM interpret
it, and republishes the dashboard. Nobody has to do anything.

You can also run it on demand: **Actions → competitor-monitor → Run
workflow**. Leave the mode on `monitor`.

A run takes 15–45 minutes. A quiet week legitimately produces zero new
insights — that is the system working, not failing.

---

## Adding a competitor or a source

**Step 0 — test the URL first.** Before editing anything, find out whether
the pipeline can even read the page. No terminal needed:

**Actions → config-check → Run workflow → paste the URL into "Optional: a URL
to test" → Run workflow.** Open the run, expand *"Can the pipeline read this
URL?"*, and read the verdict:

| Verdict | Meaning |
|---|---|
| `READY` | Paste it into `sources.yaml` with the type it names. Done. |
| `NEEDS A SELECTOR` | Server-rendered list — usable, but someone must pick a CSS `item_selector`. |
| `NEEDS CUSTOM CODE` | JavaScript-rendered or a landing page. Config cannot fix this; it needs an engineer. |
| `BLOCKED` | The site refuses automated access. Nothing will fix it — look for a feed instead. |
| `UNREACHABLE` | Could not connect. Often the network, not the site. Check the URL opens in a browser. |

It also tells you when a page **declares an RSS feed** — always prefer the
feed over scraping the page.

**For `READY` and `NEEDS A SELECTOR`, the output ends with a ready-made prompt.**
Copy everything between the `====` lines, replace the
`<REPLACE WITH THE COMPETITOR'S NAME>` placeholder, and paste it into GitHub
Copilot Chat with this repository open. The prompt already tells Copilot which
files to edit, to add the competitor to the tier list in `analysis.yaml`, not
to touch `scraper/`, and to show you the diff first. Steps 1–3 below are then
done for you — skip to Step 4.

For `NEEDS CUSTOM CODE`, `BLOCKED` and `UNREACHABLE` there is no prompt,
because no config change can make those work. Stop and look for a feed or
sitemap on the same site instead.

Locally the same check is `python -m scraper probe --url <URL>`.

**Step 1 — find the right URL.** In order of preference:

1. **An RSS feed** (usually `/feed/`, `/rss/`, or linked in the page footer).
   This is the reliable case and needs no other configuration.
2. **An XML sitemap** (`/sitemap.xml`).
3. **A page that lists courses.** Must be a real catalog listing, not a
   marketing landing page.

**Step 2 — add it.** Edit `config/sources.yaml` on GitHub (click the file,
then the pencil icon). Copy an existing entry of the same type and change the
values. Indentation matters — copy an existing block rather than typing a new
one from scratch.

**Step 3 — add the competitor to the tiers.** In `config/analysis.yaml`, add
the competitor name to `high_priority` or `medium_priority`. If you skip this,
its insights show up untiered and mis-ranked.

**Step 4 — let the check run.** Committing to `config/` automatically triggers
the `config-check` workflow. Go to the **Actions** tab and look at it:

- **Green** — the config is valid and every source returned data.
- **Red on "Validate config files"** — you have a structural mistake. The log
  says exactly what. Fix it or revert your commit.
- **Red or warnings on "Check every source is reachable"** — the config is
  fine but a source returned nothing. See below.

You can run the same checks locally if you have the repo set up:

```bash
python -m scraper validate        # structure; instant, no network
python -m scraper check-sources   # does every source actually return data?
```

---

## Is a source still working? Check the dashboard

The dashboard has a **Source health** tab. Every source is listed with a
plain verdict:

- **Working** — read fine on the last run.
- **Returns nothing** — reached, but no items found. The URL moved, the page
  now needs JavaScript, or the selector stopped matching.
- **Cannot be read** — the fetch failed. The detail column says why.

Each row shows when it last worked. **A source that has not worked in weeks
is rot, not weather** — fix it or delete it.

This is the page to glance at monthly. It is the only place silent decay
becomes visible.

## When a source returns nothing

`check-sources` reporting `EMPTY` for a source means one of:

- **The URL moved.** Common — it has already happened twice here (Google's
  training feed moved paths, Salesforce retired a feed). Find the new URL and
  update it.
- **The page needs JavaScript.** The pipeline reads raw HTML. If the courses
  only appear after the page runs scripts, it sees nothing. Setting
  `render: true` is the documented fix, but **it does not work in the hosted
  run** — Playwright is not installed in the workflow. Treat this as needing
  an engineer.
- **The site blocks automated access.** Some sites return `403` to anything
  that is not a browser. Nothing in the config fixes this.
- **The selector is wrong.** For `course_catalog` sources, `item_selector`
  decides which links count as courses. Too broad and you get navigation
  links reported as new courses.

**If you cannot fix it in one attempt, delete the source and move on.** A
missing source is much better than a source that reports junk — junk pollutes
the knowledge base permanently, and the dashboard gives you no way to tell it
apart from real data.

### The failure `check-sources` cannot catch

A selector that matches the *wrong* elements returns plausible-looking
garbage. After adding any `course_catalog` source, run the monitor once and
**read the first few item titles**. If they say things like "Contact Us" or
"View All", the selector is wrong. Remove the source.

---

## When the weekly run fails

Actions → the red run → click the failed step.

| What the log says | What it means | What to do |
|---|---|---|
| `Validate config` failed | Someone broke a config file | Read the error; revert the last commit to `config/` if unsure |
| `401` / `invalid_api_key` | The API key expired or was revoked | Settings → Secrets and variables → Actions → update `OPENAI_API_KEY` |
| `429` / `insufficient_quota` | The API account is out of credit | Top up the account |
| `[some-source] ingestion failed` | One source broke | Not fatal — the rest of the run continues. Fix or delete that source |
| `Cache not found` | The knowledge base was lost | The run re-seeds itself. One quiet week, then normal |

**Reverting is always safe.** On GitHub, open the commit, click `...` →
Revert. The pipeline has no state that a config revert can corrupt.

---

## Things that are working as intended

- **A run produced no new insights.** Nothing new was published this week.
- **The same insights are still on the dashboard.** The dashboard shows a
  rolling history, not only this week. New items are badged `NEW`.
- **A competitor page is empty.** No insights recorded for them yet.
- **A run took 40 minutes.** Normal. The Trailhead catalog alone is ~18 MB.

---

## Changing what counts as important

`config/analysis.yaml` controls how insights are judged. The parts worth
knowing:

- **`drop_categories`** — categories deleted outright. Currently
  `partnership` and `pricing`, because this team tracks skilling products,
  not company news. Set it to `[]` to keep everything.
- **`non_priority_max_significance: medium`** — nobody outside
  `high_priority` can be rated `high`. This is why ServiceNow cannot outrank
  AWS.
- **`significance_rules`** — the actual rubric the LLM follows. Edit with
  care: mistakes here do not throw errors, they just quietly produce worse
  insights.

After any change here, run the monitor once and read the output before
trusting it.

---

## What needs an engineer

Be willing to say "this needs help" for:

- A source that needs `render: true` (JavaScript-rendered)
- A competitor whose catalog is a web app with no feed or sitemap — the AWS
  and Google catalog readers each took a few hundred lines of custom code
- Anything requiring a change inside `scraper/`
- Restoring a knowledge base that was deleted along with its history

---

## Facts worth keeping

- **Repo:** `amyge0510-tech/Compete-Dashboard`
- **Dashboard:** GitHub Pages, served from the `gh-pages` branch
- **Secret:** `OPENAI_API_KEY` in Actions secrets — the only credential
- **Knowledge base:** `knowledge_base.db`, kept in the Actions cache between
  runs. It is the pipeline's memory of everything ever seen. Losing it is
  recoverable (the next run re-seeds) but the run history is gone.
- **Sources last verified live:** 2026-07-28. Re-verify every few months with
  `check-sources`; feed URLs move.
