from __future__ import annotations

import sqlite3

from watch import jd_relay_journal_db

_SCHEMA = """
CREATE TABLE alerts (alert_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE closed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT NOT NULL, ticker TEXT NOT NULL,
    exit_reason TEXT NOT NULL, pnl_dollars REAL NOT NULL, closed_at TEXT NOT NULL
);
"""


def make_journal_db(tmp_path, account):
    db_path = tmp_path / f"jd_relay_{account}.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return tmp_path, db_path


def insert_closed_trade(db_path, alert_id, product, ticker, closed_at, pnl_dollars=10.0, exit_reason="target"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO alerts VALUES (?, ?, ?)",
        (alert_id, closed_at, f'{{"product": "{product}"}}'),
    )
    conn.execute(
        "INSERT INTO closed_trades (alert_id, ticker, exit_reason, pnl_dollars, closed_at) VALUES (?, ?, ?, ?, ?)",
        (alert_id, ticker, exit_reason, pnl_dollars, closed_at),
    )
    conn.commit()
    conn.close()


def test_missing_db_returns_empty(tmp_path):
    assert jd_relay_journal_db.get_latest_closed_trade_by_product(str(tmp_path), "sandbox") == {}


def test_empty_tables_returns_empty(tmp_path):
    repo_path, _ = make_journal_db(tmp_path, "sandbox")
    assert jd_relay_journal_db.get_latest_closed_trade_by_product(str(repo_path), "sandbox") == {}


def test_returns_most_recent_close_per_product(tmp_path):
    repo_path, db_path = make_journal_db(tmp_path, "live")
    insert_closed_trade(db_path, "a1", "fuse", "SPY", "2026-07-20T10:00:00+00:00")
    insert_closed_trade(db_path, "a2", "fuse", "QQQ", "2026-07-25T10:00:00+00:00")  # more recent fuse close

    result = jd_relay_journal_db.get_latest_closed_trade_by_product(str(repo_path), "live")

    assert result["fuse"]["ticker"] == "QQQ"
    assert result["fuse"]["closed_at"] == "2026-07-25T10:00:00+00:00"
    assert result["fuse"]["account"] == "live"


def test_separates_by_product(tmp_path):
    repo_path, db_path = make_journal_db(tmp_path, "alpaca_live")
    insert_closed_trade(db_path, "a1", "beacon", "AAPL", "2026-07-20T10:00:00+00:00")
    insert_closed_trade(db_path, "a2", "fuse", "SPY", "2026-07-21T10:00:00+00:00")

    result = jd_relay_journal_db.get_latest_closed_trade_by_product(str(repo_path), "alpaca_live")

    assert set(result.keys()) == {"beacon", "fuse"}


def test_alert_with_no_resolvable_product_is_skipped(tmp_path):
    repo_path, db_path = make_journal_db(tmp_path, "sandbox")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO alerts VALUES ('a1', '2026-07-20T10:00:00+00:00', '{}')")  # no "product" key
    conn.execute(
        "INSERT INTO closed_trades (alert_id, ticker, exit_reason, pnl_dollars, closed_at) "
        "VALUES ('a1', 'SPY', 'target', 10.0, '2026-07-20T10:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    result = jd_relay_journal_db.get_latest_closed_trade_by_product(str(repo_path), "sandbox")

    assert result == {}


def test_all_accounts_merges_and_picks_most_recent(tmp_path):
    repo_path, live_db = make_journal_db(tmp_path, "live")
    insert_closed_trade(live_db, "a1", "fuse", "SPY", "2026-07-20T10:00:00+00:00")
    _, alpaca_db = make_journal_db(tmp_path, "alpaca_live")
    insert_closed_trade(alpaca_db, "a2", "fuse", "QQQ", "2026-07-25T10:00:00+00:00")  # more recent

    result = jd_relay_journal_db.get_latest_closed_trade_by_product_all_accounts(
        str(repo_path), ["live", "alpaca_live"],
    )

    assert result["fuse"]["account"] == "alpaca_live"
    assert result["fuse"]["ticker"] == "QQQ"


def test_all_accounts_missing_db_for_one_account_does_not_crash(tmp_path):
    repo_path, live_db = make_journal_db(tmp_path, "live")
    insert_closed_trade(live_db, "a1", "fuse", "SPY", "2026-07-20T10:00:00+00:00")
    # "schwab_live" has no db file at all

    result = jd_relay_journal_db.get_latest_closed_trade_by_product_all_accounts(
        str(repo_path), ["live", "schwab_live"],
    )

    assert result["fuse"]["account"] == "live"
