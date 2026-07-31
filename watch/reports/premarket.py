"""Pre-market readiness report: one scheduled Discord post enumerating
every gate PASS/FAIL and every account's halt state explicitly (never just
an implicit all-clear -- a standing rule from real incidents where a
rolled-up "all fine" summary hid one halted account). INFO-only: no gate
failure here ever halts anything itself -- this reports on state that
mostly already exists (kill-switch dry-fire result from phase 1, JD-Relay
reachability, account halt state), plus two new, purely informational
inputs (config-file change detection, disk/memory headroom).
"""

from __future__ import annotations

import logging

from watch import market_hours, store
from watch.checks import killswitch_dryfire
from watch.reports import common
from watch.severity import Severity

log = logging.getLogger("watch.reports.premarket")

NAME = "premarket_report"


def _threshold(ctx, key: str, default: float) -> float:
    return float(ctx.watch_cfg.get(NAME, {}).get(key, default))


def run(ctx) -> None:
    today = market_hours.now_et().date()
    if market_hours.is_market_holiday(today):
        ctx.alerter.alert(NAME, Severity.INFO, f"[premarket_report] Market holiday ({today}) -- no report.",
                           force=True)
        return

    gates: list[tuple[bool, str]] = []

    try:
        status = ctx.relay.get_status()
        gates.append((True, "JD-Relay reachable"))
    except Exception as exc:
        status = None
        gates.append((False, f"JD-Relay UNREACHABLE: {exc}"))

    dryfire_ok = all(
        store.get_open_incident(ctx.db, killswitch_dryfire.NAME, acct, f"{killswitch_dryfire.NAME}:{acct}") is None
        for acct in killswitch_dryfire.TARGETS
    )
    gates.append((dryfire_ok, "Kill-switch dry-fire" if dryfire_ok else "Kill-switch dry-fire FAILING -- see RUNBOOK.md"))

    disk_pct = common.disk_headroom_pct()
    disk_min = _threshold(ctx, "disk_headroom_min_pct", 15.0)
    if disk_pct is not None:
        gates.append((disk_pct >= disk_min, f"Disk headroom: {disk_pct:.0f}% free (min {disk_min:.0f}%)"))

    mem_pct = common.mem_headroom_pct()
    mem_min = _threshold(ctx, "mem_headroom_min_pct", 10.0)
    if mem_pct is not None:
        gates.append((mem_pct >= mem_min, f"Memory headroom: {mem_pct:.0f}% free (min {mem_min:.0f}%)"))

    config_results = common.check_config_files(ctx)
    changed = [c["path"] for c in config_results if c["changed"]]
    if changed:
        gates.append((True, f"Config changed since last report (informational, not a failure): {', '.join(changed)}"))

    lines = [f"[premarket_report] {today}"]
    overall_go = all(ok for ok, _ in gates)
    lines.append(f"Overall: {'GO' if overall_go else 'NO-GO'}")
    lines.append("")
    lines.append("Gates:")
    for ok, label in gates:
        lines.append(f"{'OK' if ok else 'FAIL'} -- {label}")

    lines.append("")
    lines.append("Account halt status:")
    accounts = status.get("accounts", {}) if status else {}
    if accounts:
        for name, state in accounts.items():
            if state.get("any_halted"):
                lines.append(f"- {name}: HALTED -- {state.get('halt_reason')}")
            else:
                lines.append(f"- {name}: not halted")
    else:
        lines.append("- unavailable (JD-Relay unreachable)")

    ctx.alerter.alert(
        NAME, Severity.INFO if overall_go else Severity.WARN, "\n".join(lines), force=True,
    )
