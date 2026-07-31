from __future__ import annotations

from watch.reports import common


def test_hash_file_missing_returns_none(tmp_path):
    assert common.hash_file(tmp_path / "nope.yaml") is None


def test_hash_file_deterministic(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n")
    h1 = common.hash_file(f)
    h2 = common.hash_file(f)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest


def test_hash_file_changes_with_content(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n")
    h1 = common.hash_file(f)
    f.write_text("a: 2\n")
    h2 = common.hash_file(f)
    assert h1 != h2


def test_check_config_files_first_run_never_changed(db, tmp_path):
    from tests.conftest import FakeContext, FakeSettings
    jd_signal = tmp_path / "JD-Signal"
    jd_signal.mkdir()
    (jd_signal / "rules.yaml").write_text("x: 1\n")
    ctx = FakeContext(relay=None, alerter=None, db=db, settings=FakeSettings(jd_signal_repo_path=str(jd_signal)))

    results = common.check_config_files(ctx)

    rules_result = next(r for r in results if r["path"].endswith("rules.yaml"))
    assert rules_result["exists"] is True
    assert rules_result["changed"] is False  # nothing to compare against yet


def test_check_config_files_detects_change_on_second_run(db, tmp_path):
    from tests.conftest import FakeContext, FakeSettings
    jd_signal = tmp_path / "JD-Signal"
    jd_signal.mkdir()
    rules = jd_signal / "rules.yaml"
    rules.write_text("x: 1\n")
    ctx = FakeContext(relay=None, alerter=None, db=db, settings=FakeSettings(jd_signal_repo_path=str(jd_signal)))

    common.check_config_files(ctx)  # first run, establishes baseline
    rules.write_text("x: 2\n")
    results = common.check_config_files(ctx)

    rules_result = next(r for r in results if r["path"].endswith("rules.yaml"))
    assert rules_result["changed"] is True


def test_check_config_files_no_change_when_content_stable(db, tmp_path):
    from tests.conftest import FakeContext, FakeSettings
    jd_signal = tmp_path / "JD-Signal"
    jd_signal.mkdir()
    (jd_signal / "rules.yaml").write_text("x: 1\n")
    ctx = FakeContext(relay=None, alerter=None, db=db, settings=FakeSettings(jd_signal_repo_path=str(jd_signal)))

    common.check_config_files(ctx)
    results = common.check_config_files(ctx)

    rules_result = next(r for r in results if r["path"].endswith("rules.yaml"))
    assert rules_result["changed"] is False


def test_check_config_files_missing_file_reported_not_exists(db, tmp_path):
    from tests.conftest import FakeContext, FakeSettings
    jd_signal = tmp_path / "JD-Signal"
    jd_signal.mkdir()  # no rules.yaml created
    ctx = FakeContext(relay=None, alerter=None, db=db, settings=FakeSettings(jd_signal_repo_path=str(jd_signal)))

    results = common.check_config_files(ctx)

    rules_result = next(r for r in results if r["path"].endswith("rules.yaml"))
    assert rules_result["exists"] is False
    assert rules_result["changed"] is False


def test_disk_headroom_pct_returns_plausible_value():
    pct = common.disk_headroom_pct()
    assert pct is None or 0.0 <= pct <= 100.0


def test_mem_headroom_pct_does_not_raise():
    pct = common.mem_headroom_pct()
    assert pct is None or 0.0 <= pct <= 100.0


def test_start_of_trading_day_utc_is_midnight_et_in_utc():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now_et = datetime(2026, 7, 31, 14, 30, tzinfo=et)  # 2:30pm ET

    result = common.start_of_trading_day_utc(now_et)

    # Midnight ET on 2026-07-31 (EDT, UTC-4) is 04:00 UTC.
    assert result.hour == 4
    assert result.date().isoformat() == "2026-07-31"
