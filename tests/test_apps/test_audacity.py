"""Hermetic tests for the Audacity adapter (adapters/apps/audacity.py).

No real application is ever launched: detection is replaced with
fakes returning a fixed executable, process creation goes through a
monkeypatched ``audacity._spawn`` (or a monkeypatched
``subprocess.Popen`` for the default ``_spawn``), and MCP registration
is captured with a fake mcp object.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import audacity

EXE = "C:/Program Files/Audacity/audacity.exe"
ALLOWED_FILE = str(Path.home() / "take.wav")


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _FakeProc:
    pid = 4321


def _installed(monkeypatch, exe=EXE):
    """Make Audacity detection report *exe* as installed."""
    monkeypatch.setattr(
        audacity, "_detect",
        lambda: {"ok": True, "installed": True, "path": exe, "via": "path"})


def _not_installed(monkeypatch):
    monkeypatch.setattr(
        audacity, "_detect",
        lambda: {"ok": True, "installed": False, "path": None, "via": None})


def _capture_spawn(monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(audacity, "_spawn",
                        lambda argv: spawned.append(list(argv)) or _FakeProc())
    return spawned


# ------------------------------------------------------------------ register

def test_register_binds_three_tools():
    mcp = FakeMCP()
    audacity.register(mcp)
    assert set(mcp.tools) == {"audacity_status", "audacity_launch",
                              "audacity_open_file"}


def test_status_and_launch_delegate_to_catalog(monkeypatch):
    monkeypatch.setattr(audacity, "catalog_status",
                        lambda app_id: {"ok": True, "app_id": app_id,
                                        "installed": True})
    monkeypatch.setattr(audacity, "catalog_launch",
                        lambda app_id: {"ok": True, "app_id": app_id,
                                        "launched": True})
    mcp = FakeMCP()
    audacity.register(mcp)
    status = mcp.tools["audacity_status"]()
    assert status["ok"] is True and status["app_id"] == "audacity"
    launch = mcp.tools["audacity_launch"]()
    assert launch["ok"] is True and launch["app_id"] == "audacity"


def test_status_reports_catalog_strategy(monkeypatch):
    # Real catalog_status over faked detection: strategy is "plugin".
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id, which_fn=None: {
                            "ok": True, "id": app_id, "installed": True,
                            "path": EXE, "via": "path"})
    mcp = FakeMCP()
    audacity.register(mcp)
    result = mcp.tools["audacity_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "audacity"
    assert result["installed"] is True
    assert result["path"] == EXE
    # The catalog record itself carries the "plugin" strategy for Audacity.
    assert apps_catalog.get_app("audacity")["app"]["strategy"] == "plugin"


# ------------------------------------------------------------- argv builder

def test_build_open_argv_exact():
    assert audacity.build_open_argv(EXE, "C:/Users/me/take.wav") == [
        EXE, "C:/Users/me/take.wav"]
    assert audacity.build_open_argv(Path("/opt/audacity"), Path("/home/me/a.aup3")) == [
        "/opt/audacity", "/home/me/a.aup3"]


# ---------------------------------------------------------------- open file

def test_open_file_launches_exe_with_file_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    _installed(monkeypatch)
    spawned = _capture_spawn(monkeypatch)

    result = audacity.open_file(ALLOWED_FILE)

    expected_path = str(Path(ALLOWED_FILE).resolve())
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["app_id"] == "audacity"
    assert result["path"] == expected_path
    assert result["exe"] == EXE
    assert result["argv"] == [EXE, expected_path]
    assert result["pid"] == 4321
    assert spawned == [[EXE, expected_path]]


def test_open_file_via_registered_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    _installed(monkeypatch)
    spawned = _capture_spawn(monkeypatch)
    mcp = FakeMCP()
    audacity.register(mcp)
    result = mcp.tools["audacity_open_file"](ALLOWED_FILE)
    assert result["ok"] is True
    assert spawned == [[EXE, str(Path(ALLOWED_FILE).resolve())]]


def test_open_file_under_tmp_allowed_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    song = tmp_path / "song.flac"
    song.write_bytes(b"fake audio")
    _installed(monkeypatch)
    spawned = _capture_spawn(monkeypatch)

    result = audacity.open_file(str(song))
    assert result["ok"] is True
    assert spawned == [[EXE, str(song.resolve())]]


def test_open_file_rejects_disallowed_path(monkeypatch):
    _installed(monkeypatch)
    spawned = _capture_spawn(monkeypatch)

    result = audacity.open_file("/etc/passwd.wav")

    assert result["ok"] is False
    assert "not allowed" in result["error"]
    # Nothing may be launched for a rejected path.
    assert spawned == []


def test_open_file_missing_exe_returns_err(monkeypatch):
    _not_installed(monkeypatch)
    spawned = _capture_spawn(monkeypatch)

    result = audacity.open_file(ALLOWED_FILE)

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert spawned == []


def test_open_file_detection_error_is_returned(monkeypatch):
    monkeypatch.setattr(audacity, "_detect",
                        lambda: {"ok": False, "error": "Unknown app id: 'audacity'"})
    _capture_spawn(monkeypatch)
    result = audacity.open_file(ALLOWED_FILE)
    assert result["ok"] is False
    assert "Unknown app id" in result["error"]


def test_open_file_empty_path_returns_err(monkeypatch):
    _installed(monkeypatch)
    spawned = _capture_spawn(monkeypatch)
    result = audacity.open_file("")
    assert result["ok"] is False
    assert spawned == []


def test_open_file_spawn_failure_returns_err_not_raise(monkeypatch):
    _installed(monkeypatch)

    def boom(argv):
        raise OSError("cannot fork")

    monkeypatch.setattr(audacity, "_spawn", boom)
    result = audacity.open_file(ALLOWED_FILE)
    assert result["ok"] is False
    assert "cannot fork" in result["error"]


# ------------------------------------------------------------- default spawn

def test_default_spawn_uses_subprocess_popen(monkeypatch):
    calls = []

    class FakePopen:
        pid = 99

        def __init__(self, argv, **kwargs):
            calls.append((list(argv), kwargs))

    monkeypatch.setattr(audacity.subprocess, "Popen", FakePopen)
    proc = audacity._spawn([EXE, "x.wav"])
    assert proc.pid == 99
    assert calls == [([EXE, "x.wav"], {"close_fds": True})]


def test_detection_goes_through_catalog(monkeypatch):
    seen = []
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id, which_fn=None: seen.append(app_id) or {
                            "ok": True, "installed": False, "path": None})
    result = audacity._detect()
    assert seen == ["audacity"]
    assert result["ok"] is True
