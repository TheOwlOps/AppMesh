"""Hermetic tests for the Telegram adapter (adapters/apps/telegram.py).

No real app, window, protocol handler or network is touched: tools are
captured with a fake MCP registrar, the OS URI opener ``_open_uri`` is
monkeypatched, and the shared ``_common`` helpers are replaced with
recording fakes for the delegation tests.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import _common, telegram


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
def tools(tmp_path, monkeypatch):
    # Audit records (open_chat audits on success) go to a throwaway home.
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    mcp = _FakeMCP()
    telegram.register(mcp)
    return mcp.tools


@pytest.fixture()
def no_open(monkeypatch):
    """Fail loudly if any code path tries to open a URI for real."""
    def _boom(uri):
        raise AssertionError(f"_open_uri must not be called, got {uri!r}")
    monkeypatch.setattr(telegram, "_open_uri", _boom)


# ---------------------------------------------------------------- register

def test_register_binds_four_tools(tools):
    assert set(tools) == {"telegram_status", "telegram_launch",
                          "telegram_focus", "telegram_open_chat"}


def test_app_id_matches_catalog():
    assert telegram.APP_ID == "telegram"
    from desktop_mcp.apps_catalog import get_app
    result = get_app("telegram")
    assert result["ok"] and result["app"]["name"] == telegram.APP_NAME


# ------------------------------------------------------------ URI builder

def test_build_resolve_uri_exact_output():
    assert telegram.build_resolve_uri("durov") == "tg://resolve?domain=durov"


def test_build_resolve_uri_keeps_allowed_punctuation():
    assert telegram.build_resolve_uri("a.b_c-d9") == \
        "tg://resolve?domain=a.b_c-d9"


# -------------------------------------------------------------- validation

@pytest.mark.parametrize("bad", [
    "",                     # empty
    "a b",                  # space
    "a/b",                  # slash
    "a?x=1",                # query string smuggling
    "https://t.me/x",       # URL-ish input, not a bare username
    "a" * 65,               # one over the 64-char limit
    "a&b",                  # ampersand (extra URI parameter)
    "@durov",               # leading @ is not part of the username
])
def test_open_chat_rejects_invalid_usernames(tools, no_open, bad):
    result = tools["telegram_open_chat"](bad)
    assert result["ok"] is False
    assert result["error"]  # a human-readable reason is always present


def test_open_chat_accepts_max_length_username(tools, monkeypatch):
    opened = []
    monkeypatch.setattr(telegram, "_open_uri", opened.append)
    name = "a" * 64
    result = tools["telegram_open_chat"](name)
    assert result["ok"] is True
    assert opened == [f"tg://resolve?domain={name}"]


# --------------------------------------------------------------- open_chat

def test_open_chat_opens_exact_uri_and_returns_shape(tools, monkeypatch):
    opened = []
    monkeypatch.setattr(telegram, "_open_uri", opened.append)
    result = tools["telegram_open_chat"]("durov")
    assert opened == ["tg://resolve?domain=durov"]
    assert result == {"ok": True, "app_id": "telegram", "username": "durov",
                      "uri": "tg://resolve?domain=durov", "opened": True}


def test_open_chat_opener_failure_is_err_dict_not_raise(tools, monkeypatch):
    def _fail(uri):
        raise RuntimeError("no handler")
    monkeypatch.setattr(telegram, "_open_uri", _fail)
    result = tools["telegram_open_chat"]("durov")
    assert result["ok"] is False and "no handler" in result["error"]


def test_open_uri_falls_back_to_webbrowser_off_windows(monkeypatch):
    # On this (Linux) test machine IS_WINDOWS is False, so _open_uri must
    # dispatch through webbrowser; patch it so nothing real opens.
    seen = []
    monkeypatch.setattr(telegram.webbrowser, "open",
                        lambda uri: seen.append(uri) or True)
    telegram._open_uri("tg://resolve?domain=durov")
    assert seen == ["tg://resolve?domain=durov"]


# -------------------------------------------------------------- delegation

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "telegram", "installed": False}
    monkeypatch.setattr(_common, "catalog_status",
                        lambda app_id: calls.append(app_id) or sentinel)
    assert tools["telegram_status"]() is sentinel
    assert calls == ["telegram"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "telegram", "launched": True}
    monkeypatch.setattr(_common, "catalog_launch",
                        lambda app_id: calls.append(app_id) or sentinel)
    assert tools["telegram_launch"]() is sentinel
    assert calls == ["telegram"]


def test_focus_delegates_to_focus_window_with_display_name(tools, monkeypatch):
    calls = []
    sentinel = {"ok": True, "focused": True, "window": "Telegram Desktop"}
    monkeypatch.setattr(_common, "focus_window",
                        lambda title: calls.append(title) or sentinel)
    assert tools["telegram_focus"]() is sentinel
    assert calls == ["Telegram Desktop"]
