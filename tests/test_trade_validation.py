from __future__ import annotations

import datetime as dt
import json
import subprocess
from zoneinfo import ZoneInfo

from watch import market_hours, store
from watch.reports import trade_validation
from watch.severity import Severity

ET = ZoneInfo("America/New_York")


def run_at(ctx, monkeypatch, hour=16, minute=25, date=(2026, 7, 31)):  # 2026-07-31 is a Friday
    fixed_now = dt.datetime(*date, hour, minute, tzinfo=ET)
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    trade_validation.run(ctx)


def _configure(ctx):
    ctx.settings.jd_relay_repo_path = "/fake/JD-Relay"
    ctx.settings.jd_relay_python = "/fake/venv/bin/python3"


def _mock_subprocess(monkeypatch, md_content: str, json_entries: list[dict], returncode: int = 0):
    def fake_run(args, cwd=None, timeout=None, capture_output=None, text=None):
        if "--json" in args:
            return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=json.dumps(json_entries), stderr="")
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=md_content, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)


def test_noop_on_non_friday(ctx, monkeypatch):
    _configure(ctx)
    run_at(ctx, monkeypatch, date=(2026, 7, 30))  # Thursday
    assert ctx.alerter.calls == []


def test_not_configured_does_nothing(ctx, monkeypatch):
    ctx.settings.jd_relay_repo_path = ""
    ctx.settings.jd_relay_python = ""
    run_at(ctx, monkeypatch)
    assert ctx.alerter.calls == []


def test_successful_run_posts_and_appends_to_doc(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    doc = tmp_path / "TRADE_PERFORMANCE_VALIDATION.md"
    doc.write_text("# Doc\n\n## 2026-08-01\n\nold content\n\n---\n\n## Next scheduled check\n\nTBD\n")
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(doc)}}
    _mock_subprocess(monkeypatch, "**Trade Performance Validation**\n\n| a | b |", [])

    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.INFO
    assert "Trade Performance Validation" in ctx.alerter.calls[0].content
    assert "Readiness" in ctx.alerter.calls[0].content

    doc_text = doc.read_text()
    assert "## 2026-07-31" in doc_text
    assert "Trade Performance Validation" in doc_text
    assert doc_text.index("## 2026-07-31") < doc_text.index("## Next scheduled check")
    assert "## 2026-08-01" in doc_text  # old section preserved


def test_doc_missing_locally_notes_it_but_still_posts(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(tmp_path / "does_not_exist.md")}}
    _mock_subprocess(monkeypatch, "content", [])

    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert "NOT appended" in ctx.alerter.calls[0].content


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


def test_nonzero_exit_alerts_warn(ctx, monkeypatch):
    _configure(ctx)
    _mock_subprocess(monkeypatch, "", [], returncode=1)

    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.WARN


def test_empty_markdown_output_alerts_warn(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(tmp_path / "doc.md")}}
    _mock_subprocess(monkeypatch, "   ", [])

    run_at(ctx, monkeypatch)

    assert len(ctx.alerter.calls) == 1
    assert ctx.alerter.calls[0].severity == Severity.WARN
    assert "no output" in ctx.alerter.calls[0].content


def test_readiness_shows_sample_size_progress_for_live_accounts(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    doc = tmp_path / "doc.md"
    doc.write_text("## Next scheduled check\n")
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(doc), "sample_size_floor": 30}}
    entries = [
        {"name": "alpaca_live", "kind": "live", "broker": "alpaca",
         "by_product": {"beacon": {"n": 5, "pnl": -100.0, "wins": 1, "approx": 0}}},
        {"name": "tradier_sandbox", "kind": "paper", "broker": "tradier",
         "by_product": {"fuse": {"n": 15, "pnl": 1000.0, "wins": 9, "approx": 0}}},
    ]
    _mock_subprocess(monkeypatch, "md content", entries)

    run_at(ctx, monkeypatch)

    content = ctx.alerter.calls[0].content
    assert "alpaca_live/beacon: 5/30 closed trades" in content
    assert "tradier_sandbox" not in content.split("Readiness")[1].split("This week")[0]  # paper excluded from sample-size section


def test_readiness_marks_ready_once_floor_reached(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    doc = tmp_path / "doc.md"
    doc.write_text("## Next scheduled check\n")
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(doc), "sample_size_floor": 10}}
    entries = [{"name": "tradier_live", "kind": "live", "broker": "tradier",
                "by_product": {"sentinel": {"n": 12, "pnl": 50.0, "wins": 6, "approx": 0}}}]
    _mock_subprocess(monkeypatch, "md content", entries)

    run_at(ctx, monkeypatch)

    assert "tradier_live/sentinel: 12/10 closed trades -- READY" in ctx.alerter.calls[0].content


def test_clean_week_with_no_approx_and_no_incidents_increments_streak(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    doc = tmp_path / "doc.md"
    doc.write_text("## Next scheduled check\n")
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(doc)}}
    entries = [{"name": "tradier_sandbox", "kind": "paper", "broker": "tradier",
                "by_product": {"fuse": {"n": 15, "pnl": 1000.0, "wins": 9, "approx": 0}}}]
    _mock_subprocess(monkeypatch, "md content", entries)

    run_at(ctx, monkeypatch)

    assert "This week: CLEAN" in ctx.alerter.calls[0].content
    assert "Consecutive clean weeks: 1" in ctx.alerter.calls[0].content
    assert store.get_validation_streak(ctx.db) == 1


def test_week_with_approx_exits_is_not_clean_and_resets_streak(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    doc = tmp_path / "doc.md"
    doc.write_text("## Next scheduled check\n")
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(doc)}}
    store.record_validation_week(ctx.db, "2026-07-24", clean=True)  # prior streak of 1
    entries = [{"name": "alpaca_sandbox", "kind": "paper", "broker": "alpaca",
                "by_product": {"beacon": {"n": 6, "pnl": -800.0, "wins": 1, "approx": 6}}}]
    _mock_subprocess(monkeypatch, "md content", entries)

    run_at(ctx, monkeypatch)

    assert "This week: NOT CLEAN" in ctx.alerter.calls[0].content
    assert "Consecutive clean weeks: 0" in ctx.alerter.calls[0].content
    assert store.get_validation_streak(ctx.db) == 0


def test_week_with_new_critical_incident_is_not_clean(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    doc = tmp_path / "doc.md"
    doc.write_text("## Next scheduled check\n")
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(doc)}}
    store.open_incident(ctx.db, "stop_coverage", "sandbox", "sandbox:AAPL:c1", Severity.CRITICAL, "")
    entries = [{"name": "tradier_sandbox", "kind": "paper", "broker": "tradier",
                "by_product": {"fuse": {"n": 15, "pnl": 1000.0, "wins": 9, "approx": 0}}}]
    _mock_subprocess(monkeypatch, "md content", entries)

    run_at(ctx, monkeypatch)

    assert "This week: NOT CLEAN" in ctx.alerter.calls[0].content


def test_malformed_json_degrades_gracefully(ctx, monkeypatch, tmp_path):
    _configure(ctx)
    doc = tmp_path / "doc.md"
    doc.write_text("## Next scheduled check\n")
    ctx.watch_cfg = {"trade_validation": {"doc_path": str(doc)}}

    def fake_run(args, cwd=None, timeout=None, capture_output=None, text=None):
        if "--json" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="not json", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="md content", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    run_at(ctx, monkeypatch)  # must not raise

    assert len(ctx.alerter.calls) == 1
    assert "This week: CLEAN" in ctx.alerter.calls[0].content  # 0 entries -> 0 approx, treated as clean
