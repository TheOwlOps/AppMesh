"""Hermetic tests for the CorelDRAW per-app adapter.

No real CorelDRAW and no real COM: the COM wrapper is replaced with a
fake ``ComApp``, detection/spawn for the exe fallback are monkeypatched
module helpers, and status/launch go through fake catalog helpers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import coreldraw


class _FakeMCP:
    """Captures the tools register() binds, keyed by function name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeComApp:
    """Stand-in for _common.ComApp; behaviour set via class attrs."""

    instances: list = []
    behavior = "ok"  # "ok" | "err"

    def __init__(self, prog_id, factory=None):
        self.prog_id = prog_id
        self.factory = factory
        self.open_calls: list = []
        type(self).instances.append(self)

    def open_via(self, collection, path):
        self.open_calls.append((collection, path))
        if type(self).behavior == "ok":
            return {"ok": True, "opened": True,
                    "document": Path(path).name, "path": path}
        return {"ok": False, "error": "COM unavailable (fake)"}


class _FakeProc:
    pid = 777


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Allowlist = tmp_path only; audit log lands in tmp_path too."""
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    _FakeComApp.instances = []
    _FakeComApp.behavior = "ok"
    yield


def _tools():
    mcp = _FakeMCP()
    coreldraw.register(mcp)
    return mcp.tools


def _install_fake_com(monkeypatch, behavior):
    _FakeComApp.behavior = behavior
    monkeypatch.setattr(coreldraw, "ComApp", _FakeComApp)


def _install_fake_exe(monkeypatch, exe="C:/Program Files/Corel/CorelDRW.exe"):
    """Patch detect + spawn; returns the list of spawn calls."""
    spawn_calls: list = []
    monkeypatch.setattr(coreldraw, "_detect_exe", lambda: exe)

    def fake_spawn(exe_path, file_path):
        spawn_calls.append((exe_path, str(file_path)))
        return _FakeProc()

    monkeypatch.setattr(coreldraw, "_spawn_with_file", fake_spawn)
    return spawn_calls


# ---------------------------------------------------------------- register

def test_register_binds_at_least_three_tools():
    tools = _tools()
    assert len(tools) >= 3
    assert {"coreldraw_status", "coreldraw_launch",
            "coreldraw_open_file"} <= set(tools)


# ------------------------------------------------------------ open via COM

def test_open_file_com_success_route_com(monkeypatch, tmp_path):
    _install_fake_com(monkeypatch, "ok")
    monkeypatch.setattr(
        coreldraw, "_spawn_with_file",
        lambda *a: pytest.fail("exe fallback must not spawn on COM success"))
    tools = _tools()

    target = tmp_path / "poster.cdr"
    result = tools["coreldraw_open_file"](str(target))

    assert result["ok"] is True and result["route"] == "com"
    assert result["opened"] is True
    assert len(_FakeComApp.instances) == 1
    fake = _FakeComApp.instances[0]
    assert fake.prog_id == "CorelDRAW.Application"
    assert fake.open_calls == [("Documents", str(target.resolve()))]


def test_open_file_com_error_falls_back_to_exe(monkeypatch, tmp_path):
    _install_fake_com(monkeypatch, "err")
    spawn_calls = _install_fake_exe(monkeypatch)
    tools = _tools()

    target = tmp_path / "poster.cdr"
    result = tools["coreldraw_open_file"](str(target))

    assert result["ok"] is True and result["route"] == "exe"
    assert result["opened"] is True and result["pid"] == 777
    assert result["exe"] == "C:/Program Files/Corel/CorelDRW.exe"
    assert result["com_error"] == "COM unavailable (fake)"
    assert spawn_calls == [("C:/Program Files/Corel/CorelDRW.exe",
                            str(target.resolve()))]
    # COM really was attempted first, through the Documents collection.
    assert _FakeComApp.instances[0].open_calls == [
        ("Documents", str(target.resolve()))]


def test_open_file_both_routes_fail_returns_err(monkeypatch, tmp_path):
    _install_fake_com(monkeypatch, "err")
    spawn_calls = _install_fake_exe(monkeypatch, exe=None)
    tools = _tools()

    result = tools["coreldraw_open_file"](str(tmp_path / "poster.cdr"))

    assert result["ok"] is False
    assert "COM failed" in result["error"]
    assert "exe fallback failed" in result["error"]
    assert spawn_calls == []


# ------------------------------------------------------------- allowlist

def test_open_file_disallowed_path_attempts_nothing(monkeypatch):
    _install_fake_com(monkeypatch, "ok")
    spawn_calls = _install_fake_exe(monkeypatch)
    tools = _tools()

    result = tools["coreldraw_open_file"]("/etc/passwd.cdr")

    assert result["ok"] is False
    assert "not allowed" in result["error"]
    assert _FakeComApp.instances == [], "COM must not be attempted"
    assert spawn_calls == [], "exe fallback must not be attempted"


# ------------------------------------------------------ status / launch

def test_status_delegates_to_catalog_status(monkeypatch):
    seen = []

    def fake_status(app_id):
        seen.append(app_id)
        return {"ok": True, "sentinel": "status", "app_id": app_id}

    monkeypatch.setattr(coreldraw, "catalog_status", fake_status)
    result = _tools()["coreldraw_status"]()
    assert result == {"ok": True, "sentinel": "status", "app_id": "coreldraw"}
    assert seen == ["coreldraw"]


def test_launch_delegates_to_catalog_launch(monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "sentinel": "launch", "app_id": app_id,
                "launched": True}

    monkeypatch.setattr(coreldraw, "catalog_launch", fake_launch)
    result = _tools()["coreldraw_launch"]()
    assert result["sentinel"] == "launch" and result["launched"] is True
    assert seen == ["coreldraw"]
