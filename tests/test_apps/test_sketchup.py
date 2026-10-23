"""Hermetic tests for the SketchUp adapter — no real SketchUp required.

Detection goes through monkeypatched ``apps_catalog.detect_app``,
spawning through monkeypatched ``sketchup._spawn``, and script runs
through an injected fake runner (the ``_common.run_cli`` runner
protocol: ``runner(argv, timeout) -> (returncode, stdout, stderr)``).
"""
from __future__ import annotations

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import sketchup
from desktop_mcp.adapters.apps.sketchup import (
    build_open_argv,
    build_run_script_argv,
)

FAKE_EXE = "/fake/SketchUp.exe"


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to tmp_path and isolate the audit log."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


def _detect_installed(app_id, **kwargs):
    assert app_id == "sketchup"
    return {"ok": True, "installed": True, "path": FAKE_EXE,
            "via": "path", "name": "SketchUp"}


def _detect_not_installed(app_id, **kwargs):
    assert app_id == "sketchup"
    return {"ok": True, "installed": False, "path": None,
            "via": None, "name": "SketchUp"}


class _FakeProc:
    pid = 4242


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
# Builders — exact argv
# ---------------------------------------------------------------------------

def test_build_open_argv_exact():
    assert build_open_argv(FAKE_EXE, "C:/models/house.skp") == [
        FAKE_EXE, "C:/models/house.skp",
    ]


def test_build_run_script_argv_exact():
    assert build_run_script_argv(FAKE_EXE, "C:/scripts/export.rb") == [
        FAKE_EXE, "-RubyStartup", "C:/scripts/export.rb",
    ]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_at_least_three_tools():
    mcp = _FakeMCP()
    names = sketchup.register(mcp)
    assert set(mcp.tools) >= {
        "sketchup_status", "sketchup_launch",
        "sketchup_open_file", "sketchup_run_script",
    }
    assert len(mcp.tools) >= 3
    assert set(names) == set(mcp.tools)


# ---------------------------------------------------------------------------
# sketchup_status — monkeypatched detection
# ---------------------------------------------------------------------------

def test_status_via_monkeypatched_detect(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    res = sketchup.sketchup_status()
    assert res["ok"] is True
    assert res["app_id"] == "sketchup"
    assert res["installed"] is True
    assert res["path"] == FAKE_EXE
    assert res["via"] == "path"
    # NB: _common.catalog_status surfaces strategy/prog_id/name keys,
    # but they are None with the current catalog get_app shape — the
    # detection fields above are the ones it fills in for real.


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    res = sketchup.sketchup_status()
    assert res["ok"] is True
    assert res["installed"] is False
    assert res["path"] is None


# ---------------------------------------------------------------------------
# sketchup_open_file — detect + spawn monkeypatched
# ---------------------------------------------------------------------------

def test_open_file_spawns_detected_exe_with_file(allowed, monkeypatch):
    target = allowed / "house.skp"
    target.write_bytes(b"skp")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(sketchup, "_spawn", _fake_spawn(calls))
    res = sketchup.sketchup_open_file(str(target))
    assert res["ok"] is True
    assert res["opened"] is True
    assert res["route"] == "exe_with_file_arg"
    assert res["exe"] == FAKE_EXE
    assert res["pid"] == 4242
    assert calls == [[FAKE_EXE, str(target.resolve())]]
    assert res["argv"] == calls[0]


def test_open_file_rejects_disallowed_path(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(sketchup, "_spawn", _fake_spawn(calls))
    res = sketchup.sketchup_open_file("/etc/passwd.skp")
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []  # nothing was launched


def test_open_file_not_installed_errs_and_never_spawns(allowed, monkeypatch):
    target = allowed / "house.skp"
    target.write_bytes(b"skp")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    calls = []
    monkeypatch.setattr(sketchup, "_spawn", _fake_spawn(calls))
    res = sketchup.sketchup_open_file(str(target))
    assert res["ok"] is False
    assert "not installed" in res["error"].lower()
    assert calls == []


# ---------------------------------------------------------------------------
# sketchup_run_script — fake runner, exact argv
# ---------------------------------------------------------------------------

def test_run_script_builds_exact_rubystartup_argv(allowed, monkeypatch):
    script = allowed / "export.rb"
    script.write_text("Sketchup.quit\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    res = sketchup.sketchup_run_script(
        str(script), runner=_fake_runner((0, "script done", ""), calls))
    assert res["ok"] is True
    assert res["stdout"] == "script done"
    assert res["returncode"] == 0
    assert res["route"] == "ruby_startup"
    assert res["script"] == str(script.resolve())
    # Exact argv: detected exe + -RubyStartup + resolved script path.
    assert calls == [([FAKE_EXE, "-RubyStartup", str(script.resolve())],
                      sketchup.SCRIPT_TIMEOUT)]
    assert res["argv"] == calls[0][0]


def test_run_script_nonzero_exit_surfaces_err(allowed, monkeypatch):
    script = allowed / "boom.rb"
    script.write_text("raise 'x'\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    res = sketchup.sketchup_run_script(
        str(script), runner=_fake_runner((1, "", "ruby boom")))
    assert res["ok"] is False
    assert "ruby boom" in res["error"]


def test_run_script_missing_exe_graceful_err_runner_not_called(allowed, monkeypatch):
    script = allowed / "export.rb"
    script.write_text("Sketchup.quit\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    calls = []
    res = sketchup.sketchup_run_script(
        str(script), runner=_fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not installed" in res["error"].lower()
    assert calls == []


def test_run_script_rejects_non_ruby_file(allowed, monkeypatch):
    script = allowed / "notes.txt"
    script.write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    res = sketchup.sketchup_run_script(
        str(script), runner=_fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert ".rb" in res["error"]
    assert calls == []


def test_run_script_rejects_disallowed_path(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    res = sketchup.sketchup_run_script(
        "/etc/passwd.rb", runner=_fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []


def test_run_script_missing_file_errs(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    res = sketchup.sketchup_run_script(
        str(allowed / "nope.rb"), runner=_fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "does not exist" in res["error"]
    assert calls == []
