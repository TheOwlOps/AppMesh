"""Hermetic tests for the Fusion 360 adapter (plugin strategy).

No real Fusion 360 is probed, launched or spawned: detection goes
through a monkeypatched ``apps_catalog.detect_app``, opening a file goes
through a monkeypatched ``_spawn``, and running a script goes through a
fake ``run_cli`` runner.
"""
from __future__ import annotations

from pathlib import Path

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import fusion_360

EXE = "C:/Program Files/Autodesk/Fusion 360/Fusion360.exe"
ALLOWED_FILE = str(Path.home() / "design.f3d")
ALLOWED_SCRIPT = str(Path.home() / "make_box.py")


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class FakeProc:
    pid = 777


def fake_detect_installed(app_id, which_fn=None, **_kw):
    assert app_id == fusion_360.APP_ID == "fusion-360"
    return {"ok": True, "id": app_id, "name": "Fusion 360",
            "installed": True, "path": EXE, "via": "path",
            "strategy": "plugin", "prog_id": None}


def fake_detect_missing(app_id, which_fn=None, **_kw):
    return {"ok": True, "id": app_id, "name": "Fusion 360",
            "installed": False, "path": None, "via": None,
            "strategy": "plugin", "prog_id": None}


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools():
    mcp = FakeMCP()
    fusion_360.register(mcp)
    assert {"fusion_360_status", "fusion_360_launch",
            "fusion_360_open_file", "fusion_360_run_script"} <= set(mcp.tools)
    assert len(mcp.tools) >= 3


# -------------------------------------------------------------------- status

def test_status_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    mcp = FakeMCP()
    fusion_360.register(mcp)
    result = mcp.tools["fusion_360_status"]()
    assert result["ok"] is True
    assert result["installed"] is True
    assert result["path"] == EXE
    assert "strategy" in result  # value comes from _common.catalog_status
    assert result["app_id"] == "fusion-360"


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_missing)
    mcp = FakeMCP()
    fusion_360.register(mcp)
    result = mcp.tools["fusion_360_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


# ----------------------------------------------------------------- open_file

def test_open_file_via_exe_file_arg(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    spawned = []
    monkeypatch.setattr(
        fusion_360, "_spawn",
        lambda argv: spawned.append(argv) or FakeProc())
    result = fusion_360.open_file(ALLOWED_FILE)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["route"] == "exe-file-arg"
    assert result["path"] == ALLOWED_FILE
    assert result["exe"] == EXE
    assert result["pid"] == 777
    assert spawned == [[EXE, ALLOWED_FILE]]


def test_open_file_via_registered_tool(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    spawned = []
    monkeypatch.setattr(
        fusion_360, "_spawn",
        lambda argv: spawned.append(argv) or FakeProc())
    mcp = FakeMCP()
    fusion_360.register(mcp)
    result = mcp.tools["fusion_360_open_file"](ALLOWED_FILE)
    assert result["ok"] is True and result["route"] == "exe-file-arg"
    assert spawned == [[EXE, ALLOWED_FILE]]


def test_open_file_with_injected_popen(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    calls = []

    def fake_popen(argv):
        calls.append(argv)
        return FakeProc()

    result = fusion_360.open_file(ALLOWED_FILE, popen_fn=fake_popen)
    assert result["ok"] is True
    assert calls == [[EXE, ALLOWED_FILE]]


def test_open_file_exe_missing(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_missing)
    monkeypatch.setattr(
        fusion_360, "_spawn",
        lambda argv: (_ for _ in ()).throw(AssertionError("must not spawn")))
    result = fusion_360.open_file(ALLOWED_FILE)
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_open_file_rejects_disallowed_path(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    spawned = []
    monkeypatch.setattr(fusion_360, "_spawn",
                        lambda argv: spawned.append(argv) or FakeProc())
    result = fusion_360.open_file("/etc/passwd.f3d")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert spawned == []  # allowlist check runs before spawning


# ----------------------------------------------------------------- run_script

def test_run_script_via_fake_runner(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    calls = []

    def fake_runner(argv, timeout):
        calls.append((argv, timeout))
        return (0, "script ran", "")

    result = fusion_360.run_script(ALLOWED_SCRIPT, runner=fake_runner)
    assert result["ok"] is True
    assert result["route"] == "run_cli"
    assert result["script"] == ALLOWED_SCRIPT
    assert result["exe"] == EXE
    assert result["stdout"] == "script ran"
    assert calls[0][0] == [EXE, ALLOWED_SCRIPT]


def test_run_script_exe_missing_graceful(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_missing)
    called = []
    result = fusion_360.run_script(
        ALLOWED_SCRIPT,
        runner=lambda argv, timeout: called.append(argv) or (0, "", ""))
    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert called == []


def test_run_script_rejects_disallowed_and_non_python(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    runner = lambda argv, timeout: (0, "", "")  # noqa: E731
    bad_path = fusion_360.run_script("/etc/passwd.py", runner=runner)
    assert bad_path["ok"] is False
    assert "not allowed" in bad_path["error"].lower()
    not_py = fusion_360.run_script(str(Path.home() / "notes.txt"),
                                   runner=runner)
    assert not_py["ok"] is False
    assert ".py" in not_py["error"]
