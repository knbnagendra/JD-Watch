from __future__ import annotations

import datetime as dt
import subprocess
from zoneinfo import ZoneInfo

from watch import market_hours
from watch.reports import daily_trade_report
from watch.severity import Severity

ET = ZoneInfo("America/New_York")


def run_at(ctx, monkeypatch, hour=16, minute=16, date=(2026, 7, 31)):
    fixed_now = dt.datetime(*date, hour, minute, tzinfo=ET)
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    daily_trade_report.run(ctx)


def _configure(ctx):
    ctx.settings.jd_relay_repo_path = "/fake/JD-Relay"
    ctx.settings.jd_relay_python = "/fake/venv/bin/python3"


def test_market_holiday_no_report(ctx, monkeypatch):
    _configure(ctx)
    run_at(ctx, monkeypatch, date=(2026, 1, 1))
    assert ctx.alerter.calls == []


def test_not_configured_does_nothing(ctx, monkeypatch):
    ctx.settings.jd_relay_repo_path = ""
    ctx.settings.jd_relay_python = ""
    run_at(ctx, monkeypatch)
    assert ctx.alerter.calls == []


def test_successful_run_posts_report_content_as_info(ctx, monkeypatch):
    _configure(ctx)
    report_text = "**Auto Trades Report -- since 2026-07-31**\n\n**Grand total: $165.50**"
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0, stdout=report_text, stderr=""),
    )

    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.INFO
    assert ctx.alerter.calls[0].content == report_text


def test_successful_run_invokes_trade_report_with_configured_python_and_cwd(ctx, monkeypatch):
    _configure(ctx)
    captured = {}

    def fake_run(args, cwd=None, timeout=None, capture_output=None, text=None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_at(ctx, monkeypatch)

    assert captured["args"] == ["/fake/venv/bin/python3", "trade_report.py"]
    assert captured["cwd"] == "/fake/JD-Relay"


def test_empty_output_alerts_warn_instead_of_posting_nothing(ctx, monkeypatch):
    _configure(ctx)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0, stdout="   ", stderr=""),
    )

    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.WARN
    assert "no output" in ctx.alerter.calls[0].content


def test_nonzero_exit_code_alerts_warn(ctx, monkeypatch):
    _configure(ctx)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=1, stdout="", stderr="Traceback: boom"),
    )

    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.WARN
    assert "exited 1" in ctx.alerter.calls[0].content
    assert "boom" in ctx.alerter.calls[0].content


def test_timeout_alerts_warn(ctx, monkeypatch):
    _configure(ctx)

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="trade_report.py", timeout=60)

    monkeypatch.setattr(subprocess, "run", _raise)
    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.WARN
    assert "timed out" in ctx.alerter.calls[0].content


def test_launch_failure_alerts_warn(ctx, monkeypatch):
    _configure(ctx)

    def _raise(*a, **k):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", _raise)
    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.WARN
    assert "failed to launch" in ctx.alerter.calls[0].content
