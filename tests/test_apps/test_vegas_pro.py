"""Hermetic tests for the VEGAS Pro adapter (adapters/apps/vegas_pro.py).

No real VEGAS Pro is probed or launched: detection is monkeypatched via
``detect_app`` in the vegas_pro module namespace, and process spawning
via the adapter's ``_spawn`` helper.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common
from desktop_mcp.adapters.apps import vegas_pro as vp

EXE = r"C:\Program Files\VEGAS\VEGAS Pro 21.0\vegas.exe"


class FakeMCP:
    """Minimal FastMCP stand-in: captures tool registrations."""

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
    vp.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed_tmp(monkeypatch, tmp_path):
    """Make tmp_path the only allowed root, and keep audit writes there."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    return tmp_path


def _patch_detect(monkeypatch, **overrides):
    """Monkeypatch detect_app in the vegas_pro module namespace."""
    record = {
        "ok": True,
        "id": "vegas-pro",
        "name": "VEGAS Pro",
        "category": "Video & Audio",
        "strategy": "plugin",
        "prog_id": None,
        "installed": True,
        "path": EXE,
        "via": "path",
    }
    record.update(overrides)
    monkeypatch.setattr(vp, "detect_app", lambda app_id: dict(record))
    return record


def _patch_spawn(monkeypatch):
    calls = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(vp, "_spawn", fake_spawn)
    return calls


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools(tools):
    assert len(tools) >= 3
    assert set(tools) == {
        "vegas_pro_status",
        "vegas_pro_launch",
        "vegas_pro_run_script",
        "vegas_pro_open_project",
    }


def test_app_id_and_catalog_entry():
    assert vp.APP_ID == "vegas-pro"
    app = apps_catalog.get_app("vegas-pro")
    assert app["ok"] and app["app"]["strategy"] == "plugin"
    assert app["app"]["prog_id"] is None
    assert app["app"]["exe_names"] == ["VEGAS Pro.exe", "vegas.exe"]


# ------------------------------------------------------------------ status / launch

def test_status_with_detect_app_patched_in_module_namespace(tools, monkeypatch):
    _patch_detect(monkeypatch)

    result = tools["vegas_pro_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "vegas-pro"
    assert result["installed"] is True
    assert result["path"] == EXE
    assert result["via"] == "path"
    assert result["strategy"] == "plugin"
    assert result["prog_id"] is None
    assert result["name"] == "VEGAS Pro"


def test_status_not_installed_with_patched_detect(tools, monkeypatch):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)

    result = tools["vegas_pro_status"]()

    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(
        _common, "catalog_status",
        lambda app_id: seen.append(app_id) or {"ok": True, "sentinel": 1})
    result = tools["vegas_pro_status"]()
    assert result == {"ok": True, "sentinel": 1}
    assert seen == ["vegas-pro"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(
        _common, "catalog_launch",
        lambda app_id: seen.append(app_id) or {"ok": True, "launched": True})
    result = tools["vegas_pro_launch"]()
    assert result == {"ok": True, "launched": True}
    assert seen == ["vegas-pro"]


# ------------------------------------------------------------------ builders

def test_build_script_command_is_exact():
    # The -SCRIPT: prefix is glued to the path with a colon, no space.
    assert vp.build_script_command("vegas.exe", "movie.cs") == [
        "vegas.exe", "-SCRIPT:movie.cs"]
    assert vp.build_script_command(EXE, r"C:\work\render.js") == [
        EXE, r"-SCRIPT:C:\work\render.js"]
    assert vp.build_script_command(Path("vegas.exe"), Path("a.vb")) == [
        "vegas.exe", "-SCRIPT:a.vb"]


def test_build_open_command_is_exact():
    assert vp.build_open_command("vegas.exe", "movie.veg") == [
        "vegas.exe", "movie.veg"]
    assert vp.build_open_command(EXE, r"C:\work\film.veg") == [
        EXE, r"C:\work\film.veg"]
    assert vp.build_open_command(Path("vegas.exe"), Path("a.veg")) == [
        "vegas.exe", "a.veg"]


# ------------------------------------------------------------------ run_script

def test_run_script_spawns_with_script_argument(tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    script = allowed_tmp / "export.cs"
    script.write_text("// VEGAS script", encoding="utf-8")

    result = tools["vegas_pro_run_script"](str(script))

    assert result["ok"] is True
    assert result["launched"] is True
    assert result["pid"] == FakeProc.pid
    assert result["exe"] == EXE
    expected = [EXE, f"-SCRIPT:{script.resolve()}"]
    assert calls == [expected]
    assert result["argv"] == expected
    assert result["script"] == str(script.resolve())


def test_run_script_missing_exe_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)
    calls = _patch_spawn(monkeypatch)
    script = allowed_tmp / "export.cs"

    result = tools["vegas_pro_run_script"](str(script))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert "VEGAS Pro" in result["error"]
    assert calls == []


def test_run_script_detect_failure_returns_err(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(vp, "detect_app",
                        lambda app_id: {"ok": False, "error": "boom"})
    calls = _patch_spawn(monkeypatch)

    result = tools["vegas_pro_run_script"](str(allowed_tmp / "x.cs"))

    assert result == {"ok": False, "error": "boom"}
    assert calls == []


def test_run_script_disallowed_path_rejected_before_spawn(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["vegas_pro_run_script"]("/etc/passwd.cs")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


# ------------------------------------------------------------------ open_project

def test_open_project_spawns_with_project_argument(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "film.veg"
    project.write_bytes(b"fake veg project")

    result = tools["vegas_pro_open_project"](str(project))

    assert result["ok"] is True
    assert result["launched"] is True
    assert result["pid"] == FakeProc.pid
    assert result["exe"] == EXE
    expected = [EXE, str(project.resolve())]
    assert calls == [expected]
    assert result["argv"] == expected
    assert result["project"] == str(project.resolve())


def test_open_project_missing_exe_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "film.veg"

    result = tools["vegas_pro_open_project"](str(project))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert calls == []


def test_open_project_disallowed_path_rejected_before_spawn(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["vegas_pro_open_project"]("/etc/passwd.veg")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_tools_never_raise_on_empty_path(tools):
    for name in ("vegas_pro_run_script", "vegas_pro_open_project"):
        result = tools[name]("")
        assert isinstance(result, dict) and result["ok"] is False
