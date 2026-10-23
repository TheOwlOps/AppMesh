"""Hermetic tests for the Android Studio adapter (adapters/apps/android_studio.py).

No real app is probed or launched: detection goes through a monkeypatched
``_detect`` (or ``apps_catalog.detect_app``) and process spawning through a
monkeypatched ``_spawn`` that only records argv.
"""
from __future__ import annotations

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import _common, android_studio as mod

EXE = "C:/Program Files/Android/Android Studio/bin/studio64.exe"


class _FakeMCP:
    """Minimal stand-in for FastMCP: captures @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeProc:
    pid = 4321


@pytest.fixture()
def tools():
    mcp = _FakeMCP()
    mod.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed(monkeypatch, tmp_path):
    """Restrict the path allowlist to tmp_path and isolate the audit log."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


def _installed(exe=EXE):
    return {"ok": True, "installed": True, "path": exe,
            "name": "Android Studio", "via": "path"}


def _not_installed():
    return {"ok": True, "installed": False, "path": None,
            "name": "Android Studio", "via": None}


# ------------------------------------------------------------------ register

def test_register_binds_at_least_the_required_tools(tools):
    assert {"android_studio_status", "android_studio_launch",
            "android_studio_open_project", "android_studio_focus"} <= set(tools)


# ------------------------------------------------------- status / launch

def test_status_delegates_to_common_catalog_status(monkeypatch, tools):
    seen = []
    monkeypatch.setattr(_common, "catalog_status",
                        lambda app_id: seen.append(app_id) or {"ok": True, "sentinel": "status"})
    result = tools["android_studio_status"]()
    assert result == {"ok": True, "sentinel": "status"}
    assert seen == ["android-studio"]


def test_launch_delegates_to_common_catalog_launch(monkeypatch, tools):
    seen = []
    monkeypatch.setattr(_common, "catalog_launch",
                        lambda app_id: seen.append(app_id) or {"ok": True, "sentinel": "launch"})
    result = tools["android_studio_launch"]()
    assert result == {"ok": True, "sentinel": "launch"}
    assert seen == ["android-studio"]


def test_status_real_catalog_shape(tools):
    # Unpatched: real catalog detection runs (finds nothing on this Linux
    # VM, which is still a well-formed ok dict, never an exception).
    result = tools["android_studio_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "android-studio"
    # NB: _common.catalog_status surfaces name/strategy as None (it reads
    # them off the get_app wrapper rather than the nested record) — that
    # helper is out of scope here, so only its stable shape is asserted.
    assert "name" in result and "strategy" in result
    assert result["installed"] is False


# ------------------------------------------------------------- open_project

def test_open_project_spawns_exe_with_project_path(monkeypatch, tools, allowed):
    project = allowed / "MyApp"
    project.mkdir()
    spawned = []
    monkeypatch.setattr(mod, "_detect", lambda: _installed())
    monkeypatch.setattr(mod, "_spawn",
                        lambda argv: spawned.append(list(argv)) or _FakeProc())

    result = tools["android_studio_open_project"](str(project))

    assert result["ok"] is True
    assert result["launched"] is True and result["opened"] is True
    assert result["pid"] == 4321
    assert spawned == [[EXE, str(project.resolve())]]
    assert result["argv"] == [EXE, str(project.resolve())]
    assert result["exe"] == EXE
    assert result["path"] == str(project.resolve())


def test_open_project_goes_through_catalog_detection(monkeypatch, tools, allowed):
    """_detect (unpatched) must consult apps_catalog.detect_app('android-studio')."""
    from desktop_mcp import apps_catalog
    project = allowed / "CatalogApp"
    project.mkdir()
    seen = []
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id: seen.append(app_id) or _installed())
    spawned = []
    monkeypatch.setattr(mod, "_spawn",
                        lambda argv: spawned.append(list(argv)) or _FakeProc())

    result = tools["android_studio_open_project"](str(project))

    assert result["ok"] is True
    assert seen == ["android-studio"]
    assert spawned == [[EXE, str(project.resolve())]]


def test_open_project_not_installed_returns_err_and_never_spawns(monkeypatch, tools, allowed):
    project = allowed / "GhostApp"
    project.mkdir()
    monkeypatch.setattr(mod, "_detect", lambda: _not_installed())
    monkeypatch.setattr(mod, "_spawn",
                        lambda argv: pytest.fail("must not spawn when not installed"))

    result = tools["android_studio_open_project"](str(project))

    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_open_project_detection_error_is_returned_not_raised(monkeypatch, tools, allowed):
    project = allowed / "ErrApp"
    project.mkdir()
    monkeypatch.setattr(mod, "_detect", lambda: {"ok": False, "error": "boom"})
    monkeypatch.setattr(mod, "_spawn",
                        lambda argv: pytest.fail("must not spawn on detection error"))

    result = tools["android_studio_open_project"](str(project))

    assert result == {"ok": False, "error": "boom"}


def test_open_project_disallowed_path_rejected_before_spawn(monkeypatch, tools, allowed):
    monkeypatch.setattr(mod, "_detect", lambda: _installed())
    monkeypatch.setattr(mod, "_spawn",
                        lambda argv: pytest.fail("must not spawn for a disallowed path"))

    result = tools["android_studio_open_project"]("/etc/passwd")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_open_project_spawn_failure_returns_err(monkeypatch, tools, allowed):
    project = allowed / "FailApp"
    project.mkdir()
    monkeypatch.setattr(mod, "_detect", lambda: _installed())

    def _boom(argv):
        raise OSError("cannot start process")

    monkeypatch.setattr(mod, "_spawn", _boom)

    result = tools["android_studio_open_project"](str(project))

    assert result["ok"] is False
    assert "cannot start process" in result["error"]


# -------------------------------------------------------------------- focus

def test_focus_delegates_to_common_focus_window(monkeypatch, tools):
    seen = []
    monkeypatch.setattr(_common, "focus_window",
                        lambda title: seen.append(title) or {"ok": True, "focused": True, "window": title})

    result = tools["android_studio_focus"]()

    assert result == {"ok": True, "focused": True, "window": "Android Studio"}
    # Display name comes from the catalog entry for android-studio.
    assert seen == ["Android Studio"]


def test_focus_real_call_never_raises(tools):
    # Unpatched on this Linux VM: graceful err dict, not an exception.
    result = tools["android_studio_focus"]()
    assert result["ok"] is False
    assert "error" in result
