"""Hermetic tests for the Visual Studio Code adapter — fakes only.

No real ``code`` binary is required: happy paths inject a runner into
``vscode_open`` / ``vscode_version`` (forwarded to ``_common.run_cli``,
whose runner contract is ``runner(argv, timeout) -> (rc, out, err)``),
builders are asserted argv-for-argv, and status/launch are tested by
monkeypatching the catalog helpers they delegate to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common, vscode
from desktop_mcp.adapters.apps.vscode import (
    build_vscode_open,
    build_vscode_version,
    vscode_open,
    vscode_status,
    vscode_launch,
    vscode_version,
)


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir (+ audit home)."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


def fake_runner(result, calls=None):
    """A _common.run_cli-compatible runner: (argv, timeout) -> tuple."""
    def run(argv, timeout=120):
        if calls is not None:
            calls.append(list(argv))
        return result
    return run


# ---------------------------------------------------------------------------
# Builders — exact argv
# ---------------------------------------------------------------------------

def test_build_vscode_open_argv():
    assert build_vscode_open("/some/project") == [
        "code", "--reuse-window", "/some/project",
    ]
    assert build_vscode_open("file.py") == ["code", "--reuse-window", "file.py"]


def test_build_vscode_version_argv():
    assert build_vscode_version() == ["code", "--version"]


# ---------------------------------------------------------------------------
# vscode_open
# ---------------------------------------------------------------------------

def test_open_happy_path(allowed):
    target = allowed / "project"
    target.mkdir()
    calls = []
    res = vscode_open(str(target), runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is True
    assert res["opened"] is True
    assert res["path"] == str(target.resolve())
    assert res["returncode"] == 0
    assert calls == [["code", "--reuse-window", str(target.resolve())]]


def test_open_file_path(allowed):
    target = allowed / "main.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    calls = []
    res = vscode_open(str(target), runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is True
    assert calls == [build_vscode_open(str(target.resolve()))]


def test_open_disallowed_path_rejected_before_running(allowed):
    calls = []
    res = vscode_open("/etc/passwd", runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []  # runner / CLI never invoked


def test_open_nonzero_exit_is_err(allowed):
    target = allowed / "project"
    target.mkdir()
    res = vscode_open(str(target), runner=fake_runner((1, "", "boom")))
    assert res["ok"] is False
    assert "boom" in res["error"]


def test_open_via_monkeypatched_run_cli(allowed, monkeypatch):
    """vscode_open must route through _common.run_cli (patchable)."""
    seen = {}

    def fake_run_cli(argv, timeout=120, runner=None):
        seen["argv"] = list(argv)
        return core.ok(argv=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_common, "run_cli", fake_run_cli)
    target = allowed / "ws"
    target.mkdir()
    res = vscode_open(str(target))
    assert res["ok"] is True
    assert seen["argv"] == ["code", "--reuse-window", str(target.resolve())]


# ---------------------------------------------------------------------------
# vscode_version
# ---------------------------------------------------------------------------

def test_version_happy_path():
    calls = []
    out = "1.95.0\nabc123def\nx64\n"
    res = vscode_version(runner=fake_runner((0, out, ""), calls))
    assert res["ok"] is True
    assert res["version"] == "1.95.0"
    assert res["stdout"] == out
    assert calls == [["code", "--version"]]


def test_version_nonzero_exit_is_err():
    res = vscode_version(runner=fake_runner((2, "", "nope")))
    assert res["ok"] is False
    assert "nope" in res["error"]


def test_version_missing_binary_is_err(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = vscode_version()  # no runner -> real which() check in run_cli
    assert res["ok"] is False
    assert "code" in res["error"]


# ---------------------------------------------------------------------------
# Status / launch — delegation to catalog helpers
# ---------------------------------------------------------------------------

def test_status_delegates_to_catalog_status(monkeypatch):
    seen = {}

    def fake_catalog_status(app_id):
        seen["app_id"] = app_id
        return core.ok(app_id=app_id, installed=True, path="/fake/Code",
                       via="which", strategy="cli", prog_id=None,
                       name="Visual Studio Code")

    monkeypatch.setattr(_common, "catalog_status", fake_catalog_status)
    res = vscode_status()
    assert seen["app_id"] == "vscode"
    assert res["ok"] is True
    assert res["installed"] is True
    assert res["strategy"] == "cli"


def test_status_via_catalog_functions(monkeypatch):
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id: core.ok(app_id=app_id, installed=False, path=None,
                               via=None))
    monkeypatch.setattr(
        apps_catalog, "get_app",
        lambda app_id: core.ok(id=app_id, name="Visual Studio Code",
                               strategy="cli", prog_id=None))
    res = vscode_status()
    assert res["ok"] is True
    assert res["app_id"] == "vscode"
    assert res["installed"] is False
    assert res["strategy"] == "cli"
    assert res["name"] == "Visual Studio Code"


def test_launch_delegates_to_catalog_launch(monkeypatch):
    seen = {}

    def fake_catalog_launch(app_id):
        seen["app_id"] = app_id
        return core.ok(app_id=app_id, launched=True, path="/fake/Code")

    monkeypatch.setattr(_common, "catalog_launch", fake_catalog_launch)
    res = vscode_launch()
    assert seen["app_id"] == "vscode"
    assert res["ok"] is True
    assert res["launched"] is True


def test_launch_via_catalog_function(monkeypatch):
    seen = {}

    def fake_launch(app_id):
        seen["app_id"] = app_id
        return core.ok(app_id=app_id, launched=True)

    monkeypatch.setattr(apps_catalog, "launch_app_from_catalog", fake_launch)
    res = vscode_launch()
    assert seen["app_id"] == "vscode"
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_binds_all_tools():
    mcp = _FakeMCP()
    names = vscode.register(mcp)
    expected = {"vscode_status", "vscode_launch", "vscode_open",
                "vscode_version"}
    assert len(names) >= 3
    assert set(names) == expected
    assert set(mcp.tools) == expected


def test_registered_open_rejects_disallowed_path(allowed):
    mcp = _FakeMCP()
    vscode.register(mcp)
    res = mcp.tools["vscode_open"]("/etc/passwd")
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()


def test_registered_status_and_launch_delegate(allowed, monkeypatch):
    monkeypatch.setattr(_common, "catalog_status",
                        lambda app_id: core.ok(app_id=app_id, installed=True))
    monkeypatch.setattr(_common, "catalog_launch",
                        lambda app_id: core.ok(app_id=app_id, launched=True))
    mcp = _FakeMCP()
    vscode.register(mcp)
    assert mcp.tools["vscode_status"]()["installed"] is True
    assert mcp.tools["vscode_launch"]()["launched"] is True
