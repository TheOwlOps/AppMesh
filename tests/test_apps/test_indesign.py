"""Hermetic tests for the Adobe InDesign adapter (adapters/apps/indesign.py).

No real InDesign and no real COM: the adapter's ``ComApp`` seam is
replaced with a fake, catalog detection and process spawning are
monkeypatched, and status/launch delegation is checked with fakes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp.adapters.apps import indesign

ALLOWED_FILE = str(Path.home() / "layout.indd")
DISALLOWED_FILE = "/etc/passwd.indd"
FAKE_EXE = "C:/Program Files/Adobe/Adobe InDesign 2025/InDesign.exe"


# --------------------------------------------------------------------- fakes

class FakeMCP:
    """Minimal FastMCP stand-in: captures tools registered via @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeComApp:
    """Drop-in replacement for _common.ComApp, fully test-controlled."""

    instances: list["FakeComApp"] = []
    result: dict = {"ok": True, "opened": True, "document": "layout.indd"}

    def __init__(self, prog_id, factory=None):
        self.prog_id = prog_id
        self.factory = factory
        self.open_calls: list[tuple[str, str]] = []
        type(self).instances.append(self)

    def open_via(self, collection, path):
        self.open_calls.append((collection, path))
        return dict(type(self).result)


@pytest.fixture(autouse=True)
def _reset_fake_com():
    FakeComApp.instances = []
    FakeComApp.result = {"ok": True, "opened": True, "document": "layout.indd"}
    yield
    FakeComApp.instances = []


def _register():
    mcp = FakeMCP()
    indesign.register(mcp)
    return mcp


def _no_spawn_allowed(monkeypatch):
    monkeypatch.setattr(indesign, "_spawn_with_file",
                        lambda *a: pytest.fail("must not spawn"))


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools():
    mcp = _register()
    assert len(mcp.tools) >= 3
    assert {"indesign_status", "indesign_launch", "indesign_open_file"} <= set(mcp.tools)


# ------------------------------------------------------- status / launch

def test_status_and_launch_delegate_to_catalog(monkeypatch):
    seen = []
    monkeypatch.setattr(indesign, "catalog_status",
                        lambda app_id: seen.append(("status", app_id)) or
                        {"ok": True, "app_id": app_id, "installed": True})
    monkeypatch.setattr(indesign, "catalog_launch",
                        lambda app_id: seen.append(("launch", app_id)) or
                        {"ok": True, "app_id": app_id, "launched": True})
    mcp = _register()

    status = mcp.tools["indesign_status"]()
    launch = mcp.tools["indesign_launch"]()

    assert status["ok"] and status["installed"] is True
    assert launch["ok"] and launch["launched"] is True
    assert seen == [("status", "indesign"), ("launch", "indesign")]


# -------------------------------------------------------------- open: COM

def test_open_file_com_success_reports_route_com(monkeypatch):
    monkeypatch.setattr(indesign, "ComApp", FakeComApp)
    _no_spawn_allowed(monkeypatch)
    mcp = _register()

    result = mcp.tools["indesign_open_file"](ALLOWED_FILE)

    assert result["ok"] and result["route"] == "com"
    assert result["opened"] is True
    assert len(FakeComApp.instances) == 1
    fake = FakeComApp.instances[0]
    assert fake.prog_id == "InDesign.Application"
    assert fake.open_calls == [("Documents", str(Path(ALLOWED_FILE).resolve()))]


# -------------------------------------------------------------- open: exe

def test_open_file_com_error_falls_back_to_exe(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))  # audit stays in tmp
    FakeComApp.result = {"ok": False, "error": "COM unavailable"}
    monkeypatch.setattr(indesign, "ComApp", FakeComApp)
    monkeypatch.setattr(indesign, "_detect_exe",
                        lambda: {"ok": True, "installed": True,
                                 "path": FAKE_EXE, "via": "path"})
    spawned = []

    class _FakeProc:
        pid = 777

    def fake_spawn(exe_path, file_path):
        spawned.append((exe_path, file_path))
        return _FakeProc()

    monkeypatch.setattr(indesign, "_spawn_with_file", fake_spawn)
    mcp = _register()

    result = mcp.tools["indesign_open_file"](ALLOWED_FILE)

    assert result["ok"] and result["route"] == "exe"
    assert result["exe"] == FAKE_EXE and result["pid"] == 777
    assert result["com_error"] == "COM unavailable"
    assert spawned == [(FAKE_EXE, str(Path(ALLOWED_FILE).resolve()))]
    assert len(FakeComApp.instances) == 1  # COM was attempted first


def test_open_file_com_error_and_not_installed_returns_err(monkeypatch):
    FakeComApp.result = {"ok": False, "error": "COM unavailable"}
    monkeypatch.setattr(indesign, "ComApp", FakeComApp)
    monkeypatch.setattr(indesign, "_detect_exe",
                        lambda: {"ok": True, "installed": False, "path": None})
    _no_spawn_allowed(monkeypatch)
    mcp = _register()

    result = mcp.tools["indesign_open_file"](ALLOWED_FILE)

    assert result["ok"] is False
    assert "COM unavailable" in result["error"]


# ------------------------------------------------------- open: disallowed

def test_open_file_disallowed_path_attempts_nothing(monkeypatch):
    monkeypatch.setattr(indesign, "ComApp", FakeComApp)
    monkeypatch.setattr(indesign, "_detect_exe",
                        lambda: pytest.fail("must not detect for disallowed paths"))
    _no_spawn_allowed(monkeypatch)
    mcp = _register()

    result = mcp.tools["indesign_open_file"](DISALLOWED_FILE)

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert FakeComApp.instances == []  # COM never even constructed
