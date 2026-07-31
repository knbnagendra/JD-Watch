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
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
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
