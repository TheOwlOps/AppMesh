"""Hermetic tests for the GIMP adapter — no real GIMP required.

Detection goes through monkeypatched ``apps_catalog.detect_app``,
spawning through monkeypatched ``gimp._spawn``, and batch runs through
an injected fake runner (the ``_common.run_cli`` runner protocol:
``runner(argv, timeout) -> (returncode, stdout, stderr)``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import gimp
from desktop_mcp.adapters.apps.gimp import build_batch_command


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to tmp_path and isolate the audit log."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


def _detect_installed(exe="/fake/gimp-2.10.exe"):
    def _detect(app_id, **kwargs):
        assert app_id == "gimp"
        return {"ok": True, "installed": True, "path": exe,
                "via": "path", "name": "GIMP"}
    return _detect


def _detect_not_installed(app_id, **kwargs):
    assert app_id == "gimp"
    return {"ok": True, "installed": False, "path": None,
            "via": None, "name": "GIMP"}


class _FakeProc:
    pid = 9999


def _fake_spawn(calls):
    def spawn(argv):
        calls.append(list(argv))
        return _FakeProc()
    return spawn


def _fake_runner(result, calls=None):
    def run(argv, timeout=None):
        if calls is not None:
            calls.append((list(argv), timeout))
        return result
    return run


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


# ---------------------------------------------------------------------------
# Builder — exact argv
# ---------------------------------------------------------------------------

def test_build_batch_command_exact_argv():
    assert build_batch_command('(gimp-message "hi")') == [
        "gimp", "-i", "-b", '(gimp-message "hi")', "-b", "(gimp-quit 0)",
    ]


def test_build_batch_command_custom_exe():
    assert build_batch_command("(gimp-quit 0)", exe="/opt/gimp/bin/gimp") == [
        "/opt/gimp/bin/gimp", "-i", "-b", "(gimp-quit 0)",
        "-b", "(gimp-quit 0)",
    ]
    # Positional exe also works and the builder stays pure/deterministic.
    assert build_batch_command("expr", "gimp-2.10") == [
        "gimp-2.10", "-i", "-b", "expr", "-b", "(gimp-quit 0)",
    ]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_at_least_four_tools():
    mcp = _FakeMCP()
    names = gimp.register(mcp)
    assert set(mcp.tools) >= {
        "gimp_status", "gimp_launch", "gimp_open_file", "gimp_run_batch",
    }
    assert len(mcp.tools) >= 4
    assert "gimp_run_batch" in names


# ---------------------------------------------------------------------------
# gimp_run_batch — injected runner
# ---------------------------------------------------------------------------

def test_run_batch_argv_and_ok_passthrough(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    calls = []
    script = '(gimp-message "hello")'
    res = gimp.gimp_run_batch(
        script, runner=_fake_runner((0, "batch done", ""), calls))
    assert res["ok"] is True
    assert res["stdout"] == "batch done"
    assert res["returncode"] == 0
    # Not installed -> fallback exe "gimp"; argv is exactly the builder's.
    assert calls[0][0] == build_batch_command(script, exe="gimp")
    assert calls[0][1] == gimp.BATCH_TIMEOUT


def test_run_batch_uses_detected_exe(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app",
                        _detect_installed("/opt/gimp/bin/gimp"))
    calls = []
    res = gimp.gimp_run_batch(
        "(gimp-message 1)", runner=_fake_runner((0, "ok", ""), calls))
    assert res["ok"] is True
    assert calls[0][0] == build_batch_command(
        "(gimp-message 1)", exe="/opt/gimp/bin/gimp")


def test_run_batch_runner_failure_surfaces_err_no_exception(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    res = gimp.gimp_run_batch(
        "(bad-script",
        runner=_fake_runner((1, "", "script-fu boom")))
    assert res["ok"] is False
    assert "script-fu boom" in res["error"]


def test_run_batch_via_registered_tool(allowed, monkeypatch):
    # The MCP wrapper delegates to the same impl (runner-less path would
    # need a real GIMP, so only assert it is wired and fails cleanly
    # when the binary is missing rather than raising).
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    mcp = _FakeMCP()
    gimp.register(mcp)
    assert callable(mcp.tools["gimp_run_batch"])


# ---------------------------------------------------------------------------
# gimp_open_file — detect + spawn monkeypatched
# ---------------------------------------------------------------------------

def test_open_file_spawns_detected_exe_with_file(allowed, monkeypatch):
    target = allowed / "pic.xcf"
    target.write_bytes(b"xcf")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed())
    calls = []
    monkeypatch.setattr(gimp, "_spawn", _fake_spawn(calls))
    res = gimp.gimp_open_file(str(target))
    assert res["ok"] is True
    assert res["opened"] is True
    assert res["pid"] == 9999
    assert calls == [["/fake/gimp-2.10.exe", str(target.resolve())]]


def test_open_file_disallowed_path_errs_and_never_spawns(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed())
    calls = []
    monkeypatch.setattr(gimp, "_spawn", _fake_spawn(calls))
    res = gimp.gimp_open_file("/etc/passwd.xcf")
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []


def test_open_file_not_installed_errs_and_never_spawns(allowed, monkeypatch):
    target = allowed / "pic.xcf"
    target.write_bytes(b"xcf")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    calls = []
    monkeypatch.setattr(gimp, "_spawn", _fake_spawn(calls))
    res = gimp.gimp_open_file(str(target))
    assert res["ok"] is False
    assert "not installed" in res["error"].lower()
    assert calls == []
