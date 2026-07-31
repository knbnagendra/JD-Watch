"""US equity market-hours helpers, all in exchange (America/New_York) time.

Vendored verbatim from JD-Relay's jd_relay/market_hours.py rather than
imported as a package dependency -- JD-Watch runs as a fully separate
process/repo by design (see JD-Watch/README.md), the same reasoning that
already led ops/error_monitor.py in JD-Relay to be stdlib-only so it
survives a broken venv. This file has zero jd_relay-internal dependencies
(stdlib only), so vendoring it here means JD-Watch's own session-awareness
never silently drifts from JD-Relay's real trading-hours logic without
someone having to consciously copy the change over -- keep this in sync by
hand if JD-Relay's NYSE_HOLIDAYS ever gets its annual refresh.

NYSE_HOLIDAYS is a static per-year list, needs a manual refresh each year.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)

# Full-day NYSE closures. Source: NYSE Group's official 2025/2026/2027
# holiday and early-closings calendar announcement. Early closes (1pm ET,
# e.g. the Friday after Thanksgiving) are NOT included here.
NYSE_HOLIDAYS: dict[int, frozenset[date]] = {
    2026: frozenset([
        date(2026, 1, 1),   # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),   # Independence Day (observed -- Jul 4 falls on a Saturday)
        date(2026, 9, 7),   # Labor Day
        date(2026, 11, 26), # Thanksgiving
        date(2026, 12, 25), # Christmas
    ]),
}


def is_market_holiday(d: date) -> bool:
    return d in NYSE_HOLIDAYS.get(d.year, frozenset())


def now_et() -> datetime:
    return datetime.now(ET)


def to_et(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("to_et requires a timezone-aware datetime")
    return dt.astimezone(ET)


def is_regular_session_now(as_of_et: datetime) -> bool:
    if as_of_et.weekday() >= 5 or is_market_holiday(as_of_et.date()):
        return False
    return REGULAR_OPEN <= as_of_et.time() <= REGULAR_CLOSE


def parse_et_time_str(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def is_past_et_time(as_of_et: datetime, cutoff: str) -> bool:
    return as_of_et.time() >= parse_et_time_str(cutoff)
