"""Hermetic tests for the FL Studio adapter (adapters/apps/fl_studio.py).

No real FL Studio is ever probed or launched: a fake MCP captures the
tool registrations, catalog detection is monkeypatched on
``apps_catalog.detect_app``, process spawning goes through a
monkeypatched ``fl_studio._popen`` that only records argv, and window
focusing through a monkeypatched ``fl_studio.focus_window``.
"""
from __future__ import annotations

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import fl_studio
from desktop_mcp.adapters.apps.fl_studio import build_open_argv

FAKE_EXE = "C:/Program Files/Image-Line/FL Studio 21/FL64.exe"


# ------------------------------------------------------------------- fakes

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


def _registered():
    mcp = _FakeMCP()
    fl_studio.register(mcp)
    return mcp.tools


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to tmp_path and isolate the audit log."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


def _detect_installed(app_id, **kwargs):
    assert app_id == "fl-studio"
    return {"ok": True, "id": "fl-studio", "name": "FL Studio",
            "installed": True, "path": FAKE_EXE, "via": "path",
            "strategy": "ui", "prog_id": None}


def _detect_not_installed(app_id, **kwargs):
    assert app_id == "fl-studio"
    return {"ok": True, "id": "fl-studio", "name": "FL Studio",
            "installed": False, "path": None, "via": None,
            "strategy": "ui", "prog_id": None}


def _fake_popen(calls):
    def popen(argv):
        calls.append(list(argv))
        return _FakeProc()
    return popen


# ---------------------------------------------------------------- identity

def test_app_identity_matches_catalog():
    assert fl_studio.APP_ID == "fl-studio"
    assert fl_studio.APP_NAME == "FL Studio"
    app = apps_catalog.get_app("fl-studio")
    assert app["ok"] and app["app"]["strategy"] == "ui"
    assert app["app"]["name"] == fl_studio.APP_NAME
    assert "FL64.exe" in app["app"]["exe_names"]


def test_register_binds_exactly_four_fl_studio_tools():
    tools = _registered()
    assert set(tools) == {"fl_studio_status", "fl_studio_launch",
                          "fl_studio_open_file", "fl_studio_focus"}
    assert len(tools) >= 3


# ------------------------------------------------------------------ builder

def test_build_open_argv_exact():
    assert build_open_argv(FAKE_EXE, "C:/music/song.flp") == [
        FAKE_EXE, "C:/music/song.flp"]
    # Pure and deterministic: stringifies path-like inputs, no side effects.
    from pathlib import Path
    assert build_open_argv(Path("/opt/FL64.exe"), Path("/tmp/a.flp")) == [
        "/opt/FL64.exe", "/tmp/a.flp"]
    assert build_open_argv(FAKE_EXE, "x.flp") == build_open_argv(FAKE_EXE, "x.flp")


def test_popen_delegates_to_subprocess_popen(monkeypatch):
    calls = []

    def fake_popen(argv, close_fds=False):
        calls.append((list(argv), close_fds))
        return _FakeProc()

    monkeypatch.setattr(fl_studio.subprocess, "Popen", fake_popen)
    proc = fl_studio._popen([FAKE_EXE, "x.flp"])
    assert proc.pid == 4242
    assert calls == [([FAKE_EXE, "x.flp"], True)]


# --------------------------------------------------------------- delegation

def test_status_delegates_to_catalog_status(monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "fl-studio", "installed": True}

    def fake_catalog_status(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(fl_studio, "catalog_status", fake_catalog_status)
    tools = _registered()
    assert tools["fl_studio_status"]() is sentinel
    assert calls == ["fl-studio"]


def test_launch_delegates_to_catalog_launch(monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "fl-studio", "launched": True}

    def fake_catalog_launch(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(fl_studio, "catalog_launch", fake_catalog_launch)
    tools = _registered()
    assert tools["fl_studio_launch"]() is sentinel
    assert calls == ["fl-studio"]


def test_focus_delegates_to_focus_window_with_fl_studio_title(monkeypatch):
    calls = []
    sentinel = {"ok": True, "focused": True, "window": "song - FL Studio 21"}

    def fake_focus_window(title_contains):
        calls.append(title_contains)
        return sentinel

    monkeypatch.setattr(fl_studio, "focus_window", fake_focus_window)
    tools = _registered()
    assert tools["fl_studio_focus"]() is sentinel
    assert calls == ["FL Studio"]
    assert fl_studio.WINDOW_TITLE_FRAGMENT == "FL Studio"


def test_tools_pass_through_error_dicts(monkeypatch):
    err = {"ok": False, "error": "boom"}
    monkeypatch.setattr(fl_studio, "catalog_status", lambda app_id: err)
    monkeypatch.setattr(fl_studio, "catalog_launch", lambda app_id: err)
    monkeypatch.setattr(fl_studio, "focus_window", lambda title: err)
    tools = _registered()
    assert tools["fl_studio_status"]() is err
    assert tools["fl_studio_launch"]() is err
    assert tools["fl_studio_focus"]() is err


# --------------------------------------------------------------- open_file

def test_open_file_spawns_detected_exe_with_project(allowed, monkeypatch):
    target = allowed / "song.flp"
    target.write_bytes(b"flp")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(fl_studio, "_popen", _fake_popen(calls))
    tools = _registered()
    res = tools["fl_studio_open_file"](str(target))
    assert res["ok"] is True
    assert res["opened"] is True
    assert res["app_id"] == "fl-studio"
    assert res["exe"] == FAKE_EXE
    assert res["pid"] == 4242
    assert res["path"] == str(target.resolve())
    # argv is exactly what the pure builder produces: [exe, project].
    assert calls == [build_open_argv(FAKE_EXE, str(target.resolve()))]
    assert res["argv"] == calls[0]


def test_open_file_via_module_function(allowed, monkeypatch):
    target = allowed / "beat.flp"
    target.write_bytes(b"flp")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(fl_studio, "_popen", _fake_popen(calls))
    res = fl_studio.open_file(str(target))
    assert res["ok"] is True
    assert calls == [[FAKE_EXE, str(target.resolve())]]


def test_open_file_disallowed_path_rejected_and_never_spawns(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(fl_studio, "_popen", _fake_popen(calls))
    tools = _registered()
    res = tools["fl_studio_open_file"]("/etc/passwd.flp")
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []  # Popen never reached


def test_open_file_missing_exe_returns_err_and_never_spawns(allowed, monkeypatch):
    target = allowed / "song.flp"
    target.write_bytes(b"flp")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    calls = []
    monkeypatch.setattr(fl_studio, "_popen", _fake_popen(calls))
    res = fl_studio.open_file(str(target))
    assert res["ok"] is False
    assert "not installed" in res["error"].lower()
    assert calls == []


def test_open_file_detection_error_is_returned_not_raised(allowed, monkeypatch):
    target = allowed / "song.flp"
    target.write_bytes(b"flp")
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id, **kw: {"ok": False, "error": "boom"})
    monkeypatch.setattr(fl_studio, "_popen",
                        lambda argv: pytest.fail("must not spawn on detection error"))
    res = fl_studio.open_file(str(target))
    assert res == {"ok": False, "error": "boom"}


def test_open_file_popen_failure_returns_err_not_raise(allowed, monkeypatch):
    target = allowed / "song.flp"
    target.write_bytes(b"flp")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)

    def boom(argv):
        raise OSError("cannot start process")

    monkeypatch.setattr(fl_studio, "_popen", boom)
    res = fl_studio.open_file(str(target))
    assert res["ok"] is False
    assert "cannot start process" in res["error"]
