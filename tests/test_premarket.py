from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from watch import market_hours
from watch.checks import killswitch_dryfire
from watch.reports import premarket
from watch.severity import Severity

ET = ZoneInfo("America/New_York")


def run_at(ctx, monkeypatch, hour=8, minute=45, weekday_date=(2026, 7, 31)):
    fixed_now = dt.datetime(*weekday_date, hour, minute, tzinfo=ET)
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    premarket.run(ctx)


def healthy_status(accounts=("sandbox", "live")):
    return {"accounts": {a: {"any_halted": False, "halt_reason": None, "degraded_tickers": {}} for a in accounts}}


def test_market_holiday_short_circuits(ctx, monkeypatch):
    run_at(ctx, monkeypatch, weekday_date=(2026, 1, 1))  # New Year's Day
    assert len(ctx.alerter.calls) == 1
    assert "holiday" in ctx.alerter.calls[0].content.lower()


def test_all_gates_pass_reports_go(ctx, monkeypatch):
    ctx.relay.get_status = lambda: healthy_status()
    run_at(ctx, monkeypatch)
    assert len(ctx.alerter.calls) == 1
    call = ctx.alerter.calls[0]
    assert call.severity == Severity.INFO
    assert "Overall: GO" in call.content


def test_relay_unreachable_is_nogo(ctx, monkeypatch):
    def _raise():
        raise RuntimeError("connection refused")
    ctx.relay.get_status = _raise

    run_at(ctx, monkeypatch)

    call = ctx.alerter.calls[0]
    assert call.severity == Severity.WARN
    assert "Overall: NO-GO" in call.content
    assert "UNREACHABLE" in call.content


def test_open_dryfire_incident_is_nogo(ctx, monkeypatch):
    ctx.relay.get_status = lambda: healthy_status()
    from watch import store
    key = f"{killswitch_dryfire.NAME}:sandbox"
    store.open_incident(ctx.db, killswitch_dryfire.NAME, "sandbox", key, Severity.CRITICAL, "dry-fire failed")

    run_at(ctx, monkeypatch)

    call = ctx.alerter.calls[0]
    assert "Overall: NO-GO" in call.content
    assert "dry-fire" in call.content.lower()


def test_low_disk_headroom_is_nogo(ctx, monkeypatch):
    ctx.relay.get_status = lambda: healthy_status()
    from watch.reports import common
    monkeypatch.setattr(common, "disk_headroom_pct", lambda path="/": 5.0)

    run_at(ctx, monkeypatch)

    call = ctx.alerter.calls[0]
    assert "Overall: NO-GO" in call.content
    assert "Disk headroom" in call.content


def test_halted_account_shown_explicitly_even_if_other_gates_pass(ctx, monkeypatch):
    status = healthy_status()
    status["accounts"]["schwab_live"] = {
        "any_halted": True, "halt_reason": "daily_circuit_breaker: x", "degraded_tickers": {},
    }
    ctx.relay.get_status = lambda: status

    run_at(ctx, monkeypatch)

    call = ctx.alerter.calls[0]
    assert "schwab_live: HALTED" in call.content
    assert "daily_circuit_breaker" in call.content


def test_config_change_noted_but_not_a_nogo(ctx, monkeypatch, tmp_path):
    ctx.relay.get_status = lambda: healthy_status()
    jd_signal = tmp_path / "JD-Signal"
    jd_signal.mkdir()
    rules = jd_signal / "rules.yaml"
    rules.write_text("x: 1\n")
    ctx.settings.jd_signal_repo_path = str(jd_signal)

    run_at(ctx, monkeypatch)  # establishes baseline hash
    rules.write_text("x: 2\n")
    run_at(ctx, monkeypatch)  # second run should see the change

    call = ctx.alerter.calls[-1]
    assert "Overall: GO" in call.content  # a config change alone doesn't block
    assert "Config changed" in call.content
