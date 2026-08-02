# Competitor Monitor — Setup Guide (no tech background needed)

This tool checks competitor learning platforms (AWS Skill Builder, Google
Skills, Salesforce Trailhead, Anthropic Academy, OpenAI Academy) for you,
uses AI to figure out what changed and why it matters, and builds a
dashboard you open in your browser. One run takes about 10–15 minutes and
you don't have to watch it.

There are two ways to use it. **Option A needs zero setup** — start there
if someone has already turned on the automatic runs.

---

## Option A — Just view the dashboard (zero setup)

If the automatic weekly runs are switched on, you don't install anything:

1. Bookmark the dashboard:
   **https://neoapollo18.github.io/compete-dashboard/**
2. Open it whenever you want. It updates itself every Monday, and the
   "Refresh data" button in the top-right runs a fresh check on demand
   (you need to be added as a collaborator on the GitHub project for the
   button's Run-workflow page to work).

That's the whole thing. Only continue below if you want to run the monitor
on your own computer (for example, to refresh it on demand).

### Optional: one-click in-page Refresh (admin setup, ~5 minutes)

Out of the box the "Refresh data" button opens a GitHub page (viewers need
repo access). To make it refresh silently in the background instead:

1. Create a free account at dash.cloudflare.com → **Workers & Pages** →
   **Create** → "Hello World" → name it `compete-refresh` → Deploy →
   **Edit code** → paste the contents of `refresh-worker/worker.js` from
   this project → Deploy.
2. Create a GitHub fine-grained token (Settings → Developer settings →
   Fine-grained tokens): repository access = only the monitor repo,
   permissions = **Actions: Read and write** (Metadata: Read is added
   automatically). Note the expiry date — the button stops working quietly
   when it expires, so set a calendar reminder to rotate it.
3. In the worker: **Settings → Variables and Secrets** → add **Secret**
   named `GITHUB_TOKEN` with that token → Deploy.
4. In the monitor repo: **Settings → Secrets and variables → Actions →
   Variables** → new variable `REFRESH_HOOK_URL` = the worker URL
   (`https://compete-refresh.<your-subdomain>.workers.dev`).

The next pipeline run publishes the upgraded button. Abuse protection is
built in: one refresh per 15 minutes, and clicks during a run report
"already in progress" instead of piling up runs.

---

## Option B — Run it on your own computer

### One-time setup (about 10 minutes)

**Step 1 — Install Python** (the free program that runs the monitor)

- Go to [python.org/downloads](https://www.python.org/downloads/) and click
  the big yellow download button.
- Run the installer.
- **Windows only, important:** on the first installer screen, tick the box
  that says **"Add Python to PATH"** before clicking Install. If you miss
  it, re-run the installer and choose "Modify".
- Mac: just click through the installer.

**Step 2 — Get the monitor folder**

- Easiest: use the folder you were sent (unzip it anywhere you like — your
  Documents folder is fine).
- Or from GitHub: open the repository page, click the green **Code**
  button → **Download ZIP**, then unzip it.

**Step 3 — Add the key file**

The monitor needs one small file called `.env` that contains the AI key
(this is what lets it summarize the news — it's like a password).

**Easiest: if you were sent a `.env` file**, just put it inside the
monitor folder, next to the file called `README.md`, and you're done.
Can't see it after copying? Files starting with a dot are hidden by
default — that's fine, it's still there.

**Making it yourself** (if nobody sent you one):

1. **Get a key.** Sign in at
   [platform.claude.com](https://platform.claude.com) (Anthropic) →
   **API Keys** → **Create Key** → copy it (it starts with `sk-ant-` and
   is only shown once). If your organization uses OpenAI instead, get a
   key from [platform.openai.com](https://platform.openai.com) →
   **API Keys**. Either works. Note: the account needs a few dollars of
   credit — each weekly run costs well under $1.
2. **Find the template.** The monitor folder contains a file named
   `.env.example`. If you can't see it: on Mac press
   **Cmd + Shift + . (period)** in Finder to show hidden files; on
   Windows, in File Explorer turn on **View → Show → Hidden items** and
   also **File name extensions**.
3. **Copy it and rename the copy to exactly `.env`** — nothing before the
   dot, nothing after. Windows may warn about changing the extension —
   click Yes. (If Windows keeps saving it as `.env.txt`, that's the
   hidden-extensions setting from step 2.) Your computer may also warn
   that names starting with a dot are reserved — it still works; on Mac
   the file will just become hidden, which is fine.
4. **Open the new `.env` in a plain text editor** — Notepad on Windows,
   TextEdit on Mac (in TextEdit, if you see formatting toolbars, press
   **Cmd + Shift + T** to switch to plain text first).
5. **Paste your key** right after the `=` sign — the file itself has
   comments telling you exactly which line, for either kind of key.
   No quotes, no spaces around the `=`. Save and close.

**Treat the finished file like a password:** don't forward it, post it,
or screenshot it. Anyone who has it can spend your AI credits.

That's setup done.

### Running it (whenever you want fresh data)

- **Mac:** double-click **`Run Monitor.command`**
  - The very first time, your Mac may say it can't verify the developer.
    If so: right-click (or Control-click) the file → **Open** → **Open**.
    You only have to do this once.
- **Windows:** double-click **`run.bat`**
  - If Windows shows a blue "protected your PC" screen: click
    **More info** → **Run anyway**. Only needed once.

A black text window opens and starts working. **Leave it alone for about
10–15 minutes.** The very first run takes a few minutes longer because it
sets itself up.

When it finishes, the dashboard opens in your browser by itself. Done.

### Reading the dashboard

- **Overview** — the run summary in plain English, plus "What's New" cards.
  Anything tagged **NEW** was found in the run you just did; anything
  tagged **HIGH** is a big move worth knowing about.
- **Competitor pages** (AWS, Google, etc. in the top bar) — everything
  we know about that one competitor: their catalog, what they added or
  removed, and the full history of findings.
- **Trends** — how big each competitor's course catalog is over time, and
  how fast each one is shipping.
- **Run history** — proof of what each check found, week by week.

The dashboard is just a file on your computer — you can reopen it anytime
without running anything: open the `site` folder and double-click
`index.html`.

---

## If something goes wrong

| What you see | What it means / what to do |
|---|---|
| "No API key found" | The `.env` file isn't in the folder (or got renamed). Re-copy it next to README.md. |
| "credit balance is too low" | The AI account is out of credits — tell whoever gave you the key. The rest of the check still works; only the AI summaries are skipped. |
| Window closes instantly (Windows) | Python probably isn't installed, or "Add to PATH" wasn't ticked. Redo Step 1. |
| A source shows "FAILED" in the text | A competitor changed their website. The run continues without it — mention it to whoever maintains the tool. |
| It seems stuck | The AWS catalog check takes ~4 quiet minutes — that's normal. If nothing has happened for 20+ minutes, close the window and try again. |

Nothing you do while running the monitor can break anything — worst case,
you close the window and double-click again.

## Good habits

- Run it about **once a week** (or just use the bookmarked link if
  automatic runs are on). Running it more often is harmless.
- Each run only reports what's **new since the last run**, so you never
  see the same news twice.
- To share what you see: the dashboard is the `site` folder — zip it and
  send it, or just screenshot the Overview page.
