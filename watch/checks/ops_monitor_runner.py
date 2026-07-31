"""Meta-check on ops_monitor.py's own health: runs the existing, previously
manually-invoked CLI (JD-Signal's ops_monitor.py) on a schedule instead of
requiring a human to remember to. This does NOT duplicate what
ops_monitor.py itself posts to Discord -- it only cares whether the
subprocess exits cleanly and on time; ops_monitor.py keeps posting its own
US/India summaries exactly as it does today, no double-posting.
"""

from __future__ import annotations

import logging
import subprocess

from watch.severity import Severity

log = logging.getLogger("watch.checks.ops_monitor_runner")

NAME = "ops_monitor_runner"


def _timeout(ctx) -> float:
    return float(ctx.watch_cfg.get("ops_monitor_runner", {}).get("timeout_seconds", 120))


def run(ctx) -> None:
    python = ctx.settings.jd_signal_python
    repo = ctx.settings.jd_signal_repo_path
    if not python or not repo:
        log.warning(
            "ops_monitor_runner_not_configured jd_signal_python=%r jd_signal_repo_path=%r", python, repo,
        )
        return

    timeout = _timeout(ctx)
    try:
        result = subprocess.run(
            [python, "ops_monitor.py"], cwd=repo, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        ctx.alerter.alert(NAME, Severity.WARN, f"[ops_monitor_runner] ops_monitor.py timed out after {timeout}s.")
        return
    except Exception as exc:
        ctx.alerter.alert(NAME, Severity.WARN, f"[ops_monitor_runner] failed to launch ops_monitor.py: {exc}")
        return

    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-500:]
        ctx.alerter.alert(
            NAME, Severity.WARN,
            f"[ops_monitor_runner] ops_monitor.py exited {result.returncode}. stderr tail: {stderr_tail}",
        )
    else:
        ctx.alerter.resolve(NAME)
