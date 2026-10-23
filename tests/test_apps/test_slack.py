"""Tests for the Slack per-app adapter.

Hermetic: a FakeMCP records registrations, the ``_common`` helpers and
the module-level ``_open_uri`` launcher are monkeypatched, so no real
Slack install, window, protocol handler or network is ever touched.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import _common, slack
from desktop_mcp.adapters.apps.slack import build_protocol_uri, register


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    register(mcp)
    return mcp.tools


# ---------------------------------------------------------------- registration

def test_register_binds_four_tools(tools):
    assert set(tools) == {
        "slack_status",
        "slack_launch",
        "slack_focus",
        "slack_open_via_protocol",
    }


# ------------------------------------------------------------------- builder

def test_build_protocol_uri_no_team():
    assert build_protocol_uri() == "slack://"
    assert build_protocol_uri("") == "slack://"


def test_build_protocol_uri_with_team():
    assert build_protocol_uri("T0123ABCD") == "slack://open?team=T0123ABCD"
    assert build_protocol_uri("my-team_1.x") == "slack://open?team=my-team_1.x"


@pytest.mark.parametrize("bad", [
    "a b",
    "a/b",
    "https://slack.com",
    "a?x=1",
    "a&b",
    "a:b",
    "a#b",
])
def test_build_protocol_uri_rejects_bad_teams(bad):
    with pytest.raises(ValueError):
        build_protocol_uri(bad)


# ------------------------------------------------------- slack_open_via_protocol

def test_open_via_protocol_no_team_opens_plain_uri(tools, monkeypatch):
    seen = []

    def fake_open(uri):
        seen.append(uri)
        return {"ok": True, "opened": True, "uri": uri}

    monkeypatch.setattr(slack, "_open_uri", fake_open)
    result = tools["slack_open_via_protocol"]()
    assert seen == ["slack://"]
    assert result == {"ok": True, "opened": True, "uri": "slack://"}


def test_open_via_protocol_with_team_opens_team_uri(tools, monkeypatch):
    seen = []

    def fake_open(uri):
        seen.append(uri)
        return {"ok": True, "opened": True, "uri": uri}

    monkeypatch.setattr(slack, "_open_uri", fake_open)
    result = tools["slack_open_via_protocol"]("T0123ABCD")
    assert seen == ["slack://open?team=T0123ABCD"]
    assert result["ok"] is True
    assert result["uri"] == "slack://open?team=T0123ABCD"


@pytest.mark.parametrize("bad", ["a b", "a/b", "https://slack.com", "a?x=1"])
def test_open_via_protocol_rejects_bad_team_without_opening(tools, monkeypatch, bad):
    seen = []
    monkeypatch.setattr(slack, "_open_uri", lambda uri: seen.append(uri))
    result = tools["slack_open_via_protocol"](bad)
    assert result["ok"] is False
    assert "error" in result
    assert seen == []  # nothing was handed to the OS


# ----------------------------------------------------------------- delegation

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "slack", "installed": True}
    calls = []

    def fake_status(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    assert tools["slack_status"]() is sentinel
    assert calls == ["slack"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "slack", "launched": True}
    calls = []

    def fake_launch(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    assert tools["slack_launch"]() is sentinel
    assert calls == ["slack"]


def test_focus_delegates_to_focus_window_with_display_name(tools, monkeypatch):
    sentinel = {"ok": True, "focused": True, "window": "Slack"}
    calls = []

    def fake_focus(title_contains):
        calls.append(title_contains)
        return sentinel

    monkeypatch.setattr(_common, "focus_window", fake_focus)
    assert tools["slack_focus"]() is sentinel
    assert calls == ["Slack"]
