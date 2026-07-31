from __future__ import annotations

import datetime as dt
import subprocess
from zoneinfo import ZoneInfo

from watch import market_hours, store
from watch.reports import weekly
from watch.severity import Severity

ET = ZoneInfo("America/New_York")


def run_at(ctx, monkeypatch, date, hour=16, minute=20):
    fixed_now = dt.datetime(*date, hour, minute, tzinfo=ET)
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)
    weekly.run(ctx)


def fake_subprocess_run(*a, **k):
    return subprocess.CompletedProcess(args=a, returncode=0, stdout="3\n", stderr="")


def test_noop_on_non_friday(ctx, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    run_at(ctx, monkeypatch, date=(2026, 7, 30))  # Thursday
    assert ctx.alerter.calls == []


def test_runs_on_friday(ctx, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    run_at(ctx, monkeypatch, date=(2026, 7, 31))  # Friday
    assert len(ctx.alerter.calls) == 1
    assert "jd-relay: 3" in ctx.alerter.calls[0].content


def test_restart_count_failure_reported_as_unavailable(ctx, monkeypatch):
    def _raise(*a, **k):
        raise subprocess.SubprocessError("boom")
    monkeypatch.setattr(subprocess, "run", _raise)

    run_at(ctx, monkeypatch, date=(2026, 7, 31))

    assert "jd-relay: unavailable" in ctx.alerter.calls[0].content


def test_incident_counts_grouped_by_check(ctx, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    store.open_incident(ctx.db, "stop_coverage", "sandbox", "k1", Severity.CRITICAL, "")
    store.open_incident(ctx.db, "stop_coverage", "sandbox", "k2", Severity.CRITICAL, "")
    store.open_incident(ctx.db, "flat_by_close", "live", "k3", Severity.CRITICAL, "")

    run_at(ctx, monkeypatch, date=(2026, 7, 31))

    content = ctx.alerter.calls[0].content
    assert "stop_coverage: 2" in content
    assert "flat_by_close: 1" in content


def test_incident_outside_week_excluded(ctx, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    incident_id = store.open_incident(ctx.db, "stop_coverage", "sandbox", "k1", Severity.CRITICAL, "")
    ctx.db.execute("UPDATE incidents SET opened_at = '2026-07-01T10:00:00+00:00' WHERE id = ?", (incident_id,))
    ctx.db.commit()

    run_at(ctx, monkeypatch, date=(2026, 7, 31))

    assert "Incidents this week: none" in ctx.alerter.calls[0].content
