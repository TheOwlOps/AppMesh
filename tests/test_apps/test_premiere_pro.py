"""Hermetic tests for the Premiere Pro adapter (adapters/apps/premiere_pro.py).

No real Premiere Pro is probed or launched: detection is faked by
monkeypatching ``detect_app`` in the ``premiere_pro`` module namespace
(the adapter's test seam), and process spawning by monkeypatching the
adapter's ``_spawn``. The path allowlist is restricted to each test's
``tmp_path`` via ``core.ALLOWED_ROOTS``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common
from desktop_mcp.adapters.apps import premiere_pro
from desktop_mcp.adapters.apps.premiere_pro import build_open_command

EXE = r"C:\Program Files\Adobe\Adobe Premiere Pro.exe"


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
    pid = 4242


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    premiere_pro.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed_tmp(monkeypatch, tmp_path):
    """Make tmp_path the only allowed root, and keep audit writes there."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    return tmp_path


def _fake_detect(installed=True):
    """A detect_app replacement with the real catalog return shape."""
    record = {
        "ok": True,
        "id": "premiere-pro",
        "name": "Adobe Premiere Pro",
        "category": "Video & Audio",
        "strategy": "plugin",
        "prog_id": None,
        "installed": installed,
        "path": EXE if installed else None,
        "via": "path" if installed else None,
    }

    def fake(app_id, *args, **kwargs):
        assert app_id == "premiere-pro"
        return dict(record)

    return fake


def _patch_spawn(monkeypatch):
    calls = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(premiere_pro, "_spawn", fake_spawn)
    return calls


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools(tools):
    assert len(tools) >= 3
    assert set(tools) == {
        "premiere_pro_status",
        "premiere_pro_launch",
        "premiere_pro_open_project",
    }
    assert premiere_pro.APP_ID == "premiere-pro"
    for fn in tools.values():
        assert callable(fn)


def test_catalog_entry_is_plugin_strategy():
    app = apps_catalog.get_app("premiere-pro")
    assert app["ok"] and app["app"]["strategy"] == "plugin"
    assert app["app"]["exe_names"] == ["Adobe Premiere Pro.exe"]
    assert app["app"]["prog_id"] is None


# ------------------------------------------------------------------ status

def test_status_with_detect_app_monkeypatched_in_premiere_pro_namespace(
        tools, monkeypatch):
    """Patching premiere_pro.detect_app must drive premiere_pro_status."""
    monkeypatch.setattr(premiere_pro, "detect_app", _fake_detect(installed=True))

    result = tools["premiere_pro_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "premiere-pro"
    assert result["installed"] is True
    assert result["path"] == EXE
    assert result["via"] == "path"


def test_status_not_installed_with_monkeypatched_detect_app(tools, monkeypatch):
    monkeypatch.setattr(premiere_pro, "detect_app", _fake_detect(installed=False))

    result = tools["premiere_pro_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "premiere-pro"
    assert result["installed"] is False
    assert result["path"] is None


def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(
        _common, "catalog_status",
        lambda app_id: seen.append(app_id) or {"ok": True, "sentinel": 1})
    result = tools["premiere_pro_status"]()
    assert result == {"ok": True, "sentinel": 1}
    assert seen == ["premiere-pro"]


# ------------------------------------------------------------------ launch

def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(
        _common, "catalog_launch",
        lambda app_id: seen.append(app_id) or {"ok": True, "launched": True})
    result = tools["premiere_pro_launch"]()
    assert result == {"ok": True, "launched": True}
    assert seen == ["premiere-pro"]


# ------------------------------------------------------------- argv builder

def test_build_open_command_is_exact():
    assert build_open_command(EXE, "movie.prproj") == [EXE, "movie.prproj"]
    assert build_open_command("Adobe Premiere Pro.exe", "a b.prproj") == [
        "Adobe Premiere Pro.exe", "a b.prproj"]
    # Path inputs are stringified, nothing else is added.
    assert build_open_command(Path("premiere.exe"), Path("x.prproj")) == [
        "premiere.exe", "x.prproj"]
    assert premiere_pro.build_open_command(EXE, "y.prproj") == [EXE, "y.prproj"]


# ------------------------------------------------------------- open_project

def test_open_project_happy_path(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(premiere_pro, "detect_app", _fake_detect(installed=True))
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "edit.prproj"
    project.write_bytes(b"prproj")

    result = tools["premiere_pro_open_project"](str(project))

    assert result["ok"] is True
    assert result["opened"] is True
    assert result["pid"] == FakeProc.pid
    assert result["exe"] == EXE
    assert result["path"] == str(project.resolve())
    assert result["argv"] == [EXE, str(project.resolve())]
    assert calls == [[EXE, str(project.resolve())]]


def test_open_project_missing_exe_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(premiere_pro, "detect_app", _fake_detect(installed=False))
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "edit.prproj"

    result = tools["premiere_pro_open_project"](str(project))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert calls == []


def test_open_project_detect_failure_returns_err(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(
        premiere_pro, "detect_app",
        lambda app_id, *a, **kw: {"ok": False, "error": "boom"})
    calls = _patch_spawn(monkeypatch)

    result = tools["premiere_pro_open_project"](str(allowed_tmp / "edit.prproj"))

    assert result == {"ok": False, "error": "boom"}
    assert calls == []


def test_open_project_rejects_etc_passwd_prproj(tools, monkeypatch, allowed_tmp):
    """A .prproj outside the allowed roots is rejected before any launch,
    even when Premiere Pro is 'installed'."""
    monkeypatch.setattr(premiere_pro, "detect_app", _fake_detect(installed=True))
    calls = _patch_spawn(monkeypatch)

    result = tools["premiere_pro_open_project"]("/etc/passwd.prproj")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_open_project_rejects_non_prproj(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(premiere_pro, "detect_app", _fake_detect(installed=True))
    calls = _patch_spawn(monkeypatch)

    result = tools["premiere_pro_open_project"](str(allowed_tmp / "notes.txt"))

    assert result["ok"] is False
    assert ".prproj" in result["error"]
    assert calls == []
