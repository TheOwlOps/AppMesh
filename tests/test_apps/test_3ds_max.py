"""Hermetic tests for the 3ds Max adapter (adapters/apps/3ds_max.py).

No real 3ds Max is probed, launched or executed: detection goes
through monkeypatched ``apps_catalog.detect_app``, process spawning
through a fake ``popen`` / monkeypatched ``_spawn``, and headless
script runs through an injected fake runner (the ``_common.run_cli``
runner protocol: ``runner(argv, timeout) -> (rc, stdout, stderr)``).

The module is imported via ``importlib`` because its file name —
like its tool names (``3ds_max_*``) — starts with a digit and so
cannot be named by a plain ``import`` statement.
"""
from __future__ import annotations

import importlib
import shutil

import pytest

from desktop_mcp import apps_catalog, core

max3d = importlib.import_module("desktop_mcp.adapters.apps.3ds_max")

FAKE_EXE = "/fake/Autodesk/3ds Max 2025/3dsmax.exe"
FAKE_BATCH_EXE = "/fake/Autodesk/3ds Max 2025/3dsmaxbatch.exe"


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

class FakeMCP:
    """Minimal FastMCP stand-in capturing registered tools by name.

    Supports both ``@mcp.tool()`` and the explicit-name form
    ``@mcp.tool(name="3ds_max_status")`` the adapter must use (tool
    names starting with a digit are not valid Python identifiers).
    """

    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        explicit = kwargs.get("name") or (args[0] if args else None)

        def deco(fn):
            self.tools[explicit or fn.__name__] = fn
            return fn

        return deco


class FakeProc:
    pid = 4242


