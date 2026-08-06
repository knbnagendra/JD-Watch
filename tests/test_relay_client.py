from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from watch.relay_client import RelayClient


def _fake_response(json_body=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error", response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture
def client():
    return RelayClient("http://127.0.0.1:8787/", "test-secret", timeout=5.0)


def test_base_url_trailing_slash_stripped():
    c = RelayClient("http://127.0.0.1:8787/", "secret")
    assert c.base_url == "http://127.0.0.1:8787"


def test_get_positions_sends_secret_header_and_returns_json(client, monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _fake_response({"accounts": {}})

    monkeypatch.setattr(requests, "get", fake_get)
    result = client.get_positions()

    assert result == {"accounts": {}}
    assert captured["url"] == "http://127.0.0.1:8787/positions"
    assert captured["headers"] == {"X-JD-Relay-Secret": "test-secret"}
    assert captured["timeout"] == 5.0


def test_get_status_hits_control_status_endpoint(client, monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _fake_response({"accounts": {}})

    monkeypatch.setattr(requests, "get", fake_get)
    client.get_status()

    assert captured["url"] == "http://127.0.0.1:8787/control/status"


def test_halt_sends_account_and_flatten_params(client, monkeypatch):
    captured = {}

    def fake_post(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _fake_response({"ok": True})

    monkeypatch.setattr(requests, "post", fake_post)
    result = client.halt("live", flatten=True)

    assert result == {"ok": True}
    assert captured["url"] == "http://127.0.0.1:8787/control/halt"
    assert captured["params"] == {"account": "live", "flatten": "true"}


def test_halt_defaults_flatten_to_false(client, monkeypatch):
    captured = {}

    def fake_post(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _fake_response({"ok": True})

    monkeypatch.setattr(requests, "post", fake_post)
    client.halt("live")

    assert captured["params"] == {"account": "live", "flatten": "false"}


def test_rearm_sends_account_param(client, monkeypatch):
    captured = {}

    def fake_post(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _fake_response({"ok": True})

    monkeypatch.setattr(requests, "post", fake_post)
    result = client.rearm("sandbox")

    assert result == {"ok": True}
    assert captured["url"] == "http://127.0.0.1:8787/control/rearm"
    assert captured["params"] == {"account": "sandbox"}


@pytest.mark.parametrize("method_name,call", [
    ("get_positions", lambda c: c.get_positions()),
    ("get_status", lambda c: c.get_status()),
])
def test_get_methods_raise_on_http_error(client, monkeypatch, method_name, call):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(status_code=500))
    with pytest.raises(requests.HTTPError):
        call(client)


@pytest.mark.parametrize("call", [
    lambda c: c.halt("live"),
    lambda c: c.rearm("live"),
])
def test_post_methods_raise_on_http_error(client, monkeypatch, call):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _fake_response(status_code=500))
    with pytest.raises(requests.HTTPError):
        call(client)
