"""Hermetic tests for the VLC adapter — no real player is ever launched.

Detection is monkeypatched (``shutil.which`` / the ``_common`` catalog
helpers re-exported into the adapter), process spawning is replaced via
the module-level ``vlc._spawn``, and the version probe uses the
injectable runner accepted by ``_common.run_cli``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import vlc
from desktop_mcp.adapters.apps.vlc import build_play_argv


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir (+ hermetic audit)."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeProc:
    pid = 4242


def _fake_spawn(calls):
    def spawn(argv):
        calls.append(list(argv))
        return _FakeProc()
    return spawn


def _no_vlc(monkeypatch):
    """Pretend VLC is not on PATH at all."""
    monkeypatch.setattr(shutil, "which", lambda name: None)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_all_tools():
    mcp = _FakeMCP()
    names = vlc.register(mcp)
    expected = {"vlc_status", "vlc_launch", "vlc_play_media", "vlc_version"}
    assert set(names) == expected
    assert set(mcp.tools) == expected


def test_registered_status_and_launch_use_catalog_helpers(monkeypatch):
    monkeypatch.setattr(vlc, "catalog_status",
                        lambda app_id: core.ok(app_id=app_id, installed=True))
    monkeypatch.setattr(vlc, "catalog_launch",
                        lambda app_id: core.ok(app_id=app_id, launched=True))
    mcp = _FakeMCP()
    vlc.register(mcp)
    assert mcp.tools["vlc_status"]() == {"ok": True, "app_id": "vlc",
                                         "installed": True}
    assert mcp.tools["vlc_launch"]() == {"ok": True, "app_id": "vlc",
                                         "launched": True}


# ---------------------------------------------------------------------------
# Pure argv builder
# ---------------------------------------------------------------------------

def test_build_play_argv_exact():
    assert build_play_argv("vlc", "/media/movie.mp4") == ["vlc", "/media/movie.mp4"]
    assert build_play_argv("/usr/bin/vlc", "clip.mkv") == ["/usr/bin/vlc", "clip.mkv"]
    assert build_play_argv("C:/Program Files/VideoLAN/VLC/vlc.exe",
                           Path("a b.mp4")) == [
        "C:/Program Files/VideoLAN/VLC/vlc.exe", "a b.mp4"]


def test_build_play_argv_falsy_exe_falls_back_to_vlc():
    assert build_play_argv(None, "x.mp4") == ["vlc", "x.mp4"]
    assert build_play_argv("", "x.mp4") == ["vlc", "x.mp4"]


# ---------------------------------------------------------------------------
# vlc_play_media
# ---------------------------------------------------------------------------

def test_play_media_uses_detected_exe(allowed, monkeypatch):
    media = allowed / "movie.mp4"
    media.write_bytes(b"x")
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/fake/bin/vlc" if name == "vlc" else None)
    calls = []
    monkeypatch.setattr(vlc, "_spawn", _fake_spawn(calls))

    res = vlc.vlc_play_media(str(media))

    expected_argv = ["/fake/bin/vlc", str(media.resolve())]
    assert res["ok"] is True
    assert res["playing"] is True
    assert res["pid"] == 4242
    assert res["argv"] == expected_argv
    assert calls == [expected_argv]


def test_play_media_falls_back_to_bare_vlc(allowed, monkeypatch):
    media = allowed / "song.flac"
    media.write_bytes(b"x")
    _no_vlc(monkeypatch)
    calls = []
    monkeypatch.setattr(vlc, "_spawn", _fake_spawn(calls))

    res = vlc.vlc_play_media(str(media))

    assert res["ok"] is True
    assert calls == [["vlc", str(media.resolve())]]


def test_play_media_rejects_disallowed_path(allowed, monkeypatch):
    calls = []
    monkeypatch.setattr(vlc, "_spawn", _fake_spawn(calls))

    res = vlc.vlc_play_media("/etc/passwd.mp4")

    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []  # nothing was spawned


def test_play_media_missing_exe_returns_err(allowed, monkeypatch):
    media = allowed / "movie.mp4"
    media.write_bytes(b"x")
    _no_vlc(monkeypatch)

    def missing_spawn(argv):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(vlc, "_spawn", missing_spawn)
    res = vlc.vlc_play_media(str(media))
    assert res["ok"] is False
    assert res["error"]


def test_registered_play_media_tool(allowed, monkeypatch):
    media = allowed / "clip.webm"
    media.write_bytes(b"x")
    _no_vlc(monkeypatch)
    calls = []
    monkeypatch.setattr(vlc, "_spawn", _fake_spawn(calls))
    mcp = _FakeMCP()
    vlc.register(mcp)

    res = mcp.tools["vlc_play_media"](str(media))

    assert res["ok"] is True
    assert calls == [["vlc", str(media.resolve())]]


# ---------------------------------------------------------------------------
# vlc_version
# ---------------------------------------------------------------------------

def test_version_with_injected_runner(monkeypatch):
    _no_vlc(monkeypatch)
    seen = []

    def runner(argv, timeout):
        seen.append((list(argv), timeout))
        return 0, "VLC media player 3.0.21 Vetinari (revision 3.0.21-0)\n", ""

    res = vlc.vlc_version(runner=runner)

    assert res["ok"] is True
    assert res["version"].startswith("VLC media player 3.0.21")
    assert seen[0][0] == ["vlc", "--version"]


def test_version_nonzero_exit_returns_err(monkeypatch):
    _no_vlc(monkeypatch)
    res = vlc.vlc_version(runner=lambda argv, timeout: (1, "", "boom"))
    assert res["ok"] is False
    assert "boom" in res["error"]


def test_version_missing_exe_returns_err(monkeypatch):
    _no_vlc(monkeypatch)
    # No runner injected: run_cli's own PATH probe must turn the missing
    # executable into an err dict (and no real process is started).
    res = vlc.vlc_version()
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


def test_registered_version_tool_missing_exe(monkeypatch):
    _no_vlc(monkeypatch)
    mcp = _FakeMCP()
    vlc.register(mcp)
    res = mcp.tools["vlc_version"]()
    assert res["ok"] is False
