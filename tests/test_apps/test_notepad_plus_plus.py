"""Hermetic tests for the Notepad++ adapter — no real Notepad++, no real spawn.

Detection is monkeypatched on ``apps_catalog`` (the adapter and
``_common`` both look it up there at call time) and the adapter's
``_spawn`` is replaced with a recorder, so argv can be asserted exactly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import notepad_plus_plus as npp

FAKE_EXE = r"C:\Program Files\Notepad++\notepad++.exe"


class FakeMCP:
    """Minimal stand-in for FastMCP: captures tools by function name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    npp.register(mcp)
    return mcp.tools


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / ".desktop-mcp"))


def _detected(installed: bool):
    def fake_detect(app_id, which_fn=None):
        assert app_id == "notepad-plus-plus"
        return core.ok(id="notepad-plus-plus", name="Notepad++",
                       installed=installed,
                       path=FAKE_EXE if installed else None,
                       via="path" if installed else None)

    return fake_detect


class FakeProc:
    pid = 4242


# ------------------------------------------------------------------ tools

def test_register_binds_three_tools(tools):
    assert set(tools) == {"notepad_plus_plus_status",
                          "notepad_plus_plus_launch",
                          "notepad_plus_plus_open_file"}


def test_status_delegates_to_catalog(tools, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detected(True))
    res = tools["notepad_plus_plus_status"]()
    assert res["ok"] is True
    assert res["app_id"] == "notepad-plus-plus"
    assert res["installed"] is True
    assert res["path"] == FAKE_EXE


def test_launch_delegates_to_catalog(tools, monkeypatch):
    calls = []

    def fake_launch(app_id):
        calls.append(app_id)
        return core.ok(app_id=app_id, launched=True, pid=1)

    monkeypatch.setattr(apps_catalog, "launch_app_from_catalog", fake_launch)
    res = tools["notepad_plus_plus_launch"]()
    assert res["ok"] is True and res["launched"] is True
    assert calls == ["notepad-plus-plus"]


# --------------------------------------------------------------- open_file

def test_open_file_spawns_exe_with_file_as_argument(tools, monkeypatch, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(apps_catalog, "detect_app", _detected(True))
    spawned = []
    monkeypatch.setattr(npp, "_spawn", lambda argv: spawned.append(argv) or FakeProc())

    res = tools["notepad_plus_plus_open_file"](str(target))

    assert res["ok"] is True
    assert res["opened"] is True
    assert res["pid"] == 4242
    assert spawned == [[FAKE_EXE, str(target.resolve())]]
    assert res["argv"] == [FAKE_EXE, str(target.resolve())]


def test_open_file_not_installed_returns_err_and_never_spawns(tools, monkeypatch, tmp_path):
    target = tmp_path / "notes.txt"
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(apps_catalog, "detect_app", _detected(False))
    spawned = []
    monkeypatch.setattr(npp, "_spawn", lambda argv: spawned.append(argv))

    res = tools["notepad_plus_plus_open_file"](str(target))

    assert res["ok"] is False
    assert "not installed" in res["error"]
    assert spawned == []


def test_open_file_disallowed_path_rejected_before_spawn(tools, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detected(True))
    spawned = []
    monkeypatch.setattr(npp, "_spawn", lambda argv: spawned.append(argv))

    res = tools["notepad_plus_plus_open_file"]("/etc/passwd")

    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert spawned == []


def test_open_file_spawn_failure_returns_err_not_raise(tools, monkeypatch, tmp_path):
    target = tmp_path / "notes.txt"
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(apps_catalog, "detect_app", _detected(True))

    def boom(argv):
        raise OSError("cannot start process")

    monkeypatch.setattr(npp, "_spawn", boom)
    res = tools["notepad_plus_plus_open_file"](str(target))
    assert res["ok"] is False
    assert "cannot start process" in res["error"]
