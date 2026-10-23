"""Hermetic tests for the PotPlayer adapter (adapters/apps/potplayer.py).

No real PotPlayer is ever detected or launched: detection goes through
a monkeypatched ``apps_catalog.detect_app`` and process start through a
monkeypatched module-level ``_spawn``. Builders are asserted argv-for-
argv, disallowed paths are rejected before anything spawns, and a
missing executable yields an ``err`` dict instead of a raise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import potplayer
from desktop_mcp.adapters.apps.potplayer import (
    build_play_argv,
    potplayer_play_media,
)

FAKE_EXE = "C:/Program Files/PotPlayer/PotPlayerMini64.exe"


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir + isolate audit."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


def _detect_installed(app_id, which_fn=None):
    assert app_id == "potplayer"
    return {"ok": True, "id": "potplayer", "name": "PotPlayer",
            "installed": True, "path": FAKE_EXE, "via": "path",
            "strategy": "cli", "prog_id": None}


def _detect_missing(app_id, which_fn=None):
    assert app_id == "potplayer"
    return {"ok": True, "id": "potplayer", "name": "PotPlayer",
            "installed": False, "path": None, "via": None,
            "strategy": "cli", "prog_id": None}


class _FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid


def _patch_spawn(monkeypatch, calls, pid=4321):
    def fake_spawn(argv):
        calls.append(list(argv))
        return _FakeProc(pid)
    monkeypatch.setattr(potplayer, "_spawn", fake_spawn)


def _forbid_spawn(monkeypatch):
    def boom(argv):
        raise AssertionError(f"must not spawn, got {argv!r}")
    monkeypatch.setattr(potplayer, "_spawn", boom)


# ---------------------------------------------------------------------------
# Builder — exact argv
# ---------------------------------------------------------------------------

def test_build_play_argv_exact():
    assert build_play_argv(FAKE_EXE, "D:/media/movie.mkv") == [
        FAKE_EXE, "D:/media/movie.mkv",
    ]
    assert build_play_argv("PotPlayer.exe", "song.mp3") == [
        "PotPlayer.exe", "song.mp3",
    ]
    # Path-like inputs are stringified.
    assert build_play_argv(Path("C:/pp/PotPlayer.exe"), Path("a b.mkv")) == [
        "C:/pp/PotPlayer.exe", "a b.mkv",
    ]


def test_spawn_delegates_to_subprocess_popen(monkeypatch):
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return _FakeProc(99)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    proc = potplayer._spawn([FAKE_EXE, "x.mkv"])
    assert proc.pid == 99
    assert calls == [([FAKE_EXE, "x.mkv"], {"close_fds": True})]


# ---------------------------------------------------------------------------
# play_media — happy path, via the module-level function
# ---------------------------------------------------------------------------

def test_play_media_happy_path(allowed, monkeypatch):
    media = allowed / "movie.mkv"
    media.write_bytes(b"x")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    _patch_spawn(monkeypatch, calls)

    res = potplayer_play_media(str(media))

    assert res["ok"] is True
    assert res["app_id"] == "potplayer"
    assert res["played"] is True and res["launched"] is True
    assert res["exe"] == FAKE_EXE
    assert res["path"] == str(media.resolve())
    assert res["media"] == str(media.resolve())
    assert res["pid"] == 4321
    assert res["argv"] == [FAKE_EXE, str(media.resolve())]
    assert calls == [[FAKE_EXE, str(media.resolve())]]


# ---------------------------------------------------------------------------
# play_media — error paths (err dicts, nothing spawns, never raises)
# ---------------------------------------------------------------------------

def test_play_media_disallowed_path_rejected(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    _forbid_spawn(monkeypatch)

    res = potplayer_play_media("/etc/passwd.mkv")

    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()


def test_play_media_missing_exe_returns_err(allowed, monkeypatch):
    media = allowed / "movie.mkv"
    media.write_bytes(b"x")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_missing)
    _forbid_spawn(monkeypatch)

    res = potplayer_play_media(str(media))

    assert res["ok"] is False
    assert "not installed" in res["error"].lower()


def test_play_media_detection_error_passthrough(allowed, monkeypatch):
    media = allowed / "movie.mkv"
    media.write_bytes(b"x")
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, which_fn=None: {"ok": False, "error": "boom"})
    _forbid_spawn(monkeypatch)

    res = potplayer_play_media(str(media))

    assert res == {"ok": False, "error": "boom"}


def test_play_media_spawn_failure_returns_err_not_raise(allowed, monkeypatch):
    media = allowed / "movie.mkv"
    media.write_bytes(b"x")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)

    def bad_spawn(argv):
        raise OSError("cannot start")

    monkeypatch.setattr(potplayer, "_spawn", bad_spawn)

    res = potplayer_play_media(str(media))

    assert res["ok"] is False
    assert "cannot start" in res["error"]


# ---------------------------------------------------------------------------
# Registration — fake MCP captures the tools
# ---------------------------------------------------------------------------

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_binds_three_tools():
    mcp = _FakeMCP()
    names = potplayer.register(mcp)
    assert set(names) == {"potplayer_status", "potplayer_launch",
                          "potplayer_play_media"}
    assert set(mcp.tools) == {"potplayer_status", "potplayer_launch",
                              "potplayer_play_media"}
    for fn in mcp.tools.values():
        assert fn.__doc__  # LLM-facing docstring


def test_status_and_launch_tools_delegate_to_catalog(monkeypatch):
    seen = []
    monkeypatch.setattr(
        potplayer, "catalog_status",
        lambda app_id: seen.append(("status", app_id)) or
        {"ok": True, "app_id": app_id, "installed": True})
    monkeypatch.setattr(
        potplayer, "catalog_launch",
        lambda app_id: seen.append(("launch", app_id)) or
        {"ok": True, "app_id": app_id, "launched": True})

    mcp = _FakeMCP()
    potplayer.register(mcp)

    status = mcp.tools["potplayer_status"]()
    assert status["ok"] is True and status["installed"] is True
    launch = mcp.tools["potplayer_launch"]()
    assert launch["ok"] is True and launch["launched"] is True
    assert seen == [("status", "potplayer"), ("launch", "potplayer")]


def test_registered_play_tool_end_to_end(allowed, monkeypatch):
    media = allowed / "clip.mp4"
    media.write_bytes(b"x")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    _patch_spawn(monkeypatch, calls, pid=777)

    mcp = _FakeMCP()
    potplayer.register(mcp)
    res = mcp.tools["potplayer_play_media"](str(media))

    assert res["ok"] is True and res["pid"] == 777
    assert calls == [build_play_argv(FAKE_EXE, str(media.resolve()))]

    missing = mcp.tools["potplayer_play_media"]("/etc/passwd.mkv")
    assert missing["ok"] is False
    assert "not allowed" in missing["error"].lower()
    assert len(calls) == 1  # the rejected call spawned nothing
