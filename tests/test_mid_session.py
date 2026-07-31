from __future__ import annotations

import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo

from watch import market_hours, store
from watch.reports import mid_session
from watch.severity import Severity

ET = ZoneInfo("America/New_York")

_SIGNAL_DB_SCHEMA = """
CREATE TABLE market_regime (
    regime_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, regime TEXT NOT NULL, confidence INTEGER
);
CREATE TABLE signal_intents (intent_id TEXT PRIMARY KEY);
CREATE TABLE gate_decisions (
    decision_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, gate_name TEXT NOT NULL,
    passed INTEGER NOT NULL, decided_at TEXT NOT NULL
);
"""


def make_jd_signal_db(tmp_path):
    (tmp_path / "trading_bot.db")
    conn = sqlite3.connect(tmp_path / "trading_bot.db")
    conn.executescript(_SIGNAL_DB_SCHEMA)
    conn.commit()
    conn.close()


def run_during_rth(ctx, monkeypatch, hour=11, minute=0):
    fixed_now = dt.datetime(2026, 7, 31, hour, minute, tzinfo=ET)  # Friday, RTH
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    mid_session.run(ctx)


def test_noop_outside_rth(ctx, monkeypatch):
    run_during_rth(ctx, monkeypatch, hour=20)  # after close
    assert ctx.alerter.calls == []


def test_no_open_incidents_reports_none(ctx, monkeypatch, tmp_path):
    make_jd_signal_db(tmp_path)
    ctx.settings.jd_signal_repo_path = str(tmp_path)
    ctx.relay.get_status = lambda: {"accounts": {}}

    run_during_rth(ctx, monkeypatch)

    assert "Open incidents: none" in ctx.alerter.calls[0].content


def test_open_incident_listed(ctx, monkeypatch, tmp_path):
    make_jd_signal_db(tmp_path)
    ctx.settings.jd_signal_repo_path = str(tmp_path)
    ctx.relay.get_status = lambda: {"accounts": {}}
    store.open_incident(ctx.db, "stop_coverage", "sandbox", "sandbox:AAPL:c1", Severity.CRITICAL, "")

    run_during_rth(ctx, monkeypatch)

    content = ctx.alerter.calls[0].content
    assert "Open incidents (1)" in content
    assert "stop_coverage" in content


def test_regime_reported_when_present(ctx, monkeypatch, tmp_path):
    make_jd_signal_db(tmp_path)
    ctx.settings.jd_signal_repo_path = str(tmp_path)
    ctx.relay.get_status = lambda: {"accounts": {}}
    conn = sqlite3.connect(tmp_path / "trading_bot.db")
    conn.execute("INSERT INTO market_regime VALUES ('r1', '2026-07-31T14:00:00+00:00', 'PIN', 80)")
    conn.commit()
    conn.close()

    run_during_rth(ctx, monkeypatch)

    assert "Regime: PIN" in ctx.alerter.calls[0].content


def test_regime_unavailable_when_no_data(ctx, monkeypatch, tmp_path):
    make_jd_signal_db(tmp_path)
    ctx.settings.jd_signal_repo_path = str(tmp_path)
    ctx.relay.get_status = lambda: {"accounts": {}}

    run_during_rth(ctx, monkeypatch)

    assert "Regime: unavailable" in ctx.alerter.calls[0].content


def test_degraded_tickers_reported(ctx, monkeypatch, tmp_path):
    make_jd_signal_db(tmp_path)
    ctx.settings.jd_signal_repo_path = str(tmp_path)
    ctx.relay.get_status = lambda: {"accounts": {"schwab_live": {"degraded_tickers": {"PYPL": "x"}}}}

    run_during_rth(ctx, monkeypatch)

    assert "Degraded (schwab_live): PYPL" in ctx.alerter.calls[0].content


def test_relay_unreachable_does_not_raise(ctx, monkeypatch, tmp_path):
    make_jd_signal_db(tmp_path)
    ctx.settings.jd_signal_repo_path = str(tmp_path)

    def _raise():
        raise RuntimeError("down")
    ctx.relay.get_status = _raise

    run_during_rth(ctx, monkeypatch)  # must not raise

    assert "UNAVAILABLE" in ctx.alerter.calls[0].content
