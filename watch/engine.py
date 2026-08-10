"""Scheduler/state machine: runs each registered check on its own schedule,
either a fixed interval or once per ET calendar day at/after a target time.

No existing scheduler exists in either JD-Signal or JD-Relay to reuse --
ops_monitor.py is a manually-invoked CLI, not a daemon (see JD-Watch's
README.md). This is deliberately small: a CheckSpec plus a tick loop, not a
general task-queue framework, matching the scope actually needed here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from watch import market_hours, store

log = logging.getLogger("watch.engine")


@dataclass
class CheckSpec:
    name: str
    run_fn: Callable[[object], None]
    interval_seconds: float | None = None
    daily_at_et: str | None = None  # "HH:MM", run once per ET date at/after this time
    _last_run_ts: float = field(default=0.0, repr=False)
    _last_run_date: date | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.interval_seconds is None) == (self.daily_at_et is None):
            raise ValueError(f"{self.name}: exactly one of interval_seconds/daily_at_et must be set")

    def due(self, now_ts: float, now_et_dt) -> bool:
        if self.interval_seconds is not None:
            return now_ts - self._last_run_ts >= self.interval_seconds
        today = now_et_dt.date()
        if self._last_run_date == today:
            return False
        return now_et_dt.time() >= market_hours.parse_et_time_str(self.daily_at_et)

    def mark_ran(self, now_ts: float, now_et_dt) -> None:
        self._last_run_ts = now_ts
        self._last_run_date = now_et_dt.date()


class Engine:
    def __init__(self, ctx, checks: list[CheckSpec], tick_seconds: float = 15.0) -> None:
        self.ctx = ctx
        self.checks = checks
        self.tick_seconds = tick_seconds
        self._hydrate_from_store()

    def _hydrate_from_store(self) -> None:
        """Restores each check's last-ran state from disk so a process
        restart doesn't forget a daily_at_et check already ran today --
        see store.py's check_schedule table docstring for the live
        incident this fixes. No-ops if ctx has no real db (e.g. tests
        constructing Engine with a bare string/None ctx) -- a report
        failing to persist its schedule must never look like a check
        failing to run."""
        db = getattr(self.ctx, "db", None)
        if db is None:
            return
        for check in self.checks:
            last_run_date, last_run_ts = store.get_check_schedule(db, check.name)
            check._last_run_ts = last_run_ts
            check._last_run_date = date.fromisoformat(last_run_date) if last_run_date else None

    async def run_forever(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.tick_seconds)

    async def tick(self) -> None:
        now_ts = time.time()
        now_et_dt = market_hours.now_et()
        db = getattr(self.ctx, "db", None)
        for check in self.checks:
            if not check.due(now_ts, now_et_dt):
                continue
            try:
                await asyncio.to_thread(check.run_fn, self.ctx)
            except Exception:
                log.exception("check_failed name=%s", check.name)
            check.mark_ran(now_ts, now_et_dt)
            if db is not None:
                store.set_check_schedule(
                    db, check.name,
                    check._last_run_date.isoformat() if check._last_run_date else None,
                    check._last_run_ts,
                )
