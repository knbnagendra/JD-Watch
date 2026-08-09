from __future__ import annotations

import sys

from watch import log_bug, store


def test_log_bug_records_entry_with_all_fields(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "watch_test.db"

    class FakeSettings:
        store_path = str(db_path)

    monkeypatch.setattr(log_bug, "WatchSettings", lambda: FakeSettings())
    monkeypatch.setattr(sys, "argv", [
        "log_bug.py", "--repo", "jd-relay", "--description", "fixed the thing", "--commit", "abc123",
    ])

    log_bug.main()

    conn = store.connect(db_path)
    row = conn.execute("SELECT * FROM bug_log").fetchone()
    conn.close()
    assert row["repo"] == "jd-relay"
    assert row["description"] == "fixed the thing"
    assert row["commit_sha"] == "abc123"

    out = capsys.readouterr().out
    assert "fixed the thing" in out
    assert "abc123" in out


def test_log_bug_commit_defaults_to_empty(tmp_path, monkeypatch):
    db_path = tmp_path / "watch_test.db"

    class FakeSettings:
        store_path = str(db_path)

    monkeypatch.setattr(log_bug, "WatchSettings", lambda: FakeSettings())
    monkeypatch.setattr(sys, "argv", [
        "log_bug.py", "--repo", "jd-watch", "--description", "no commit yet",
    ])

    log_bug.main()

    conn = store.connect(db_path)
    row = conn.execute("SELECT * FROM bug_log").fetchone()
    conn.close()
    assert row["commit_sha"] == ""
