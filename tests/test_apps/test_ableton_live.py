"""Hermetic tests for the Ableton Live adapter.

No real app is ever launched: detection, the process spawner and
window focusing are all monkeypatched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import ableton_live


class FakeMCP:
    """Minimal stand-in for FastMCP that captures tool registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    ableton_live.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed(tmp_path, monkeypatch):
    """Make *tmp_path* an allowed root for check_path_allowed."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / ".desktop-mcp"))
    return tmp_path


def _fake_detect(installed: bool, exe: str | None):
    def fake_detect(app_id, which_fn=None):
        assert app_id == "ableton-live"
        return core.ok(id=app_id, name="Ableton Live", installed=installed,
                       path=exe, via="path" if exe else None)
    return fake_detect


# ------------------------------------------------------------------ register

def test_register_binds_all_tools(tools):
    assert set(tools) == {
        "ableton_live_status",
        "ableton_live_launch",
        "ableton_live_open_file",
        "ableton_live_focus",
    }


# ------------------------------------------------------------------- status

def test_status_delegates_to_catalog(tools, monkeypatch):
    exe = "C:/Program Files/Ableton/Live/Ableton Live.exe"
    monkeypatch.setattr(apps_catalog, "detect_app",
                        _fake_detect(True, exe))
    result = tools["ableton_live_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "ableton-live"
    assert result["installed"] is True
    assert result["path"] == exe
    # NOTE: _common.catalog_status reads strategy/name off the get_app()
    # result dict, but get_app nests the record under "app" — so those
    # fields currently come back None from the shared helper itself.


def test_launch_delegates_to_catalog(tools, monkeypatch):
    exe = "C:/Program Files/Ableton/Live/Ableton Live.exe"
    monkeypatch.setattr(apps_catalog, "detect_app",
                        _fake_detect(True, exe))
    spawned = {}

    def fake_spawn(path):
        spawned["path"] = path
        return FakeProc(pid=777)

    monkeypatch.setattr(apps_catalog, "_spawn", fake_spawn)
    result = tools["ableton_live_launch"]()
    assert result["ok"] is True
    assert result["launched"] is True
    assert result["pid"] == 777
    assert spawned["path"] == exe


# --------------------------------------------------------------- argv builder

def test_build_open_argv_exact():
    exe = "C:/Program Files/Ableton/Live/Ableton Live.exe"
    als = "C:/Users/anh/Music/My Set.als"
    assert ableton_live.build_open_argv(exe, als) == [exe, als]
    # Coerces path-like values to plain strings.
    assert ableton_live.build_open_argv(Path("live.exe"), Path("a.als")) == [
        "live.exe", "a.als"]


# ----------------------------------------------------------------- open file

def test_open_file_launches_exe_with_set(tools, allowed, monkeypatch):
    als = allowed / "My Set.als"
    als.write_bytes(b"fake-live-set")
    exe = "C:/Program Files/Ableton/Live/Ableton Live.exe"
    monkeypatch.setattr(apps_catalog, "detect_app",
                        _fake_detect(True, exe))
    captured = {}

    def fake_popen(argv):
        captured["argv"] = argv
        return FakeProc(pid=999)

    monkeypatch.setattr(ableton_live, "_popen", fake_popen)
    result = tools["ableton_live_open_file"](str(als))
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["path"] == str(als.resolve())
    assert result["exe"] == exe
    assert result["pid"] == 999
    assert captured["argv"] == [exe, str(als.resolve())]
    assert result["argv"] == captured["argv"]


def test_open_file_rejects_disallowed_path(tools, allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app",
                        _fake_detect(True, "C:/Ableton Live.exe"))
    monkeypatch.setattr(
        ableton_live, "_popen",
        lambda argv: pytest.fail("must not launch for a disallowed path"))
    result = tools["ableton_live_open_file"]("/etc/passwd.als")
    assert result["ok"] is False
    assert "not allowed" in result["error"]


def test_open_file_missing_exe_returns_err(tools, allowed, monkeypatch):
    als = allowed / "song.als"
    als.write_bytes(b"fake-live-set")
    monkeypatch.setattr(apps_catalog, "detect_app",
                        _fake_detect(False, None))
    monkeypatch.setattr(
        ableton_live, "_popen",
        lambda argv: pytest.fail("must not launch when Live is missing"))
    result = tools["ableton_live_open_file"](str(als))
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_open_file_popen_failure_returns_err(tools, allowed, monkeypatch):
    als = allowed / "song.als"
    als.write_bytes(b"fake-live-set")
    monkeypatch.setattr(apps_catalog, "detect_app",
                        _fake_detect(True, "C:/Ableton Live.exe"))

    def boom(argv):
        raise OSError("cannot start process")

    monkeypatch.setattr(ableton_live, "_popen", boom)
    result = tools["ableton_live_open_file"](str(als))
    assert result["ok"] is False
    assert "cannot start process" in result["error"]


# --------------------------------------------------------------------- focus

def test_focus_uses_ableton_title_fragment(tools, monkeypatch):
    seen = {}

    def fake_focus(fragment):
        seen["fragment"] = fragment
        return core.ok(focused=True, window="My Set - Ableton Live 12 Suite")

    monkeypatch.setattr(ableton_live, "focus_window", fake_focus)
    result = tools["ableton_live_focus"]()
    assert seen["fragment"] == "Ableton Live"
    assert result == {"ok": True, "focused": True,
                      "window": "My Set - Ableton Live 12 Suite"}


def test_focus_unpatched_degrades_to_err_on_non_windows(tools):
    if core.IS_WINDOWS:
        pytest.skip("non-Windows degradation only")
    result = tools["ableton_live_focus"]()
    assert result["ok"] is False
    assert result["error"]
