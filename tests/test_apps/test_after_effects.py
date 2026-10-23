"""Hermetic tests for the After Effects adapter — fake runners only.

No real After Effects is probed, launched or rendered: detection is
monkeypatched through the ``detect_app`` reference in the
after_effects module namespace, and CLI execution goes through a
monkeypatched ``run_cli`` or an injected ``runner``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import after_effects
from desktop_mcp.adapters.apps.after_effects import (
    build_aerender_command,
    build_extendscript_command,
    resolve_aerender_exe,
)

FAKE_EXE = "/fake/Adobe After Effects/AfterFX.exe"
FAKE_AERENDER = "/fake/Adobe After Effects/aerender.exe"


class _FakeMCP:
    """Minimal FastMCP stand-in: captures @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture()
def tools():
    mcp = _FakeMCP()
    after_effects.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    return tmp_path


def _fake_detect(installed=True, path=FAKE_EXE):
    """Build a detect_app replacement with the real catalog shape."""
    def detect(app_id):
        assert app_id == "after-effects"
        return {
            "ok": True,
            "id": "after-effects",
            "name": "Adobe After Effects",
            "category": "Video & Audio",
            "strategy": "plugin",
            "prog_id": None,
            "installed": installed,
            "path": path if installed else None,
            "via": "path" if installed else None,
        }
    return detect


def _patch_detect(monkeypatch, installed=True, path=FAKE_EXE):
    monkeypatch.setattr(after_effects, "detect_app",
                        _fake_detect(installed=installed, path=path))


def _patch_run_cli(monkeypatch, result=None):
    """Replace the adapter's run_cli with a capturing fake."""
    calls = []
    if result is None:
        result = {"ok": True, "returncode": 0, "stdout": "done", "stderr": ""}

    def fake_run_cli(argv, timeout=120, runner=None):
        calls.append((list(argv), timeout))
        return dict(result)

    monkeypatch.setattr(after_effects, "run_cli", fake_run_cli)
    return calls


def fake_runner(result, calls=None):
    """A run_cli-style runner: callable(argv, timeout) -> (rc, out, err)."""
    def run(argv, timeout):
        if calls is not None:
            calls.append((list(argv), timeout))
        return result
    return run


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_at_least_three_tools(tools):
    assert len(tools) >= 3
    assert {"after_effects_status", "after_effects_launch",
            "after_effects_run_script",
            "after_effects_render"} <= set(tools)
    assert after_effects.APP_ID == "after-effects"


def test_catalog_entry_is_plugin_with_extendscript_notes():
    from desktop_mcp import apps_catalog
    app = apps_catalog.get_app("after-effects")
    assert app["ok"] and app["app"]["strategy"] == "plugin"
    assert app["app"]["exe_names"] == ["AfterFX.exe"]
    assert app["app"]["prog_id"] is None


# ---------------------------------------------------------------------------
# Status / launch — detect_app monkeypatched in the module namespace
# ---------------------------------------------------------------------------

