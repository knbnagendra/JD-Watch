from __future__ import annotations

import concurrent.futures

from watch import store
from watch.severity import Severity


def test_connection_usable_from_a_different_thread(tmp_path):
    """Regression, first production deploy (2026-07-31): engine.py runs
    each check via asyncio.to_thread(), which the default executor may hand
    to a different worker thread than the one that created ctx.db --
    sqlite3's default check_same_thread=True raised a ProgrammingError on
    the very first real tick. store.connect() must build a connection
    that's safe to use from a thread other than the one that created it."""
    conn = store.connect(tmp_path / "thread_test.db")

    def use_from_other_thread():
        incident_id = store.open_incident(conn, "check", "sandbox", "AAPL", Severity.CRITICAL, "")
        return store.get_open_incident(conn, "check", "sandbox", "AAPL")["id"] == incident_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(use_from_other_thread).result()

    assert result is True


def test_no_open_incident_initially(db):
    assert store.get_open_incident(db, "check", "sandbox", "AAPL") is None


def test_open_incident_then_found(db):
    incident_id = store.open_incident(db, "check", "sandbox", "AAPL", Severity.CRITICAL, "detail")
    row = store.get_open_incident(db, "check", "sandbox", "AAPL")
    assert row is not None
    assert row["id"] == incident_id
    assert row["severity"] == "CRITICAL"


def test_closed_incident_not_returned_as_open(db):
    incident_id = store.open_incident(db, "check", "sandbox", "AAPL", Severity.CRITICAL, "detail")
    store.close_incident(db, incident_id)
    assert store.get_open_incident(db, "check", "sandbox", "AAPL") is None


def test_get_open_incidents_scoped_to_check_and_account(db):
    store.open_incident(db, "check_a", "sandbox", "AAPL", Severity.CRITICAL, "")
    store.open_incident(db, "check_a", "sandbox", "MSFT", Severity.CRITICAL, "")
    store.open_incident(db, "check_a", "live", "AAPL", Severity.CRITICAL, "")
    store.open_incident(db, "check_b", "sandbox", "AAPL", Severity.CRITICAL, "")

    rows = store.get_open_incidents(db, "check_a", "sandbox")

    assert {r["key"] for r in rows} == {"AAPL", "MSFT"}


def test_should_realert_true_when_never_alerted(db):
    incident_id = store.open_incident(db, "check", "sandbox", "AAPL", Severity.CRITICAL, "")
    assert store.should_realert(db, incident_id, realert_interval_seconds=300) is True


def test_should_realert_false_immediately_after_marking(db):
    incident_id = store.open_incident(db, "check", "sandbox", "AAPL", Severity.CRITICAL, "")
    store.mark_incident_alerted(db, incident_id)
    assert store.should_realert(db, incident_id, realert_interval_seconds=300) is False


def test_record_check_result_does_not_raise(db):
    store.record_check_result(db, "check", "sandbox", Severity.INFO, "all good")
    row = db.execute("SELECT * FROM check_results").fetchone()
    assert row["check_name"] == "check"
    assert row["severity"] == "INFO"


def test_get_all_open_incidents_across_checks_and_accounts(db):
    store.open_incident(db, "check_a", "sandbox", "AAPL", Severity.CRITICAL, "")
    store.open_incident(db, "check_b", "live", "MSFT", Severity.CRITICAL, "")
    closed_id = store.open_incident(db, "check_a", "sandbox", "TSLA", Severity.CRITICAL, "")
    store.close_incident(db, closed_id)

    rows = store.get_all_open_incidents(db)

    assert {(r["check_name"], r["account"], r["key"]) for r in rows} == {
        ("check_a", "sandbox", "AAPL"), ("check_b", "live", "MSFT"),
    }


