"""Thin HTTP client for JD-Relay's control surface. JD-Watch talks to
JD-Relay purely over its existing HTTP API (GET /positions, POST
/control/halt, POST /control/rearm, GET /control/status) rather than
importing jd_relay as a package -- keeps the two repos fully decoupled
processes, matching the out-of-band design (a crashed JD-Relay must not
crash JD-Watch and vice versa) and the notifier.py finding from this
project's own research that reuse-by-import isn't worth the coupling here.

All requests carry the same X-JD-Relay-Secret constant-time-compared header
JD-Relay's own /control/* routes already require.
"""

from __future__ import annotations

import requests


class RelayClient:
    def __init__(self, base_url: str, secret: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"X-JD-Relay-Secret": self.secret}

    def get_positions(self) -> dict:
        resp = requests.get(f"{self.base_url}/positions", headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_status(self) -> dict:
        resp = requests.get(f"{self.base_url}/control/status", headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def halt(self, account: str, flatten: bool = False) -> dict:
        resp = requests.post(
            f"{self.base_url}/control/halt",
            params={"account": account, "flatten": "true" if flatten else "false"},
            headers=self._headers(), timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def rearm(self, account: str) -> dict:
        resp = requests.post(
            f"{self.base_url}/control/rearm", params={"account": account},
            headers=self._headers(), timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
