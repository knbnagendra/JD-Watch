"""Mid-session snapshot: a periodic RTH-only Discord post -- check status
matrix, current regime, today's blocked-trade counts by gate, and account
halt/degraded state. INFO-only, no new detection or auto-action; every
data source here already exists (phase 1's incident store, JD-Signal's
regime_store/signal_audit_store, JD-Relay's /control/status).
"""

from __future__ import annotations

import logging

from watch import jd_signal_db, market_hours, store
from watch.reports import common
from watch.severity import Severity

log = logging.getLogger("watch.reports.mid_session")

NAME = "mid_session_report"


def run(ctx) -> None:
    now_et = market_hours.now_et()
    if not market_hours.is_regular_session_now(now_et):
        return

    lines = [f"[mid_session_report] {now_et.strftime('%Y-%m-%d %H:%M ET')}"]

    open_incidents = store.get_all_open_incidents(ctx.db)
    if open_incidents:
        lines.append(f"Open incidents ({len(open_incidents)}):")
        for inc in open_incidents:
            lines.append(f"- {inc['check_name']} account={inc['account']} key={inc['key']} since {inc['opened_at']}")
    else:
        lines.append("Open incidents: none")

    regime = jd_signal_db.get_latest_regime(ctx.settings.jd_signal_repo_path)
    if regime:
        lines.append(f"Regime: {regime['regime']} (confidence={regime['confidence']}) as of {regime['observed_at']}")
    else:
        lines.append("Regime: unavailable")

    since = common.start_of_trading_day_utc(now_et)
    blocked = jd_signal_db.get_blocked_trade_counts(ctx.settings.jd_signal_repo_path, since)
    if blocked:
        lines.append("Blocked trades today by gate:")
        for row in blocked:
            lines.append(f"- {row['gate_name']}: {row['blocked_count']}")
    else:
        lines.append("Blocked trades today: none recorded")

    try:
        status = ctx.relay.get_status()
        degraded_any = False
        for name, state in status.get("accounts", {}).items():
            tickers = state.get("degraded_tickers", {})
            if tickers:
                degraded_any = True
                lines.append(f"Degraded ({name}): {', '.join(tickers.keys())}")
        if not degraded_any:
            lines.append("Degraded tickers: none")
    except Exception as exc:
        lines.append(f"Account status: UNAVAILABLE ({exc})")

    ctx.alerter.alert(NAME, Severity.INFO, "\n".join(lines), force=True)
