"""Hermetic tests for the REAPER adapter (adapters/apps/reaper.py).

No real REAPER is probed or launched: detection is monkeypatched on
``apps_catalog`` (and, for status/launch, on ``_common``) and process
spawning on the adapter's ``_spawn``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common
from desktop_mcp.adapters.apps import reaper as adapter

EXE = r"C:\Program Files\REAPER (x64)\reaper.exe"


class FakeMCP:
    """Minimal FastMCP stand-in: captures @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeProc:
    pid = 4242


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    adapter.register(mcp)
    return mcp.tools


@pytest.fixture()
def allowed_tmp(monkeypatch, tmp_path):
    """Make tmp_path the only allowed root, and keep audit writes there."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    return tmp_path


def _patch_detect(monkeypatch, **overrides):
    record = {
        "ok": True,
        "id": "reaper",
        "name": "REAPER",
        "category": "Video & Audio",
        "strategy": "plugin",
        "prog_id": None,
        "installed": True,
        "path": EXE,
        "via": "path",
    }
    record.update(overrides)
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id: dict(record))
    return record


def _patch_spawn(monkeypatch):
    calls = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(adapter, "_spawn", fake_spawn)
    return calls


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools(tools):
    assert set(tools) == {
        "reaper_status",
        "reaper_launch",
        "reaper_open_file",
        "reaper_open_project",
    }


def test_catalog_entry_is_plugin_strategy():
    app = apps_catalog.get_app("reaper")
    assert app["ok"] and app["app"]["strategy"] == "plugin"
    assert app["app"]["exe_names"] == ["reaper.exe"]


# ------------------------------------------------------------------ status / launch

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "catalog_status",
                        lambda app_id: seen.append(app_id) or {"ok": True, "sentinel": 1})
    result = tools["reaper_status"]()
    assert result == {"ok": True, "sentinel": 1}
    assert seen == ["reaper"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "catalog_launch",
                        lambda app_id: seen.append(app_id) or {"ok": True, "launched": True})
    result = tools["reaper_launch"]()
    assert result == {"ok": True, "launched": True}
    assert seen == ["reaper"]


# ------------------------------------------------------------------ argv builder

def test_build_open_argv_is_exact_and_pure():
    assert adapter.build_open_argv(EXE, "song.rpp") == [EXE, "song.rpp"]
    assert adapter.build_open_argv(Path("reaper.exe"), Path("a.wav")) == [
        "reaper.exe", "a.wav"]
    # The shared alias and the project builder agree exactly.
    assert adapter.build_argv(EXE, "x") == [EXE, "x"]
    assert adapter.build_project_argv(EXE, "song.rpp") == [EXE, "song.rpp"]


def test_spawn_delegates_to_popen(monkeypatch):
    seen = []
    monkeypatch.setattr(adapter, "_popen",
                        lambda argv: seen.append(list(argv)) or FakeProc())
    proc = adapter._spawn([EXE, "song.rpp"])
    assert proc.pid == FakeProc.pid
    assert seen == [[EXE, "song.rpp"]]


# ------------------------------------------------------------------ open_file

def test_open_file_spawns_exe_with_file(tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    media = allowed_tmp / "take.wav"
    media.write_bytes(b"RIFF")

    result = tools["reaper_open_file"](str(media))

    assert result["ok"] is True
    assert result["opened"] is True and result["project"] is False
    assert result["pid"] == FakeProc.pid
    assert result["exe"] == EXE
    assert calls == [[EXE, str(media.resolve())]]
    assert result["argv"] == [EXE, str(media.resolve())]
    assert result["path"] == str(media.resolve())


def test_open_file_disallowed_path_rejected_before_spawn(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["reaper_open_file"]("/etc/passwd")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_open_file_missing_exe_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)
    calls = _patch_spawn(monkeypatch)
    media = allowed_tmp / "take.wav"

    result = tools["reaper_open_file"](str(media))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert calls == []


def test_open_file_detect_failure_returns_err(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id: {"ok": False, "error": "boom"})
    calls = _patch_spawn(monkeypatch)

    result = tools["reaper_open_file"](str(allowed_tmp / "take.wav"))

    assert result == {"ok": False, "error": "boom"}
    assert calls == []


# ------------------------------------------------------------------ open_project

def test_open_project_spawns_exe_with_rpp(tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "song.rpp"
    project.write_text("REAPER_PROJECT", encoding="utf-8")

    result = tools["reaper_open_project"](str(project))

    assert result["ok"] is True
    assert result["opened"] is True and result["project"] is True
    assert result["pid"] == FakeProc.pid
    assert calls == [[EXE, str(project.resolve())]]
    assert result["argv"] == [EXE, str(project.resolve())]


def test_open_project_accepts_uppercase_rpp(tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "SONG.RPP"

    result = tools["reaper_open_project"](str(project))

    assert result["ok"] is True
    assert calls == [[EXE, str(project.resolve())]]


def test_open_project_non_rpp_rejected_before_spawn(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    not_a_project = allowed_tmp / "notes.txt"

    result = tools["reaper_open_project"](str(not_a_project))

    assert result["ok"] is False
    assert ".rpp" in result["error"]
    assert calls == []


def test_open_project_disallowed_path_rejected_before_spawn(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["reaper_open_project"]("/etc/evil.rpp")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_open_project_missing_exe_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)
    calls = _patch_spawn(monkeypatch)
    project = allowed_tmp / "song.rpp"

    result = tools["reaper_open_project"](str(project))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert calls == []


def test_tools_never_raise_on_empty_path(tools):
    for name in ("reaper_open_file", "reaper_open_project"):
        result = tools[name]("")
        assert isinstance(result, dict) and result["ok"] is False
