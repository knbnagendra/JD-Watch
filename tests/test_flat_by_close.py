from __future__ import annotations

from watch import store
from watch.checks import flat_by_close
from watch.severity import Severity

# Tests that need the check to actually run monkeypatch market_hours.now_et
# instead of relying on the real wall clock, so they're deterministic
# regardless of when they're run.


def make_positions(account: str, ticker: str, quantity: float, swing_eligible: bool = False) -> dict:
    return {
        "accounts": {
            account: {
                "positions": [
                    {"ticker": ticker, "quantity": quantity, "swing_eligible": swing_eligible, "chunks": []},
                ] if quantity != 0 else []
            }
        }
    }


def run_after_cutoff(ctx, monkeypatch, hour=16, minute=10):
    import datetime as dt
    from zoneinfo import ZoneInfo

    from watch import market_hours

    fixed_now = dt.datetime(2026, 7, 30, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    flat_by_close.run(ctx)


def test_noop_before_check_time(ctx, monkeypatch):
    import datetime as dt
    from zoneinfo import ZoneInfo

    from watch import market_hours

    fixed_now = dt.datetime(2026, 7, 30, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    ctx.relay.positions = make_positions("sandbox", "AAPL", 10)

    flat_by_close.run(ctx)

    assert ctx.alerter.calls == []
    assert ctx.relay.halt_calls == []


def test_flat_account_after_cutoff_no_alert(ctx, monkeypatch):
    ctx.relay.positions = {"accounts": {"sandbox": {"positions": []}}}
    run_after_cutoff(ctx, monkeypatch)
    assert ctx.alerter.calls == []


def test_open_non_swing_position_after_cutoff_halts_and_alerts(ctx, monkeypatch):
    ctx.relay.positions = make_positions("sandbox", "AAPL", 10, swing_eligible=False)
    run_after_cutoff(ctx, monkeypatch)

    assert ctx.relay.halt_calls == [("sandbox", False)]
    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.CRITICAL
    assert "AAPL" in ctx.alerter.calls[0].content


def test_swing_eligible_position_after_cutoff_not_a_violation(ctx, monkeypatch):
    ctx.relay.positions = make_positions("sandbox", "AAPL", 10, swing_eligible=True)
    run_after_cutoff(ctx, monkeypatch)

    assert ctx.relay.halt_calls == []
    assert ctx.alerter.calls == []


def test_realert_respects_interval(ctx, monkeypatch):
    ctx.watch_cfg = {"flat_by_close": {"realert_interval_seconds": 300}}
    ctx.relay.positions = make_positions("sandbox", "AAPL", 10)

    run_after_cutoff(ctx, monkeypatch)  # opens incident, alerts once
    run_after_cutoff(ctx, monkeypatch)  # immediately again -- should NOT realert yet

    assert len(ctx.alerter.calls) == 1


def test_resolved_after_becoming_flat(ctx, monkeypatch):
    ctx.relay.positions = make_positions("sandbox", "AAPL", 10)
    run_after_cutoff(ctx, monkeypatch)

    ctx.relay.positions = {"accounts": {"sandbox": {"positions": []}}}
    run_after_cutoff(ctx, monkeypatch)

    severities = [c.severity for c in ctx.alerter.calls]
    assert Severity.CRITICAL in severities
    assert Severity.INFO in severities
    assert store.get_open_incident(ctx.db, flat_by_close.NAME, "sandbox", "sandbox") is None


def test_covers_an_account_not_in_any_hardcoded_list(ctx, monkeypatch):
    """Regression: see test_stop_coverage.py's identical-purpose test --
    this check must cover any account GET /positions reports (e.g.
    schwab_live, added 2026-07-30), not a fixed set known at write time."""
    ctx.relay.positions = make_positions("schwab_live", "SPY", 10, swing_eligible=False)
    run_after_cutoff(ctx, monkeypatch)

    assert ctx.relay.halt_calls == [("schwab_live", False)]
    assert len(ctx.alerter.calls) == 1


def test_positions_fetch_failure_does_not_raise(ctx, monkeypatch):
    ctx.relay.raise_on_positions = True
    run_after_cutoff(ctx, monkeypatch)  # must not raise
    assert ctx.alerter.calls == []
