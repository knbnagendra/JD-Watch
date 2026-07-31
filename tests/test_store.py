from __future__ import annotations

from watch import store
from watch.severity import Severity


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
