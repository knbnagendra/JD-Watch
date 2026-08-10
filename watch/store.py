"""SQLite persistence for check results and incidents.

Two tables:
- check_results: an append-only log of every check run, for the weekly
  reliability digest (later phase) and for manual debugging.
- incidents: one open row per (check_name, account, key) violation
  currently in progress. Persisted (not in-memory) specifically so a
  JD-Watch restart mid-incident doesn't lose "this was already alerted and
  auto-actioned" state and re-fire the same halt/page from scratch -- the
  same "persisted to disk so a restart doesn't forget" principle
  JD-Signal's candidate_cooldown.py already applies to signal cooldowns.

Deliberately not an ORM -- two tables, a handful of queries, matching the
plain-sqlite3 style already used by JD-Relay's journal.py and JD-Signal's
regime_store.py rather than adding a new dependency for this scope.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from watch.severity import Severity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    severity TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    last_alerted_at TEXT,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_incidents_lookup ON incidents(check_name, account, key);
CREATE INDEX IF NOT EXISTS idx_incidents_opened_at ON incidents(opened_at);

-- One row per watched config file (rules.yaml, universe.yaml, config.yaml)
-- -- the pre-market/EOD reports diff against this to flag an unreviewed
-- change since the last report, not to validate/reload anything itself
-- (that stays entirely JD-Signal/JD-Relay's own job).
CREATE TABLE IF NOT EXISTS config_state (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

-- One row per weekly trade-validation cycle (see reports/trade_validation.py)
-- -- append-only, so "N consecutive clean weeks" is derived from real
-- history, not a counter that could silently drift from what actually
-- happened. `clean` = zero approximate exits AND zero new CRITICAL
-- incidents that week; `streak_after` is the consecutive-clean-week count
-- as of this row (0 the moment any week isn't clean).
CREATE TABLE IF NOT EXISTS validation_weeks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_ending TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    clean INTEGER NOT NULL,
    streak_after INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

-- Manually-logged real bug fixes (log_bug.py CLI, one row per fix, across
-- any of the three repos) -- append-only, so "days since last bug fix" and
-- "bugs fixed this week" (weekly_digest) are derived from a real record a
-- human deliberately made, not inferred from commit messages or incident
-- resolution (neither of which reliably distinguishes "a real bug" from
-- routine work).
CREATE TABLE IF NOT EXISTS bug_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    description TEXT NOT NULL,
    commit_sha TEXT NOT NULL DEFAULT '',
    logged_at TEXT NOT NULL
);

-- One row per registered check, tracking engine.py's CheckSpec._last_run_date/
-- _last_run_ts across restarts. Confirmed live 2026-08-10: those fields were
-- in-memory-only, so any process restart landing after a daily_at_et check's
-- scheduled time (e.g. a deploy in the afternoon) made CheckSpec.due() see a
-- fresh None _last_run_date and re-fire that same check again the same day --
-- harmless for killswitch_dryfire (sandbox-only halt/rearm) but would
-- double-count a trade_validation week (corrupting its consecutive-clean-
-- weeks streak) or double-post/double-commit any other daily report if a
-- restart ever lands after its scheduled time.
CREATE TABLE IF NOT EXISTS check_schedule (
    check_name TEXT PRIMARY KEY,
    last_run_date TEXT,
    last_run_ts REAL NOT NULL DEFAULT 0
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False: engine.py runs each check via
    # asyncio.to_thread(), which the default executor may hand to a
    # different worker thread on each tick -- confirmed live 2026-07-31,
    # first production deploy: sqlite3.ProgrammingError on the very first
    # tick since this connection is built once in main()'s startup thread.
    # Safe to disable sqlite3's own same-thread guard here specifically
    # because Engine.tick() awaits each check sequentially (a plain for
    # loop, not asyncio.gather) -- exactly one check's run_fn ever touches
    # this connection at a time, so there's no real concurrent-access race
    # for the guard to have been protecting against.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_check_result(conn: sqlite3.Connection, check_name: str, account: str,
                         severity: Severity, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO check_results (check_name, account, ts, severity, detail) VALUES (?, ?, ?, ?, ?)",
        (check_name, account, _now_iso(), severity.value, detail),
    )
    conn.commit()


def get_open_incident(conn: sqlite3.Connection, check_name: str, account: str, key: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM incidents WHERE check_name = ? AND account = ? AND key = ? AND closed_at IS NULL",
        (check_name, account, key),
    ).fetchone()
    return row


def get_open_incidents(conn: sqlite3.Connection, check_name: str, account: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM incidents WHERE check_name = ? AND account = ? AND closed_at IS NULL",
        (check_name, account),
    ).fetchall()


def open_incident(conn: sqlite3.Connection, check_name: str, account: str, key: str,
                   severity: Severity, detail: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO incidents (check_name, account, key, severity, opened_at, detail) VALUES (?, ?, ?, ?, ?, ?)",
        (check_name, account, key, severity.value, _now_iso(), detail),
    )
    conn.commit()
    return cur.lastrowid


def mark_incident_alerted(conn: sqlite3.Connection, incident_id: int) -> None:
    conn.execute("UPDATE incidents SET last_alerted_at = ? WHERE id = ?", (_now_iso(), incident_id))
    conn.commit()


def close_incident(conn: sqlite3.Connection, incident_id: int) -> None:
    conn.execute("UPDATE incidents SET closed_at = ? WHERE id = ?", (_now_iso(), incident_id))
    conn.commit()


def should_realert(conn: sqlite3.Connection, incident_id: int, realert_interval_seconds: float) -> bool:
    row = conn.execute("SELECT last_alerted_at FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if row is None or row["last_alerted_at"] is None:
        return True
    last = datetime.fromisoformat(row["last_alerted_at"])
    return (datetime.now(timezone.utc) - last).total_seconds() >= realert_interval_seconds


def get_all_open_incidents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every currently-open incident across every check/account -- the
    mid-session snapshot's status matrix."""
    return conn.execute("SELECT * FROM incidents WHERE closed_at IS NULL ORDER BY opened_at").fetchall()


