"""Tests for the PyCharm adapter (strategy: ui).

Hermetic: a FakeMCP captures the registered tools, catalog detection is
monkeypatched, and the process spawn is replaced with a recorder — no
PyCharm installation, Windows machine or real process is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common, pycharm

FAKE_EXE = r"C:\Program Files\JetBrains\PyCharm\bin\pycharm64.exe"


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    pycharm.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed_root(monkeypatch, tmp_path):
    """Restrict the path allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


@pytest.fixture()
def spawn_calls(monkeypatch):
    """Replace pycharm._spawn with a recorder; returns the argv list."""
    calls: list[list[str]] = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(pycharm, "_spawn", fake_spawn)
    return calls


def _fake_detect(installed: bool, path: str | None = FAKE_EXE):
    def detect(app_id, **kwargs):
        assert app_id == "pycharm"
        return {
            "ok": True,
            "id": "pycharm",
            "name": "PyCharm",
            "installed": installed,
            "path": path if installed else None,
            "via": "path" if installed else None,
        }

    return detect


# --------------------------------------------------------------------------
# registration / delegation
# --------------------------------------------------------------------------

def test_register_binds_four_tools(tools):
    assert set(tools) == {
        "pycharm_status",
        "pycharm_launch",
        "pycharm_open_project",
        "pycharm_focus",
    }


def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = {}

    def fake_status(app_id):
        seen["app_id"] = app_id
        return {"ok": True, "app_id": app_id, "installed": True}

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    result = tools["pycharm_status"]()
    assert result["ok"] is True
    assert seen["app_id"] == "pycharm"


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = {}

    def fake_launch(app_id):
        seen["app_id"] = app_id
        return {"ok": True, "app_id": app_id, "launched": True}

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    result = tools["pycharm_launch"]()
    assert result["ok"] is True
    assert result["launched"] is True
    assert seen["app_id"] == "pycharm"


def test_focus_delegates_to_common_focus_window(tools, monkeypatch):
    seen = {}

    def fake_focus(title_contains):
        seen["title"] = title_contains
        return {"ok": True, "focused": True, "window": title_contains}

    monkeypatch.setattr(_common, "focus_window", fake_focus)
    result = tools["pycharm_focus"]()
    assert result["ok"] is True
    assert result["focused"] is True
    # Display name comes from the real catalog entry for pycharm.
    assert seen["title"] == "PyCharm"


# --------------------------------------------------------------------------
# pycharm_open_project
# --------------------------------------------------------------------------

def test_open_project_spawns_exe_with_project_path(
        tools, monkeypatch, allowed_root, spawn_calls):
    project = allowed_root / "demo-project"
    project.mkdir()
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))

    result = tools["pycharm_open_project"](str(project))

    assert result["ok"] is True
    assert result["launched"] is True
    assert spawn_calls == [[FAKE_EXE, str(project.resolve())]]
    assert result["argv"] == [FAKE_EXE, str(project.resolve())]
    assert result["path"] == str(project.resolve())
    assert result["pid"] == 4242


def test_open_project_not_installed_returns_err(
        tools, monkeypatch, allowed_root, spawn_calls):
    project = allowed_root / "demo-project"
    project.mkdir()
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(False))

    result = tools["pycharm_open_project"](str(project))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert spawn_calls == []


def test_open_project_disallowed_path_rejected_before_spawn(
        tools, monkeypatch, allowed_root, spawn_calls):
    detect_calls = []

    def detect(app_id, **kwargs):
        detect_calls.append(app_id)
        return _fake_detect(True)(app_id, **kwargs)

    monkeypatch.setattr(apps_catalog, "detect_app", detect)

    result = tools["pycharm_open_project"]("/etc/passwd")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert spawn_calls == []
    assert detect_calls == []  # rejected before detection/spawn


def test_open_project_spawn_failure_returns_err_not_raise(
        tools, monkeypatch, allowed_root):
    project = allowed_root / "demo-project"
    project.mkdir()
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))

    def boom(argv):
        raise OSError("cannot start process")

    monkeypatch.setattr(pycharm, "_spawn", boom)

    result = tools["pycharm_open_project"](str(project))

    assert result["ok"] is False
    assert "cannot start process" in result["error"]


def test_open_project_detect_error_is_returned(
        tools, monkeypatch, allowed_root, spawn_calls):
    project = allowed_root / "demo-project"
    project.mkdir()
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": False, "error": "catalog boom"})

    result = tools["pycharm_open_project"](str(project))

    assert result == {"ok": False, "error": "catalog boom"}
    assert spawn_calls == []
