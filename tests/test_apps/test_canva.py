"""Hermetic tests for the Canva adapter (adapters/apps/canva.py).

No real Canva, no real windows, no real processes: detection goes
through a monkeypatched ``apps_catalog.detect_app`` (as referenced
from the canva module), process spawning through the module-level
``canva._spawn`` helper, and status/launch/focus through faked
``_common`` helpers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import _common, canva


class _FakeMCP:
    """Minimal FastMCP stand-in capturing tools by function name."""

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
    canva.register(mcp)
    return mcp.tools


def _allowed_file(name: str = "design.png") -> str:
    """A path under an allowed root (check_path_allowed must pass).

    No file needs to exist: the allowlist check only resolves paths.
    """
    return str(core.ALLOWED_ROOTS[0] / name)


def _fake_detect(installed: bool, exe: str | None = "C:/Apps/Canva/Canva.exe"):
    def _detect(app_id):
        assert app_id == "canva"
        return {"ok": True, "app_id": app_id, "installed": installed,
                "path": exe if installed else None, "via": "path" if installed else None}
    return _detect


# ------------------------------------------------------------------ register

def test_register_binds_at_least_four_tools(tools):
    assert len(tools) >= 4
    assert {"canva_status", "canva_launch", "canva_open_file",
            "canva_focus"} <= set(tools)


# ------------------------------------------------------- status / launch

def test_status_delegates_to_common_catalog_status(tools, monkeypatch):
    seen = []

    def fake_status(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "installed": True, "fake": "status"}

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    result = tools["canva_status"]()
    assert result["ok"] and result["fake"] == "status"
    assert seen == ["canva"]


def test_launch_delegates_to_common_catalog_launch(tools, monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True, "fake": "launch"}

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    result = tools["canva_launch"]()
    assert result["ok"] and result["fake"] == "launch" and result["launched"] is True
    assert seen == ["canva"]


# ---------------------------------------------------------------- open_file

def test_open_file_spawns_exe_with_file_and_returns_ok(tools, monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    monkeypatch.setattr(canva.apps_catalog, "detect_app",
                        _fake_detect(installed=True))
    spawned = []

    def fake_spawn(argv):
        spawned.append(argv)
        return _FakeProc()

    monkeypatch.setattr(canva, "_spawn", fake_spawn)

    target = _allowed_file()
    result = tools["canva_open_file"](target)

    assert spawned == [["C:/Apps/Canva/Canva.exe", target]]
    assert result["ok"] is True
    assert result["opened"] is True and result["launched"] is True
    assert result["exe"] == "C:/Apps/Canva/Canva.exe"
    assert result["path"] == target
    assert result["pid"] == 4321
    # The launch is audited.
    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists() and "canva_open_file" in audit_file.read_text(encoding="utf-8")


def test_open_file_disallowed_path_errors_and_never_spawns(tools, monkeypatch):
    monkeypatch.setattr(canva.apps_catalog, "detect_app",
                        _fake_detect(installed=True))
    monkeypatch.setattr(canva, "_spawn",
                        lambda argv: pytest.fail("must not spawn for a disallowed path"))

    result = tools["canva_open_file"]("/etc/passwd.png")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_open_file_not_installed_errors_and_never_spawns(tools, monkeypatch):
    monkeypatch.setattr(canva.apps_catalog, "detect_app",
                        _fake_detect(installed=False, exe=None))
    monkeypatch.setattr(canva, "_spawn",
                        lambda argv: pytest.fail("must not spawn when Canva is not installed"))

    result = tools["canva_open_file"](_allowed_file())
    assert result["ok"] is False
    assert "not installed" in result["error"].lower()


def test_open_file_detect_failure_is_returned_not_raised(tools, monkeypatch):
    monkeypatch.setattr(canva.apps_catalog, "detect_app",
                        lambda app_id: {"ok": False, "error": "Unknown app id: 'canva'."})
    monkeypatch.setattr(canva, "_spawn",
                        lambda argv: pytest.fail("must not spawn when detection fails"))

    result = tools["canva_open_file"](_allowed_file())
    assert result["ok"] is False and "Unknown app id" in result["error"]


# -------------------------------------------------------------------- focus

def test_focus_passes_through_common_focus_window(tools, monkeypatch):
    seen = []

    def fake_focus(title_contains):
        seen.append(title_contains)
        return {"ok": True, "focused": True, "window": "design - Canva"}

    # Patch focus_window as referenced from the canva module.
    monkeypatch.setattr(canva._common, "focus_window", fake_focus)

    result = tools["canva_focus"]()
    assert result == {"ok": True, "focused": True, "window": "design - Canva"}
    assert seen == ["Canva"]


def test_focus_graceful_error_passthrough(tools, monkeypatch):
    monkeypatch.setattr(canva._common, "focus_window",
                        lambda title: {"ok": False, "error": "not running Windows"})
    result = tools["canva_focus"]()
    assert result["ok"] is False and "not running Windows" in result["error"]
