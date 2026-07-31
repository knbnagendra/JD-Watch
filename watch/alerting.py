"""Standalone Discord alerting: bot-token REST POST to a single channel ID,
mirroring JD-Signal's ops_monitor.py _post() (same auth shape, same 2000-
char truncation safety net -- see ops_monitor.py's _truncate_for_discord
docstring for the real 2026-07-22 incident that made truncation mandatory
rather than optional here), reusing DISCORD_OPS_CHANNEL_ID conventions
rather than standing up new severity-tiered channels/webhooks.

Dedupe/backoff mirrors JD-Relay's ops/error_monitor.py exactly (same base
window, doubling, cap, streak-reset-after-quiet logic) -- that pattern
already survived a real incident (one recurring error posted ~322 times in
a day before backoff existed) and there's no reason for JD-Watch to
reinvent a different one. The one deliberate difference: error_monitor.py
keys its dedupe off a regex-scrubbed slice of the message text (it has no
other identity for a raw log line); JD-Watch's alerts are already
structured per-check, so the caller passes an explicit stable `key`
(typically f"{check_name}:{account}") instead.
"""

from __future__ import annotations

import logging
import time

import requests

from watch.severity import Severity

log = logging.getLogger("watch.alerting")

DISCORD_MAX_MESSAGE_LENGTH = 2000

_SEVERITY_PREFIX = {
    Severity.INFO: "ℹ️",       # info
    Severity.WARN: "⚠️",       # warning
    Severity.CRITICAL: "\U0001F534",     # red circle
    Severity.HALT: "\U0001F6A8",         # rotating light
}


def _truncate_for_discord(content: str) -> str:
    if len(content) <= DISCORD_MAX_MESSAGE_LENGTH:
        return content
    marker = "\n... [truncated]"
    return content[: DISCORD_MAX_MESSAGE_LENGTH - len(marker)] + marker


class DiscordAlerter:
    def __init__(self, token: str, channel_id: str, *,
                 base_dedupe_window_seconds: float = 300, max_dedupe_window_seconds: float = 7200) -> None:
        self.token = token
        self.channel_id = channel_id
        self.base_dedupe_window_seconds = base_dedupe_window_seconds
        self.max_dedupe_window_seconds = max_dedupe_window_seconds
        self.streak_reset_after_seconds = 2 * max_dedupe_window_seconds
        self._last_sent: dict[str, float] = {}
        self._streak: dict[str, int] = {}

    def _dedupe_window_seconds(self, sends_so_far: int) -> float:
        return min(
            self.base_dedupe_window_seconds * (2 ** max(sends_so_far - 1, 0)),
            self.max_dedupe_window_seconds,
        )

    def _post(self, content: str) -> None:
        if not self.token or not self.channel_id:
            log.warning("discord_alert_not_sent_missing_config content=%s", content[:200])
            return
        resp = requests.post(
            f"https://discord.com/api/v10/channels/{self.channel_id}/messages",
            headers={"Authorization": f"Bot {self.token}"},
            json={"content": _truncate_for_discord(content)},
            timeout=10,
        )
        if resp.status_code >= 300:
            log.error("discord_post_failed status=%s body=%s", resp.status_code, resp.text[:500])

    def alert(self, key: str, severity: Severity, content: str, *, force: bool = False,
              now: float | None = None) -> bool:
        """Returns True if actually sent (vs. suppressed by the backoff
        window). `key` must be a stable identity for the underlying
        condition, not derived from message text."""
        now = now if now is not None else time.time()
        last = self._last_sent.get(key)
        if last is not None and now - last >= self.streak_reset_after_seconds:
            self._streak[key] = 0

        streak = self._streak.get(key, 0)
        if not force and last is not None and now - last < self._dedupe_window_seconds(streak):
            return False

        prefix = _SEVERITY_PREFIX.get(severity, "")
        self._post(f"{prefix} **{severity.value}** {content}")
        self._last_sent[key] = now
        self._streak[key] = streak + 1
        return True

    def resolve(self, key: str) -> None:
        """Clears backoff state for `key` so a genuinely new future incident
        on the same key starts back at the base window instead of
        inheriting a stale streak from an already-resolved one."""
        self._last_sent.pop(key, None)
        self._streak.pop(key, None)
