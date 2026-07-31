from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from watch import jd_signal_db

_SCHEMA = """
CREATE TABLE market_regime (
    regime_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, regime TEXT NOT NULL, confidence INTEGER
);
CREATE TABLE signal_intents (intent_id TEXT PRIMARY KEY);
CREATE TABLE gate_decisions (
    decision_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, gate_name TEXT NOT NULL,
    passed INTEGER NOT NULL, decided_at TEXT NOT NULL
);
"""


def make_trading_bot_db(tmp_path):
    db_path = tmp_path / "trading_bot.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return tmp_path, db_path


def test_get_latest_regime_returns_none_when_db_missing(tmp_path):
    assert jd_signal_db.get_latest_regime(str(tmp_path)) is None


def test_get_latest_regime_returns_none_when_table_empty(tmp_path):
    repo_path, _ = make_trading_bot_db(tmp_path)
    assert jd_signal_db.get_latest_regime(str(repo_path)) is None


def test_get_latest_regime_returns_most_recent_row(tmp_path):
    repo_path, db_path = make_trading_bot_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO market_regime VALUES ('r1', '2026-07-31T10:00:00+00:00', 'DRIFT', 70)")
    conn.execute("INSERT INTO market_regime VALUES ('r2', '2026-07-31T11:00:00+00:00', 'PIN', 85)")
    conn.commit()
    conn.close()

    result = jd_signal_db.get_latest_regime(str(repo_path))

    assert result["regime"] == "PIN"
    assert result["confidence"] == 85


def test_get_blocked_trade_counts_groups_and_orders_by_count(tmp_path):
    repo_path, db_path = make_trading_bot_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO signal_intents VALUES ('i1')")
    rows = [
        ("d1", "i1", "earnings_blackout", 0, "2026-07-31T10:00:00+00:00"),
        ("d2", "i1", "earnings_blackout", 0, "2026-07-31T11:00:00+00:00"),
        ("d3", "i1", "regime_gate", 0, "2026-07-31T12:00:00+00:00"),
        ("d4", "i1", "earnings_blackout", 1, "2026-07-31T13:00:00+00:00"),  # passed=1, must not count
    ]
    conn.executemany("INSERT INTO gate_decisions VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    since = datetime(2026, 7, 31, tzinfo=timezone.utc)
    result = jd_signal_db.get_blocked_trade_counts(str(repo_path), since)

    assert result == [
        {"gate_name": "earnings_blackout", "blocked_count": 2},
        {"gate_name": "regime_gate", "blocked_count": 1},
    ]


def test_get_blocked_trade_counts_respects_since(tmp_path):
    repo_path, db_path = make_trading_bot_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO signal_intents VALUES ('i1')")
    conn.execute("INSERT INTO gate_decisions VALUES ('d1', 'i1', 'regime_gate', 0, '2026-07-30T10:00:00+00:00')")
    conn.commit()
    conn.close()

    since = datetime(2026, 7, 31, tzinfo=timezone.utc)  # after the one row above
    result = jd_signal_db.get_blocked_trade_counts(str(repo_path), since)

    assert result == []


def test_get_blocked_trade_counts_missing_db_returns_empty(tmp_path):
    assert jd_signal_db.get_blocked_trade_counts(str(tmp_path), datetime.now(timezone.utc)) == []
