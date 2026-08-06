from __future__ import annotations

from dataclasses import dataclass

from watch.checks import flat_by_close, killswitch_dryfire, ops_monitor_runner, stop_coverage
from watch.main import build_checks
from watch.reports import daily_trade_report, eod, mid_session, premarket, weekly


@dataclass
class _FakeCtx:
    watch_cfg: dict


def _by_name(checks, name):
    return next(c for c in checks if c.name == name)


def test_build_checks_registers_all_eight_checks():
    checks = build_checks(_FakeCtx(watch_cfg={}))
    names = {c.name for c in checks}
    assert names == {
        stop_coverage.NAME, flat_by_close.NAME, killswitch_dryfire.NAME,
        ops_monitor_runner.NAME, premarket.NAME, mid_session.NAME,
        eod.NAME, daily_trade_report.NAME, weekly.NAME,
    }


def test_build_checks_uses_documented_defaults_when_watch_cfg_empty():
    checks = build_checks(_FakeCtx(watch_cfg={}))

    assert _by_name(checks, stop_coverage.NAME).interval_seconds == 60
    assert _by_name(checks, flat_by_close.NAME).interval_seconds == 300
    assert _by_name(checks, killswitch_dryfire.NAME).daily_at_et == "08:30"
    assert _by_name(checks, ops_monitor_runner.NAME).interval_seconds == 1800
    assert _by_name(checks, premarket.NAME).daily_at_et == "08:45"
    assert _by_name(checks, mid_session.NAME).interval_seconds == 3600
    assert _by_name(checks, eod.NAME).daily_at_et == "16:15"
    assert _by_name(checks, daily_trade_report.NAME).daily_at_et == "16:16"
    assert _by_name(checks, weekly.NAME).daily_at_et == "16:20"


def test_build_checks_honors_watch_cfg_overrides():
    cfg = {
        "stop_coverage": {"poll_interval_seconds": 15},
        "flat_by_close": {"realert_interval_seconds": 120},
        "killswitch_dryfire": {"run_time_et": "09:00"},
        "ops_monitor_runner": {"interval_seconds_rth": 600},
        premarket.NAME: {"run_time_et": "08:00"},
        mid_session.NAME: {"interval_seconds": 1800},
        eod.NAME: {"run_time_et": "16:05"},
        daily_trade_report.NAME: {"run_time_et": "16:10"},
        weekly.NAME: {"run_time_et": "17:00"},
    }
    checks = build_checks(_FakeCtx(watch_cfg=cfg))

    assert _by_name(checks, stop_coverage.NAME).interval_seconds == 15
    assert _by_name(checks, flat_by_close.NAME).interval_seconds == 120
    assert _by_name(checks, killswitch_dryfire.NAME).daily_at_et == "09:00"
    assert _by_name(checks, ops_monitor_runner.NAME).interval_seconds == 600
    assert _by_name(checks, premarket.NAME).daily_at_et == "08:00"
    assert _by_name(checks, mid_session.NAME).interval_seconds == 1800
    assert _by_name(checks, eod.NAME).daily_at_et == "16:05"
    assert _by_name(checks, daily_trade_report.NAME).daily_at_et == "16:10"
    assert _by_name(checks, weekly.NAME).daily_at_et == "17:00"
