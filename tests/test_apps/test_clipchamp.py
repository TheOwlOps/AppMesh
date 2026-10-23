"""Hermetic tests for the Clipchamp adapter — no real Clipchamp, no real spawn.

Detection is monkeypatched on ``apps_catalog`` (the adapter and
``_common`` both look it up there at call time), the adapter's
``_spawn`` is replaced with a recorder so argv can be asserted exactly,
and ``_common.focus_window`` is monkeypatched for the focus tool.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common
from desktop_mcp.adapters.apps import clipchamp as adapter
from desktop_mcp.adapters.apps.clipchamp import build_open_argv

FAKE_EXE = r"C:\Users\Public\Apps\Clipchamp\Clipchamp.exe"


class FakeMCP:
    """Minimal stand-in for FastMCP: captures tools by function name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class FakeProc:
    pid = 4321


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    adapter.register(mcp)
    return mcp.tools


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / ".desktop-mcp"))


@pytest.fixture()
def allowed_tmp(monkeypatch, tmp_path):
    """Make tmp_path the only allowed root for check_path_allowed."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


def _patch_detect(monkeypatch, **overrides):
    record = {
        "ok": True,
        "id": "clipchamp",
        "name": "Clipchamp",
        "category": "Video & Audio",
        "strategy": "ui",
        "prog_id": None,
        "installed": True,
        "path": FAKE_EXE,
        "via": "path",
    }
    record.update(overrides)

    def fake_detect(app_id, which_fn=None):
        assert app_id == "clipchamp"
        return dict(record)

    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect)
    return record


def _patch_spawn(monkeypatch):
    calls = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(adapter, "_spawn", fake_spawn)
    return calls


# ------------------------------------------------------------------ register

def test_register_binds_all_tools(tools):
    assert set(tools) == {
        "clipchamp_status",
        "clipchamp_launch",
        "clipchamp_open_file",
        "clipchamp_focus",
    }


# ------------------------------------------------------------------ status / launch

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "catalog_status",
                        lambda app_id: seen.append(app_id) or {"ok": True, "sentinel": 1})
    result = tools["clipchamp_status"]()
    assert result == {"ok": True, "sentinel": 1}
    assert seen == ["clipchamp"]


def test_status_reports_catalog_detection(tools, monkeypatch):
    _patch_detect(monkeypatch)
    result = tools["clipchamp_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "clipchamp"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["via"] == "path"


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "catalog_launch",
                        lambda app_id: seen.append(app_id) or {"ok": True, "launched": True})
    result = tools["clipchamp_launch"]()
    assert result == {"ok": True, "launched": True}
    assert seen == ["clipchamp"]


# ------------------------------------------------------------------ argv builder

def test_build_open_argv_exact():
    assert build_open_argv(FAKE_EXE, r"D:\Videos\clip.mp4") == [
        FAKE_EXE, r"D:\Videos\clip.mp4"]
    # Path inputs are stringified, order is [exe, media], nothing extra.
    assert build_open_argv(Path("Clipchamp.exe"), Path("movie.mp4")) == [
        "Clipchamp.exe", "movie.mp4"]


# ------------------------------------------------------------------ open_file

def test_open_file_spawns_exe_with_file_as_argument(tools, monkeypatch, allowed_tmp):
    det = _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    media = allowed_tmp / "holiday.mp4"
    media.write_bytes(b"fake video")

    result = tools["clipchamp_open_file"](str(media))

    assert result["ok"] is True
    assert result["opened"] is True
    assert result["pid"] == FakeProc.pid
    assert calls == [[det["path"], str(media.resolve())]]
    assert result["argv"] == [det["path"], str(media.resolve())]
    assert result["argv"] == build_open_argv(det["path"], media.resolve())
    assert result["path"] == str(media.resolve())
    assert result["exe"] == det["path"]


def test_open_file_missing_exe_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)
    calls = _patch_spawn(monkeypatch)
    media = allowed_tmp / "holiday.mp4"

    result = tools["clipchamp_open_file"](str(media))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert calls == []


def test_open_file_disallowed_path_rejected_before_spawn(tools, monkeypatch):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["clipchamp_open_file"]("/etc/passwd.mp4")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_open_file_detect_failure_returns_err(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id, which_fn=None: {"ok": False, "error": "boom"})
    calls = _patch_spawn(monkeypatch)

    result = tools["clipchamp_open_file"](str(allowed_tmp / "holiday.mp4"))

    assert result == {"ok": False, "error": "boom"}
    assert calls == []


def test_open_file_spawn_failure_returns_err_not_raise(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)

    def boom(argv):
        raise OSError("cannot start process")

    monkeypatch.setattr(adapter, "_spawn", boom)
    result = tools["clipchamp_open_file"](str(allowed_tmp / "holiday.mp4"))
    assert result["ok"] is False
    assert "cannot start process" in result["error"]


# ------------------------------------------------------------------ focus

def test_focus_delegates_to_common_focus_window_with_clipchamp_title(
        tools, monkeypatch):
    seen = []
    monkeypatch.setattr(_common, "focus_window",
                        lambda title: seen.append(title) or {"ok": True, "focused": True})
    result = tools["clipchamp_focus"]()
    assert result == {"ok": True, "focused": True}
    assert seen == ["Clipchamp"]
    # The fragment matches the catalog display name for clipchamp.
    assert seen == [apps_catalog.get_app("clipchamp")["app"]["name"]]


def test_tools_never_raise_on_real_focus_off_windows(tools):
    # Real _common.focus_window on this (non-Windows) test box returns an
    # err dict instead of raising.
    result = tools["clipchamp_focus"]()
    assert isinstance(result, dict)
    assert result["ok"] is False
