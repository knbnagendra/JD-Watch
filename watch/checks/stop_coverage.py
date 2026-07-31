"""5.7: periodic sweep asserting every open, tracked position still has a
live protective stop. JD-Relay's own protection-failure handling
(_handle_entry_protective_failure / _reattempt_missing_protection) is
reactive -- triggered only when a specific chunk's protective order dies.
There was no standing, external check of the whole book that could catch
that retry/notify path silently breaking on its own. This is that check.

Requires a violation to persist across CONFIRM_POLLS consecutive polls
before firing, mirroring position_manager.py's own mismatch_grace_seconds
pattern -- avoids false positives during the brief window of a legitimate
stop-replace in flight. The per-poll pending counter is in-memory only (a
JD-Watch restart mid-count just delays detection by one extra poll, not a
safety gap); confirmed incidents and their alert-dedup state persist in
store.py so a restart doesn't lose "this was already halted and alerted."
"""

from __future__ import annotations

import logging

from watch import store
from watch.severity import Severity

log = logging.getLogger("watch.checks.stop_coverage")

NAME = "stop_coverage"
ACCOUNTS = ("sandbox", "live", "alpaca_sandbox", "alpaca_live")

# JD-Relay's tracked_chunks_snapshot() already excludes these before they
# reach GET /positions -- filtered here too, defensively, rather than
# trusting that contract to hold forever. Matches this codebase's existing
# "second line of defense, independent of the upstream's own state"
# philosophy (see JD-Signal's _detect_alert_repeats(), independent of each
# product's own cooldown).
_TERMINAL_STATUSES = {"closed", "flattened", "canceled", "cancelled", "rejected", "expired"}

# key -> consecutive-poll count. Module-level so it survives across engine
# ticks within one process lifetime without threading state through ctx.
_pending: dict[str, int] = {}


def _confirm_polls(ctx) -> int:
    return int(ctx.watch_cfg.get("stop_coverage", {}).get("confirm_polls", 2))


def run(ctx) -> None:
    try:
        data = ctx.relay.get_positions()
    except Exception:
        log.warning("stop_coverage_positions_fetch_failed", exc_info=True)
        return

    accounts_data = data.get("accounts", {})
    confirm_polls = _confirm_polls(ctx)
    seen_this_poll: set[str] = set()

    for account in ACCOUNTS:
        for pos in accounts_data.get(account, {}).get("positions", []):
            ticker = pos["ticker"]
            for chunk in pos.get("chunks", []):
                if chunk.get("status") in _TERMINAL_STATUSES:
                    continue
                if not chunk.get("entry_protective_failed"):
                    continue
                key = f"{account}:{ticker}:{chunk['client_order_id']}"
                seen_this_poll.add(key)
                _pending[key] = _pending.get(key, 0) + 1
                if _pending[key] < confirm_polls:
                    continue
                _fire(ctx, account, ticker, chunk, key)

        _close_resolved(ctx, account, seen_this_poll)

    for key in list(_pending.keys()):
        if key not in seen_this_poll:
            del _pending[key]


def _fire(ctx, account: str, ticker: str, chunk: dict, key: str) -> None:
    incident = store.get_open_incident(ctx.db, NAME, account, key)
    detail = (
        f"ticker={ticker} chunk={chunk['client_order_id']} "
        f"broker_order_id={chunk.get('broker_order_id')} status={chunk.get('status')}"
    )
    if incident is not None:
        return  # already open and already alerted on open; nothing new to do each poll

    incident_id = store.open_incident(ctx.db, NAME, account, key, Severity.CRITICAL, detail)
    try:
        ctx.relay.halt(account, flatten=False)
        action = f"halted new entries on account={account}"
    except Exception as exc:
        action = f"HALT ATTEMPT FAILED: {exc}"
        log.error("stop_coverage_halt_failed account=%s", account, exc_info=True)

    ctx.alerter.alert(
        key, Severity.CRITICAL,
        f"[stop_coverage] Unprotected position detected: {detail}. "
        f"Auto-action: {action}. Resume: POST /control/rearm?account={account} "
        f"once the position is manually verified/re-protected -- see RUNBOOK.md.",
        force=True,
    )
    store.mark_incident_alerted(ctx.db, incident_id)


def _close_resolved(ctx, account: str, seen_this_poll: set[str]) -> None:
    for incident in store.get_open_incidents(ctx.db, NAME, account):
        if incident["key"] in seen_this_poll:
            continue
        store.close_incident(ctx.db, incident["id"])
        ctx.alerter.resolve(incident["key"])
        ctx.alerter.alert(
            incident["key"], Severity.INFO,
            f"[stop_coverage] RESOLVED: {incident['key']} no longer shows an unprotected chunk.",
            force=True,
        )
