"""Hermetic tests for the OBS Studio adapter (obs_studio.py).

No real OBS, no real network, no real obs-websocket-py: detection is
substituted through the module-level ``detect_app`` reference, the TCP
probe through a fake connector, and the websocket client through a
fake ``obswebsocket`` module handed to ``_connect`` via ``import_fn``.
"""
from __future__ import annotations

import types

import pytest

from desktop_mcp.adapters.apps import obs_studio
from desktop_mcp.adapters.apps.obs_studio import _connect, _tcp_reachable


# ------------------------------------------------------------------ fake mcp

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _registered():
    mcp = _FakeMCP()
    obs_studio.register(mcp)
    return mcp.tools


def test_register_binds_at_least_three_tools():
    tools = _registered()
    assert len(tools) >= 3
    assert {"obs_studio_status", "obs_studio_launch",
            "obs_studio_websocket_status"} <= set(tools)
    assert obs_studio.APP_ID == "obs-studio"


# -------------------------------------------------------------------- status

def test_status_uses_module_level_detect_app(monkeypatch):
    def fake_detect(app_id):
        assert app_id == "obs-studio"
        return {"ok": True, "id": app_id, "name": "OBS Studio",
                "installed": True, "path": "C:/Program Files/obs-studio/bin/64bit/obs64.exe",
                "via": "path", "strategy": "plugin", "prog_id": None}

    monkeypatch.setattr(obs_studio, "detect_app", fake_detect)
    monkeypatch.setattr(obs_studio, "_tcp_reachable",
                        lambda host, port: True)

    result = _registered()["obs_studio_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "obs-studio"
    assert result["installed"] is True
    assert result["path"].endswith("obs64.exe")
    assert result["strategy"] == "plugin"
    # websocket enrichment from the (faked) TCP probe
    assert result["websocket_reachable"] is True
    assert result["websocket"]["reachable"] is True
    assert result["websocket"]["host"] == "127.0.0.1"
    assert result["websocket"]["port"] == 4455


def test_status_unreachable_websocket_is_a_clean_dict(monkeypatch):
    monkeypatch.setattr(obs_studio, "detect_app",
                        lambda app_id: {"ok": True, "installed": False,
                                        "path": None, "via": None,
                                        "name": "OBS Studio"})
    monkeypatch.setattr(obs_studio, "_tcp_reachable",
                        lambda host, port: False)
    result = _registered()["obs_studio_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["websocket_reachable"] is False


def test_launch_delegates_to_catalog(monkeypatch):
    calls = []
    monkeypatch.setattr(obs_studio, "catalog_launch",
                        lambda app_id: calls.append(app_id) or
                        {"ok": True, "app_id": app_id, "launched": True})
    result = _registered()["obs_studio_launch"]()
    assert result["ok"] is True and result["launched"] is True
    assert calls == ["obs-studio"]


# --------------------------------------------------------------- TCP probe

class _FakeSock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_tcp_reachable_success_with_fake_connector():
    seen = []
    sock = _FakeSock()

    def connector(address, timeout=None):
        seen.append((address, timeout))
        return sock

    assert _tcp_reachable("127.0.0.1", 4455, connector=connector) is True
    assert seen[0][0] == ("127.0.0.1", 4455)
    assert sock.closed is True


def test_tcp_reachable_failure_with_fake_connector():
    def connector(address, timeout=None):
        raise OSError("connection refused")

    assert _tcp_reachable("127.0.0.1", 4455, connector=connector) is False


# ------------------------------------------------------- websocket _connect

def _missing_import():
    raise ImportError("No module named 'obswebsocket'")


def test_connect_missing_package_returns_err_dict():
    result = _connect(import_fn=_missing_import)
    assert result["ok"] is False
    assert "obs-websocket-py not installed" in result["error"]


class _FakeGetVersionRequest:
    """Stand-in for an obswebsocket request object."""


class _FakeGetVersionResponse:
    def getObsVersion(self):
        return "30.2.3"

    def getObsWebSocketVersion(self):
        return "5.5.2"


class _FakeOBWS:
    instances: list = []

    def __init__(self, host, port, password):
        self.host, self.port, self.password = host, port, password
        self.connected = False
        self.disconnected = False
        self.calls = []
        _FakeOBWS.instances.append(self)

    def connect(self):
        self.connected = True

    def call(self, request):
        self.calls.append(request)
        return _FakeGetVersionResponse()

    def disconnect(self):
        self.disconnected = True


def _fake_obswebsocket_module():
    module = types.SimpleNamespace()
    module.obsws = _FakeOBWS
    module.requests = types.SimpleNamespace(
        GetVersion=lambda: _FakeGetVersionRequest())
    return module


def test_connect_with_fake_module_returns_versions():
    _FakeOBWS.instances.clear()
    result = _connect("10.0.0.9", 4455, "s3cret",
                      import_fn=_fake_obswebsocket_module)
    assert result["ok"] is True
    assert result["connected"] is True
    assert result["host"] == "10.0.0.9" and result["port"] == 4455
    assert result["obs_version"] == "30.2.3"
    assert result["websocket_version"] == "5.5.2"

    (client,) = _FakeOBWS.instances
    assert (client.host, client.port, client.password) == ("10.0.0.9", 4455, "s3cret")
    assert client.connected is True and client.disconnected is True
    assert len(client.calls) == 1
    assert isinstance(client.calls[0], _FakeGetVersionRequest)


def test_connect_unreachable_obs_returns_err_dict_not_exception():
    class _RefusingOBWS(_FakeOBWS):
        def connect(self):
            raise ConnectionRefusedError("Connection refused")

    module = _fake_obswebsocket_module()
    module.obsws = _RefusingOBWS
    result = _connect(import_fn=lambda: module)
    assert result["ok"] is False
    assert "Could not reach OBS websocket" in result["error"]
    assert "4455" in result["error"]


def test_websocket_tool_never_raises_without_obs():
    # On this machine obswebsocket is absent (ImportError path) or OBS
    # is not running (connection path) — either way a dict comes back.
    result = _registered()["obs_studio_websocket_status"](
        host="127.0.0.1", port=4455)
    assert isinstance(result, dict)
    assert result["ok"] is False and result["error"]
