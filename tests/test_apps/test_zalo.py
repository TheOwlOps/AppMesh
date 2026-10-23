"""Hermetic tests for the Zalo adapter (adapters/apps/zalo.py).

No real windows or UI: a fake MCP captures registrations and the
``_common`` helpers — imported into the zalo module — are monkeypatched
to record how the tools delegate and to return sentinel dicts.
"""
from __future__ import annotations

from desktop_mcp.adapters.apps import zalo


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
    zalo.register(mcp)
    return mcp.tools


# ------------------------------------------------------------------ register

def test_register_binds_exactly_three_zalo_tools():
    tools = _registered()
    assert set(tools) == {"zalo_status", "zalo_launch", "zalo_focus"}


def test_app_identity_matches_catalog():
    assert zalo.APP_ID == "zalo"
    assert zalo.APP_NAME == "Zalo"


# ---------------------------------------------------------------- delegation

def test_zalo_status_delegates_to_catalog_status(monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "zalo", "installed": True}

    def fake_catalog_status(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(zalo, "catalog_status", fake_catalog_status)
    tools = _registered()
    assert tools["zalo_status"]() is sentinel
    assert calls == ["zalo"]


def test_zalo_launch_delegates_to_catalog_launch(monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "zalo", "launched": True}

    def fake_catalog_launch(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(zalo, "catalog_launch", fake_catalog_launch)
    tools = _registered()
    assert tools["zalo_launch"]() is sentinel
    assert calls == ["zalo"]


def test_zalo_focus_delegates_to_focus_window_with_zalo_title(monkeypatch):
    calls = []
    sentinel = {"ok": True, "focused": True, "window": "Zalo"}

    def fake_focus_window(title_contains):
        calls.append(title_contains)
        return sentinel

    monkeypatch.setattr(zalo, "focus_window", fake_focus_window)
    tools = _registered()
    assert tools["zalo_focus"]() is sentinel
    assert calls == ["Zalo"]


def test_tools_pass_through_error_dicts(monkeypatch):
    err = {"ok": False, "error": "boom"}
    monkeypatch.setattr(zalo, "catalog_status", lambda app_id: err)
    monkeypatch.setattr(zalo, "catalog_launch", lambda app_id: err)
    monkeypatch.setattr(zalo, "focus_window", lambda title_contains: err)
    tools = _registered()
    assert tools["zalo_status"]() is err
    assert tools["zalo_launch"]() is err
    assert tools["zalo_focus"]() is err
