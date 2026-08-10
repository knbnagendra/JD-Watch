from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from watch import market_hours
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


def test_engine_persists_daily_check_state_across_restart(ctx, monkeypatch):
    """Regression, confirmed live 2026-08-10: CheckSpec._last_run_date was
    in-memory only, so a process restart landing after a daily_at_et
    check's scheduled time made Engine.due() see a fresh None
    _last_run_date and re-fire that same check again the same ET date --
    demonstrated by real killswitch_dryfire halt/rearm cycles re-firing on
    deploy-triggered restarts. A brand-new Engine/CheckSpec pair built
    against the same ctx.db (simulating a restart) must see the persisted
    state and NOT re-run a check that already ran today."""
    fixed_now = et(2026, 8, 10, 16, 5)
    monkeypatch.setattr(market_hours, "now_et", lambda: fixed_now)

    calls = []
    spec1 = CheckSpec(name="daily_x", run_fn=lambda c: calls.append("ran"), daily_at_et="16:05")
    engine1 = Engine(ctx=ctx, checks=[spec1])
    asyncio.run(engine1.tick())
    assert calls == ["ran"]

    # Simulate a restart: fresh CheckSpec/Engine instances, same ctx.db.
    spec2 = CheckSpec(name="daily_x", run_fn=lambda c: calls.append("ran"), daily_at_et="16:05")
    engine2 = Engine(ctx=ctx, checks=[spec2])
    asyncio.run(engine2.tick())

    assert calls == ["ran"]  # unchanged -- must not have re-run


def test_engine_hydration_is_a_noop_without_a_real_db():
    """ctx values without a .db (every pre-existing test in this file)
    must keep working exactly as before -- persistence degrades
    gracefully rather than raising."""
    Engine(ctx="the-context", checks=[])  # must not raise
    Engine(ctx=None, checks=[])  # must not raise


def test_engine_persists_interval_check_state_across_restart(ctx):
    """Same persistence mechanism also covers interval-mode checks --
    lower stakes than the daily_at_et case (worst case pre-fix was firing
    a bit early after a restart, not double-counting a whole day), but
    should round-trip through the store correctly regardless."""
    from watch import store

    spec1 = CheckSpec(name="interval_x", run_fn=lambda c: None, interval_seconds=3600)
    engine1 = Engine(ctx=ctx, checks=[spec1])
    asyncio.run(engine1.tick())
    persisted_ts = spec1._last_run_ts
    assert persisted_ts > 0

    # A fresh CheckSpec/Engine pair (simulating a restart) must load the
    # exact same _last_run_ts from the store, not reset to 0.0.
    spec2 = CheckSpec(name="interval_x", run_fn=lambda c: None, interval_seconds=3600)
    Engine(ctx=ctx, checks=[spec2])  # hydration happens at construction
    assert spec2._last_run_ts == persisted_ts

    last_run_date, last_run_ts = store.get_check_schedule(ctx.db, "interval_x")
    assert last_run_ts == persisted_ts