@pytest.fixture()
def allowed(tmp_path, monkeypatch):
    """Restrict the path allowlist to tmp_path and isolate the audit log."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    max3d.register(mcp)
    return mcp.tools


def fake_detect(installed=True, path=FAKE_EXE):
    def _detect(app_id, **kwargs):
        assert app_id == "3ds-max"
        return {
            "ok": True, "id": "3ds-max", "name": "3ds Max",
            "category": "CAD & 3D", "strategy": "plugin", "prog_id": None,
            "installed": installed, "path": path if installed else None,
            "via": "path" if installed else None,
        }
    return _detect


def _fake_spawn(calls):
    def spawn(argv):
        calls.append(list(argv))
        return FakeProc()
    return spawn


def _fake_runner(result, calls=None):
    def run(argv, timeout=None):
        if calls is not None:
            calls.append((list(argv), timeout))
        return result
    return run


def _scene(allowed, name="scene.max"):
    p = allowed / name
    p.write_bytes(b"max-scene")
    return p


def _script(allowed, name="job.ms"):
    p = allowed / name
    p.write_text('format "hello\\n"', encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_at_least_three_tools():
    mcp = FakeMCP()
    names = max3d.register(mcp)
    assert len(mcp.tools) >= 3
    assert {"3ds_max_status", "3ds_max_launch",
            "3ds_max_open_file", "3ds_max_run_script"} <= set(mcp.tools)
    assert set(names) == set(mcp.tools)


# ---------------------------------------------------------------------------
# Builders — exact argv
# ---------------------------------------------------------------------------

def test_build_open_argv():
    assert max3d.build_open_argv("a.max") == ["3dsmax.exe", "a.max"]
    assert max3d.build_open_argv("a.max", exe=FAKE_EXE) == [FAKE_EXE, "a.max"]


def test_build_run_script_argv():
    assert max3d.build_run_script_argv("job.ms") == [
        "3dsmax.exe", "-silent", "-mxs", 'fileIn @"job.ms"',
    ]
    assert max3d.build_run_script_argv("/x/job.ms", exe=FAKE_EXE) == [
        FAKE_EXE, "-silent", "-mxs", 'fileIn @"/x/job.ms"',
    ]


def test_build_batch_argv():
    assert max3d.build_batch_argv("job.ms") == ["3dsmaxbatch.exe", "job.ms"]
    assert max3d.build_batch_argv("job.ms", exe=FAKE_BATCH_EXE) == [
        FAKE_BATCH_EXE, "job.ms",
    ]


# ---------------------------------------------------------------------------
# Status / launch
# ---------------------------------------------------------------------------

def test_status_via_monkeypatched_detect_app(tools, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    result = tools["3ds_max_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "3ds-max"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["via"] == "path"
    # The authoritative strategy lives in the catalog entry itself
    # (catalog_status in _common reads get_app()'s nested payload).
    assert apps_catalog.get_app("3ds-max")["app"]["strategy"] == "plugin"


def test_status_not_installed(tools, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    result = tools["3ds_max_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


def test_launch_delegates_to_catalog(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "3ds-max", "launched": True}
    seen = []
    monkeypatch.setattr(
        max3d, "catalog_launch", lambda app_id: seen.append(app_id) or sentinel)
    assert tools["3ds_max_launch"]() is sentinel
    assert seen == ["3ds-max"]


# ---------------------------------------------------------------------------
# open_file — detected exe with the scene as argument
# ---------------------------------------------------------------------------

def test_open_file_via_detected_exe_with_fake_popen(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    scene = _scene(allowed)
    spawned = []

    def fake_popen(argv):
        spawned.append(list(argv))
        return FakeProc()

    result = max3d.open_file(str(scene), popen=fake_popen)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["route"] == "exe"
    assert result["via"] == "exe"
    assert result["pid"] == 4242
    assert result["path"] == str(scene.resolve())
    assert spawned == [max3d.build_open_argv(str(scene.resolve()), exe=FAKE_EXE)]


def test_open_file_tool_with_monkeypatched_spawn(tools, allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    scene = _scene(allowed, "shot.max")
    calls = []
    monkeypatch.setattr(max3d, "_spawn", _fake_spawn(calls))
    result = tools["3ds_max_open_file"](str(scene))
    assert result["ok"] is True and result["route"] == "exe"
    assert calls == [[FAKE_EXE, str(scene.resolve())]]


def test_open_file_not_installed_errs_and_never_spawns(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    scene = _scene(allowed)
    calls = []
    monkeypatch.setattr(max3d, "_spawn", _fake_spawn(calls))
    result = max3d.open_file(str(scene))
    assert result["ok"] is False
    assert "not installed" in result["error"].lower()
    assert calls == []


def test_open_file_rejects_disallowed_path(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    calls = []
    monkeypatch.setattr(max3d, "_spawn", _fake_spawn(calls))
    result = max3d.open_file("/etc/passwd.max")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []  # nothing spawned for a rejected path


def test_open_file_rejects_non_scene_extension(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    bad = allowed / "notes.txt"
    bad.write_text("x", encoding="utf-8")
    calls = []
    monkeypatch.setattr(max3d, "_spawn", _fake_spawn(calls))
    result = max3d.open_file(str(bad))
    assert result["ok"] is False
    assert ".max" in result["error"]
    assert calls == []


# ---------------------------------------------------------------------------
# run_script — headless MAXScript via run_cli with an injected runner
# ---------------------------------------------------------------------------

def test_run_script_argv_and_ok_passthrough(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    script = _script(allowed)
    calls = []
    result = max3d.run_script(
        str(script), runner=_fake_runner((0, "render done", ""), calls))
    assert result["ok"] is True
    assert result["route"] == "3dsmax-mxs"
    assert result["exe"] == FAKE_EXE
    assert result["stdout"] == "render done"
    assert result["returncode"] == 0
    assert calls == [(
        max3d.build_run_script_argv(str(script.resolve()), exe=FAKE_EXE),
        max3d.SCRIPT_TIMEOUT,
    )]


def test_run_script_falls_back_to_plain_exe_name(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    script = _script(allowed)
    calls = []
    result = max3d.run_script(
        str(script), runner=_fake_runner((0, "ok", ""), calls))
    assert result["ok"] is True
    assert calls[0][0] == max3d.build_run_script_argv(str(script.resolve()))


def test_run_script_batch_route_uses_sibling_3dsmaxbatch(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    script = _script(allowed, "batch_job.ms")
    calls = []
    result = max3d.run_script(
        str(script), runner=_fake_runner((0, "batch ok", ""), calls),
        use_batch=True)
    assert result["ok"] is True
    assert result["route"] == "3dsmaxbatch"
    assert result["exe"] == FAKE_BATCH_EXE
    assert calls[0][0] == max3d.build_batch_argv(
        str(script.resolve()), exe=FAKE_BATCH_EXE)


def test_run_script_nonzero_exit_surfaces_err(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    script = _script(allowed, "bad.ms")
    result = max3d.run_script(
        str(script), runner=_fake_runner((3, "", "MAXScript boom")))
    assert result["ok"] is False
    assert "MAXScript boom" in result["error"]


def test_run_script_missing_exe_is_graceful_err(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    script = _script(allowed)
    result = max3d.run_script(str(script))  # no runner -> real which() check
    assert result["ok"] is False
    assert "not found" in result["error"].lower()
    assert "3dsmax.exe" in result["error"]


def test_run_script_rejects_disallowed_path_before_running(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    calls = []
    result = max3d.run_script(
        "/etc/passwd.ms", runner=_fake_runner((0, "", ""), calls))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []  # runner never invoked


def test_run_script_rejects_non_script_extension(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    bad = allowed / "scene.max"
    bad.write_bytes(b"x")
    calls = []
    result = max3d.run_script(
        str(bad), runner=_fake_runner((0, "", ""), calls))
    assert result["ok"] is False
    assert ".ms" in result["error"]
    assert calls == []


def test_run_script_tool_is_wired(tools, allowed, monkeypatch):
    # The MCP wrapper delegates to the same impl; with no 3ds Max
    # executable available it must fail cleanly, not raise.
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    script = _script(allowed, "via_tool.ms")
    result = tools["3ds_max_run_script"](str(script))
    assert result["ok"] is False
    assert "not found" in result["error"].lower()
