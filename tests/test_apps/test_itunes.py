"""Tests for the iTunes COM adapter.

Hermetic: tool registrations are captured with a fake MCP object and
the COM app is injected by monkeypatching the adapter's module-level
``_get_app`` factory seam with a fake app object recording
``PlayPause()`` / ``NextTrack()`` calls — no Windows machine, iTunes
installation or pywin32 is required.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import itunes
from desktop_mcp.core import IS_WINDOWS


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeITunesApp:
    """Fake iTunes COM application object recording playback calls."""

    def __init__(self):
        self.calls: list[str] = []

    def PlayPause(self):
        self.calls.append("PlayPause")

    def NextTrack(self):
        self.calls.append("NextTrack")


class ExplodingITunesApp:
    """Fake whose COM methods raise, like a dead COM connection would."""

    def PlayPause(self):
        raise RuntimeError("COM connection lost")

    def NextTrack(self):
        raise RuntimeError("COM connection lost")


def make_tools(monkeypatch, tmp_path, app_factory=None):
    """Register the adapter on a FakeMCP and (optionally) inject a factory."""
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    if app_factory is not None:
        monkeypatch.setattr(itunes, "_get_app", app_factory)
    mcp = FakeMCP()
    itunes.register(mcp)
    return mcp.tools


# ---------------------------------------------------------------------------
# Registration + catalog tools
# ---------------------------------------------------------------------------


def test_register_binds_at_least_three_tools(monkeypatch, tmp_path):
    tools = make_tools(monkeypatch, tmp_path)
    assert {"itunes_status", "itunes_launch", "itunes_play_pause"} <= set(tools)
    assert "itunes_next_track" in tools


def test_status_comes_from_catalog(monkeypatch, tmp_path):
    tools = make_tools(monkeypatch, tmp_path)
    result = tools["itunes_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "itunes"
    assert isinstance(result["installed"], bool)
    # NOTE: _common.catalog_status currently surfaces strategy/prog_id
    # as None (it reads them off the ok()-wrapper of apps_catalog
    # .get_app instead of the nested record) — outside this adapter's
    # files, so only the reliably-populated fields are asserted here.


# ---------------------------------------------------------------------------
# Playback tools against a fake COM app
# ---------------------------------------------------------------------------


def test_play_pause_calls_fake_exactly_once_and_returns_ok(monkeypatch, tmp_path):
    fake = FakeITunesApp()
    tools = make_tools(monkeypatch, tmp_path, app_factory=lambda: fake)

    result = tools["itunes_play_pause"]()

    assert fake.calls == ["PlayPause"]
    assert result["ok"] is True
    assert result["app_id"] == "itunes"
    assert result["action"] == "play_pause"


def test_next_track_calls_fake_exactly_once_and_returns_ok(monkeypatch, tmp_path):
    fake = FakeITunesApp()
    tools = make_tools(monkeypatch, tmp_path, app_factory=lambda: fake)

    result = tools["itunes_next_track"]()

    assert fake.calls == ["NextTrack"]
    assert result["ok"] is True
    assert result["app_id"] == "itunes"
    assert result["action"] == "next_track"


def test_module_level_helpers_use_the_same_seam(monkeypatch, tmp_path):
    fake = FakeITunesApp()
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    monkeypatch.setattr(itunes, "_get_app", lambda: fake)

    assert itunes.play_pause()["ok"] is True
    assert itunes.next_track()["ok"] is True
    assert fake.calls == ["PlayPause", "NextTrack"]


# ---------------------------------------------------------------------------
# Failure paths: err dicts, never exceptions
# ---------------------------------------------------------------------------


def test_raising_factory_returns_err_dict(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("iTunes.Application uses COM automation, which requires Windows.")

    tools = make_tools(monkeypatch, tmp_path, app_factory=boom)

    for name in ("itunes_play_pause", "itunes_next_track"):
        result = tools[name]()
        assert result["ok"] is False
        assert "requires Windows" in result["error"]


def test_raising_com_method_returns_err_dict(monkeypatch, tmp_path):
    tools = make_tools(monkeypatch, tmp_path, app_factory=lambda: ExplodingITunesApp())

    result = tools["itunes_play_pause"]()
    assert result["ok"] is False
    assert "COM connection lost" in result["error"]

    result = tools["itunes_next_track"]()
    assert result["ok"] is False
    assert "COM connection lost" in result["error"]


def test_missing_com_method_returns_err_dict(monkeypatch, tmp_path):
    tools = make_tools(monkeypatch, tmp_path, app_factory=lambda: object())

    result = tools["itunes_play_pause"]()
    assert result["ok"] is False
    assert "PlayPause" in result["error"]


@pytest.mark.skipif(IS_WINDOWS, reason="real factory only degrades off Windows")
def test_real_factory_off_windows_degrades_to_err(monkeypatch, tmp_path):
    """Without injection, the real ComApp factory must err, not raise,
    on a non-Windows machine (this suite runs on Linux)."""
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    monkeypatch.setattr(itunes, "_com_app", None)
    mcp = FakeMCP()
    itunes.register(mcp)

    result = mcp.tools["itunes_play_pause"]()
    assert result["ok"] is False
    assert "Windows" in result["error"] or "pywin32" in result["error"]
