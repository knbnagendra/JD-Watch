"""Shared test fixtures. `no_network` mirrors the same-named fixture in both
JD-Signal's and JD-Relay's own conftest.py: no test in this suite may make a
real HTTP call -- everything external (JD-Relay, Discord) is faked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from watch import store
from watch.alerting import DiscordAlerter
from watch.severity import Severity


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("real network call attempted in a test -- use FakeRelayClient/FakeAlerter instead")

    monkeypatch.setattr("requests.get", _blocked)
    monkeypatch.setattr("requests.post", _blocked)


@pytest.fixture
def db(tmp_path):
    conn = store.connect(tmp_path / "watch_test.db")
    yield conn
    conn.close()


class FakeRelayClient:
    """Test double for watch.relay_client.RelayClient -- in-memory, no HTTP.
    `positions` is the exact dict shape GET /positions returns; `halted`
    tracks per-account manual_halt state so a test can assert the mocked
    halt()/rearm() calls actually flip it, mirroring the real endpoint's
    round-trip behavior."""

    def __init__(self, positions: dict | None = None) -> None:
        self.positions = positions if positions is not None else {"accounts": {}}
        self.halted: dict[str, bool] = {}
        self.halt_calls: list[tuple[str, bool]] = []
        self.rearm_calls: list[str] = []
        self.raise_on_positions = False
        self.raise_on_halt = False
        self.raise_on_rearm = False
        self.raise_on_status = False

    def get_positions(self) -> dict:
        if self.raise_on_positions:
            raise RuntimeError("simulated JD-Relay unreachable")
        return self.positions

    def get_status(self) -> dict:
        if self.raise_on_status:
            raise RuntimeError("simulated JD-Relay unreachable")
        return {"accounts": {acct: {"manual_halt": halted} for acct, halted in self.halted.items()}}

    def halt(self, account: str, flatten: bool = False) -> dict:
        if self.raise_on_halt:
            raise RuntimeError("simulated halt failure")
        self.halt_calls.append((account, flatten))
        self.halted[account] = True
        return {"ok": True, "account": account, "halted": True}

    def rearm(self, account: str) -> dict:
        if self.raise_on_rearm:
            raise RuntimeError("simulated rearm failure")
        self.rearm_calls.append(account)
        self.halted[account] = False
        return {"ok": True, "account": account, "halted": False}


@dataclass
class RecordedAlert:
    key: str
    severity: Severity
    content: str
    sent: bool


class FakeAlerter:
    """Test double for watch.alerting.DiscordAlerter -- records every call
    instead of posting, but keeps the real dedupe/backoff arithmetic (a
    subclass, not a from-scratch fake) so tests can exercise dedupe
    behavior against the real implementation."""

    def __init__(self) -> None:
        self.calls: list[RecordedAlert] = []
        self.resolved: list[str] = []
        self._inner = DiscordAlerter(token="", channel_id="")

    def alert(self, key: str, severity: Severity, content: str, *, force: bool = False, now=None) -> bool:
        sent = self._inner.alert(key, severity, content, force=force, now=now)
        self.calls.append(RecordedAlert(key=key, severity=severity, content=content, sent=sent))
        return sent

    def resolve(self, key: str) -> None:
        self._inner.resolve(key)
        self.resolved.append(key)


@dataclass
class FakeSettings:
    jd_signal_repo_path: str = ""
    jd_signal_python: str = ""
    jd_relay_repo_path: str = ""
    jd_relay_python: str = ""


@dataclass
class FakeContext:
    relay: FakeRelayClient
    alerter: FakeAlerter
    db: object
    watch_cfg: dict = field(default_factory=dict)
    settings: FakeSettings = field(default_factory=FakeSettings)


@pytest.fixture
def ctx(db):
    return FakeContext(relay=FakeRelayClient(), alerter=FakeAlerter(), db=db)
