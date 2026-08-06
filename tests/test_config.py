from __future__ import annotations

from watch.config import load_watch_yaml


def test_load_watch_yaml_missing_file_returns_empty_dict(tmp_path):
    assert load_watch_yaml(tmp_path / "missing.yaml") == {}


def test_load_watch_yaml_reads_existing_file(tmp_path):
    p = tmp_path / "watch.yaml"
    p.write_text("stop_coverage:\n  poll_interval_seconds: 30\n", encoding="utf-8")
    assert load_watch_yaml(p) == {"stop_coverage": {"poll_interval_seconds": 30}}


def test_load_watch_yaml_empty_file_returns_empty_dict(tmp_path):
    """An empty (or all-comments) YAML file parses to None, not {} --
    yaml.safe_load's own behavior. Must normalize to {} so callers can
    always .get() without a None-check."""
    p = tmp_path / "watch.yaml"
    p.write_text("", encoding="utf-8")
    assert load_watch_yaml(p) == {}


def test_load_watch_yaml_accepts_path_as_string(tmp_path):
    p = tmp_path / "watch.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    assert load_watch_yaml(str(p)) == {"a": 1}
