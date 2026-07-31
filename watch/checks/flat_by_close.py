"""5.8: standalone assertion that every US account is actually flat by end
of day, independent of JD-Relay's own EOD sweep (maybe_run_eod_sweep) --
that sweep submits flatten orders but nothing outside it verifies the
result. A stuck flatten today only gets JD-Relay's own re-notify throttle
(_eod_flatten_failed_notified), not an escalating external alarm.

Deliberately does NOT attempt to flatten the position itself: JD-Relay's
own EOD sweep already tried and failed/is stuck, so a second, independent
flatten attempt from a different process against the same broker account
risks exactly the kind of double-submission/race this whole system exists
to catch. This check only halts new entries and escalates to a human --
see RUNBOOK.md for the manual remediation.
"""

from __future__ import annotations

import logging

from watch import market_hours, store
from watch.severity import Severity

log = logging.getLogger("watch.checks.flat_by_close")

NAME = "flat_by_close"

# Deliberately NOT a hardcoded account tuple -- see stop_coverage.py's
# identical note. run() iterates whatever accounts GET /positions actually
# returns, so a newly added account (e.g. schwab_live, 2026-07-30) is
# covered automatically instead of silently unmonitored.


def _realert_interval(ctx) -> float:
    return float(ctx.watch_cfg.get("flat_by_close", {}).get("realert_interval_seconds", 300))


def _check_time_et(ctx) -> str:
    return ctx.watch_cfg.get("flat_by_close", {}).get("check_time_et", "16:05")


def run(ctx) -> None:
    """Registered as an interval check (see main.py, same interval as
    realert_interval_seconds) rather than a once-daily one: the escalating
    re-alert behavior below needs this to keep running repeatedly after the
    cutoff, not fire once and stop. Before the cutoff it's a fast no-op."""
    if not market_hours.is_past_et_time(market_hours.now_et(), _check_time_et(ctx)):
        return
    try:
        data = ctx.relay.get_positions()
    except Exception:
        log.warning("flat_by_close_positions_fetch_failed", exc_info=True)
        return

    accounts_data = data.get("accounts", {})
    for account, account_data in accounts_data.items():
        violations = [
            pos for pos in account_data.get("positions", [])
            if pos.get("quantity", 0) != 0 and not pos.get("swing_eligible", False)
        ]
        key = account
        if violations:
            _fire_or_realert(ctx, account, key, violations)
        else:
            _close_if_open(ctx, account, key)


def _fire_or_realert(ctx, account: str, key: str, violations: list[dict]) -> None:
    tickers = ", ".join(f"{p['ticker']} qty={p['quantity']}" for p in violations)
    incident = store.get_open_incident(ctx.db, NAME, account, key)

    if incident is None:
        incident_id = store.open_incident(ctx.db, NAME, account, key, Severity.CRITICAL, tickers)
        try:
            ctx.relay.halt(account, flatten=False)
            action = f"halted new entries on account={account}"
        except Exception as exc:
            action = f"HALT ATTEMPT FAILED: {exc}"
            log.error("flat_by_close_halt_failed account=%s", account, exc_info=True)
        ctx.alerter.alert(
            key, Severity.CRITICAL,
            f"[flat_by_close] {account} is NOT flat past the close grace period: {tickers}. "
            f"JD-Relay's own EOD sweep already attempted and failed/is stuck -- this check does "
            f"NOT retry the flatten automatically, see RUNBOOK.md. Auto-action: {action}.",
            force=True,
        )
        store.mark_incident_alerted(ctx.db, incident_id)
        return

    if store.should_realert(ctx.db, incident["id"], _realert_interval(ctx)):
        ctx.alerter.alert(
            key, Severity.CRITICAL,
            f"[flat_by_close] STILL NOT FLAT: {account}: {tickers}.",
            force=True,
        )
        store.mark_incident_alerted(ctx.db, incident["id"])


def _close_if_open(ctx, account: str, key: str) -> None:
    incident = store.get_open_incident(ctx.db, NAME, account, key)
    if incident is None:
        return
    store.close_incident(ctx.db, incident["id"])
    ctx.alerter.resolve(key)
    ctx.alerter.alert(key, Severity.INFO, f"[flat_by_close] RESOLVED: {account} is now flat.", force=True)
