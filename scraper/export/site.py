"""Static-site generator — the pipeline's primary, human-facing output.

Every run regenerates `site/` from the knowledge base: an overview page with
the run summary and What's-New cards, one page per competitor (insight
history + catalog adds/removals), and a trends page with catalog-size charts.
Everything is self-contained HTML/CSS/SVG plus a dab of inline JS for the
update-table filters and the optional Refresh button (which POSTs to the
refresh-worker proxy when REFRESH_HOOK_URL is set) — no CDN, no build step.
Open site/index.html directly (the Refresh button needs the hosted origin,
everything else works from file://), serve it with
`python -m http.server -d site`, or publish the folder to any static host
(GitHub Pages, Azure Static Web Apps, SharePoint).

Regenerate on demand without a pipeline run: `python -m scraper site`.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from scraper.store import Store

logger = logging.getLogger(__name__)

SITE_DIR = Path("site")
LOOKBACK_DAYS = 30
MAX_CHANGES_SHOWN = 25

_SIG_COLORS = {"high": "#b3402e", "medium": "#a8722a", "low": "#8a8a8a"}
_LINE_COLORS = ["#6c5898", "#b3402e", "#5c7a3f", "#a8722a", "#2b7a8c",
                "#54447a", "#a04a68"]

_CSS = """
  :root { color-scheme: light;
          --bg: #faf7ef; --band: #f2ecdb; --card: #ffffff;
          --line: #e7e1d2; --ink: #17181a; --muted: #6f6f6f;
          --teal: #6c5898; --teal-deep: #54447a; --teal-fill: #efecf6;
          --red: #b3402e; --amber: #a8722a; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: "Segoe UI", -apple-system, system-ui, sans-serif;
         background: var(--bg); color: var(--ink); }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 0 24px 64px; }
  nav { background: var(--band); border-bottom: 1px solid var(--line);
        margin: 0 -24px 28px; padding: 14px 24px;
        display: flex; gap: 18px; flex-wrap: wrap; align-items: baseline; }
  nav .brand { color: var(--ink); font-weight: 700; font-size: 15px;
               letter-spacing: .02em; margin-right: 8px; text-decoration: none; }
  nav a { color: var(--muted); text-decoration: none; font-size: 13px; }
  nav a:hover { color: var(--teal-deep); }
  nav a.active { color: var(--teal-deep); border-bottom: 2px solid var(--teal);
                 padding-bottom: 2px; font-weight: 600; }
  nav .refresh { margin-left: auto; background: var(--teal-deep); color: #fff;
                 border: none; border-radius: 8px; padding: 6px 14px;
                 font-size: 12px; font-weight: 600; cursor: pointer;
                 text-decoration: none; }
  nav .refresh:hover { background: var(--teal); color: #fff; }
  nav button.refresh { font-family: inherit; }
  nav .refresh[disabled] { opacity: .75; cursor: default; }
  h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -.01em; }
  .sub { color: var(--muted); font-size: 14px; margin-bottom: 20px; }
  .summary { background: var(--card); border: 1px solid var(--line);
             border-left: 4px solid var(--teal); border-radius: 12px;
             padding: 14px 18px; font-size: 15px; line-height: 1.5; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr));
          gap: 12px; margin: 20px 0 8px; }
  .kpi { background: var(--card); border: 1.5px solid var(--teal);
         border-radius: 12px; padding: 12px 16px; }
  .kpi .n { font-size: 24px; font-weight: 700; color: var(--teal);
            font-variant-numeric: tabular-nums; }
  .kpi .l { font-size: 11px; color: var(--muted); text-transform: uppercase;
            letter-spacing: .06em; }
  h2 { font-size: 15px; margin: 32px 0 12px; text-transform: uppercase;
       letter-spacing: .05em; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px,1fr));
           gap: 12px; }
  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: 12px; padding: 14px 16px; }
  .card .meta { font-size: 12px; margin-bottom: 6px; }
  .badge { display: inline-block; border-radius: 10px; padding: 1px 9px;
           font-size: 11px; font-weight: 600; color: #fff; }
  .chip { display: inline-block; background: var(--teal-fill); border-radius: 10px;
          padding: 1px 9px; font-size: 11px; color: var(--teal-deep);
          margin-left: 6px; }
  .chip.new { background: #e4efe2; color: #3c6b35; font-weight: 700; }
  .card h3 { font-size: 15px; margin: 0 0 6px; }
  .card h3 a, td a, .comp-block a { color: var(--teal-deep); text-decoration: none; }
  .card p { font-size: 13px; color: #444; margin: 0; line-height: 1.45; }
  .card p + p { margin-top: 6px; }
  .card p strong { color: var(--ink); }
  .filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
             margin: 0 0 10px; font-size: 13px; }
  .filters select, .filters input { font: inherit; font-size: 13px;
             padding: 4px 8px; border: 1px solid var(--line);
             border-radius: 8px; background: var(--card); color: var(--ink); }
  .filters label { color: var(--muted); display: flex; gap: 6px;
                   align-items: center; }
  table { width: 100%; border-collapse: collapse; background: var(--card);
          border: 1px solid var(--line); border-radius: 12px; overflow: hidden;
          font-size: 13px; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #efeadd;
           vertical-align: top; }
  th { background: #fbf9f3; font-size: 11px; color: var(--muted);
       text-transform: uppercase; letter-spacing: .06em; }
  tr:last-child td { border-bottom: none; }
  .comp-block { background: var(--card); border: 1px solid var(--line);
                border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; }
  .comp-block h3 { margin: 0 0 8px; font-size: 15px; }
  .comp-block ul { margin: 6px 0; padding-left: 20px; font-size: 13px;
                   line-height: 1.6; }
  .muted { color: var(--muted); font-size: 13px; }
  .removed { color: var(--red); }
  .chart { background: var(--card); border: 1px solid var(--line);
           border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; }
  .legend { font-size: 12px; margin-top: 6px; }
  .legend span { margin-right: 14px; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 5px;
         margin-right: 4px; }
  footer { margin-top: 40px; color: #a39d8d; font-size: 12px; }
"""


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


# Client for the one-click refresh: POSTs to the Cloudflare Worker named in
# data-hook (see refresh-worker/worker.js), renders the worker's fixed status
# enum, and re-enables only on failure. The hook URL is read from a data
# attribute — never interpolated into a JS string.
_REFRESH_SCRIPT = """
<script>
(function () {
  var btn = document.querySelector("button.refresh[data-hook]");
  if (!btn) return;
  var LABELS = {
    started: "\\u2713 Refreshing \\u2014 new data in ~15 min",
    already_running: "\\u23f3 Refresh already in progress",
    cooling_down: "\\u23f3 Refreshed recently \\u2014 try again soon",
    error: "\\u26a0 Refresh unavailable right now",
  };
  btn.addEventListener("click", function () {
    btn.disabled = true;
    btn.textContent = "Refreshing\\u2026";
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 10000);
    fetch(btn.dataset.hook, { method: "POST", signal: ctrl.signal })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var known = Object.prototype.hasOwnProperty.call(LABELS, d.status);
        btn.textContent = known ? LABELS[d.status] : LABELS.error;
        if (!known || d.status === "error") btn.disabled = false;
      })
      .catch(function () {
        btn.textContent = LABELS.error;
        btn.disabled = false;
      })
      .finally(function () { clearTimeout(timer); });
  });
})();
</script>
"""


def _nav(competitors: list[str], active: str) -> str:
    links = [("Overview", "index.html")]
    links += [(c, f"competitor-{slugify(c)}.html") for c in competitors]
    active_attr = ' class="active"'
    items = "".join(
        f'<a href="{href}"{active_attr if href == active else ""}>'
        f'{escape(label)}</a>'
        for label, href in links)
    # Business-user re-run trigger, two flavors:
    # - REFRESH_HOOK_URL (preferred): https URL of the refresh-worker proxy
    #   (refresh-worker/worker.js) — an in-page button; the viewer never
    #   sees GitHub. https-only by design.
    # - REFRESH_URL (fallback): plain link, e.g. the GitHub Actions workflow
    #   page where collaborators get a "Run workflow" button.
    hook_url = os.environ.get("REFRESH_HOOK_URL", "")
    refresh_url = os.environ.get("REFRESH_URL", "")
    refresh = ""
    if hook_url.startswith("https://"):
        refresh = (f'<button class="refresh" data-hook="{escape(hook_url)}" '
                   'title="Runs a fresh check (takes ~15 minutes); reload '
                   'this page afterwards">&#8635; Refresh data</button>'
                   + _REFRESH_SCRIPT)
    elif refresh_url.startswith(("http://", "https://")):
        refresh = (f'<a class="refresh" href="{escape(refresh_url)}" '
                   'target="_blank" rel="noopener" title="Runs a fresh check '
                   '(takes ~15 minutes); refresh this page afterwards">'
                   "&#8635; Refresh data</a>")
    return (f'<nav><a class="brand" href="index.html">Compete Monitor</a>'
            f"{items}{refresh}</nav>")


def _page(title: str, nav: str, body: str) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)} — Compete Monitor</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f'<div class="wrap">{nav}\n{body}\n'
        f"<footer>Generated {generated} by the compete-monitor pipeline. "
        "Regenerate with <code>python -m scraper site</code>.</footer>"
        "</div>\n</body>\n</html>\n"
    )


def _badge(significance: str) -> str:
    color = _SIG_COLORS.get(significance, "#6b7280")
    return (f'<span class="badge" style="background:{color}">'
            f'{escape(significance.upper())}</span>')


def _linked(text: str, url: str) -> str:
    """Escaped text, wrapped in a link only for http(s) URLs — DB-sourced
    values (LLM output, scraped hrefs) must not become javascript:/data:
    links on the hosted page."""
    safe_text = escape(text)
    if url and url.startswith(("http://", "https://")):
        return f'<a href="{escape(url)}">{safe_text}</a>'
    return safe_text


def _new_chip(run_at: str, latest_run_at: str | None) -> str:
    if latest_run_at and run_at == latest_run_at:
        return '<span class="chip new">NEW</span>'
    return ""


def _insight_card(row: tuple, latest_run_at: str | None = None) -> str:
    (run_at, competitor, category, headline, so_what, significance,
     _audience, ai_related, url, what_changed) = row
    title = _linked(headline, url)
    ai = '<span class="chip">AI</span>' if ai_related else ""
    if what_changed:
        body = (f"<p>{escape(what_changed)}</p>"
                f'<p><strong>What this means:</strong> {escape(so_what)}</p>')
    else:  # rows recorded before the what_changed column existed
        body = f"<p>{escape(so_what)}</p>"
    return (
        '<div class="card">'
        f'<div class="meta">{_badge(significance)}'
        f"{_new_chip(run_at, latest_run_at)}"
        f'<span class="chip">{escape(competitor)}</span>'
        f'<span class="chip">{escape(category)}</span>{ai}</div>'
        f"<h3>{title}</h3>{body}</div>"
    )


def _insight_rows(insights: list[tuple], show_competitor: bool = True,
                  latest_run_at: str | None = None,
                  table_id: str | None = None) -> str:
    rows = []
    for (run_at, competitor, category, headline, _so_what, significance,
         _audience, _ai, url, _what_changed) in insights:
        title = _linked(headline, url)
        comp_cell = f"<td>{escape(competitor)}</td>" if show_competitor else ""
        rows.append(f'<tr data-competitor="{escape(competitor)}" '
                    f'data-category="{escape(category)}" '
                    f'data-sig="{escape(significance)}" '
                    f'data-date="{escape(str(run_at)[:10])}">'
                    f"<td>{escape(str(run_at)[:10])}"
                    f"{_new_chip(run_at, latest_run_at)}</td>"
                    f"<td>{_badge(significance)}</td>{comp_cell}"
                    f"<td>{escape(category)}</td><td>{title}</td></tr>")
    comp_header = "<th>Competitor</th>" if show_competitor else ""
    id_attr = f' id="{table_id}"' if table_id else ""
    return (f"<table{id_attr}><tr><th>Date</th><th></th>" + comp_header +
            "<th>Category</th><th>Headline</th></tr>" + "".join(rows) +
            "</table>")


def _filter_bar(insights: list[tuple]) -> str:
    """Client-side filters for the all-updates table: competitor, category,
    significance, and a date range. Values live in each row's data-*
    attributes; the inline script below shows/hides rows — no dependencies,
    the site stays fully self-contained."""
    competitors = sorted({r[1] for r in insights})
    categories = sorted({r[2] for r in insights})
    comp_opts = "".join(f"<option>{escape(c)}</option>" for c in competitors)
    cat_opts = "".join(f"<option>{escape(c)}</option>" for c in categories)
    return (
        '<div class="filters">'
        '<select id="f-comp"><option value="">All competitors</option>'
        f"{comp_opts}</select>"
        '<select id="f-cat"><option value="">All categories</option>'
        f"{cat_opts}</select>"
        '<select id="f-sig"><option value="">All significance</option>'
        '<option value="high">High</option>'
        '<option value="medium">Medium</option>'
        '<option value="low">Low</option></select>'
        '<label>From <input type="date" id="f-from"></label>'
        '<label>To <input type="date" id="f-to"></label>'
        '<span id="f-count" class="muted"></span>'
        "</div>"
    )


_FILTER_SCRIPT = """
<script>
(function () {
  var ids = ["f-comp", "f-cat", "f-sig", "f-from", "f-to"];
  function apply() {
    var comp = document.getElementById("f-comp").value;
    var cat = document.getElementById("f-cat").value;
    var sig = document.getElementById("f-sig").value;
    var from = document.getElementById("f-from").value;
    var to = document.getElementById("f-to").value;
    var rows = document.querySelectorAll("#updates tr[data-date]");
    var shown = 0;
    rows.forEach(function (tr) {
      var d = tr.dataset;
      var ok = (!comp || d.competitor === comp) &&
               (!cat || d.category === cat) &&
               (!sig || d.sig === sig) &&
               (!from || d.date >= from) &&
               (!to || d.date <= to);
      tr.style.display = ok ? "" : "none";
      if (ok) shown++;
    });
    document.getElementById("f-count").textContent =
      shown + " of " + rows.length + " updates";
  }
  ids.forEach(function (id) {
    var el = document.getElementById(id);
    el.addEventListener("change", apply);
    el.addEventListener("input", apply);
  });
  apply();
})();
</script>
"""


def _run_history(runs: list[tuple]) -> str:
    """The continuing-value ledger: what each run scanned and surfaced."""
    if not runs:
        return ""
    rows = []
    for run_at, items_scanned, net_new, insight_count, summary in runs:
        brief = summary if len(summary) <= 220 else summary[:217] + "…"
        rows.append(f"<tr><td>{escape(str(run_at)[:16].replace('T', ' '))}</td>"
                    f"<td>{items_scanned}</td><td>{net_new}</td>"
                    f"<td>{insight_count}</td><td>{escape(brief)}</td></tr>")
    return ("<h2>Run history — what each check delivered</h2>"
            "<table><tr><th>Run</th><th>Scanned</th><th>Net-new</th>"
            "<th>Insights</th><th>Summary</th></tr>" + "".join(rows) +
            "</table>")


def _changes_list(changes: list[tuple], cap: int = MAX_CHANGES_SHOWN) -> str:
    parts = []
    for kind, css in (("added", ""), ("removed", ' class="removed"')):
        entries = [
            (title, course_id)
            for _run_at, _source, _comp, title, course_id, change_type in changes
            if change_type == kind
        ]
        if not entries:
            continue
        shown = entries[:cap]
        items = "".join(f"<li{css}>{_linked(title, cid)}</li>"
                        for title, cid in shown)
        if len(entries) > cap:
            items += (f'<li class="muted">&hellip; and '
                      f"{len(entries) - cap} more {kind}</li>")
        parts.append(f"<h3>{kind.capitalize()} ({len(entries)})</h3>"
                     f"<ul>{items}</ul>")
    return "".join(parts) or '<p class="muted">No catalog changes this period.</p>'


def _svg_trend(series: dict[str, list[tuple[str, int]]],
               width: int = 900, height: int = 260) -> str:
    """Inline SVG line chart: one line per competitor over run timestamps."""
    all_points = [p for pts in series.values() for p in pts]
    if not all_points:
        return '<p class="muted">No history yet — trends appear after two runs.</p>'
    xs = sorted({run_at for run_at, _ in all_points})
    y_max = max(n for _, n in all_points) or 1
    pad, x0, y0 = 40, 40, 10
    plot_w, plot_h = width - x0 - 10, height - y0 - pad

    def x_at(run_at: str) -> float:
        if len(xs) == 1:
            return x0 + plot_w / 2
        return x0 + plot_w * xs.index(run_at) / (len(xs) - 1)

    def y_at(n: int) -> float:
        return y0 + plot_h * (1 - n / y_max)

    shapes = [f'<line x1="{x0}" y1="{y0 + plot_h}" x2="{x0 + plot_w}" '
              f'y2="{y0 + plot_h}" stroke="#d1d5db"/>']
    for frac in (0, 0.5, 1):
        value = round(y_max * frac)
        shapes.append(f'<text x="{x0 - 6}" y="{y_at(value) + 4}" font-size="10" '
                      f'fill="#6b7280" text-anchor="end">{value}</text>')
    legend = []
    for i, (competitor, points) in enumerate(sorted(series.items())):
        color = _LINE_COLORS[i % len(_LINE_COLORS)]
        coords = " ".join(f"{x_at(r):.1f},{y_at(n):.1f}" for r, n in points)
        if len(points) > 1:
            shapes.append(f'<polyline points="{coords}" fill="none" '
                          f'stroke="{color}" stroke-width="2"/>')
        for r, n in points:
            shapes.append(f'<circle cx="{x_at(r):.1f}" cy="{y_at(n):.1f}" '
                          f'r="3" fill="{color}"/>')
        legend.append(f'<span><span class="dot" style="background:{color}">'
                      f"</span>{escape(competitor)}</span>")
    for run_at in (xs if len(xs) <= 8 else [xs[0], xs[len(xs) // 2], xs[-1]]):
        shapes.append(f'<text x="{x_at(run_at):.1f}" y="{height - 24}" '
                      f'font-size="10" fill="#6b7280" text-anchor="middle">'
                      f"{escape(str(run_at)[:10])}</text>")
    return (f'<div class="chart"><svg viewBox="0 0 {width} {height}" '
            f'width="100%" role="img">{"".join(shapes)}</svg>'
            f'<div class="legend">{"".join(legend)}</div></div>')


def _gather(store: Store, days: int):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    history: dict[str, list[tuple[str, int]]] = {}
    for run_at, competitor, total in store.count_history():
        history.setdefault(competitor, []).append((run_at, total))
    # Baseline seed loads are suppressed at the store layer (diff_courses
    # logs no change rows on a source's first snapshot), so everything here
    # is genuine competitor activity.
    changes = store.course_changes_since(since)
    insights = store.insights_since(since)
    run = store.latest_run()
    # NEW badges only when the newest insights came from the latest run —
    # quiet weeks must not keep last week's cards flagged NEW. (run_at is
    # shared between the runs and insights tables since the epilogue fix.)
    latest_insight_run = max((r[0] for r in insights), default=None)
    if not run or latest_insight_run != run[0]:
        latest_insight_run = None
    return {
        "run": run,
        "runs": store.runs_history(),
        "insights": insights,
        "latest_insight_run": latest_insight_run,
        "changes": changes,
        "counts": store.course_counts(),
        "competitors": store.competitors(),
        "history": history,
    }


def _index_page(data: dict, days: int) -> str:
    run_at, items_scanned, net_new, insight_count, summary = data["run"] or (
        "never", 0, 0, 0, "No pipeline runs recorded yet.")
    insights, changes = data["insights"], data["changes"]
    latest = data["latest_insight_run"]
    high = [r for r in insights if r[5] == "high"]

    kpis = "".join(
        f'<div class="kpi"><div class="n">{n}</div><div class="l">{label}</div></div>'
        for n, label in [
            (net_new, "new items this run"),
            (insight_count, "insights this run"),
            (sum(1 for row in changes if row[5] == "added"),
             f"courses added ({days}d)"),
            (sum(n for _, _, n in data["counts"]), "items tracked"),
        ])

    if high:
        whats_new = ("<h2>What&rsquo;s new — high significance</h2>"
                     f'<div class="cards">'
                     f'{"".join(_insight_card(r, latest) for r in high)}</div>')
    else:
        whats_new = ("<h2>What&rsquo;s new</h2><p class=\"muted\">No "
                     "high-significance moves detected this period.</p>")
    other = ""
    if insights:
        # Every insight (high included) with client-side filters — the cards
        # above stay a curated shortlist; this table is the searchable record.
        other = ("<h2>All detected updates</h2>"
                 + _filter_bar(insights)
                 + _insight_rows(insights, latest_run_at=latest,
                                 table_id="updates")
                 + _FILTER_SCRIPT)

    by_comp: dict[str, list[tuple]] = {}
    for row in changes:
        by_comp.setdefault(row[2], []).append(row)
    change_blocks = "".join(
        f'<div class="comp-block"><h3><a href="competitor-{slugify(comp)}.html">'
        f"{escape(comp)}</a></h3>{_changes_list(rows, cap=8)}</div>"
        for comp, rows in sorted(by_comp.items())) or \
        '<p class="muted">No catalog changes this period.</p>'

    counts_rows = "".join(
        f"<tr><td>{escape(comp)}</td><td>{escape(src)}</td><td>{n}</td></tr>"
        for comp, src, n in sorted(data["counts"], key=lambda r: -r[2])) or \
        '<tr><td colspan="3" class="muted">No catalogs tracked yet.</td></tr>'

    body = (
        "<h1>Competitor Learning-Platform Monitor</h1>"
        f'<div class="sub">Run {escape(str(run_at))} &middot; last {days} days '
        f"of changes &middot; {items_scanned} items scanned, {net_new} net-new</div>"
        f'<div class="summary">{escape(summary)}</div>'
        f'<div class="kpis">{kpis}</div>'
        f"{whats_new}{other}"
        f"<h2>Catalog changes ({days}d)</h2>{change_blocks}"
        f"{_run_history(data['runs'])}"
        "<h2>Catalog size by competitor</h2><table>"
        "<tr><th>Competitor</th><th>Source</th><th>Active items</th></tr>"
        f"{counts_rows}</table>"
    )
    return body


def _competitor_page(store: Store, competitor: str, data: dict,
                     days: int) -> str:
    insights = store.insights_for_competitor(competitor)
    changes = [row for row in data["changes"] if row[2] == competitor]
    counts = [(src, n) for comp, src, n in data["counts"] if comp == competitor]
    history = {competitor: data["history"].get(competitor, [])}

    counts_rows = "".join(
        f"<tr><td>{escape(src)}</td><td>{n}</td></tr>" for src, n in counts) or \
        '<tr><td colspan="2" class="muted">No catalog tracked.</td></tr>'
    insight_block = (_insight_rows(insights, show_competitor=False,
                                   latest_run_at=data["latest_insight_run"])
                     if insights else
                     '<p class="muted">No insights recorded yet — they appear '
                     "after the first analyzed run.</p>")
    body = (
        f"<h1>{escape(competitor)}</h1>"
        f'<div class="sub">Insight history for all runs; catalog changes '
        f"shown for the last {days} days.</div>"
        "<h2>Insight history</h2>" + insight_block +
        f"<h2>Catalog changes ({days}d)</h2>"
        f'<div class="comp-block">{_changes_list(changes)}</div>'
        "<h2>Catalog trend</h2>" + _svg_trend(history) +
        "<h2>Catalog</h2><table><tr><th>Source</th><th>Active items</th></tr>"
        f"{counts_rows}</table>"
    )
    return body


def export_site(store: Store, site_dir: Path = SITE_DIR,
                days: int = LOOKBACK_DAYS) -> Path:
    data = _gather(store, days)
    competitors = data["competitors"]
    site_dir.mkdir(exist_ok=True)

    pages = {"index.html": ("Overview", _index_page(data, days))}
    for competitor in competitors:
        pages[f"competitor-{slugify(competitor)}.html"] = (
            competitor, _competitor_page(store, competitor, data, days))

    for filename, (title, body) in pages.items():
        nav = _nav(competitors, filename)
        (site_dir / filename).write_text(_page(title, nav, body),
                                         encoding="utf-8")
    # Remove pages from earlier layouts (e.g. a dropped tab or a renamed
    # competitor) so stale files never linger on the published site.
    for stale in site_dir.glob("*.html"):
        if stale.name not in pages:
            stale.unlink()
    logger.info("Wrote %d pages to %s/", len(pages), site_dir)
    return site_dir / "index.html"
