from __future__ import annotations

import subprocess

from watch.checks import ops_monitor_runner
from watch.severity import Severity


def test_not_configured_does_nothing(ctx):
    ctx.settings.jd_signal_repo_path = ""
    ctx.settings.jd_signal_python = ""
    ops_monitor_runner.run(ctx)
    assert ctx.alerter.calls == []


def test_successful_run_resolves_any_prior_alert(ctx, monkeypatch):
    ctx.settings.jd_signal_repo_path = "/fake/JD-Signal"
    ctx.settings.jd_signal_python = "/fake/venv/bin/python3"
    ctx.alerter.alert(ops_monitor_runner.NAME, Severity.WARN, "prior failure", now=0.0)

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0, stdout="ok", stderr=""),
    )

    ops_monitor_runner.run(ctx)

    assert ops_monitor_runner.NAME in ctx.alerter.resolved
    # resolve() doesn't post a new message -- only the earlier manually
    # recorded "prior failure" call should be present.
    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].content == "prior failure"


def test_nonzero_exit_code_alerts_warn(ctx, monkeypatch):
    ctx.settings.jd_signal_repo_path = "/fake/JD-Signal"
    ctx.settings.jd_signal_python = "/fake/venv/bin/python3"

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=1, stdout="", stderr="Traceback: boom"),
    )

    ops_monitor_runner.run(ctx)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.WARN
    assert "exited 1" in ctx.alerter.calls[0].content
    assert "boom" in ctx.alerter.calls[0].content


def test_timeout_alerts_warn(ctx, monkeypatch):
    ctx.settings.jd_signal_repo_path = "/fake/JD-Signal"
    ctx.settings.jd_signal_python = "/fake/venv/bin/python3"

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ops_monitor.py", timeout=120)

    monkeypatch.setattr(subprocess, "run", _raise)

    ops_monitor_runner.run(ctx)

    assert len(ctx.alerter.calls) == 1
    assert "timed out" in ctx.alerter.calls[0].content


def test_launch_failure_alerts_warn(ctx, monkeypatch):
    ctx.settings.jd_signal_repo_path = "/fake/JD-Signal"
    ctx.settings.jd_signal_python = "/fake/venv/bin/python3"

    def _raise(*a, **k):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", _raise)

    ops_monitor_runner.run(ctx)

    assert len(ctx.alerter.calls) == 1
    assert "failed to launch" in ctx.alerter.calls[0].content
