"""5.7: periodic sweep asserting every open, tracked position still has a
live protective stop. JD-Relay's own protection-failure handling
(_handle_entry_protective_failure / _reattempt_missing_protection) is
reactive -- triggered only when a specific chunk's protective order dies.
There was no standing, external check of the whole book that could catch
that retry/notify path silently breaking on its own. This is that check.

Two independent signals, checked as an OR (see GET /positions's
tracked_chunks_snapshot() docstring in JD-Relay for the full reasoning):
- `entry_protective_failed`: set once, at entry time, if the initial
  protective-order submission failed. Never updated afterward.
- `live_stop_status`: a live broker.get_order() status, but ONLY for
  chunks that have already been through a stop-replace
  (is_protective_oco=True) -- catches a stop that was successfully placed
  and later externally canceled/expired, which entry_protective_failed can
  never see. Always None for a never-yet-replaced original bracket chunk;
  that scenario currently has no live re-verification anywhere in either
  system (a documented, real, still-open gap, not a silent one -- see
  JD-Relay's tracked_chunks_snapshot() docstring).

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

# Deliberately NOT a hardcoded account tuple -- run() iterates whatever
# accounts GET /positions actually returns. A hardcoded list here already
# went stale once in practice: config.yaml grew a 5th account (schwab_live,
# added 2026-07-30 alongside live MOC-lotto autotrading) that a fixed tuple
# would have silently left unmonitored. JD-Relay's own pipelines dict is
# the single source of truth for which accounts exist; mirroring it here
# would just be a second place for it to drift out of sync again.

# JD-Relay's tracked_chunks_snapshot() already excludes these before they
# reach GET /positions -- filtered here too, defensively, rather than
# trusting that contract to hold forever. Matches this codebase's existing
# "second line of defense, independent of the upstream's own state"
# philosophy (see JD-Signal's _detect_alert_repeats(), independent of each
# product's own cooldown).
_TERMINAL_STATUSES = {"closed", "flattened", "canceled", "cancelled", "rejected", "expired"}
_TERMINAL_LIVE_STOP_STATUSES = {"canceled", "cancelled", "rejected", "expired"}

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

    for account, account_data in accounts_data.items():
        for pos in account_data.get("positions", []):
            ticker = pos["ticker"]
            for chunk in pos.get("chunks", []):
                if chunk.get("status") in _TERMINAL_STATUSES:
                    continue
                unprotected = (
                    chunk.get("entry_protective_failed")
                    or chunk.get("live_stop_status") in _TERMINAL_LIVE_STOP_STATUSES
                )
                if not unprotected:
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
    reason = (
        "entry_protective_failed" if chunk.get("entry_protective_failed")
        else f"live_stop_status={chunk.get('live_stop_status')}"
    )
    detail = (
        f"ticker={ticker} chunk={chunk['client_order_id']} reason={reason} "
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
