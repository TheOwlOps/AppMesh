"""Hermetic tests for the Discord adapter (adapters/apps/discord.py).

No real app, window, protocol handler or network is touched: tools are
captured with a fake MCP registrar, the OS URI opener ``_open_uri`` is
monkeypatched, and the shared ``_common`` helpers are replaced with
recording fakes for the delegation tests.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import _common, discord
from desktop_mcp.adapters.apps.discord import build_protocol_uri, register


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
def tools(tmp_path, monkeypatch):
    # Audit records (open_via_protocol audits on success) go to a
    # throwaway home so the tests stay hermetic.
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    mcp = FakeMCP()
    register(mcp)
    return mcp.tools


@pytest.fixture()
def no_open(monkeypatch):
    """Fail loudly if any code path tries to open a URI for real."""
    def _boom(uri):
        raise AssertionError(f"_open_uri must not be called, got {uri!r}")
    monkeypatch.setattr(discord, "_open_uri", _boom)


# ---------------------------------------------------------------- registration

def test_register_binds_four_tools(tools):
    assert set(tools) == {
        "discord_status",
        "discord_launch",
        "discord_focus",
        "discord_open_via_protocol",
    }


def test_app_id_and_name_match_catalog():
    assert discord.APP_ID == "discord"
    from desktop_mcp.apps_catalog import get_app
    result = get_app("discord")
    assert result["ok"] and result["app"]["name"] == discord.APP_NAME == "Discord"
    assert result["app"]["strategy"] == "ui"


# ------------------------------------------------------------------- builder

def test_build_protocol_uri_empty_target():
    assert build_protocol_uri() == "discord://"
    assert build_protocol_uri("") == "discord://"
    assert build_protocol_uri("   ") == "discord://"


def test_build_protocol_uri_valid_segment():
    assert build_protocol_uri("friends") == "discord://friends"


def test_build_protocol_uri_keeps_allowed_punctuation():
    assert build_protocol_uri("my-target_1.x") == "discord://my-target_1.x"


@pytest.mark.parametrize("bad", [
    "a b",                      # space
    "a/b",                      # slash / multi-segment path
    "https://discord.com/app",  # full URL, not a bare segment
    "a?x=1",                    # query string smuggling
    "a&b",                      # ampersand (extra URI parameter)
    "a:b",                      # colon
    "a#b",                      # fragment
    ".",                        # dot-segment
    "..",                       # dot-dot segment
])
def test_build_protocol_uri_rejects_bad_targets(bad):
    with pytest.raises(ValueError):
        build_protocol_uri(bad)


# ------------------------------------------------------- open_via_protocol

def test_open_via_protocol_no_target_opens_plain_uri(tools, monkeypatch):
    opened = []
    monkeypatch.setattr(discord, "_open_uri", opened.append)
    result = tools["discord_open_via_protocol"]()
    assert opened == ["discord://"]
    assert result == {"ok": True, "app_id": "discord", "name": "Discord",
                      "opened": True, "uri": "discord://", "via": "protocol"}


def test_open_via_protocol_with_target_opens_segment_uri(tools, monkeypatch):
    opened = []
    monkeypatch.setattr(discord, "_open_uri", opened.append)
    result = tools["discord_open_via_protocol"]("friends")
    assert opened == ["discord://friends"]
    assert result["ok"] is True
    assert result["uri"] == "discord://friends"


@pytest.mark.parametrize("bad", [
    "a b", "a/b", "https://discord.com/app", "a?x=1", "a&b",
])
def test_open_via_protocol_rejects_bad_target_without_opening(
        tools, no_open, bad):
    # no_open makes _open_uri raise AssertionError if it is ever called.
    result = tools["discord_open_via_protocol"](bad)
    assert result["ok"] is False
    assert result["error"]  # a human-readable reason is always present


def test_open_via_protocol_opener_failure_is_err_dict_not_raise(
        tools, monkeypatch):
    def _fail(uri):
        raise RuntimeError("no handler")
    monkeypatch.setattr(discord, "_open_uri", _fail)
    result = tools["discord_open_via_protocol"]()
    assert result["ok"] is False and "no handler" in result["error"]


def test_open_uri_falls_back_to_webbrowser_off_windows(monkeypatch):
    # On this (Linux) test machine IS_WINDOWS is False, so _open_uri must
    # dispatch through webbrowser; patch it so nothing real opens.
    if discord.IS_WINDOWS:
        pytest.skip("webbrowser fallback is the non-Windows path")
    seen = []
    monkeypatch.setattr(discord.webbrowser, "open",
                        lambda uri: seen.append(uri) or True)
    discord._open_uri("discord://")
    assert seen == ["discord://"]


# ----------------------------------------------------------------- delegation

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "discord", "installed": True}
    calls = []

    def fake_status(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    assert tools["discord_status"]() is sentinel
    assert calls == ["discord"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "discord", "launched": True}
    calls = []

    def fake_launch(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    assert tools["discord_launch"]() is sentinel
    assert calls == ["discord"]


def test_focus_delegates_to_focus_window_with_display_name(tools, monkeypatch):
    sentinel = {"ok": True, "focused": True, "window": "Discord"}
    calls = []

    def fake_focus(title_contains):
        calls.append(title_contains)
        return sentinel

    monkeypatch.setattr(_common, "focus_window", fake_focus)
    assert tools["discord_focus"]() is sentinel
    assert calls == ["Discord"]