def test_status_with_monkeypatched_detect_app_installed(tools, monkeypatch):
    _patch_detect(monkeypatch, installed=True)
    result = tools["after_effects_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "after-effects"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["strategy"] == "plugin"


def test_status_with_monkeypatched_detect_app_not_installed(tools, monkeypatch):
    _patch_detect(monkeypatch, installed=False)
    result = tools["after_effects_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


def test_launch_delegates_to_catalog_launch(monkeypatch):
    sentinel = {"ok": True, "launched": True, "app_id": "after-effects"}
    monkeypatch.setattr(after_effects, "catalog_launch",
                        lambda app_id: sentinel)
    assert after_effects.after_effects_launch() == sentinel


# ---------------------------------------------------------------------------
# Pure builders — exact argv
# ---------------------------------------------------------------------------

def test_build_extendscript_command_exact():
    assert build_extendscript_command("AfterFX.exe", "fix.jsx") == [
        "AfterFX.exe", "-r", "fix.jsx"]
    assert build_extendscript_command(
        r"C:\Program Files\Adobe\AfterFX.exe", r"C:\scripts\hello.jsx") == [
        r"C:\Program Files\Adobe\AfterFX.exe", "-r", r"C:\scripts\hello.jsx"]
    # Path inputs are stringified.
    assert build_extendscript_command(Path("AfterFX.exe"), Path("a.jsx")) == [
        "AfterFX.exe", "-r", "a.jsx"]


def test_build_aerender_command_exact():
    assert build_aerender_command("aerender.exe", "p.aep", "Main", "o.mov") == [
        "aerender.exe", "-project", "p.aep", "-comp", "Main",
        "-output", "o.mov"]
    assert build_aerender_command(
        FAKE_AERENDER, "proj.aep", "Main Comp", "renders/out.mov") == [
        FAKE_AERENDER, "-project", "proj.aep", "-comp", "Main Comp",
        "-output", "renders/out.mov"]


# ---------------------------------------------------------------------------
# aerender resolution
# ---------------------------------------------------------------------------

def test_resolve_aerender_prefers_sibling_of_afterfx(monkeypatch):
    _patch_detect(monkeypatch, installed=True)
    assert resolve_aerender_exe() == FAKE_AERENDER
    # Windows-style detected paths keep their separator style.
    _patch_detect(monkeypatch, installed=True,
                  path=r"C:\Adobe\AfterFX.exe")
    assert resolve_aerender_exe() == r"C:\Adobe\aerender.exe"


def test_resolve_aerender_falls_back_to_which(monkeypatch):
    _patch_detect(monkeypatch, installed=False)
    which = lambda name: "/usr/bin/aerender" if name == "aerender" else None  # noqa: E731
    assert resolve_aerender_exe(which_fn=which) == "/usr/bin/aerender"


def test_resolve_aerender_missing_everywhere(monkeypatch):
    _patch_detect(monkeypatch, installed=False)
    assert resolve_aerender_exe(which_fn=lambda name: None) is None


# ---------------------------------------------------------------------------
# run_script
# ---------------------------------------------------------------------------

def test_run_script_happy_via_registered_tool(tools, monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    calls = _patch_run_cli(monkeypatch)
    script = allowed / "hello.jsx"
    script.write_text("alert('hi');", encoding="utf-8")

    result = tools["after_effects_run_script"](str(script))

    assert result["ok"] is True
    assert result["script"] == str(script.resolve())
    assert result["exe"] == FAKE_EXE
    assert calls == [([FAKE_EXE, "-r", str(script.resolve())],
                      after_effects.RUN_SCRIPT_TIMEOUT)]
    assert result["argv"] == [FAKE_EXE, "-r", str(script.resolve())]


def test_run_script_happy_via_injected_runner(monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    script = allowed / "job.jsx"
    calls = []
    result = after_effects.after_effects_run_script(
        str(script), runner=fake_runner((0, "ran", ""), calls))
    assert result["ok"] is True
    assert result["stdout"] == "ran"
    assert calls == [([FAKE_EXE, "-r", str(script.resolve())],
                      after_effects.RUN_SCRIPT_TIMEOUT)]


def test_run_script_missing_exe_returns_err(tools, monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=False)
    calls = _patch_run_cli(monkeypatch)
    script = allowed / "hello.jsx"

    result = tools["after_effects_run_script"](str(script))

    assert result["ok"] is False
    assert "AfterFX.exe" in result["error"]
    assert "not installed" in result["error"]
    assert calls == []  # nothing was executed


def test_run_script_rejects_etc_passwd_jsx(tools, monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    calls = _patch_run_cli(monkeypatch)

    result = tools["after_effects_run_script"]("/etc/passwd.jsx")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_run_script_nonzero_exit_is_err(monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    script = allowed / "bad.jsx"
    result = after_effects.after_effects_run_script(
        str(script), runner=fake_runner((1, "", "script blew up")))
    assert result["ok"] is False
    assert "script blew up" in result["error"]


# ---------------------------------------------------------------------------
# render (aerender)
# ---------------------------------------------------------------------------

def test_render_happy_via_registered_tool(tools, monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    calls = _patch_run_cli(monkeypatch)
    project = allowed / "proj.aep"
    project.write_bytes(b"fake-aep")
    output = allowed / "renders" / "out.mov"

    result = tools["after_effects_render"](str(project), "Main Comp",
                                           str(output))

    expected = [FAKE_AERENDER, "-project", str(project.resolve()),
                "-comp", "Main Comp", "-output", str(output.resolve())]
    assert result["ok"] is True
    assert result["comp"] == "Main Comp"
    assert result["exe"] == FAKE_AERENDER
    assert result["argv"] == expected
    assert calls == [(expected, after_effects.RENDER_TIMEOUT)]


def test_render_missing_exe_returns_err(tools, monkeypatch, allowed):
    # No After Effects detected and no aerender anywhere -> clear err.
    _patch_detect(monkeypatch, installed=False)
    monkeypatch.setattr(after_effects, "resolve_aerender_exe",
                        lambda which_fn=None: None)
    calls = _patch_run_cli(monkeypatch)
    project = allowed / "proj.aep"
    output = allowed / "out.mov"

    result = tools["after_effects_render"](str(project), "Main",
                                           str(output))

    assert result["ok"] is False
    assert "aerender" in result["error"]
    assert calls == []  # nothing was executed


def test_render_rejects_disallowed_project(tools, monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    calls = _patch_run_cli(monkeypatch)

    result = tools["after_effects_render"]("/etc/evil.aep", "Main",
                                           str(allowed / "out.mov"))

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_render_rejects_disallowed_output(tools, monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    calls = _patch_run_cli(monkeypatch)
    project = allowed / "proj.aep"

    result = tools["after_effects_render"](str(project), "Main",
                                           "/etc/out.mov")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_render_empty_comp_is_err(tools, monkeypatch, allowed):
    _patch_detect(monkeypatch, installed=True)
    calls = _patch_run_cli(monkeypatch)
    project = allowed / "proj.aep"

    result = tools["after_effects_render"](str(project), "  ",
                                           str(allowed / "out.mov"))

    assert result["ok"] is False
    assert "composition" in result["error"].lower()
    assert calls == []


def test_tools_never_raise_on_empty_paths(tools):
    for result in (
        tools["after_effects_run_script"](""),
        tools["after_effects_render"]("", "Main", ""),
    ):
        assert isinstance(result, dict)
