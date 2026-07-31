from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from watch import market_hours, store
from watch.reports import eod
from watch.severity import Severity

ET = ZoneInfo("America/New_York")


def run_at(ctx, monkeypatch, hour=16, minute=15, date=(2026, 7, 31)):
    fixed_now = dt.datetime(*date, hour, minute, tzinfo=ET)
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    eod.run(ctx)


def test_market_holiday_no_report(ctx, monkeypatch):
    run_at(ctx, monkeypatch, date=(2026, 1, 1))
    assert ctx.alerter.calls == []


def test_no_incidents_today_reports_none(ctx, monkeypatch):
    ctx.relay.get_status = lambda: {"accounts": {}}
    run_at(ctx, monkeypatch)
    assert "Incidents today: none" in ctx.alerter.calls[0].content


def test_incident_opened_today_is_listed(ctx, monkeypatch):
    ctx.relay.get_status = lambda: {"accounts": {}}
    store.open_incident(ctx.db, "stop_coverage", "sandbox", "sandbox:AAPL:c1", Severity.CRITICAL, "detail")

    run_at(ctx, monkeypatch)

    content = ctx.alerter.calls[0].content
    assert "Incidents today (1)" in content
    assert "OPEN" in content


def test_incident_from_yesterday_excluded(ctx, monkeypatch):
    ctx.relay.get_status = lambda: {"accounts": {}}
    # Manually backdate an incident's opened_at to well before today.
    incident_id = store.open_incident(ctx.db, "stop_coverage", "sandbox", "sandbox:AAPL:c1", Severity.CRITICAL, "")
    ctx.db.execute("UPDATE incidents SET opened_at = '2026-07-29T10:00:00+00:00' WHERE id = ?", (incident_id,))
    ctx.db.commit()

    run_at(ctx, monkeypatch)

    assert "Incidents today: none" in ctx.alerter.calls[0].content


def test_account_halt_and_degraded_state_reported(ctx, monkeypatch):
    ctx.relay.get_status = lambda: {
        "accounts": {
            "schwab_live": {"any_halted": True, "halt_reason": "daily_circuit_breaker: x",
                             "degraded_tickers": {"PYPL": "y"}},
            "sandbox": {"any_halted": False, "degraded_tickers": {}},
        }
    }

    run_at(ctx, monkeypatch)

    content = ctx.alerter.calls[0].content
    assert "schwab_live: HALTED (daily_circuit_breaker: x), degraded=['PYPL']" in content
    assert "sandbox: not halted" in content


def test_relay_unreachable_does_not_raise(ctx, monkeypatch):
    def _raise():
        raise RuntimeError("down")
    ctx.relay.get_status = _raise

    run_at(ctx, monkeypatch)  # must not raise

    assert "UNAVAILABLE" in ctx.alerter.calls[0].content
