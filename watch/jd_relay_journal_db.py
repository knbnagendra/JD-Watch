"""Read-only access to JD-Relay's per-account journal DBs
(jd_relay_<account>.db) -- specifically each account's `alerts`/
`closed_trades` tables, to answer "has this product ever actually closed a
real trade live" for premarket_report's product live-cycle status. No new
instrumentation: reads data JD-Relay already writes on every real close,
the same read-only-sibling-file pattern jd_signal_db.py already uses for
JD-Signal's own database.

Opened read-only (`mode=ro`) so a JD-Watch report can never contend for a
write lock with JD-Relay's own process, and a missing file, missing table
(a fresh/empty db), or any query error degrades to an empty result rather
than raising -- a report failing to build must never look like a check
failing to run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("watch.jd_relay_journal_db")


def _connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        log.warning("jd_relay_journal_db_connect_failed path=%s", db_path, exc_info=True)
        return None


def get_latest_closed_trade_by_product(jd_relay_repo_path: str, account: str) -> dict:
    """Most recent closed_trades row per product for this one account,
    keyed by product -> {"ticker", "closed_at", "account"}. product comes
    from the alerts.payload_json this trade's alert_id joins back to; a
    trade whose alert has no resolvable "product" key is skipped rather
    than guessed at."""
    conn = _connect_ro(Path(jd_relay_repo_path) / f"jd_relay_{account}.db")
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT ct.ticker, ct.closed_at, a.payload_json FROM closed_trades ct "
            "JOIN alerts a ON a.alert_id = ct.alert_id ORDER BY ct.closed_at"
        ).fetchall()
    except sqlite3.Error:
        log.warning("get_latest_closed_trade_by_product_query_failed account=%s", account, exc_info=True)
        return {}
    finally:
        conn.close()

    result: dict = {}
    for row in rows:
        try:
            product = json.loads(row["payload_json"]).get("product")
        except (json.JSONDecodeError, AttributeError):
            product = None
        if not product:
            continue
        # Rows are ordered oldest-first, so the last write per product key
        # wins -- always the most recent close.
        result[product] = {"ticker": row["ticker"], "closed_at": row["closed_at"], "account": account}
    return result


def get_latest_closed_trade_by_product_all_accounts(jd_relay_repo_path: str, accounts: list[str]) -> dict:
    """Merges get_latest_closed_trade_by_product across every account,
    keeping only the most recent close per product overall. One account
    missing its db file (never traded yet, or a typo'd name) never blocks
    the others -- get_latest_closed_trade_by_product already degrades that
    to {} on its own."""
    merged: dict = {}
    for account in accounts:
        per_account = get_latest_closed_trade_by_product(jd_relay_repo_path, account)
        for product, info in per_account.items():
            existing = merged.get(product)
            if existing is None or info["closed_at"] > existing["closed_at"]:
                merged[product] = info
    return merged
