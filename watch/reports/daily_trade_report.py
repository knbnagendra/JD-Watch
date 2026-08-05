"""Daily auto-trades report: shells out to JD-Relay's own trade_report.py
(account-kind-then-broker grouped closed-trade summary, added 2026-08-04)
and posts its output to Discord once per day, right after close.

Same subprocess pattern as watch/checks/ops_monitor_runner.py -- but that
check only monitors ops_monitor.py's own health (ops_monitor.py posts its
own output directly to Discord). trade_report.py has no Discord-posting
of its own; it only prints, so this module owns turning that output into
the actual scheduled post.
"""

from __future__ import annotations

import logging
import subprocess

from watch import market_hours
from watch.severity import Severity

log = logging.getLogger("watch.reports.daily_trade_report")

NAME = "daily_trade_report"


def _timeout(ctx) -> float:
    return float(ctx.watch_cfg.get(NAME, {}).get("timeout_seconds", 60))


def run(ctx) -> None:
    now_et = market_hours.now_et()
    today = now_et.date()
    if market_hours.is_market_holiday(today):
        return

    python = ctx.settings.jd_relay_python
    repo = ctx.settings.jd_relay_repo_path
    if not python or not repo:
        log.warning(
            "daily_trade_report_not_configured jd_relay_python=%r jd_relay_repo_path=%r", python, repo,
        )
        return

    timeout = _timeout(ctx)
    try:
        result = subprocess.run(
            [python, "trade_report.py"], cwd=repo, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        ctx.alerter.alert(
            NAME, Severity.WARN, f"[daily_trade_report] trade_report.py timed out after {timeout}s.",
            force=True,
        )
        return
    except Exception as exc:
        ctx.alerter.alert(
            NAME, Severity.WARN, f"[daily_trade_report] failed to launch trade_report.py: {exc}",
            force=True,
        )
        return

    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-500:]
        ctx.alerter.alert(
            NAME, Severity.WARN,
            f"[daily_trade_report] trade_report.py exited {result.returncode}. stderr tail: {stderr_tail}",
            force=True,
        )
        return

    content = result.stdout.strip()
    if not content:
        ctx.alerter.alert(NAME, Severity.WARN, "[daily_trade_report] trade_report.py produced no output.",
                           force=True)
        return

    # force=True: this is a scheduled once-per-day digest, not a
    # deduped incident stream -- every day's report must post regardless
    # of yesterday's content/dedupe window (same reasoning as
    # premarket/eod/weekly's own force=True).
    ctx.alerter.alert(NAME, Severity.INFO, content, force=True)
