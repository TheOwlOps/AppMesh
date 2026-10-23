"""Hermetic tests for the Figma adapter (adapters/apps/figma.py).

No real Figma, no real windows, no real processes: detection goes
through a monkeypatched ``apps_catalog.detect_app`` (as referenced
from the figma module), process spawning through the module-level
``figma._spawn`` helper, and status/launch/focus through faked
``_common`` helpers.
"""
from __future__ import annotations

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import _common, figma

FAKE_EXE = "C:/Users/test/AppData/Local/Figma/Figma.exe"


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
    figma.register(mcp)
    return mcp.tools


def _allowed_file(name: str = "design.fig") -> str:
    """A path under an allowed root (check_path_allowed must pass).

    No file needs to exist: the allowlist check only resolves paths.
    """
    return str(core.ALLOWED_ROOTS[0] / name)


def _fake_detect(installed: bool, exe: str | None = FAKE_EXE):
    def _detect(app_id):
        assert app_id == "figma"
        return {"ok": True, "app_id": app_id, "installed": installed,
                "path": exe if installed else None,
                "via": "path" if installed else None}
    return _detect


# ------------------------------------------------------------------ register

def test_register_binds_at_least_four_tools(tools):
    assert len(tools) >= 4
    assert {"figma_status", "figma_launch", "figma_open_file",
            "figma_focus"} <= set(tools)


# ------------------------------------------------------- status / launch

def test_status_delegates_to_common_catalog_status(tools, monkeypatch):
    seen = []

    def fake_status(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "installed": True, "fake": "status"}

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    result = tools["figma_status"]()
    assert result["ok"] and result["fake"] == "status"
    assert seen == ["figma"]


def test_launch_delegates_to_common_catalog_launch(tools, monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True, "fake": "launch"}

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    result = tools["figma_launch"]()
    assert result["ok"] and result["fake"] == "launch" and result["launched"] is True
    assert seen == ["figma"]


def test_status_real_catalog_shape(tools):
    # Unpatched: real catalog detection runs (finds nothing on this Linux
    # VM, which is still a well-formed ok dict, never an exception).
    result = tools["figma_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "figma"
    assert result["installed"] is False


# ---------------------------------------------------------------- open_file

def test_open_file_spawns_exe_with_file_and_returns_ok(tools, monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    monkeypatch.setattr(figma.apps_catalog, "detect_app",
                        _fake_detect(installed=True))
    spawned = []

    def fake_spawn(argv):
        spawned.append(argv)
        return _FakeProc()

    monkeypatch.setattr(figma, "_spawn", fake_spawn)

    target = _allowed_file()
    result = tools["figma_open_file"](target)

    assert spawned == [[FAKE_EXE, target]]
    assert result["ok"] is True
    assert result["opened"] is True and result["launched"] is True
    assert result["exe"] == FAKE_EXE
    assert result["path"] == target
    assert result["argv"] == [FAKE_EXE, target]
    assert result["pid"] == 4321
    # The launch is audited.
    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists() and "figma_open_file" in audit_file.read_text(encoding="utf-8")


def test_open_file_disallowed_path_errors_and_never_spawns(tools, monkeypatch):
    monkeypatch.setattr(figma.apps_catalog, "detect_app",
                        _fake_detect(installed=True))
    spawned = []
    monkeypatch.setattr(figma, "_spawn", lambda argv: spawned.append(argv))

    result = tools["figma_open_file"]("/etc/passwd.fig")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert spawned == []


def test_open_file_not_installed_errors_and_never_spawns(tools, monkeypatch):
    monkeypatch.setattr(figma.apps_catalog, "detect_app",
                        _fake_detect(installed=False, exe=None))
    spawned = []
    monkeypatch.setattr(figma, "_spawn", lambda argv: spawned.append(argv))

    result = tools["figma_open_file"](_allowed_file())

    assert result["ok"] is False
    assert "not installed" in result["error"].lower()
    assert spawned == []


def test_open_file_detect_failure_is_returned_not_raised(tools, monkeypatch):
    monkeypatch.setattr(figma.apps_catalog, "detect_app",
                        lambda app_id: {"ok": False, "error": "Unknown app id: 'figma'."})
    spawned = []
    monkeypatch.setattr(figma, "_spawn", lambda argv: spawned.append(argv))

    result = tools["figma_open_file"](_allowed_file())
    assert result["ok"] is False and "Unknown app id" in result["error"]
    assert spawned == []


def test_open_file_spawn_failure_returns_err_not_raise(tools, monkeypatch):
    monkeypatch.setattr(figma.apps_catalog, "detect_app",
                        _fake_detect(installed=True))

    def boom(argv):
        raise OSError("cannot start process")

    monkeypatch.setattr(figma, "_spawn", boom)
    result = tools["figma_open_file"](_allowed_file())
    assert result["ok"] is False
    assert "cannot start process" in result["error"]


# -------------------------------------------------------------------- focus

def test_focus_passes_through_common_focus_window(tools, monkeypatch):
    seen = []

    def fake_focus(title_contains):
        seen.append(title_contains)
        return {"ok": True, "focused": True, "window": "Untitled - Figma"}

    # Patch focus_window as referenced from the figma module.
    monkeypatch.setattr(figma._common, "focus_window", fake_focus)

    result = tools["figma_focus"]()
    assert result == {"ok": True, "focused": True, "window": "Untitled - Figma"}
    assert seen == ["Figma"]


def test_focus_graceful_error_passthrough(tools, monkeypatch):
    monkeypatch.setattr(figma._common, "focus_window",
                        lambda title: {"ok": False, "error": "not running Windows"})
    result = tools["figma_focus"]()
    assert result["ok"] is False and "not running Windows" in result["error"]


def test_focus_real_call_never_raises(tools):
    # Unpatched on this Linux VM: graceful err dict, not an exception.
    result = tools["figma_focus"]()
    assert result["ok"] is False
    assert "error" in result
