"""Shared helpers for the four scheduled reports (premarket/mid_session/
eod/weekly): config-file change detection and disk/memory headroom.
Deliberately just informational inputs to a report, not new detection
checks with their own severity/auto-action -- see watch/checks/ for that
pattern.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from watch import store

log = logging.getLogger("watch.reports.common")


def start_of_trading_day_utc(now_et_dt: datetime) -> datetime:
    """Midnight ET, converted to UTC -- store.py's opened_at/ts columns are
    UTC isoformat strings, but "today" for a trading report means the ET
    trading day, not the UTC calendar day."""
    return now_et_dt.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def hash_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        log.warning("hash_file_failed path=%s", path, exc_info=True)
        return None


def watched_config_files(ctx) -> list[Path]:
    jd_signal = Path(ctx.settings.jd_signal_repo_path)
    jd_relay = jd_signal.parent / "JD-Relay"  # sibling-directory convention, see ops_monitor.py
    return [jd_signal / "rules.yaml", jd_signal / "universe.yaml", jd_relay / "config.yaml"]


def check_config_files(ctx) -> list[dict]:
    """Returns one entry per watched file: {path, exists, changed}.
    `changed` compares against the hash stored from the last report run
    (store.py's config_state table) and always updates it to the current
    hash -- so "changed" means "since the last report," not "since ever."
    """
    results = []
    for path in watched_config_files(ctx):
        current = hash_file(path)
        key = str(path)
        previous = store.get_config_hash(ctx.db, key)
        changed = previous is not None and current is not None and current != previous
        if current is not None:
            store.set_config_hash(ctx.db, key, current)
        results.append({"path": key, "exists": current is not None, "changed": changed})
    return results


def disk_headroom_pct(path: str = "/") -> float | None:
    try:
        usage = shutil.disk_usage(path)
        return 100.0 * usage.free / usage.total
    except OSError:
        log.warning("disk_headroom_check_failed", exc_info=True)
        return None


def mem_headroom_pct() -> float | None:
    """Linux-only (/proc/meminfo) -- the VM this always runs on. Returns
    None off-Linux (e.g. local dev on Windows) rather than raising."""
    try:
        fields = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                name, _, rest = line.partition(":")
                fields[name] = int(rest.strip().split()[0])  # kB
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
        if not total or available is None:
            return None
        return 100.0 * available / total
    except (OSError, ValueError, IndexError):
        return None
