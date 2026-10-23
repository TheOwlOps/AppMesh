"""Hermetic tests for the FreeCAD adapter (plugin strategy).

No real FreeCAD install is required: detection goes through a
monkeypatched ``apps_catalog.detect_app``, launching/opening through a
monkeypatched ``subprocess.Popen``, and script execution through the
injectable ``runner`` forwarded to ``_common.run_cli``.
"""
from __future__ import annotations

from pathlib import Path

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import freecad
from desktop_mcp.adapters.apps.freecad import (
    _console_exe_from_gui,
    build_run_script_argv,
    open_file,
    register,
    run_script,
)

ALLOWED_FILE = str(Path.home() / "model.FCStd")
ALLOWED_SCRIPT = str(Path.home() / "macro.py")
FAKE_GUI = "C:/Program Files/FreeCAD/bin/FreeCAD.exe"
FAKE_CMD = "C:/Program Files/FreeCAD/bin/FreeCADCmd.exe"


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _fake_detect(installed=True, path=FAKE_GUI):
    """Build a detect_app replacement with the real return shape."""
    def _detect(app_id, which_fn=None):
        assert app_id == "freecad"
        return {
            "ok": True,
            "id": "freecad",
            "name": "FreeCAD",
            "category": "CAD & 3D",
            "strategy": "plugin",
            "prog_id": None,
            "installed": installed,
            "path": path if installed else None,
            "via": "path" if installed else None,
        }

    return _detect


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools():
    mcp = FakeMCP()
    register(mcp)
    assert len(mcp.tools) >= 3
    assert "freecad_status" in mcp.tools
    assert "freecad_launch" in mcp.tools
    assert "freecad_open_file" in mcp.tools
    assert "freecad_run_script" in mcp.tools


# -------------------------------------------------------------------- status

def test_status_via_monkeypatched_detect(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect())
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["freecad_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "freecad"
    assert result["installed"] is True
    assert result["path"] == FAKE_GUI
    # catalog_status surfaces a strategy field straight from the catalog
    # helper; the catalog record itself is the strategy authority.
    assert "strategy" in result
    assert apps_catalog.get_app("freecad")["app"]["strategy"] == "plugin"


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(installed=False))
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["freecad_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


# ----------------------------------------------------------------- open_file

def test_open_file_rejects_disallowed_path(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect())
    # Direct impl and through the registered tool.
    result = open_file("/etc/passwd.FCStd")
    assert result["ok"] is False
    assert "not allowed" in result["error"]
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["freecad_open_file"]("/etc/passwd.FCStd")
    assert result["ok"] is False
    assert "not allowed" in result["error"]


def test_open_file_uses_exe_with_file_arg(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect())
    spawned = []

    class _FakeProc:
        pid = 4321

    def fake_popen(argv, **kwargs):
        spawned.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(freecad.subprocess, "Popen", fake_popen)
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["freecad_open_file"](ALLOWED_FILE)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["route"] == "exe"
    assert result["exe"] == FAKE_GUI
    assert spawned == [[FAKE_GUI, str(Path(ALLOWED_FILE).resolve())]]


def test_open_file_not_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(installed=False))
    result = open_file(ALLOWED_FILE)
    assert result["ok"] is False
    assert "not installed" in result["error"]


# ---------------------------------------------------------------- run_script

def test_console_exe_derivation():
    assert _console_exe_from_gui(FAKE_GUI) == FAKE_CMD
    assert _console_exe_from_gui("/usr/bin/freecad") == "/usr/bin/freecadcmd"
    assert build_run_script_argv(FAKE_CMD, ALLOWED_SCRIPT) == [FAKE_CMD, ALLOWED_SCRIPT]


def test_run_script_argv_via_fake_runner(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect())
    captured = []

    def fake_runner(argv, timeout=120):
        captured.append(list(argv))
        return (0, "script done", "")

    result = run_script(ALLOWED_SCRIPT, runner=fake_runner)
    assert result["ok"] is True
    assert result["route"] == "freecadcmd"
    assert result["console_exe"] == FAKE_CMD
    assert result["returncode"] == 0
    assert result["stdout"] == "script done"
    # The core assertion: argv is [console exe, script path].
    assert captured == [[FAKE_CMD, str(Path(ALLOWED_SCRIPT).resolve())]]
    assert result["argv"] == captured[0]


def test_run_script_falls_back_to_freecadcmd_on_path(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(installed=False))
    monkeypatch.setattr(
        freecad.shutil, "which",
        lambda name: "/usr/bin/freecadcmd" if name == "freecadcmd" else None,
    )
    captured = []

    def fake_runner(argv, timeout=120):
        captured.append(list(argv))
        return (0, "", "")

    result = run_script(ALLOWED_SCRIPT, runner=fake_runner)
    assert result["ok"] is True
    assert captured[0][0] == "/usr/bin/freecadcmd"
    assert captured[0][1] == str(Path(ALLOWED_SCRIPT).resolve())


def test_run_script_missing_exe_is_graceful_err(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(installed=False))
    monkeypatch.setattr(freecad.shutil, "which", lambda name: None)

    def runner_must_not_run(argv, timeout=120):  # pragma: no cover
        raise AssertionError("runner must not be called without a console exe")

    result = run_script(ALLOWED_SCRIPT, runner=runner_must_not_run)
    assert result["ok"] is False
    assert "FreeCADCmd" in result["error"] or "freecadcmd" in result["error"]


def test_run_script_rejects_disallowed_path(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect())
    result = run_script("/etc/passwd.py", runner=lambda argv, timeout=120: (0, "", ""))
    assert result["ok"] is False
    assert "not allowed" in result["error"]


def test_run_script_nonzero_exit_is_err(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect())
    result = run_script(
        ALLOWED_SCRIPT, runner=lambda argv, timeout=120: (1, "", "boom")
    )
    assert result["ok"] is False
    assert "boom" in result["error"] or "exited 1" in result["error"]
