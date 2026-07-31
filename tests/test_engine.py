from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from watch.engine import CheckSpec, Engine

ET = ZoneInfo("America/New_York")


def et(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=ET)


def test_checkspec_requires_exactly_one_schedule_kind():
    with pytest.raises(ValueError):
        CheckSpec(name="x", run_fn=lambda ctx: None)
    with pytest.raises(ValueError):
        CheckSpec(name="x", run_fn=lambda ctx: None, interval_seconds=60, daily_at_et="08:30")


def test_interval_check_due_on_first_call():
    spec = CheckSpec(name="x", run_fn=lambda ctx: None, interval_seconds=60)
    assert spec.due(now_ts=1000.0, now_et_dt=et(2026, 7, 30, 10, 0)) is True


def test_interval_check_not_due_until_interval_elapses():
    spec = CheckSpec(name="x", run_fn=lambda ctx: None, interval_seconds=60)
    spec.mark_ran(now_ts=1000.0, now_et_dt=et(2026, 7, 30, 10, 0))
    assert spec.due(now_ts=1030.0, now_et_dt=et(2026, 7, 30, 10, 0, s=30)) is False
    assert spec.due(now_ts=1060.0, now_et_dt=et(2026, 7, 30, 10, 1)) is True


def test_daily_at_check_not_due_before_target_time():
    spec = CheckSpec(name="x", run_fn=lambda ctx: None, daily_at_et="16:05")
    assert spec.due(now_ts=0.0, now_et_dt=et(2026, 7, 30, 16, 0)) is False


def test_daily_at_check_due_at_or_after_target_time():
    spec = CheckSpec(name="x", run_fn=lambda ctx: None, daily_at_et="16:05")
    assert spec.due(now_ts=0.0, now_et_dt=et(2026, 7, 30, 16, 5)) is True
    assert spec.due(now_ts=0.0, now_et_dt=et(2026, 7, 30, 18, 0)) is True


def test_daily_at_check_runs_only_once_per_et_date():
    spec = CheckSpec(name="x", run_fn=lambda ctx: None, daily_at_et="16:05")
    spec.mark_ran(now_ts=0.0, now_et_dt=et(2026, 7, 30, 16, 5))
    assert spec.due(now_ts=0.0, now_et_dt=et(2026, 7, 30, 20, 0)) is False
    # New ET date -- due again.
    assert spec.due(now_ts=0.0, now_et_dt=et(2026, 7, 31, 16, 5)) is True


def test_tick_runs_due_checks_and_marks_them_ran():
    calls = []
    spec = CheckSpec(name="x", run_fn=lambda ctx: calls.append(ctx), interval_seconds=60)
    engine = Engine(ctx="the-context", checks=[spec])

    asyncio.run(engine.tick())

    assert calls == ["the-context"]


def test_tick_swallows_exceptions_from_one_check_and_still_marks_it_ran():
    def boom(ctx):
        raise RuntimeError("boom")

    calls = []
    bad = CheckSpec(name="bad", run_fn=boom, interval_seconds=60)
    good = CheckSpec(name="good", run_fn=lambda ctx: calls.append("ran"), interval_seconds=60)
    engine = Engine(ctx=None, checks=[bad, good])

    asyncio.run(engine.tick())  # must not raise

    assert calls == ["ran"]
    assert bad._last_run_ts > 0  # marked ran despite raising, so it doesn't spin every tick
