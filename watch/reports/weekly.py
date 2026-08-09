"""Weekly reliability digest: restart counts per systemd service over the
week, plus incident counts per check type. INFO-only. Runs on the same
once-per-ET-date CheckSpec scheduling as the other daily reports, but
no-ops on every day except Friday -- CheckSpec has no native weekly
cadence, and gating inside run() is simpler than adding one for a single
caller.
"""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
from datetime import timedelta, timezone

from watch import market_hours, store
from watch.severity import Severity

log = logging.getLogger("watch.reports.weekly")

NAME = "weekly_digest"

_SERVICES = ["jd-relay", "jd-signal", "jd-watch"]


def _restart_count(service: str) -> int | None:
    try:
        result = subprocess.run(
            ["systemctl", "show", service, "--property=NRestarts", "--value"],
            capture_output=True, text=True, timeout=10,
        )
        return int(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        log.warning("restart_count_check_failed service=%s", service, exc_info=True)
        return None


def run(ctx) -> None:
    now_et = market_hours.now_et()
    if now_et.weekday() != 4:  # Friday only
        return

    lines = [f"[weekly_digest] week ending {now_et.date()}"]

    lines.append("Service restarts (all-time counter, not just this week):")
    for service in _SERVICES:
        count = _restart_count(service)
        lines.append(f"- {service}: {count if count is not None else 'unavailable'}")

    since_utc = (now_et - timedelta(days=7)).astimezone(timezone.utc)
    incidents = store.get_incidents_since(ctx.db, since_utc)
    counts = Counter(inc["check_name"] for inc in incidents)
    if counts:
        lines.append("Incidents this week by check:")
        for check_name, count in counts.most_common():
            lines.append(f"- {check_name}: {count}")
    else:
        lines.append("Incidents this week: none")

    since_bug = store.days_since_last_bug(ctx.db)
    lines.append("")
    lines.append(f"Days since last logged bug fix: {since_bug if since_bug is not None else 'none logged yet'}")

    bugs = store.get_bugs_since(ctx.db, since_utc)
    if bugs:
        lines.append("Bugs fixed this week:")
        for bug in bugs:
            lines.append(f"- [{bug['repo']}] {bug['description']}")

    ctx.alerter.alert(NAME, Severity.INFO, "\n".join(lines), force=True)