def get_incidents_since(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    """Every incident (open or already closed) opened at/after `since` --
    the EOD/weekly reports' timeline."""
    return conn.execute(
        "SELECT * FROM incidents WHERE opened_at >= ? ORDER BY opened_at", (since.isoformat(),),
    ).fetchall()


def get_config_hash(conn: sqlite3.Connection, path: str) -> str | None:
    row = conn.execute("SELECT sha256 FROM config_state WHERE path = ?", (path,)).fetchone()
    return row["sha256"] if row else None


def set_config_hash(conn: sqlite3.Connection, path: str, sha256: str) -> None:
    conn.execute(
        "INSERT INTO config_state (path, sha256, last_seen_at) VALUES (?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, last_seen_at = excluded.last_seen_at",
        (path, sha256, _now_iso()),
    )
    conn.commit()


def get_validation_streak(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT streak_after FROM validation_weeks ORDER BY id DESC LIMIT 1").fetchone()
    return row["streak_after"] if row else 0


def record_validation_week(conn: sqlite3.Connection, week_ending: str, clean: bool, detail: str = "") -> int:
    """Appends this week's result and returns the new streak. Never
    updates a prior row -- the whole point is a real, append-only history
    a "N consecutive clean weeks" claim can point back to."""
    streak = (get_validation_streak(conn) + 1) if clean else 0
    conn.execute(
        "INSERT INTO validation_weeks (week_ending, recorded_at, clean, streak_after, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (week_ending, _now_iso(), 1 if clean else 0, streak, detail),
    )
    conn.commit()
    return streak


def record_bug(conn: sqlite3.Connection, repo: str, description: str, commit_sha: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO bug_log (repo, description, commit_sha, logged_at) VALUES (?, ?, ?, ?)",
        (repo, description, commit_sha, _now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def days_since_last_bug(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT logged_at FROM bug_log ORDER BY logged_at DESC LIMIT 1").fetchone()
    if row is None:
        return None
    logged_at = datetime.fromisoformat(row["logged_at"])
    return (datetime.now(timezone.utc) - logged_at).days


def get_bugs_since(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM bug_log WHERE logged_at >= ? ORDER BY logged_at", (since.isoformat(),),
    ).fetchall()


def get_check_schedule(conn: sqlite3.Connection, check_name: str) -> tuple[str | None, float]:
    row = conn.execute(
        "SELECT last_run_date, last_run_ts FROM check_schedule WHERE check_name = ?", (check_name,),
    ).fetchone()
    if row is None:
        return None, 0.0
    return row["last_run_date"], row["last_run_ts"]


def set_check_schedule(conn: sqlite3.Connection, check_name: str, last_run_date: str | None, last_run_ts: float) -> None:
    conn.execute(
        "INSERT INTO check_schedule (check_name, last_run_date, last_run_ts) VALUES (?, ?, ?) "
        "ON CONFLICT(check_name) DO UPDATE SET last_run_date = excluded.last_run_date, last_run_ts = excluded.last_run_ts",
        (check_name, last_run_date, last_run_ts),
    )
    conn.commit()
