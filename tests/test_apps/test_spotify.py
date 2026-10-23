"""Hermetic tests for the Spotify adapter (adapters/apps/spotify.py).

No real URI is ever opened and no real app is launched or focused:
``_open_uri`` and ``focus_window`` are monkeypatched in every test that
could reach them, and status/launch delegation is verified through
monkeypatched catalog helpers. Tool registrations are captured with a
fake MCP object.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import spotify


# ---------------------------------------------------------------------------
# Fake MCP server — captures @mcp.tool() registrations
# ---------------------------------------------------------------------------


class FakeMCP:
    """Minimal stand-in for FastMCP: records tools by function name."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    spotify.register(mcp)
    return mcp.tools


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registers_all_expected_tools(tools):
    assert {"spotify_status", "spotify_launch",
            "spotify_open_uri", "spotify_focus"} <= set(tools)
    for fn in tools.values():
        assert callable(fn)


def test_app_id_matches_catalog():
    assert spotify.APP_ID == "spotify"
    from desktop_mcp.apps_catalog import get_app

    result = get_app("spotify")
    assert result["ok"] and result["app"]["strategy"] == "ui"


# ---------------------------------------------------------------------------
# spotify_status / spotify_launch — delegate to _common catalog helpers
# ---------------------------------------------------------------------------


def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = {}

    def fake_status(app_id):
        seen["app_id"] = app_id
        return {"ok": True, "app_id": app_id, "installed": True}

    monkeypatch.setattr(spotify, "catalog_status", fake_status)
    result = tools["spotify_status"]()
    assert result["ok"] is True and result["installed"] is True
    assert seen["app_id"] == "spotify"


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = {}

    def fake_launch(app_id):
        seen["app_id"] = app_id
        return {"ok": True, "app_id": app_id, "launched": True}

    monkeypatch.setattr(spotify, "catalog_launch", fake_launch)
    result = tools["spotify_launch"]()
    assert result["ok"] is True and result["launched"] is True
    assert seen["app_id"] == "spotify"


def test_status_never_raises_on_helper_error(tools, monkeypatch):
    def boom(app_id):
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr(spotify, "catalog_status", boom)
    result = tools["spotify_status"]()
    assert result["ok"] is False and "catalog exploded" in result["error"]


# ---------------------------------------------------------------------------
# spotify_open_uri — valid URIs are opened via the (patched) OS handler
# ---------------------------------------------------------------------------


@pytest.fixture()
def opened(monkeypatch):
    """Replace _open_uri with a recorder; returns the list of URIs seen."""
    calls: list[str] = []
    monkeypatch.setattr(spotify, "_open_uri", calls.append)
    return calls


@pytest.mark.parametrize("uri", [
    "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
    "spotify:album:1DFixLWuPkv3KT3TnV35m3",
    "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
    "spotify:artist:0TnOYISbd1XYRBk9myaseg",
    "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
    "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123",
])
def test_open_uri_accepts_valid_spotify_uris(tools, opened, uri):
    result = tools["spotify_open_uri"](uri)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["uri"] == uri
    assert result["app_id"] == "spotify"
    assert opened == [uri]


def test_open_uri_strips_surrounding_whitespace(tools, opened):
    result = tools["spotify_open_uri"]("  spotify:track:abc  ")
    assert result["ok"] is True
    assert opened == ["spotify:track:abc"]


# ---------------------------------------------------------------------------
# spotify_open_uri — invalid URIs are rejected and _open_uri is NOT called
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uri", [
    "https://evil.example/x",
    "https://evil.example/spotify:track:abc",
    "file:///etc/passwd",
    "file:///C:/Windows/System32/calc.exe",
    "http://open.spotify.com/track/abc",            # http, not https
    "https://open.spotify.com.evil.example/track",  # look-alike host
    "https://open.spotify.com",                     # no trailing path
    "https://example.com/track/abc",
    "spotify",                                      # missing colon
    "spotifyx:track:abc",
    "ms-settings:display",
    "",
    "   ",
])
def test_open_uri_rejects_invalid_uris_without_opening(tools, opened, uri):
    result = tools["spotify_open_uri"](uri)
    assert result["ok"] is False
    assert result["error"]
    assert opened == [], f"_open_uri must not be called for {uri!r}"


@pytest.mark.parametrize("uri", [None, 123, ["spotify:track:abc"]])
def test_open_uri_rejects_non_string_without_opening(tools, opened, uri):
    result = tools["spotify_open_uri"](uri)
    assert result["ok"] is False
    assert opened == []


def test_open_uri_os_failure_returns_err_not_raise(tools, monkeypatch):
    def boom(uri):
        raise RuntimeError("no handler registered")

    monkeypatch.setattr(spotify, "_open_uri", boom)
    result = tools["spotify_open_uri"]("spotify:track:abc")
    assert result["ok"] is False
    assert "no handler registered" in result["error"]


# ---------------------------------------------------------------------------
# spotify_focus — delegates to _common.focus_window("Spotify")
# ---------------------------------------------------------------------------


def test_focus_delegates_with_spotify_title(tools, monkeypatch):
    seen = {}

    def fake_focus(title_contains):
        seen["title"] = title_contains
        return {"ok": True, "focused": True, "window": "Spotify Premium"}

    monkeypatch.setattr(spotify, "focus_window", fake_focus)
    result = tools["spotify_focus"]()
    assert result["ok"] is True and result["focused"] is True
    assert seen["title"] == "Spotify"


def test_focus_error_dict_passes_through(tools, monkeypatch):
    monkeypatch.setattr(
        spotify, "focus_window",
        lambda title: {"ok": False, "error": "No window matching 'Spotify' is currently open."})
    result = tools["spotify_focus"]()
    assert result["ok"] is False
    assert "No window" in result["error"]


def test_focus_never_raises_on_helper_error(tools, monkeypatch):
    def boom(title):
        raise RuntimeError("uia exploded")

    monkeypatch.setattr(spotify, "focus_window", boom)
    result = tools["spotify_focus"]()
    assert result["ok"] is False and "uia exploded" in result["error"]
