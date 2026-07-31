"""End-of-day report: incident timeline for the trading day, reconciliation
state per account, and config changes since the last report. INFO-only,
scheduled after flat_by_close's own check window so its result for today
is already known.
"""

from __future__ import annotations

import logging

from watch import market_hours, store
from watch.reports import common
from watch.severity import Severity

log = logging.getLogger("watch.reports.eod")

NAME = "eod_report"


def run(ctx) -> None:
    now_et = market_hours.now_et()
    today = now_et.date()
    if market_hours.is_market_holiday(today):
        return

    since_utc = common.start_of_trading_day_utc(now_et)

    lines = [f"[eod_report] {today}"]

    incidents = store.get_incidents_since(ctx.db, since_utc)
    if incidents:
        lines.append(f"Incidents today ({len(incidents)}):")
        for inc in incidents:
            state = "OPEN" if inc["closed_at"] is None else f"closed {inc['closed_at']}"
            lines.append(
                f"- {inc['check_name']} account={inc['account']} key={inc['key']} "
                f"opened={inc['opened_at']} [{state}]"
            )
    else:
        lines.append("Incidents today: none")

    try:
        status = ctx.relay.get_status()
        lines.append("Reconciliation / halt state:")
        for name, state in status.get("accounts", {}).items():
            halted = f"HALTED ({state.get('halt_reason')})" if state.get("any_halted") else "not halted"
            degraded = state.get("degraded_tickers", {})
            degraded_note = f", degraded={list(degraded.keys())}" if degraded else ""
            lines.append(f"- {name}: {halted}{degraded_note}")
    except Exception as exc:
        lines.append(f"Reconciliation / halt state: UNAVAILABLE ({exc})")

    config_results = common.check_config_files(ctx)
    changed = [c["path"] for c in config_results if c["changed"]]
    lines.append(f"Config changed today: {', '.join(changed) if changed else 'none'}")

    ctx.alerter.alert(NAME, Severity.INFO, "\n".join(lines), force=True)
