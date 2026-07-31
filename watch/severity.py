"""Severity ladder shared by every check and by alerting.py's dedupe/backoff.

Phase 1 only ever uses INFO/WARN/CRITICAL in practice -- HALT (halt new
entries, flatten per policy, page, require human resume) is defined here
for the severity table's completeness and for later phases, but no phase-1
check emits it: every phase-1 auto-action stops at "halt new entries",
never flatten. See JD-Watch/README.md's "Hard safety constraint" section.
"""

from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"
    HALT = "HALT"
