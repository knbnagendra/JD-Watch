"""Read-only access to JD-Signal's trading_bot.db -- regime classification
history and blocked-trade telemetry, both already persisted there
(regime_store.py's market_regime table, signal_audit_store.py's
signal_intents/gate_decisions tables). No new instrumentation: this reads
existing data JD-Signal already writes on every classification/gate
decision, the same way JD-Signal's own ops_monitor.py reads JD-Relay's
sqlite files read-only rather than importing jd_relay as a package.

Opened read-only (`mode=ro`) so a JD-Watch report can never contend for a
write lock with JD-Signal's own process, and a missing/locked file
degrades to empty results rather than raising -- a report failing to
build must never look like a check failing to run.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger("watch.jd_signal_db")


def _connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        log.warning("jd_signal_db_connect_failed path=%s", db_path, exc_info=True)
        return None


def get_latest_regime(jd_signal_repo_path: str) -> dict | None:
    """Most recent market_regime row, or None if the DB/table is missing
    or empty (e.g. REGIME_ENABLED=false, or JD-Signal hasn't classified
    yet today)."""
    conn = _connect_ro(Path(jd_signal_repo_path) / "trading_bot.db")
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT regime, confidence, observed_at FROM market_regime ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        log.warning("get_latest_regime_query_failed", exc_info=True)
        return None
    finally:
        conn.close()


def get_blocked_trade_counts(jd_signal_repo_path: str, since: datetime) -> list[dict]:
    """Count of failed (passed=0) gate_decisions per gate_name since
    `since` (UTC-aware). Ordered highest-count first -- a gate blocking
    nothing at all simply won't appear, which is itself informational
    (see JD-Signal's CLAUDE.md note on "a rule that blocks nothing may be
    dead code")."""
    conn = _connect_ro(Path(jd_signal_repo_path) / "trading_bot.db")
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT gate_name, COUNT(*) as blocked_count FROM gate_decisions "
            "WHERE passed = 0 AND decided_at >= ? GROUP BY gate_name ORDER BY blocked_count DESC",
            (since.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        log.warning("get_blocked_trade_counts_query_failed", exc_info=True)
        return []
    finally:
        conn.close()
