# JD-Watch Runbook

For every alert below: what it means, how to diagnose, the safe
remediation, and the exact resume command. JD-Watch's own auto-actions
never flatten and never resume on their own -- every incident below ends
with a human decision.

---

## `stop_coverage` -- Unprotected position detected

**What it means:** one of two independent signals fired on an open,
tracked chunk, confirmed across two consecutive JD-Watch polls (~2
minutes):
- `entry_protective_failed=true` -- the entry filled but its protective
  stop never got submitted successfully, or JD-Relay's own retry
  (`_reattempt_missing_protection`) hasn't cleared it.
- `live_stop_status` is `canceled`/`cancelled`/`rejected`/`expired` -- the
  stop was successfully placed at some point (so `entry_protective_failed`
  never went True) but is no longer live at the broker, checked directly
  via `broker.get_order()`. Only checked for chunks that have already been
  through at least one stop-replace (breakeven move or trailing tighten) --
  see the known limitation below.

**Auto-action taken:** JD-Watch called
`POST /control/halt?account=<account>&flatten=false` on the affected
account only -- new entries are blocked, the position itself is untouched.
A position whose broker-reported quantity is already 0 is never flagged,
regardless of chunk-level state -- confirmed live 2026-07-31 (sandbox
smoke test): right after any flatten, quantity goes to 0 immediately but
a chunk's own `status` field lags behind ("closed" arrives on the next
reconcile cycle), while `live_stop_status` correctly shows `canceled`
(closing a position cancels its resting protective legs) -- without this
guard that transient window reads as a violation with nothing left to
protect.

**Known limitation:** a position still on its original, never-replaced
entry bracket has no live re-verification at all (neither JD-Relay
internally nor JD-Watch externally) -- if that stop leg gets canceled out
of band before any stop-replace has ever happened, nothing currently
detects it. This is a real, open gap (documented in JD-Relay's
`PositionManager.tracked_chunks_snapshot()` docstring), not a JD-Watch
bug -- closing it needs the same multi-leg order parsing
`_process_routing_fills()` uses, which is a separate, larger piece of
work.

**Diagnosis:**
1. Check JD-Relay's own logs for that `client_order_id` /
   `broker_order_id`: `journalctl -u jd-relay -n 500 | grep <client_order_id>`.
2. Check the broker directly (Tradier/Alpaca dashboard or API) for the
   actual current state of that position and whether any resting stop
   order exists for it.
3. Check `GET /control/status` for the account's `degraded_tickers` --
   JD-Relay may have already flagged and degraded this ticker itself.

**Safe remediation:**
- If the position is genuinely unprotected: manually submit a protective
  stop at the broker directly, or flatten the position manually if you
  can't safely re-protect it. Do not use JD-Relay's kill-switch flatten
  (`?flatten=true`) unless you intend to close every tracked position on
  that account, not just this one.
- If it was actually protected and this was a false positive (e.g. a
  stop-replace race that outlasted the 2-minute confirm window): confirm
  the resting stop exists at the broker, then just rearm -- no other action
  needed.

**Resume:** `POST /control/rearm?account=<account>` (via `X-JD-Relay-Secret`
header) once you've verified the position is safe. JD-Watch will post an
INFO "RESOLVED" message once its next poll no longer sees the violation.

---

## `flat_by_close` -- Account not flat past the close grace period

**What it means:** past `check_time_et` (default 16:05 ET, i.e.
`time_exit_et` + 10min grace), the account still shows an open,
non-swing-eligible position. JD-Relay's own EOD sweep
(`maybe_run_eod_sweep`) already ran and either failed or is still stuck.
**Auto-action taken:** halted new entries on the affected account only.
**JD-Watch does NOT attempt to flatten the position itself** -- a second,
independent flatten attempt from a different process against the same
broker account risks a double-submission/race, which is exactly the class
of bug this whole system exists to catch, not cause.

**Diagnosis:**
1. Check why JD-Relay's own sweep failed: `journalctl -u jd-relay | grep eod_flatten`.
   Common causes already seen live: broker rejected a market order as
   outside market hours (see `_eod_flatten_market_closed_until` in
   `position_manager.py`), or a transient broker error.
