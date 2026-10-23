"""Hermetic tests for the Microsoft Teams adapter (adapters/apps/teams.py).

No real app is launched, focused or probed: registration goes through a
fake MCP, the protocol launch through a monkeypatched ``_open_uri``,
and the status/launch/focus tools through monkeypatched ``_common``
functions.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import teams


class _FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture()
def tools():
    mcp = _FakeMCP()
    teams.register(mcp)
    return mcp.tools


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    """Keep the protocol tool from writing a real audit log."""
    monkeypatch.setattr(teams, "audit", lambda *a, **k: None)


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools(tools):
    assert set(tools) == {"teams_status", "teams_launch", "teams_focus",
                          "teams_open_via_protocol"}


def test_app_id_matches_catalog():
    assert teams.APP_ID == "teams"
    assert teams.APP_NAME == "Microsoft Teams"


# ------------------------------------------------------------- pure builder

def test_builder_empty_target_is_exact_plain_uri():
    assert teams.build_protocol_uri() == "msteams://"
    assert teams.build_protocol_uri("") == "msteams://"
    assert teams.build_protocol_uri("   ") == "msteams://"
    assert teams.build_protocol_uri(None) == "msteams://"


@pytest.mark.parametrize("bad", [
    "a b",                          # space
    "a/b",                          # slash
    "https://teams.microsoft.com",  # full URL
    "a?b",                          # query marker
    "a&b",                          # query separator
    "a:b",                          # colon
    "a\\b",                         # backslash
    "team id!",                     # punctuation + space
])
def test_builder_rejects_invalid_targets(bad):
    with pytest.raises(ValueError):
        teams.build_protocol_uri(bad)


def test_builder_rejects_even_charset_valid_bare_token():
    # Honest limitation, documented in build_protocol_uri's docstring:
    # no documented msteams:// form takes a single bare token.
    with pytest.raises(ValueError, match="no documented"):
        teams.build_protocol_uri("general-channel_1.0")


# ------------------------------------------------------- protocol tool (fake)

def test_open_via_protocol_opens_exact_uri(tools, monkeypatch):
    opened = []
    monkeypatch.setattr(teams, "_open_uri", opened.append)
    result = tools["teams_open_via_protocol"]()
    assert result["ok"] is True and result["opened"] is True
    assert result["uri"] == "msteams://"
    assert opened == ["msteams://"]


def test_open_via_protocol_empty_string_target(tools, monkeypatch):
    opened = []
    monkeypatch.setattr(teams, "_open_uri", opened.append)
    result = tools["teams_open_via_protocol"]("")
    assert result["ok"] is True and opened == ["msteams://"]


@pytest.mark.parametrize("bad", ["a b", "a/b", "https://teams.microsoft.com"])
def test_open_via_protocol_rejects_without_opening(tools, monkeypatch, bad):
    opened = []
    monkeypatch.setattr(teams, "_open_uri", opened.append)
    result = tools["teams_open_via_protocol"](bad)
    assert result["ok"] is False and result["error"]
    assert opened == []  # never launched for a rejected target


def test_open_via_protocol_swallows_opener_errors(tools, monkeypatch):
    def _boom(uri):
        raise RuntimeError("shell exploded")

    monkeypatch.setattr(teams, "_open_uri", _boom)
    result = tools["teams_open_via_protocol"]()
    assert result["ok"] is False and "shell exploded" in result["error"]


# ------------------------------------------------------------- delegation

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = []

    def fake_status(app_id):
        seen.append(app_id)
        return {"ok": True, "sentinel": app_id}

    monkeypatch.setattr(teams._common, "catalog_status", fake_status)
    assert tools["teams_status"]() == {"ok": True, "sentinel": "teams"}
    assert seen == ["teams"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "launched": True, "sentinel": app_id}

    monkeypatch.setattr(teams._common, "catalog_launch", fake_launch)
    result = tools["teams_launch"]()
    assert result["ok"] is True and result["sentinel"] == "teams"
    assert seen == ["teams"]


def test_focus_delegates_with_display_name(tools, monkeypatch):
    seen = []

    def fake_focus(title_contains):
        seen.append(title_contains)
        return {"ok": True, "focused": True, "window": title_contains}

    monkeypatch.setattr(teams._common, "focus_window", fake_focus)
    result = tools["teams_focus"]()
    assert result["ok"] is True and result["focused"] is True
    assert seen == ["Microsoft Teams"]


def test_tools_never_raise_when_common_layer_raises(tools, monkeypatch):
    def _boom(*args):
        raise RuntimeError("common exploded")

    monkeypatch.setattr(teams._common, "catalog_status", _boom)
    monkeypatch.setattr(teams._common, "catalog_launch", _boom)
    monkeypatch.setattr(teams._common, "focus_window", _boom)
    for name in ("teams_status", "teams_launch", "teams_focus"):
        result = tools[name]()
        assert result["ok"] is False and "common exploded" in result["error"]
