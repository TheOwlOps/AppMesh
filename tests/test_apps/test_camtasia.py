"""Hermetic tests for the Camtasia adapter (adapters/apps/camtasia.py).

No real Camtasia, no real windows, no real processes: detection goes
through a monkeypatched ``detect_app`` in the camtasia module
namespace, spawning through the module-level ``camtasia._spawn``
helper, and status/launch/focus through faked ``_common`` helpers.
"""
from __future__ import annotations

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import _common, camtasia
from desktop_mcp.adapters.apps.camtasia import build_open_command, register

FAKE_EXE = r"C:\Program Files\TechSmith\Camtasia\CamtasiaStudio.exe"


class FakeMCP:
    """Minimal FastMCP stand-in capturing tools by function name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeProc:
    pid = 4242


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    register(mcp)
    return mcp.tools


def _allowed_file(name: str = "project.cmproj") -> str:
    """A path under an allowed root (check_path_allowed must pass).

    No file needs to exist: the allowlist check only resolves paths.
    """
    return str(core.ALLOWED_ROOTS[0] / name)


def _fake_detect(installed: bool, exe: str | None = FAKE_EXE):
    def _detect(app_id):
        assert app_id == "camtasia"
        return {"ok": True, "app_id": app_id, "id": app_id,
                "name": "Camtasia", "installed": installed,
                "path": exe if installed else None,
                "via": "path" if installed else None}
    return _detect


@pytest.fixture()
def spawned(monkeypatch):
    """Patch camtasia._spawn to record argv instead of launching."""
    calls = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(camtasia, "_spawn", fake_spawn)
    return calls


# ------------------------------------------------------------ registration

def test_register_binds_at_least_three_tools_including_open_file_and_focus(tools):
    assert len(tools) >= 3
    assert {"camtasia_status", "camtasia_launch", "camtasia_open_file",
            "camtasia_focus"} <= set(tools)


def test_app_id_matches_catalog():
    assert camtasia.APP_ID == "camtasia"


# ------------------------------------------------------------- build command

def test_build_open_command_is_exactly_exe_then_path():
    assert build_open_command(FAKE_EXE, "C:/Projects/demo.cmproj") == [
        FAKE_EXE, "C:/Projects/demo.cmproj"]
    assert build_open_command("CamtasiaStudio.exe", "a.trec") == [
        "CamtasiaStudio.exe", "a.trec"]


# ------------------------------------------------------------------- status

def test_status_with_detect_app_monkeypatched_in_camtasia_namespace(tools, monkeypatch):
    """Patching camtasia.detect_app must be reflected in camtasia_status:
    status delegates to the real catalog_status, which reads detection
    through the catalog module the adapter swaps the patched fn into."""
    monkeypatch.setattr(camtasia, "detect_app", _fake_detect(installed=True))
    result = tools["camtasia_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "camtasia"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    # NB: _common.catalog_status reads "strategy" off the get_app()
    # wrapper dict, so it surfaces as None there — assert the key is
    # present rather than a value the shared helper does not produce.
    assert "strategy" in result


def test_status_not_installed_with_monkeypatched_detect_app(tools, monkeypatch):
    monkeypatch.setattr(camtasia, "detect_app",
                        _fake_detect(installed=False, exe=None))
    result = tools["camtasia_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


def test_status_delegates_to_common_catalog_status(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "camtasia", "installed": True}
    calls = []

    def fake_status(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    assert tools["camtasia_status"]() is sentinel
    assert calls == ["camtasia"]


def test_launch_delegates_to_common_catalog_launch(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "camtasia", "launched": True}
    calls = []

    def fake_launch(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    assert tools["camtasia_launch"]() is sentinel
    assert calls == ["camtasia"]


# ----------------------------------------------------------------- open_file

def test_open_file_spawns_exe_with_file_and_returns_ok(tools, monkeypatch, spawned, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    monkeypatch.setattr(camtasia, "detect_app", _fake_detect(installed=True))

    target = _allowed_file()
    result = tools["camtasia_open_file"](target)

    assert spawned == [[FAKE_EXE, target]]
    assert result["ok"] is True
    assert result["opened"] is True and result["launched"] is True
    assert result["exe"] == FAKE_EXE
    assert result["path"] == target
    assert result["pid"] == 4242
    assert result["argv"] == [FAKE_EXE, target]
    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists() and "camtasia_open_file" in audit_file.read_text(encoding="utf-8")


def test_open_file_missing_exe_returns_err_and_never_spawns(tools, monkeypatch, spawned):
    monkeypatch.setattr(camtasia, "detect_app",
                        _fake_detect(installed=False, exe=None))
    result = tools["camtasia_open_file"](_allowed_file("recording.trec"))
    assert result["ok"] is False
    assert "not installed" in result["error"].lower()
    assert spawned == []


def test_open_file_detect_failure_is_returned_not_raised(tools, monkeypatch, spawned):
    monkeypatch.setattr(camtasia, "detect_app",
                        lambda app_id: {"ok": False, "error": "Unknown app id: 'camtasia'."})
    result = tools["camtasia_open_file"](_allowed_file())
    assert result["ok"] is False and "Unknown app id" in result["error"]
    assert spawned == []


@pytest.mark.parametrize("bad", ["/etc/passwd.cmproj", "/etc/recording.trec"])
def test_open_file_rejects_paths_under_etc(tools, monkeypatch, bad):
    monkeypatch.setattr(camtasia, "detect_app", _fake_detect(installed=True))
    monkeypatch.setattr(camtasia, "_spawn",
                        lambda argv: pytest.fail("must not spawn for a disallowed path"))
    result = tools["camtasia_open_file"](bad)
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_open_file_spawn_failure_is_graceful(tools, monkeypatch):
    monkeypatch.setattr(camtasia, "detect_app", _fake_detect(installed=True))

    def boom(argv):
        raise OSError("cannot start")

    monkeypatch.setattr(camtasia, "_spawn", boom)
    result = tools["camtasia_open_file"](_allowed_file())
    assert result["ok"] is False
    assert "cannot start" in result["error"]


# --------------------------------------------------------------------- focus

def test_focus_delegates_to_focus_window_with_catalog_name(tools, monkeypatch):
    sentinel = {"ok": True, "focused": True, "window": "Camtasia"}
    calls = []

    def fake_focus(title_contains):
        calls.append(title_contains)
        return sentinel

    monkeypatch.setattr(_common, "focus_window", fake_focus)
    assert tools["camtasia_focus"]() is sentinel
    assert calls == ["Camtasia"]


def test_focus_graceful_error_passthrough(tools, monkeypatch):
    monkeypatch.setattr(_common, "focus_window",
                        lambda title: {"ok": False, "error": "not running Windows"})
    result = tools["camtasia_focus"]()
    assert result["ok"] is False and "not running Windows" in result["error"]