def test_get_incidents_since_includes_closed_and_open(db):
    from datetime import datetime, timedelta, timezone
    id1 = store.open_incident(db, "check_a", "sandbox", "AAPL", Severity.CRITICAL, "")
    store.close_incident(db, id1)
    store.open_incident(db, "check_a", "sandbox", "MSFT", Severity.CRITICAL, "")

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = store.get_incidents_since(db, since)

    assert {r["key"] for r in rows} == {"AAPL", "MSFT"}


def test_get_incidents_since_excludes_older(db):
    from datetime import datetime, timedelta, timezone
    store.open_incident(db, "check_a", "sandbox", "AAPL", Severity.CRITICAL, "")

    since = datetime.now(timezone.utc) + timedelta(hours=1)  # future -- nothing qualifies
    rows = store.get_incidents_since(db, since)

    assert rows == []


def test_config_hash_roundtrip(db):
    assert store.get_config_hash(db, "rules.yaml") is None
    store.set_config_hash(db, "rules.yaml", "abc123")
    assert store.get_config_hash(db, "rules.yaml") == "abc123"
    store.set_config_hash(db, "rules.yaml", "def456")  # update, not insert-conflict
    assert store.get_config_hash(db, "rules.yaml") == "def456"


def test_days_since_last_bug_none_when_nothing_logged(db):
    assert store.days_since_last_bug(db) is None


def test_days_since_last_bug_zero_for_bug_logged_today(db):
    store.record_bug(db, "jd-relay", "some fix", "abc123")
    assert store.days_since_last_bug(db) == 0


def test_days_since_last_bug_uses_most_recent_entry(db):
    from datetime import datetime, timedelta, timezone
    old_id = store.record_bug(db, "jd-relay", "old fix")
    db.execute(
        "UPDATE bug_log SET logged_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(), old_id),
    )
    db.commit()
    store.record_bug(db, "jd-relay", "recent fix")

    assert store.days_since_last_bug(db) == 0


def test_get_bugs_since_excludes_older(db):
    from datetime import datetime, timedelta, timezone
    old_id = store.record_bug(db, "jd-relay", "old fix")
    db.execute(
        "UPDATE bug_log SET logged_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(), old_id),
    )
    db.commit()
    store.record_bug(db, "jd-relay", "recent fix")

    since = datetime.now(timezone.utc) - timedelta(days=1)
    rows = store.get_bugs_since(db, since)

    assert [r["description"] for r in rows] == ["recent fix"]


def test_record_bug_defaults_commit_sha_to_empty_string(db):
    store.record_bug(db, "jd-watch", "no commit yet")
    row = db.execute("SELECT commit_sha FROM bug_log").fetchone()
    assert row["commit_sha"] == ""


def test_validation_streak_zero_when_nothing_recorded(db):
    assert store.get_validation_streak(db) == 0


def test_validation_streak_increments_on_consecutive_clean_weeks(db):
    assert store.record_validation_week(db, "2026-08-08", clean=True) == 1
    assert store.record_validation_week(db, "2026-08-15", clean=True) == 2
    assert store.record_validation_week(db, "2026-08-22", clean=True) == 3
    assert store.get_validation_streak(db) == 3


def test_validation_streak_resets_on_a_dirty_week(db):
    store.record_validation_week(db, "2026-08-08", clean=True)
    store.record_validation_week(db, "2026-08-15", clean=True)
    assert store.record_validation_week(db, "2026-08-22", clean=False) == 0
    assert store.get_validation_streak(db) == 0
    # A clean week right after resumes counting from 1, not from the old streak.
    assert store.record_validation_week(db, "2026-08-29", clean=True) == 1


def test_validation_weeks_history_is_append_only(db):
    store.record_validation_week(db, "2026-08-08", clean=True, detail="0 approx, 0 incidents")
    store.record_validation_week(db, "2026-08-15", clean=False, detail="2 approx exits found")
    rows = db.execute("SELECT week_ending, clean, streak_after, detail FROM validation_weeks ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["week_ending"] == "2026-08-08"
    assert rows[0]["clean"] == 1
    assert rows[1]["clean"] == 0
    assert rows[1]["detail"] == "2 approx exits found"
