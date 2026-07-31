from __future__ import annotations

from watch import store
from watch.checks import stop_coverage
from watch.severity import Severity


def make_positions(account: str, ticker: str, chunk_id: str, *, entry_protective_failed: bool,
                    status: str = "open", live_stop_status: str | None = None, quantity: float = 10) -> dict:
    return {
        "accounts": {
            account: {
                "positions": [
                    {
                        "ticker": ticker,
                        "quantity": quantity,
                        "swing_eligible": False,
                        "chunks": [
                            {
                                "client_order_id": chunk_id,
                                "status": status,
                                "current_stop_price": None,
                                "broker_order_id": "ord-1",
                                "entry_protective_failed": entry_protective_failed,
                                "is_protective_oco": False,
                                "live_stop_status": live_stop_status,
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


def test_live_stop_status_canceled_fires_even_when_entry_protective_failed_is_false(ctx):
    """The actual gap fix: entry_protective_failed=False (protection
    succeeded at entry) but the stop was later externally canceled --
    entry_protective_failed alone would never catch this; live_stop_status
    must."""
    ctx.relay.positions = make_positions(
        "sandbox", "AAPL", "c1", entry_protective_failed=False, live_stop_status="canceled",
    )

    stop_coverage.run(ctx)
    stop_coverage.run(ctx)

    assert ctx.relay.halt_calls == [("sandbox", False)]
    assert len(ctx.alerter.calls) == 1
    assert "live_stop_status=canceled" in ctx.alerter.calls[0].content


def test_live_stop_status_resting_is_not_a_violation(ctx):
    ctx.relay.positions = make_positions(
        "sandbox", "AAPL", "c1", entry_protective_failed=False, live_stop_status="open",
    )
    for _ in range(5):
        stop_coverage.run(ctx)
    assert ctx.alerter.calls == []


def test_live_stop_status_none_is_not_a_violation_on_its_own(ctx):
    """None means "not checked" (e.g. never-yet-replaced bracket, or a
    transient fetch failure) -- must not be treated as a violation by
    itself; only entry_protective_failed or a genuinely terminal
    live_stop_status should fire."""
    ctx.relay.positions = make_positions(
        "sandbox", "AAPL", "c1", entry_protective_failed=False, live_stop_status=None,
    )
    for _ in range(5):
        stop_coverage.run(ctx)
    assert ctx.alerter.calls == []


def test_zero_quantity_position_never_flagged_even_with_canceled_stop(ctx):
    """Regression, live sandbox smoke test 2026-07-31: right after a
    flatten, quantity is already 0 at the broker but a chunk's own status
    field hasn't caught up to "closed" yet, while live_stop_status
    correctly shows "canceled" (closing a position cancels its resting
    protective legs). Without this guard that transient window reads as
    an unprotected position that needs a halt, even though there's
    nothing left to protect."""
    ctx.relay.positions = make_positions(
        "sandbox", "SPY", "c1", entry_protective_failed=False,
        live_stop_status="canceled", quantity=0,
    )
    for _ in range(5):
        stop_coverage.run(ctx)
    assert ctx.alerter.calls == []
    assert ctx.relay.halt_calls == []


def test_position_going_to_zero_quantity_auto_resolves_an_open_incident(ctx):
    ctx.relay.positions = make_positions(
        "sandbox", "SPY", "c1", entry_protective_failed=False, live_stop_status="canceled", quantity=10,
    )
    stop_coverage.run(ctx)
    stop_coverage.run(ctx)  # confirmed, incident open, sandbox halted

    ctx.relay.positions = make_positions(
        "sandbox", "SPY", "c1", entry_protective_failed=False, live_stop_status="canceled", quantity=0,
    )
    stop_coverage.run(ctx)

    severities = [c.severity for c in ctx.alerter.calls]
    assert Severity.CRITICAL in severities
    assert Severity.INFO in severities
    assert store.get_open_incident(ctx.db, stop_coverage.NAME, "sandbox", "sandbox:SPY:c1") is None


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
