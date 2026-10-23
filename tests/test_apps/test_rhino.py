"""Hermetic tests for the Rhino adapter — no real Rhino, no Windows.

Detection goes through a monkeypatched ``apps_catalog.detect_app``,
opening a file through a monkeypatched ``rhino._spawn``, and running a
script through an injectable fake runner forwarded to
``_common.run_cli`` (argv is asserted exactly).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import rhino


FAKE_EXE = "C:/Program Files/Rhino 8/System/Rhino.exe"


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
    pid = 4321


@pytest.fixture(autouse=True)
def _audit_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


def fake_detect(installed=True, path=FAKE_EXE):
    def _detect(app_id, *args, **kwargs):
        assert app_id == "rhino"
        return {
            "ok": True,
            "id": "rhino",
            "name": "Rhino",
            "installed": installed,
            "path": path if installed else None,
            "via": "path" if installed else None,
        }

    return _detect


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools():
    mcp = FakeMCP()
    rhino.register(mcp)
    assert len(mcp.tools) >= 3
    assert set(mcp.tools) == {
        "rhino_status",
        "rhino_launch",
        "rhino_open_file",
        "rhino_run_script",
    }
    assert rhino.APP_ID == "rhino"


# -------------------------------------------------------------------- status

def test_status_via_monkeypatched_detect(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    mcp = FakeMCP()
    rhino.register(mcp)
    result = mcp.tools["rhino_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "rhino"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["via"] == "path"


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(
        apps_catalog, "detect_app", fake_detect(installed=False, path=None)
    )
    # Go through the registered tool (the public surface).
    mcp = FakeMCP()
    rhino.register(mcp)
    result = mcp.tools["rhino_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


# ----------------------------------------------------------------- open file

def test_open_file_uses_exe_with_file_arg(allowed, monkeypatch):
    model = allowed / "house.3dm"
    model.write_bytes(b"fake-3dm")
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    spawned: list[list[str]] = []

    def fake_spawn(argv):
        spawned.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(rhino, "_spawn", fake_spawn)
    mcp = FakeMCP()
    rhino.register(mcp)
    result = mcp.tools["rhino_open_file"](str(model))
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["route"] == "exe"
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4321
    assert spawned == [[FAKE_EXE, str(model.resolve())]]


def test_open_file_not_installed(allowed, monkeypatch):
    model = allowed / "house.3dm"
    model.write_bytes(b"fake-3dm")
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    monkeypatch.setattr(
        rhino, "_spawn", lambda argv: pytest.fail("must not spawn when not installed")
    )
    result = rhino.open_file(str(model))
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_open_file_rejects_path_outside_allowed_roots(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    monkeypatch.setattr(
        rhino, "_spawn", lambda argv: pytest.fail("must not spawn for rejected paths")
    )
    result = rhino.open_file("/etc/passwd.3dm")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


# ---------------------------------------------------------------- run script

def test_run_script_builds_runscript_argv(allowed, monkeypatch):
    script = allowed / "make_box.py"
    script.write_text("# rhino python\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    seen: dict = {}

    def fake_runner(argv, timeout):
        seen["argv"] = list(argv)
        seen["timeout"] = timeout
        return 0, "script done", ""

    result = rhino.run_script(str(script), runner=fake_runner)
    expected_argv = [
        FAKE_EXE,
        f'/runscript="_-RunPythonScript {script.resolve()}"',
    ]
    assert result["ok"] is True
    assert result["route"] == "runscript"
    assert result["script"] == str(script.resolve())
    assert result["stdout"] == "script done"
    assert seen["argv"] == expected_argv
    assert result["argv"] == expected_argv


def test_run_script_argv_builder_for_rvb():
    argv = rhino.build_runscript_argv(FAKE_EXE, "C:/scripts/old.rvb")
    assert argv == [FAKE_EXE, '/runscript="_-RunScript C:/scripts/old.rvb"']


def test_run_script_via_tool_uses_run_cli(allowed, monkeypatch):
    script = allowed / "tool_script.py"
    script.write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    captured: dict = {}

    def fake_run_cli(argv, timeout=120, runner=None):
        captured["argv"] = list(argv)
        return core.ok(argv=list(argv), returncode=0, stdout="ok-out", stderr="")

    monkeypatch.setattr(rhino, "run_cli", fake_run_cli)
    mcp = FakeMCP()
    rhino.register(mcp)
    result = mcp.tools["rhino_run_script"](str(script))
    assert result["ok"] is True
    assert captured["argv"] == [
        FAKE_EXE,
        f'/runscript="_-RunPythonScript {script.resolve()}"',
    ]


def test_run_script_graceful_err_when_exe_missing(allowed, monkeypatch):
    script = allowed / "make_box.py"
    script.write_text("# rhino python\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))

    def exploding_runner(argv, timeout):
        raise AssertionError("runner must not be called when Rhino is missing")

    result = rhino.run_script(str(script), runner=exploding_runner)
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_run_script_rejects_path_outside_allowed_roots(allowed):
    result = rhino.run_script("/etc/passwd.py", runner=lambda a, t: (0, "", ""))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_run_script_propagates_nonzero_exit(allowed, monkeypatch):
    script = allowed / "boom.py"
    script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    result = rhino.run_script(
        str(script), runner=lambda argv, timeout: (3, "", "kaboom")
    )
    assert result["ok"] is False
    assert "3" in result["error"] and "kaboom" in result["error"]
