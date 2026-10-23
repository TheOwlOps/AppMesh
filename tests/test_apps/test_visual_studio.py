"""Hermetic tests for the Visual Studio adapter.

No real COM, no real devenv: a FakeDTE records the DTE calls, the devenv
spawn is monkeypatched, and catalog status/launch are stubbed at the
adapter's namespace so delegation can be asserted exactly.
"""
from __future__ import annotations

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import visual_studio as vs
from desktop_mcp.adapters.apps._common import ComApp


class FakeMcp:
    """Minimal FastMCP stand-in: captures tools registered via @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class FakeSolution:
    def __init__(self):
        self.opened = []

    def Open(self, path):  # noqa: N802 — mirrors the DTE API name
        self.opened.append(path)


class FakeItemOperations:
    def __init__(self):
        self.opened_files = []

    def OpenFile(self, path):  # noqa: N802 — mirrors the DTE API name
        self.opened_files.append(path)


class FakeDTE:
    """Stands in for the VisualStudio.DTE COM object."""

    def __init__(self):
        self.Solution = FakeSolution()
        self.ItemOperations = FakeItemOperations()


@pytest.fixture()
def tools():
    mcp = FakeMcp()
    vs.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed(tmp_path, monkeypatch):
    """Make tmp_path an allowed root for the duration of a test."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path])
    return tmp_path


@pytest.fixture()
def fake_dte(monkeypatch):
    """Point the adapter's lazy ComApp at a FakeDTE."""
    dte = FakeDTE()
    monkeypatch.setattr(vs, "_com", ComApp(vs.PROG_ID, factory=lambda: dte))
    return dte


def test_register_binds_all_tools(tools):
    assert {
        "visual_studio_status",
        "visual_studio_launch",
        "visual_studio_open_solution",
        "visual_studio_open_file",
    } <= set(tools)


def test_status_delegates_to_catalog(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "visual-studio", "marker": "status"}
    seen = []
    monkeypatch.setattr(
        vs, "catalog_status", lambda app_id: seen.append(app_id) or sentinel)
    assert tools["visual_studio_status"]() is sentinel
    assert seen == ["visual-studio"]


def test_launch_delegates_to_catalog(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "visual-studio", "launched": True}
    seen = []
    monkeypatch.setattr(
        vs, "catalog_launch", lambda app_id: seen.append(app_id) or sentinel)
    assert tools["visual_studio_launch"]() is sentinel
    assert seen == ["visual-studio"]


def test_open_solution_calls_dte_solution_open(tools, allowed, fake_dte):
    sln = allowed / "App.sln"
    sln.write_text("", encoding="utf-8")
    result = tools["visual_studio_open_solution"](str(sln))
    assert result["ok"] is True
    assert result["via"] == "com"
    assert result["path"] == str(sln)
    assert fake_dte.Solution.opened == [str(sln)]
    assert fake_dte.ItemOperations.opened_files == []


def test_open_file_calls_dte_item_operations_open_file(tools, allowed, fake_dte):
    cs = allowed / "Program.cs"
    cs.write_text("class C {}", encoding="utf-8")
    result = tools["visual_studio_open_file"](str(cs))
    assert result["ok"] is True
    assert result["via"] == "com"
    assert result["path"] == str(cs)
    assert fake_dte.ItemOperations.opened_files == [str(cs)]
    assert fake_dte.Solution.opened == []


def _break_com(monkeypatch):
    def factory():
        raise RuntimeError("COM unavailable (test double)")

    monkeypatch.setattr(vs, "_com", ComApp(vs.PROG_ID, factory=factory))


def _record_spawn(monkeypatch):
    calls = []

    class FakeProc:
        pid = 4242

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(vs, "_spawn", fake_spawn)
    return calls


def test_open_solution_falls_back_to_devenv(tools, allowed, monkeypatch):
    _break_com(monkeypatch)
    spawns = _record_spawn(monkeypatch)
    sln = allowed / "Fallback.sln"
    sln.write_text("", encoding="utf-8")
    result = tools["visual_studio_open_solution"](str(sln))
    assert result["ok"] is True
    assert result["via"] == "devenv"
    assert len(spawns) == 1
    assert spawns[0][0].endswith("devenv.exe")
    assert spawns[0][1] == str(sln)


def test_open_file_falls_back_to_devenv(tools, allowed, monkeypatch):
    _break_com(monkeypatch)
    spawns = _record_spawn(monkeypatch)
    cs = allowed / "Fallback.cs"
    cs.write_text("", encoding="utf-8")
    result = tools["visual_studio_open_file"](str(cs))
    assert result["ok"] is True
    assert result["via"] == "devenv"
    assert len(spawns) == 1
    assert spawns[0][1] == str(cs)


def test_fallback_failure_returns_err_not_raise(tools, allowed, monkeypatch):
    _break_com(monkeypatch)

    def bad_spawn(argv):
        raise OSError("devenv missing")

    monkeypatch.setattr(vs, "_spawn", bad_spawn)
    sln = allowed / "Nope.sln"
    sln.write_text("", encoding="utf-8")
    result = tools["visual_studio_open_solution"](str(sln))
    assert result["ok"] is False
    assert "devenv missing" in result["error"]


def test_disallowed_path_rejected_before_com_and_spawn(tools, monkeypatch):
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return FakeDTE()

    monkeypatch.setattr(vs, "_com", ComApp(vs.PROG_ID, factory=factory))
    spawns = _record_spawn(monkeypatch)

    for name in ("visual_studio_open_solution", "visual_studio_open_file"):
        result = tools[name]("/etc/passwd.sln")
        assert result["ok"] is False
        assert "not allowed" in result["error"].lower()

    assert factory_calls == []  # COM never touched
    assert spawns == []  # devenv never spawned


def test_missing_dte_chain_falls_back(tools, allowed, monkeypatch):
    """A DTE object without Solution/ItemOperations must not raise."""

    class BareDTE:
        pass

    monkeypatch.setattr(
        vs, "_com", ComApp(vs.PROG_ID, factory=lambda: BareDTE()))
    spawns = _record_spawn(monkeypatch)
    sln = allowed / "Bare.sln"
    sln.write_text("", encoding="utf-8")
    result = tools["visual_studio_open_solution"](str(sln))
    assert result["ok"] is True
    assert result["via"] == "devenv"
    assert spawns and spawns[0][1] == str(sln)
