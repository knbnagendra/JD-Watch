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
    key = f"{killswitch_dryfire.NAME}:tradier_sandbox"
    store.open_incident(ctx.db, killswitch_dryfire.NAME, "tradier_sandbox", key, Severity.CRITICAL, "dry-fire failed")

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


def _make_jd_relay_journal(tmp_path, account, product, ticker, closed_at):
    import sqlite3
    db_path = tmp_path / f"jd_relay_{account}.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE alerts (alert_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, payload_json TEXT NOT NULL);"
        "CREATE TABLE closed_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT NOT NULL, "
        "ticker TEXT NOT NULL, exit_reason TEXT NOT NULL, pnl_dollars REAL NOT NULL, closed_at TEXT NOT NULL);"
    )
    conn.execute("INSERT INTO alerts VALUES ('a1', ?, ?)", (closed_at, f'{{"product": "{product}"}}'))
    conn.execute(
        "INSERT INTO closed_trades (alert_id, ticker, exit_reason, pnl_dollars, closed_at) "
        "VALUES ('a1', ?, 'target', 10.0, ?)",
        (ticker, closed_at),
    )
    conn.commit()
    conn.close()


def test_product_cycle_status_unavailable_when_repo_path_not_configured(ctx, monkeypatch):
    ctx.relay.get_status = lambda: healthy_status()
    run_at(ctx, monkeypatch)
    call = ctx.alerter.calls[0]
    assert "unavailable (jd_relay_repo_path not configured" in call.content


def test_product_cycle_status_shows_complete_when_closed_trade_exists(ctx, monkeypatch, tmp_path):
    ctx.relay.get_status = lambda: healthy_status(accounts=("live",))
    ctx.settings.jd_relay_repo_path = str(tmp_path)
    _make_jd_relay_journal(tmp_path, "live", "fuse", "SPY", "2026-07-25T10:00:00+00:00")

    run_at(ctx, monkeypatch)

    content = ctx.alerter.calls[0].content
    assert "fuse: cycle complete (last real close 2026-07-25T10:00:00+00:00, SPY on live)" in content
    assert "sentinel: NOT YET completed live" in content


def test_product_cycle_status_all_not_yet_when_nothing_closed(ctx, monkeypatch, tmp_path):
    ctx.relay.get_status = lambda: healthy_status(accounts=("live",))
    ctx.settings.jd_relay_repo_path = str(tmp_path)
    (tmp_path / "jd_relay_live.db").touch()  # exists but empty -- no tables

    run_at(ctx, monkeypatch)

    content = ctx.alerter.calls[0].content
    for product in ("fuse", "sentinel", "swing", "beacon", "spx_moc_lotto"):
        assert f"{product}: NOT YET completed live" in content
