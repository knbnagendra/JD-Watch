"""7.2: daily kill-switch self-test. CircuitBreakers is never exercised
today except when actually needed -- this proves the halt/rearm mechanism
itself still works, against sandbox accounts only, before it's ever needed
for real.

TARGETS is HARDCODED, never read from watch.yaml or any config -- a typo in
a config file must never be able to point a dry-fire test at a live
account. Always attempts the rearm step even if halt failed outright, so
this test can never leave sandbox halted overnight by itself.
"""

from __future__ import annotations

import logging
import time

from watch.severity import Severity

log = logging.getLogger("watch.checks.killswitch_dryfire")

NAME = "killswitch_dryfire"
TARGETS = ("tradier_sandbox", "alpaca_sandbox")  # hardcoded -- see module docstring


def _poll_timeout(ctx) -> float:
    return float(ctx.watch_cfg.get("killswitch_dryfire", {}).get("poll_timeout_seconds", 10))


def _wait_for_manual_halt(ctx, account: str, expected: bool, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            status = ctx.relay.get_status()
            state = status.get("accounts", {}).get(account, {})
            if bool(state.get("manual_halt")) == expected:
                return True
        except Exception:
            log.warning("killswitch_dryfire_status_poll_failed account=%s", account, exc_info=True)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def run(ctx) -> None:
    for account in TARGETS:
        _dry_fire_one(ctx, account)


def _dry_fire_one(ctx, account: str) -> None:
    timeout = _poll_timeout(ctx)
    key = f"{NAME}:{account}"
    halt_ok = False
    rearm_ok = False
    errors: list[str] = []

    try:
        ctx.relay.halt(account, flatten=False)
        halt_ok = _wait_for_manual_halt(ctx, account, expected=True, timeout=timeout)
        if not halt_ok:
            errors.append("halt never confirmed via /control/status within timeout")
    except Exception as exc:
        errors.append(f"halt call failed: {exc}")
        log.error("killswitch_dryfire_halt_failed account=%s", account, exc_info=True)

    # Always attempted, regardless of halt outcome above.
    try:
        ctx.relay.rearm(account)
        rearm_ok = _wait_for_manual_halt(ctx, account, expected=False, timeout=timeout)
        if not rearm_ok:
            errors.append("rearm never confirmed via /control/status within timeout")
    except Exception as exc:
        errors.append(f"rearm call failed: {exc}")
        log.error("killswitch_dryfire_rearm_failed account=%s", account, exc_info=True)

    if halt_ok and rearm_ok:
        ctx.alerter.resolve(key)
        log.info("killswitch_dryfire_ok account=%s", account)
        return

    ctx.alerter.alert(
        key, Severity.CRITICAL,
        f"[killswitch_dryfire] Self-test FAILED for account={account}: "
        f"halt_confirmed={halt_ok} rearm_confirmed={rearm_ok}. {'; '.join(errors)}. "
        f"The kill-switch mechanism itself may be broken -- verify manually before it's "
        f"needed for real. No auto-action taken against any live account.",
        force=True,
    )
