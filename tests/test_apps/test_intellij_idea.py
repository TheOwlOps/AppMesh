"""Hermetic tests for the IntelliJ IDEA adapter (adapters/apps/intellij_idea.py).

No real IDE is probed or launched: detection is monkeypatched on
``apps_catalog`` and process spawning on the adapter's ``_spawn``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common, intellij_idea as adapter


class FakeMCP:
    """Minimal FastMCP stand-in: captures @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeProc:
    pid = 4321


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    adapter.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed_tmp(monkeypatch, tmp_path):
    """Make tmp_path the only allowed root for check_path_allowed."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


def _patch_detect(monkeypatch, **overrides):
    record = {
        "ok": True,
        "id": "intellij-idea",
        "name": "IntelliJ IDEA",
        "category": "Dev Tools",
        "strategy": "ui",
        "prog_id": None,
        "installed": True,
        "path": r"C:\Program Files\JetBrains\IntelliJ IDEA\bin\idea64.exe",
        "via": "path",
    }
    record.update(overrides)
    monkeypatch.setattr(apps_catalog, "detect_app", lambda app_id: dict(record))
    return record


def _patch_spawn(monkeypatch):
    calls = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(adapter, "_spawn", fake_spawn)
    return calls


# ------------------------------------------------------------------ register

def test_register_binds_all_tools(tools):
    assert set(tools) == {
        "intellij_idea_status",
        "intellij_idea_launch",
        "intellij_idea_open_project",
        "intellij_idea_focus",
    }


# ------------------------------------------------------------------ status / launch

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "catalog_status",
                        lambda app_id: seen.append(app_id) or {"ok": True, "sentinel": 1})
    # The adapter resolves _common.catalog_status at call time.
    result = tools["intellij_idea_status"]()
    assert result == {"ok": True, "sentinel": 1}
    assert seen == ["intellij-idea"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "catalog_launch",
                        lambda app_id: seen.append(app_id) or {"ok": True, "launched": True})
    result = tools["intellij_idea_launch"]()
    assert result == {"ok": True, "launched": True}
    assert seen == ["intellij-idea"]


# ------------------------------------------------------------------ open_project

def test_open_project_spawns_exe_with_project_path(tools, monkeypatch, allowed_tmp):
    det = _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "demo-project"
    project.mkdir()

    result = tools["intellij_idea_open_project"](str(project))

    assert result["ok"] is True
    assert result["opened"] is True
    assert result["pid"] == FakeProc.pid
    assert calls == [[det["path"], str(project.resolve())]]
    assert result["argv"] == [det["path"], str(project.resolve())]
    assert result["path"] == str(project.resolve())


def test_open_project_not_installed_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "demo-project"
    project.mkdir()

    result = tools["intellij_idea_open_project"](str(project))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert calls == []


def test_open_project_disallowed_path_rejected_before_spawn(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["intellij_idea_open_project"]("/etc/passwd")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_open_project_detect_failure_returns_err(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id: {"ok": False, "error": "boom"})
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "demo-project"

    result = tools["intellij_idea_open_project"](str(project))

    assert result == {"ok": False, "error": "boom"}
    assert calls == []


# ------------------------------------------------------------------ focus

def test_focus_delegates_to_common_focus_window_with_catalog_name(
        tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "focus_window",
                        lambda title: seen.append(title) or {"ok": True, "focused": True})
    result = tools["intellij_idea_focus"]()
    assert result == {"ok": True, "focused": True}
    # Display name comes from the catalog entry for intellij-idea.
    expected = apps_catalog.get_app("intellij-idea")["app"]["name"]
    assert expected == "IntelliJ IDEA"
    assert seen == [expected]


def test_tools_never_raise_on_real_focus_off_windows(tools):
    # Real _common.focus_window on this (non-Windows) test box returns an
    # err dict instead of raising.
    result = tools["intellij_idea_focus"]()
    assert isinstance(result, dict)
    assert result["ok"] is False
