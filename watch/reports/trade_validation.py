"""Weekly trade-performance validation: invokes JD-Relay's trade_report.py
--validation-summary (per-product win-rate/approx-exit-rate breakdown, see
that module's own docstring) over the trailing 7 days, appends a new dated
section to the local TRADE_PERFORMANCE_VALIDATION.md, posts the same
content to Discord, and tracks two automated readiness signals so "is this
data trustworthy enough to show anyone" stops being a judgment call:

1. **Sample size** -- per live account, per product, closed-trade count
   against a floor (config `sample_size_floor`, default 30) below which a
   win-rate/P&L number is mostly noise.
2. **Consecutive clean weeks** -- persisted in store.py's validation_weeks
   table (append-only, so the streak is derived from real history, not a
   counter that could silently drift). A week is "clean" if zero exits
   were recorded approximate (the exact signal that caught the Alpaca
   fill-price bug, 2026-08-08) and zero new CRITICAL JD-Watch incidents
   opened that week.

Deliberately does NOT commit/push the updated doc to git -- an autonomous
process silently mutating a git-tracked source file's history every week,
unattended, is a bigger step than this report needs to take. The file is
appended locally so it's visible on the VM and in the Discord post
immediately; syncing that into the repo's actual git history is a
periodic, reviewed action (a future session, or the user), not something
this report does on its own.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import timedelta, timezone
from pathlib import Path

from watch import market_hours, store
from watch.severity import Severity

log = logging.getLogger("watch.reports.trade_validation")

NAME = "trade_validation"

# The doc's own stable tail marker -- new dated sections are inserted
# directly above it, so the file's structure (data-quality notes, "next
# check" line) always stays last regardless of how many sections pile up
# above it.
_INSERT_BEFORE_MARKER = "## Next scheduled check"


def _timeout(ctx) -> float:
    return float(ctx.watch_cfg.get(NAME, {}).get("timeout_seconds", 60))


def _doc_path(ctx) -> Path:
    return Path(ctx.watch_cfg.get(NAME, {}).get("doc_path", "TRADE_PERFORMANCE_VALIDATION.md"))


def _sample_size_floor(ctx) -> int:
    return int(ctx.watch_cfg.get(NAME, {}).get("sample_size_floor", 30))


def _run_trade_report(ctx, python: str, repo: str, since_iso: str, *, as_json: bool) -> subprocess.CompletedProcess | None:
    args = [python, "trade_report.py", "--validation-summary", "--since", since_iso]
    if as_json:
        args.append("--json")
    return subprocess.run(args, cwd=repo, timeout=_timeout(ctx), capture_output=True, text=True)


def run(ctx) -> None:
    now_et = market_hours.now_et()
    if now_et.weekday() != 4:  # Friday only, same day as weekly_digest
        return

    python = ctx.settings.jd_relay_python
    repo = ctx.settings.jd_relay_repo_path
    if not python or not repo:
        log.warning(
            "trade_validation_not_configured jd_relay_python=%r jd_relay_repo_path=%r", python, repo,
        )
        return

    since_iso = (now_et - timedelta(days=7)).astimezone(timezone.utc).isoformat()

    try:
        md_result = _run_trade_report(ctx, python, repo, since_iso, as_json=False)
        json_result = _run_trade_report(ctx, python, repo, since_iso, as_json=True)
    except subprocess.TimeoutExpired:
        ctx.alerter.alert(
            NAME, Severity.WARN, f"[trade_validation] trade_report.py timed out after {_timeout(ctx)}s.",
            force=True,
        )
        return
    except Exception as exc:
        ctx.alerter.alert(
            NAME, Severity.WARN, f"[trade_validation] failed to launch trade_report.py: {exc}", force=True,
        )
        return

    if md_result.returncode != 0 or json_result.returncode != 0:
        stderr_tail = ((md_result.stderr or "") + (json_result.stderr or ""))[-500:]
        ctx.alerter.alert(
            NAME, Severity.WARN,
            f"[trade_validation] trade_report.py exited non-zero. stderr tail: {stderr_tail}", force=True,
        )
        return

    content = md_result.stdout.strip()
    if not content:
        ctx.alerter.alert(NAME, Severity.WARN, "[trade_validation] trade_report.py produced no output.",
                           force=True)
        return

    try:
        entries = json.loads(json_result.stdout)
    except (json.JSONDecodeError, TypeError):
        log.warning("trade_validation_json_parse_failed", exc_info=True)
        entries = []

    readiness = _format_readiness(ctx, entries, now_et)

    appended = _append_to_doc(_doc_path(ctx), now_et.date().isoformat(), content + "\n\n" + readiness)
    doc_note = "" if appended else " (doc file not found locally -- NOT appended, see RUNBOOK.md)"

    ctx.alerter.alert(NAME, Severity.INFO, f"[trade_validation]{doc_note}\n{content}\n\n{readiness}", force=True)


def _format_readiness(ctx, entries: list[dict], now_et) -> str:
    """Computes and persists this week's clean/dirty verdict, and formats
    the sample-size-progress + streak section appended after the raw
    validation table. Persisting happens here (not a separate check) so
    the streak is always in lock-step with the week this report actually
    covers -- there is exactly one writer."""
    floor = _sample_size_floor(ctx)
    total_exits = 0
    total_approx = 0
    lines = ["**Readiness (is this data trustworthy yet?)**", ""]

    live_entries = [e for e in entries if e.get("kind") == "live"]
    if live_entries:
        lines.append(f"Sample size (floor: {floor} closed trades/product before a win-rate number is meaningful):")
        for e in live_entries:
            if not e.get("by_product"):
                lines.append(f"- {e['name']}: 0 closed trades")
                continue
            for product, stats in e["by_product"].items():
                n = stats["n"]
                lines.append(f"- {e['name']}/{product}: {n}/{floor} closed trades"
                             + (" -- READY" if n >= floor else ""))
    else:
        lines.append("Sample size: no live accounts reporting.")

    for e in entries:
        for stats in e.get("by_product", {}).values():
            total_exits += stats["n"]
            total_approx += stats["approx"]

    since_utc = (now_et - timedelta(days=7)).astimezone(timezone.utc)
    new_critical = sum(
        1 for inc in store.get_incidents_since(ctx.db, since_utc) if inc["severity"] == "CRITICAL"
    )
    clean = total_approx == 0 and new_critical == 0
    detail = f"{total_approx}/{total_exits} approx exits, {new_critical} new CRITICAL incidents"
    streak = store.record_validation_week(ctx.db, now_et.date().isoformat(), clean, detail)

    lines.append("")
    lines.append(f"This week: {'CLEAN' if clean else 'NOT CLEAN'} ({detail})")
    lines.append(f"Consecutive clean weeks: {streak}")
    return "\n".join(lines)


def _append_to_doc(doc_path: Path, date_label: str, content: str) -> bool:
    """Inserts a new dated section directly above the "Next scheduled
    check" marker so the doc's own trailing sections (data-quality notes)
    always stay last. Returns False (and writes nothing) if the doc
    doesn't exist locally or has been restructured to drop the marker --
    this report must never create or reshape the file on its own, only
    add to the one already under version control."""
    if not doc_path.exists():
        log.warning("trade_validation_doc_missing path=%s", doc_path)
        return False
    text = doc_path.read_text(encoding="utf-8")
    if _INSERT_BEFORE_MARKER not in text:
        log.warning("trade_validation_doc_marker_missing path=%s", doc_path)
        return False
    section = f"## {date_label}\n\n{content}\n\n---\n\n"
    new_text = text.replace(_INSERT_BEFORE_MARKER, section + _INSERT_BEFORE_MARKER, 1)
    doc_path.write_text(new_text, encoding="utf-8")
    return True
