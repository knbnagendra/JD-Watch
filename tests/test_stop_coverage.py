from __future__ import annotations

from watch import store
from watch.checks import stop_coverage
from watch.severity import Severity


def make_positions(account: str, ticker: str, chunk_id: str, *, entry_protective_failed: bool,
                    status: str = "open") -> dict:
    return {
        "accounts": {
            account: {
                "positions": [
                    {
                        "ticker": ticker,
                        "quantity": 10,
                        "swing_eligible": False,
                        "chunks": [
                            {
                                "client_order_id": chunk_id,
                                "status": status,
                                "current_stop_price": None,
                                "broker_order_id": "ord-1",
                                "entry_protective_failed": entry_protective_failed,
                                "is_protective_oco": False,
                            }
                        ],
                    }
                ]
            }
        }
    }


def setup_function(_fn):
    # stop_coverage's confirm-poll counter is module-level state (see its
    # own docstring for why) -- must reset between tests or an earlier
    # test's counts leak into the next one.
    stop_coverage._pending.clear()


def test_healthy_position_never_flagged(ctx):
    ctx.relay.positions = make_positions("sandbox", "AAPL", "c1", entry_protective_failed=False)
    for _ in range(5):
        stop_coverage.run(ctx)
    assert ctx.alerter.calls == []
    assert ctx.relay.halt_calls == []


def test_single_poll_violation_not_yet_confirmed(ctx):
    ctx.relay.positions = make_positions("sandbox", "AAPL", "c1", entry_protective_failed=True)

    stop_coverage.run(ctx)  # 1st poll -- below confirm_polls default of 2

    assert ctx.alerter.calls == []
    assert ctx.relay.halt_calls == []


def test_violation_confirmed_after_two_polls_halts_and_alerts(ctx):
    ctx.relay.positions = make_positions("sandbox", "AAPL", "c1", entry_protective_failed=True)

    stop_coverage.run(ctx)  # poll 1
    stop_coverage.run(ctx)  # poll 2 -- confirms

    assert ctx.relay.halt_calls == [("sandbox", False)]
    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.CRITICAL
    assert "AAPL" in ctx.alerter.calls[0].content


def test_confirmed_violation_only_halts_once_not_every_poll(ctx):
    ctx.relay.positions = make_positions("sandbox", "AAPL", "c1", entry_protective_failed=True)

    for _ in range(5):
        stop_coverage.run(ctx)

    assert ctx.relay.halt_calls == [("sandbox", False)]
    assert len(ctx.alerter.calls) == 1


def test_resolved_violation_closes_incident_and_sends_info_alert(ctx):
    ctx.relay.positions = make_positions("sandbox", "AAPL", "c1", entry_protective_failed=True)
    stop_coverage.run(ctx)
    stop_coverage.run(ctx)  # confirmed, incident open

    ctx.relay.positions = make_positions("sandbox", "AAPL", "c1", entry_protective_failed=False)
    stop_coverage.run(ctx)

    severities = [c.severity for c in ctx.alerter.calls]
    assert Severity.CRITICAL in severities
    assert Severity.INFO in severities
    assert store.get_open_incident(ctx.db, stop_coverage.NAME, "sandbox", "sandbox:AAPL:c1") is None


def test_halt_call_failure_still_alerts_with_failure_noted(ctx):
    ctx.relay.raise_on_halt = True
    ctx.relay.positions = make_positions("sandbox", "AAPL", "c1", entry_protective_failed=True)

    stop_coverage.run(ctx)
    stop_coverage.run(ctx)

    assert len(ctx.alerter.calls) == 1
    assert "HALT ATTEMPT FAILED" in ctx.alerter.calls[0].content


def test_positions_fetch_failure_does_not_raise(ctx):
    ctx.relay.raise_on_positions = True
    stop_coverage.run(ctx)  # must not raise
    assert ctx.alerter.calls == []


def test_terminal_status_chunk_never_flagged(ctx):
    ctx.relay.positions = make_positions(
        "sandbox", "AAPL", "c1", entry_protective_failed=True, status="closed",
    )
    for _ in range(5):
        stop_coverage.run(ctx)
    assert ctx.alerter.calls == []


def test_covers_an_account_not_in_any_hardcoded_list(ctx):
    """Regression: an earlier version of this check iterated a fixed
    ACCOUNTS tuple (sandbox/live/alpaca_sandbox/alpaca_live) instead of
    whatever GET /positions actually returns -- config.yaml grew a 5th
    account (schwab_live, added 2026-07-30 alongside live MOC-lotto
    autotrading) that a fixed tuple would have silently left unmonitored.
    Any account name JD-Relay reports must be covered, not just the ones
    known when this code was written."""
    ctx.relay.positions = make_positions("schwab_live", "SPY", "c1", entry_protective_failed=True)

    stop_coverage.run(ctx)
    stop_coverage.run(ctx)

    assert ctx.relay.halt_calls == [("schwab_live", False)]
    assert len(ctx.alerter.calls) == 1


def test_multiple_accounts_tracked_independently(ctx):
    ctx.relay.positions = {
        "accounts": {
            "sandbox": make_positions("sandbox", "AAPL", "c1", entry_protective_failed=True)["accounts"]["sandbox"],
            "live": make_positions("live", "MSFT", "c2", entry_protective_failed=False)["accounts"]["live"],
        }
    }
    stop_coverage.run(ctx)
    stop_coverage.run(ctx)

    assert ctx.relay.halt_calls == [("sandbox", False)]
