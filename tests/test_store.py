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
