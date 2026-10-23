"""Hermetic tests for the Filmora adapter (adapters/apps/filmora.py).

Filmora's catalog strategy is "ui" (no scripting API), so the adapter
only does detection / launch / open-file / window-focus. No real
Filmora installation is touched: detection is faked by monkeypatching
``detect_app`` in the filmora module namespace, and process spawns go
through a monkeypatched ``filmora._spawn``.
"""
from __future__ import annotations

from pathlib import Path

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import filmora
from desktop_mcp.adapters.apps.filmora import APP_ID, build_open_command, register

FAKE_EXE = "C:/Program Files/Wondershare/Filmora/Filmora.exe"
ALLOWED_PROJECT = str(Path.home() / "my-film.wfp")


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


def _tools() -> dict:
    mcp = FakeMCP()
    register(mcp)
    return mcp.tools


def _fake_detect_installed(app_id, **_kwargs):
    assert app_id == APP_ID
    return {"ok": True, "id": APP_ID, "name": "Filmora",
            "category": "Video & Audio", "strategy": "ui", "prog_id": None,
            "installed": True, "path": FAKE_EXE, "via": "path"}


def _fake_detect_missing(app_id, **_kwargs):
    assert app_id == APP_ID
    return {"ok": True, "id": APP_ID, "name": "Filmora",
            "category": "Video & Audio", "strategy": "ui", "prog_id": None,
            "installed": False, "path": None, "via": None}


# ------------------------------------------------------------------ catalog

def test_app_id_and_catalog_entry():
    assert APP_ID == "filmora"
    assert filmora.APP_ID == "filmora"
    entry = apps_catalog.get_app("filmora")
    assert entry["ok"] and entry["app"]["name"] == "Filmora"
    assert entry["app"]["strategy"] == "ui"
    assert entry["app"]["prog_id"] is None
    assert entry["app"]["exe_names"] == ["Filmora.exe"]


# -------------------------------------------------------------- registration

def test_register_binds_at_least_the_four_filmora_tools():
    tools = _tools()
    assert len(tools) >= 3
    assert {"filmora_status", "filmora_launch",
            "filmora_open_file", "filmora_focus"} <= set(tools)


# ---------------------------------------------------------- build_open_command

def test_build_open_command_exact():
    assert build_open_command(FAKE_EXE, ALLOWED_PROJECT) == [FAKE_EXE, ALLOWED_PROJECT]
    # Path-like inputs are coerced to plain strings, order preserved.
    assert build_open_command(Path("C:/x/Filmora.exe"), Path("C:/y/a.wfp")) == [
        "C:/x/Filmora.exe", "C:/y/a.wfp"]


# -------------------------------------------------------------------- status

def test_status_with_monkeypatched_detect_app_installed(monkeypatch):
    # Patch detect_app in the filmora module namespace (the documented
    # monkeypatch point); filmora_status must honour the fake.
    monkeypatch.setattr(filmora, "detect_app", _fake_detect_installed)
    result = _tools()["filmora_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "filmora"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["strategy"] == "ui"
    assert result["name"] == "Filmora"


def test_status_with_monkeypatched_detect_app_not_installed(monkeypatch):
    monkeypatch.setattr(filmora, "detect_app", _fake_detect_missing)
    result = _tools()["filmora_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


def test_status_real_detection_returns_dict():
    # Unpatched: on this machine Filmora.exe is not on PATH, so the
    # catalog reports not-installed — but always as a dict, never a raise.
    result = _tools()["filmora_status"]()
    assert isinstance(result, dict) and result["ok"] is True
    assert result["app_id"] == "filmora"
    assert isinstance(result["installed"], bool)


# -------------------------------------------------------------------- launch

def test_launch_delegates_to_catalog_launch(monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True, "pid": 99}

    monkeypatch.setattr(filmora, "catalog_launch", fake_launch)
    result = _tools()["filmora_launch"]()
    assert result["ok"] is True and result["launched"] is True
    assert seen == ["filmora"]


def test_launch_not_installed_returns_err_dict():
    result = _tools()["filmora_launch"]()
    assert result["ok"] is False
    assert "not installed" in result["error"]


# ----------------------------------------------------------------- open_file

def test_open_file_missing_exe_returns_clear_err(monkeypatch):
    monkeypatch.setattr(filmora, "detect_app", _fake_detect_missing)
    spawned = []
    monkeypatch.setattr(filmora, "_spawn", lambda argv: spawned.append(argv))
    result = _tools()["filmora_open_file"](ALLOWED_PROJECT)
    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert "Filmora" in result["error"]
    assert spawned == []  # nothing launched for a missing app


def test_open_file_success_spawns_exe_with_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    monkeypatch.setattr(filmora, "detect_app", _fake_detect_installed)
    spawned = []

    def fake_spawn(argv):
        spawned.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(filmora, "_spawn", fake_spawn)
    result = _tools()["filmora_open_file"](ALLOWED_PROJECT)
    expected_path = str(Path(ALLOWED_PROJECT).resolve())
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["pid"] == 4321
    assert result["exe"] == FAKE_EXE
    assert result["path"] == expected_path
    assert spawned == [[FAKE_EXE, expected_path]]
    assert result["argv"] == [FAKE_EXE, expected_path]


def test_open_file_rejects_paths_outside_allowlist(monkeypatch):
    # Detection would succeed and spawn would record — neither may be
    # reached for a disallowed path: the allowlist check comes first.
    monkeypatch.setattr(filmora, "detect_app", _fake_detect_installed)
    spawned = []
    monkeypatch.setattr(filmora, "_spawn", lambda argv: spawned.append(argv))
    for bad in ("/etc/passwd.wfp", "/etc/clip.mp4"):
        result = _tools()["filmora_open_file"](bad)
        assert result["ok"] is False, bad
        assert "not allowed" in result["error"], bad
    assert spawned == []


def test_open_file_never_raises_on_bad_input():
    result = _tools()["filmora_open_file"](None)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["error"]


# --------------------------------------------------------------------- focus

def test_focus_delegates_with_catalog_display_name(monkeypatch):
    seen = []

    def fake_focus(title_contains):
        seen.append(title_contains)
        return {"ok": True, "focused": True, "window": "Filmora — my-film"}

    monkeypatch.setattr(filmora, "focus_window", fake_focus)
    result = _tools()["filmora_focus"]()
    assert result["ok"] is True and result["focused"] is True
    assert seen == ["Filmora"]


def test_focus_real_call_returns_dict_never_raises():
    # Off Windows this is a clear error dict; on Windows without an
    # open Filmora window likewise. Either way: a dict, never a raise.
    result = _tools()["filmora_focus"]()
    assert isinstance(result, dict)
    assert "ok" in result