2. Check the broker directly for the position's real current state.

**Safe remediation:**
- If the market is still open (early-close day, or this fired early):
  manually submit a market order to close the position, or wait one more
  JD-Watch poll if you believe JD-Relay's sweep is still in flight.
- If the market is closed: the position must ride overnight unintentionally
  -- monitor it and close first thing the next session; this is a real
  0DTE/lotto exposure to flag internally regardless of what caused it.

**Resume:** `POST /control/rearm?account=<account>` once the position is
actually flat (confirmed at the broker, not just submitted). JD-Watch
re-alerts every `realert_interval_seconds` (default 5min) until resolved,
then posts an INFO "RESOLVED" message.

---

## `killswitch_dryfire` -- Kill-switch self-test failed

**What it means:** JD-Watch's daily pre-market self-test (default 08:30 ET,
`sandbox`/`alpaca_sandbox` only) called `/control/halt` then
`/control/rearm` and either step didn't confirm via `/control/status`
within the poll timeout (default 10s), or the HTTP call itself failed.
**No auto-action is taken against any live account** -- a sandbox
self-test failing doesn't halt live trading by itself, since the failure
mode being tested is "is the mechanism itself broken," not "should we stop
trading right now."

**Diagnosis:**
1. Is JD-Relay even reachable? `curl http://127.0.0.1:8787/healthz` on the
   VM.
2. Check `X-JD-Relay-Secret` matches between JD-Watch's `.env` and
   JD-Relay's -- an auth failure looks like "halt call failed: 401" in the
   alert.
3. Manually try the same sequence by hand:
   `curl -X POST -H "X-JD-Relay-Secret: <secret>" "http://127.0.0.1:8787/control/halt?account=sandbox"`
   then check `/control/status`, then `/control/rearm`.

**Safe remediation:** this is a "verify before you need it" alert, not an
active incident -- treat it as high priority to investigate (the kill
switch is the last line of defense for every other check in this system)
but there is no time-critical action beyond confirming sandbox actually
ended up rearmed (check `GET /control/status`) so tomorrow's test isn't
starting from an already-halted sandbox.

**Resume:** N/A -- no account was halted by this check. If you find
sandbox left halted from a failed dry-fire, `POST /control/rearm?account=sandbox`
(and `alpaca_sandbox` if needed).

---

## `ops_monitor_runner` -- ops_monitor.py itself failed or timed out

**What it means:** the existing `ops_monitor.py` CLI (JD-Signal repo)
either exited non-zero, timed out (default 120s), or couldn't be launched
at all when JD-Watch tried to run it on schedule. **This is a WARN, not a
CRITICAL** -- it means you may not be getting ops_monitor.py's own US/India
health summaries right now, not that trading itself is unsafe.

**Diagnosis:**
1. Try running it by hand: `cd JD-Signal && venv/bin/python3 ops_monitor.py --dry-run`.
2. Check the stderr tail included in the alert for the actual exception.
3. Confirm `JD_SIGNAL_REPO_PATH`/`JD_SIGNAL_PYTHON` in JD-Watch's `.env`
   still point at valid paths (a JD-Signal venv rebuild/repath would break
   this silently otherwise).

**Safe remediation:** fix whatever `ops_monitor.py` itself needs (missing
`.env` var, broken venv, etc.) -- no JD-Watch-side action needed once it
runs cleanly again; the next scheduled run auto-resolves the alert.

**Resume:** N/A -- no account is ever halted by this check.

---

## `premarket_report` / `mid_session_report` / `eod_report` / `weekly_digest`

**What these are:** scheduled, INFO-only Discord posts -- none of the four
can halt anything or take any action. There is no remediation section for
them because there is no incident to remediate; treat a missing scheduled
post itself as the only actionable signal (check `journalctl -u jd-watch`
for an exception in that report's `run()` -- each is wrapped by
`engine.py`'s own per-check try/except, so one report's bug can't take
down the scheduler or any other check).

`premarket_report`'s "NO-GO" verdict is informational, not a gate that
blocks trading by itself -- it's a signal to look closer (an open
`killswitch_dryfire` incident, low disk/memory headroom, or JD-Relay being
unreachable), not something that requires a resume command.
