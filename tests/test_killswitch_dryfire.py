from __future__ import annotations

from watch.checks import killswitch_dryfire
from watch.severity import Severity


def test_targets_are_hardcoded_never_from_config():
    """The whole point of this check: a config typo must never be able to
    point a dry-fire test at a live account."""
    assert killswitch_dryfire.TARGETS == ("sandbox", "alpaca_sandbox")
    assert "live" not in killswitch_dryfire.TARGETS
    assert "alpaca_live" not in killswitch_dryfire.TARGETS


def test_successful_dry_fire_halts_then_rearms_both_targets_no_alert(ctx):
    killswitch_dryfire.run(ctx)

    assert set(ctx.relay.halt_calls) == {("sandbox", False), ("alpaca_sandbox", False)}
    assert set(ctx.relay.rearm_calls) == {"sandbox", "alpaca_sandbox"}
    # Final state confirmed cleared for both.
    assert ctx.relay.halted == {"sandbox": False, "alpaca_sandbox": False}
    assert ctx.alerter.calls == []


def test_halt_never_confirmed_alerts_critical_but_still_rearms(ctx):
    # FakeRelayClient.halt() sets halted[account]=True immediately, but a
    # real broken mechanism would mean /control/status never reflects it --
    # simulate that by having get_status() always report False.
    ctx.relay.get_status = lambda: {"accounts": {"sandbox": {"manual_halt": False},
                                                  "alpaca_sandbox": {"manual_halt": False}}}
    # Small timeout so the polling loop's real time.sleep() doesn't slow
    # this test down -- the behavior under test doesn't depend on the
    # timeout's exact value, only on it eventually giving up.
    ctx.watch_cfg = {"killswitch_dryfire": {"poll_timeout_seconds": 0.1}}

    killswitch_dryfire.run(ctx)

    assert len(ctx.alerter.calls) == 2  # one per target, both failed to confirm halt
    assert all(c.severity == Severity.CRITICAL for c in ctx.alerter.calls)
    assert all("halt_confirmed=False" in c.content for c in ctx.alerter.calls)
    # Rearm was still attempted for both, per the "never leaves it halted" guarantee.
    assert set(ctx.relay.rearm_calls) == {"sandbox", "alpaca_sandbox"}


def test_halt_call_exception_still_attempts_rearm_and_alerts(ctx):
    ctx.relay.raise_on_halt = True

    killswitch_dryfire.run(ctx)

    assert len(ctx.alerter.calls) == 2
    assert all("halt call failed" in c.content for c in ctx.alerter.calls)
    assert set(ctx.relay.rearm_calls) == {"sandbox", "alpaca_sandbox"}


def test_rearm_call_exception_alerts_even_if_halt_succeeded(ctx):
    ctx.relay.raise_on_rearm = True

    killswitch_dryfire.run(ctx)

    assert len(ctx.alerter.calls) == 2
    assert all("rearm call failed" in c.content for c in ctx.alerter.calls)


def test_success_resolves_any_prior_alert_state(ctx):
    ctx.alerter.alert(f"{killswitch_dryfire.NAME}:sandbox", Severity.CRITICAL, "prior failure", now=0.0)
    killswitch_dryfire.run(ctx)
    assert f"{killswitch_dryfire.NAME}:sandbox" in ctx.alerter.resolved
