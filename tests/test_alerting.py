from __future__ import annotations

from watch.alerting import DiscordAlerter, _truncate_for_discord
from watch.severity import Severity


def make_alerter(**overrides):
    defaults = dict(token="", channel_id="", base_dedupe_window_seconds=300, max_dedupe_window_seconds=7200)
    defaults.update(overrides)
    a = DiscordAlerter(**defaults)
    a.sent: list[str] = []
    a._post = lambda content: a.sent.append(content)
    return a


def test_first_alert_always_sends():
    a = make_alerter()
    sent = a.alert("k1", Severity.CRITICAL, "something broke", now=1000.0)
    assert sent is True
    assert len(a.sent) == 1
    assert "CRITICAL" in a.sent[0]


def test_second_alert_within_base_window_suppressed():
    a = make_alerter()
    a.alert("k1", Severity.WARN, "msg", now=1000.0)
    sent = a.alert("k1", Severity.WARN, "msg again", now=1000.0 + 100)  # < 300s base window
    assert sent is False
    assert len(a.sent) == 1


def test_dedupe_window_doubles_per_repeat():
    a = make_alerter()
    a.alert("k1", Severity.WARN, "1", now=0.0)          # streak=1
    assert a.alert("k1", Severity.WARN, "2", now=300.0) is True   # streak=2, waited exactly base window
    assert a.alert("k1", Severity.WARN, "3", now=300.0 + 300) is False  # needs 600s now
    assert a.alert("k1", Severity.WARN, "4", now=300.0 + 600) is True


def test_dedupe_window_caps_at_max():
    a = make_alerter(base_dedupe_window_seconds=300, max_dedupe_window_seconds=1200)
    now = 0.0
    a.alert("k1", Severity.WARN, "1", now=now)
    for _ in range(10):
        now += 1200  # always exceeds any window <= the 1200s cap
        assert a.alert("k1", Severity.WARN, "n", now=now) is True


def test_force_bypasses_dedupe_window():
    a = make_alerter()
    a.alert("k1", Severity.CRITICAL, "1", now=0.0)
    sent = a.alert("k1", Severity.CRITICAL, "2", now=1.0, force=True)
    assert sent is True
    assert len(a.sent) == 2


def test_different_keys_do_not_share_dedupe_state():
    a = make_alerter()
    a.alert("k1", Severity.WARN, "1", now=0.0)
    sent = a.alert("k2", Severity.WARN, "1", now=0.0)
    assert sent is True


def test_resolve_clears_streak_so_next_alert_starts_fresh():
    a = make_alerter()
    a.alert("k1", Severity.WARN, "1", now=0.0)
    a.alert("k1", Severity.WARN, "2", now=300.0)  # streak now 2
    a.resolve("k1")
    # Immediately after resolve, a brand-new incident on the same key must
    # not be suppressed by the old streak's now-much-longer window.
    sent = a.alert("k1", Severity.WARN, "new incident", now=300.5)
    assert sent is True


def test_streak_resets_after_long_quiet_period():
    a = make_alerter(base_dedupe_window_seconds=300, max_dedupe_window_seconds=1200)
    a.alert("k1", Severity.WARN, "1", now=0.0)
    a.alert("k1", Severity.WARN, "2", now=1200.0)  # streak=2, window now maxed at 1200
    # Quiet for longer than streak_reset_after_seconds (2 * max = 2400s)
    sent = a.alert("k1", Severity.WARN, "3", now=1200.0 + 2500.0)
    assert sent is True
    # Confirm it reset to streak=1 behavior: next alert within base window is suppressed.
    sent2 = a.alert("k1", Severity.WARN, "4", now=1200.0 + 2500.0 + 100)
    assert sent2 is False


def test_missing_token_or_channel_does_not_raise():
    a = DiscordAlerter(token="", channel_id="")
    # Uses the real _post (no monkeypatch) -- must log and return, not crash
    # or hit the network (no_network fixture would fail the test if it did).
    a.alert("k1", Severity.INFO, "no credentials configured", now=0.0)


def test_truncate_for_discord_short_message_unchanged():
    assert _truncate_for_discord("short") == "short"


def test_truncate_for_discord_long_message_capped():
    long_msg = "x" * 3000
    result = _truncate_for_discord(long_msg)
    assert len(result) <= 2000
    assert result.endswith("[truncated]")
