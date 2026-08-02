"""SQLite knowledge base — the pipeline's memory.

Everything the pipeline has ever seen lives here, so a run can tell the
difference between "AWS announced this today" and "AWS announced this three
weeks ago and we already reported it." Only net-new items flow onward to the
analysis layer.

It is also the system of record behind the Power BI tables: insights,
course_changes, course counts, and the per-run log all live here and are
exported by scraper/export/powerbi.py.
"""
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    id            TEXT PRIMARY KEY,   -- sha256 of the item URL
    source        TEXT NOT NULL,
    competitor    TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    published     TEXT,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    source        TEXT NOT NULL,
    course_id     TEXT NOT NULL,      -- stable key (course URL)
    competitor    TEXT NOT NULL,
    title         TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, course_id)
);

CREATE TABLE IF NOT EXISTS course_changes (
    run_at      TEXT NOT NULL,
    source      TEXT NOT NULL,
    competitor  TEXT NOT NULL,
    title       TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    change_type TEXT NOT NULL          -- 'added' | 'removed'
);

CREATE TABLE IF NOT EXISTS insights (
    run_at       TEXT NOT NULL,
    competitor   TEXT NOT NULL,
    category     TEXT NOT NULL,
    headline     TEXT NOT NULL,
    what_changed TEXT NOT NULL DEFAULT '',
    so_what      TEXT NOT NULL,
    significance TEXT NOT NULL,
    audience     TEXT NOT NULL DEFAULT 'unknown',
    ai_related   INTEGER NOT NULL DEFAULT 0,
    url          TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_at         TEXT NOT NULL,
    items_scanned  INTEGER NOT NULL,
    net_new_items  INTEGER NOT NULL,
    insight_count  INTEGER NOT NULL,
    summary        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_count_snapshots (
    run_at         TEXT NOT NULL,      -- catalog size per competitor per run,
    competitor     TEXT NOT NULL,      -- kept as history so the site can draw
    source         TEXT NOT NULL,      -- trend lines
    active_courses INTEGER NOT NULL
);
"""

# Columns added after the first release — applied to pre-existing databases.
_MIGRATIONS = [
    ("insights", "audience", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("insights", "ai_related", "INTEGER NOT NULL DEFAULT 0"),
    ("insights", "what_changed", "TEXT NOT NULL DEFAULT ''"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_now = now_iso


def item_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


# Below this fraction of the previous snapshot, a catalog fetch is treated as
# a suspected failure (partial render, transient 5xx) rather than a real mass
# removal — no diff is recorded for that source this run.
SHRINK_FLOOR = 0.5

# Idempotency keys: re-running the same logical run (workflow re-dispatch,
# restored CI cache) must not duplicate rows. Applied best-effort — an old DB
# that already contains duplicates just skips the index.
_UNIQUE_INDEXES = [
    ("idx_runs_run_at", "runs", "(run_at)"),
    ("idx_snapshots_key", "course_count_snapshots", "(run_at, competitor, source)"),
    ("idx_insights_key", "insights", "(run_at, competitor, headline)"),
    ("idx_changes_key", "course_changes", "(run_at, source, course_id, change_type)"),
]


class Store:
    def __init__(self, path: str | Path = "knowledge_base.db"):
        self.conn = sqlite3.connect(path)
        # Don't fail instantly if another local process (e.g. a concurrent
        # `python -m scraper site`) briefly holds the write lock.
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        for table, column, decl in _MIGRATIONS:
            cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")]
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        for name, table, cols in _UNIQUE_INDEXES:
            try:
                self.conn.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} {cols}")
            except sqlite3.IntegrityError:
                pass  # pre-existing duplicates in an old DB; index skipped
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_changes_run_at ON course_changes (run_at)")
        self.conn.commit()

    def filter_new_items(self, items: list) -> list:
        """Return only items whose URL the knowledge base has never seen.

        Read-only: items are not recorded until mark_items_seen() — called
        after analysis succeeds — so an API failure mid-run doesn't
        permanently swallow the batch.
        """
        new = []
        batch_ids = set()
        for item in items:
            iid = item_id(item.url)
            if iid in batch_ids:  # same URL from two sources in one run
                continue
            row = self.conn.execute(
                "SELECT 1 FROM seen_items WHERE id = ?", (iid,)
            ).fetchone()
            if row:
                continue
            batch_ids.add(iid)
            new.append(item)
        return new

    def mark_items_seen(self, items: list) -> None:
        """Record items so future runs treat them as known. Call only once
        the items have actually been analyzed (or deliberately skipped)."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen_items VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (item_id(i.url), i.source, i.competitor, i.url, i.title,
                 i.published, _now())
                for i in items
            ],
        )
        self.conn.commit()

    def diff_courses(self, source: str, current: list,
                     record: bool = True) -> tuple[list, list]:
        """Compare a fresh catalog snapshot against the stored one.

        Returns (added, removed_titles).

        record=False computes the diff without writing, so the pipeline can
        defer persistence until analysis succeeds — mirroring the
        filter_new_items / mark_items_seen split. A crashed analysis then
        retries the same diff instead of silently consuming it.

        Two guards live at this layer so every consumer inherits them:
        - Baseline: a source's first-ever snapshot records the courses but
          reports no adds and logs no change rows — a seed load is not
          competitor activity.
        - Shrink floor: if the snapshot shrank below SHRINK_FLOOR of the
          stored catalog (or came back empty), it's treated as a suspected
          fetch failure and skipped entirely rather than mass-"removing"
          live courses.
        """
        now = _now()
        known = {
            row[0]: (row[1], row[2])
            for row in self.conn.execute(
                "SELECT course_id, title, competitor FROM courses "
                "WHERE source = ? AND is_active = 1",
                (source,),
            )
        }
        if known and len(current) < max(1, SHRINK_FLOOR * len(known)):
            logger.error(
                "[%s] snapshot shrank from %d to %d courses — treating as a "
                "failed fetch, skipping this diff", source, len(known), len(current))
            return [], []

        is_baseline = not known
        current_ids = {c.course_id for c in current}
        added = [c for c in current if c.course_id not in known]
        removed = [(cid, title, competitor)
                   for cid, (title, competitor) in known.items()
                   if cid not in current_ids]

        if record:
            for c in current:
                if c.course_id in known:
                    self.conn.execute(
                        "UPDATE courses SET last_seen_at = ?, is_active = 1 "
                        "WHERE source = ? AND course_id = ?",
                        (now, source, c.course_id),
                    )
                else:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO courses VALUES (?, ?, ?, ?, ?, ?, 1)",
                        (c.source, c.course_id, c.competitor, c.title, now, now),
                    )
            for cid, title, competitor in removed:
                self.conn.execute(
                    "UPDATE courses SET is_active = 0 WHERE source = ? AND course_id = ?",
                    (source, cid),
                )
            if not is_baseline:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO course_changes VALUES (?, ?, ?, ?, ?, 'added')",
                    [(now, source, c.competitor, c.title, c.course_id)
                     for c in added],
                )
                self.conn.executemany(
                    "INSERT OR IGNORE INTO course_changes VALUES (?, ?, ?, ?, ?, 'removed')",
                    [(now, source, competitor, title, cid)
                     for cid, title, competitor in removed],
                )
            self.conn.commit()

        if is_baseline:
            return [], []
        return added, [title for _cid, title, _comp in removed]

    def course_counts(self) -> list[tuple[str, str, int]]:
        """(competitor, source, active course count) for the dashboard."""
        return self.conn.execute(
            "SELECT competitor, source, COUNT(*) FROM courses "
            "WHERE is_active = 1 GROUP BY competitor, source"
        ).fetchall()

    def course_changes_since(self, since_iso: str) -> list[tuple]:
        """course_changes rows recorded at or after the given ISO timestamp
        (ISO-8601 UTC strings compare correctly as text)."""
        return self.conn.execute(
            "SELECT run_at, source, competitor, title, course_id, change_type "
            "FROM course_changes WHERE run_at >= ?",
            (since_iso,),
        ).fetchall()

    def record_insights(self, insights: list[dict],
                        run_at: str | None = None) -> str:
        """Store this run's insights; returns the run_at timestamp used."""
        now = run_at or _now()
        self.conn.executemany(
            "INSERT OR IGNORE INTO insights (run_at, competitor, category, headline, "
            "what_changed, so_what, significance, audience, ai_related, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (now, i["competitor"], i["category"], i["headline"],
                 i.get("what_changed", ""),
                 i["so_what"], i["significance"], i.get("audience", "unknown"),
                 int(bool(i.get("ai_related"))), i.get("url", ""))
                for i in insights
            ],
        )
        self.conn.commit()
        return now

    def record_course_counts(self, run_at: str | None = None) -> None:
        """Snapshot current per-competitor catalog sizes for trend history."""
        run_at = run_at or _now()
        self.conn.executemany(
            "INSERT OR IGNORE INTO course_count_snapshots VALUES (?, ?, ?, ?)",
            [(run_at, competitor, source, n)
             for competitor, source, n in self.course_counts()],
        )
        self.conn.commit()

    def count_history(self) -> list[tuple]:
        """(run_at, competitor, total active) per run, oldest first."""
        return self.conn.execute(
            "SELECT run_at, competitor, SUM(active_courses) "
            "FROM course_count_snapshots GROUP BY run_at, competitor "
            "ORDER BY run_at"
        ).fetchall()

    def insights_for_competitor(self, competitor: str, limit: int = 100) -> list[tuple]:
        """Full insight history for one competitor, newest first."""
        return self.conn.execute(
            "SELECT run_at, competitor, category, headline, so_what, "
            "significance, audience, ai_related, url, what_changed "
            "FROM insights "
            "WHERE competitor = ? ORDER BY run_at DESC LIMIT ?",
            (competitor, limit),
        ).fetchall()

    def competitors(self) -> list[str]:
        """Every competitor seen in catalogs or insights, alphabetical."""
        return [r[0] for r in self.conn.execute(
            "SELECT competitor FROM courses WHERE competitor != '' UNION "
            "SELECT competitor FROM insights WHERE competitor != '' "
            "ORDER BY 1"
        )]

    def latest_run(self) -> tuple | None:
        """(run_at, items_scanned, net_new_items, insight_count, summary)
        for the most recent run, or None before the first run."""
        return self.conn.execute(
            "SELECT run_at, items_scanned, net_new_items, insight_count, "
            "summary FROM runs ORDER BY run_at DESC LIMIT 1"
        ).fetchone()

    def runs_history(self, limit: int = 12) -> list[tuple]:
        """Recent runs, newest first — the delta each run delivered."""
        return self.conn.execute(
            "SELECT run_at, items_scanned, net_new_items, insight_count, "
            "summary FROM runs ORDER BY run_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def insights_since(self, since_iso: str) -> list[tuple]:
        """insight rows recorded at or after the given ISO timestamp,
        newest first, high significance first within a run."""
        return self.conn.execute(
            "SELECT run_at, competitor, category, headline, so_what, "
            "significance, audience, ai_related, url, what_changed "
            "FROM insights "
            "WHERE run_at >= ? ORDER BY run_at DESC, "
            "CASE significance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
            "ELSE 2 END",
            (since_iso,),
        ).fetchall()

    def record_run(self, items_scanned: int, net_new_items: int,
                   insight_count: int, summary: str,
                   run_at: str | None = None) -> None:
        """One row per run. Pass the same run_at used for insights/snapshots
        so the tables share a joinable timestamp."""
        self.conn.execute(
            "INSERT OR IGNORE INTO runs VALUES (?, ?, ?, ?, ?)",
            (run_at or _now(), items_scanned, net_new_items, insight_count,
             summary),
        )
        self.conn.commit()
