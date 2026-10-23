"""Hermetic tests for the DaVinci Resolve adapter.

No real Resolve install is probed or launched: detection goes through a
fake ``detect_app`` monkeypatched into the davinci_resolve module
namespace, and the fusionscript probe is driven with fake import
functions. The only real filesystem touch is a synthetic ``.drp`` file
under ``tmp_path`` for the open-file success case, with spawning faked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp.adapters.apps import davinci_resolve as dr
from desktop_mcp.adapters.apps.davinci_resolve import _probe_scripting


# ---------------------------------------------------------------- fake mcp

class _FakeMCP:
    """Minimal FastMCP stand-in: captures @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _register():
    mcp = _FakeMCP()
    dr.register(mcp)
    return mcp


# ---------------------------------------------------------------- fake data

def _fake_detect_installed(app_id):
    assert app_id == dr.APP_ID
    return {
        "ok": True,
        "id": app_id,
        "name": "DaVinci Resolve",
        "category": "Video & Audio",
        "strategy": "plugin",
        "prog_id": None,
        "installed": True,
        "path": "C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe",
        "via": "path",
    }


class _FakeResolve:
    def __str__(self):
        return "FakeResolveInstance"


class _FakeFusionModule:
    """Fake fusionscript module whose scriptapp returns a live object."""

    def scriptapp(self, name):
        assert name == "Resolve"
        return _FakeResolve()


# ------------------------------------------------------------ registration

def test_register_binds_at_least_three_tools():
    mcp = _register()
    assert len(mcp.tools) >= 3
    assert "davinci_resolve_status" in mcp.tools
    assert "davinci_resolve_launch" in mcp.tools
    assert "davinci_resolve_scripting_status" in mcp.tools
    assert "davinci_resolve_open_project_file" in mcp.tools


def test_app_id_matches_catalog():
    assert dr.APP_ID == "davinci-resolve"


# ------------------------------------------------------------------- status

def test_status_enriched_with_scripting_probe(monkeypatch):
    # Detection is faked in the davinci_resolve module namespace — the
    # exact patch point the adapter honours via _using_module_detect().
    monkeypatch.setattr(dr, "detect_app", _fake_detect_installed)
    monkeypatch.setattr(
        dr, "_probe_scripting",
        lambda *a, **k: {
            "ok": True,
            "scripting_available": False,
            "scripting_reason": "fake: Resolve not running",
            "resolve_connected": False,
            "resolve": None,
        },
    )
    mcp = _register()
    result = mcp.tools["davinci_resolve_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "davinci-resolve"
    assert result["installed"] is True
    assert result["path"].endswith("Resolve.exe")
    assert result["strategy"] == "plugin"
    # Enrichment fields from the scripting probe are merged in.
    assert result["scripting_available"] is False
    assert result["scripting_reason"] == "fake: Resolve not running"
    assert result["resolve_connected"] is False


def test_status_with_scripting_available(monkeypatch):
    monkeypatch.setattr(dr, "detect_app", _fake_detect_installed)
    monkeypatch.setattr(
        dr, "_probe_scripting",
        lambda *a, **k: {
            "ok": True,
            "scripting_available": True,
            "scripting_reason": None,
            "resolve_connected": True,
            "resolve": "FakeResolveInstance",
        },
    )
    result = _register().tools["davinci_resolve_status"]()
    assert result["ok"] is True
    assert result["scripting_available"] is True
    assert result["resolve_connected"] is True


def test_launch_delegates_to_catalog(monkeypatch):
    monkeypatch.setattr(
        dr, "catalog_launch",
        lambda app_id: {"ok": True, "app_id": app_id, "launched": True,
                        "pid": 1234},
    )
    result = _register().tools["davinci_resolve_launch"]()
    assert result["ok"] is True
    assert result["app_id"] == "davinci-resolve"
    assert result["launched"] is True


def test_scripting_status_tool_returns_probe_dict(monkeypatch):
    monkeypatch.setattr(
        dr, "_probe_scripting",
        lambda *a, **k: {
            "ok": True,
            "scripting_available": True,
            "scripting_reason": None,
            "resolve_connected": True,
            "resolve": "FakeResolveInstance",
        },
    )
    result = _register().tools["davinci_resolve_scripting_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "davinci-resolve"
    assert result["scripting_available"] is True
    assert result["resolve_connected"] is True


# ------------------------------------------------------------- probe helper

def test_probe_scripting_fake_import_succeeds():
    result = _probe_scripting(import_fn=lambda name: _FakeFusionModule())
    assert result["ok"] is True
    assert "error" not in result
    assert result["scripting_available"] is True
    assert result["resolve_connected"] is True
    assert result["resolve"] == "FakeResolveInstance"


def test_probe_scripting_fake_import_no_arg_callable():
    # Fakes taking no argument at all must also work.
    result = _probe_scripting(import_fn=lambda: _FakeFusionModule())
    assert result["scripting_available"] is True


def test_probe_scripting_import_error_is_err_free_dict():
    def bad_import(name):
        raise ImportError("No module named 'fusionscript'")

    result = _probe_scripting(import_fn=bad_import)
    # Never raises and never returns an err dict: availability is data.
    assert result["ok"] is True
    assert "error" not in result
    assert result["scripting_available"] is False
    assert result["resolve_connected"] is False
    assert result["scripting_reason"]
    assert "fusionscript" in result["scripting_reason"]


def test_probe_scripting_scriptapp_returning_none_is_unavailable():
    class _NoneModule:
        def scriptapp(self, name):
            return None

    result = _probe_scripting(import_fn=lambda name: _NoneModule())
    assert result["ok"] is True
    assert result["scripting_available"] is False
    assert result["resolve_connected"] is False
    assert "None" in result["scripting_reason"]


def test_probe_scripting_scriptapp_raising_is_unavailable():
    class _BoomModule:
        def scriptapp(self, name):
            raise RuntimeError("connection refused")

    result = _probe_scripting(import_fn=lambda name: _BoomModule())
    assert result["ok"] is True
    assert result["scripting_available"] is False
    assert "connection refused" in result["scripting_reason"]


def test_probe_scripting_module_without_scriptapp_is_unavailable():
    result = _probe_scripting(import_fn=lambda name: object())
    assert result["ok"] is True
    assert result["scripting_available"] is False
    assert "scriptapp" in result["scripting_reason"]


def test_probe_scripting_real_environment_never_raises():
    # On the test machine fusionscript is (almost certainly) absent, so
    # this exercises the default import path end-to-end: whatever it
    # finds, the result must be a clean dict with a bool availability.
    result = _probe_scripting()
    assert result["ok"] is True
    assert isinstance(result["scripting_available"], bool)
    assert isinstance(result["resolve_connected"], bool)


# -------------------------------------------------------------- open file

def test_open_project_file_rejects_path_outside_allowlist():
    result = _register().tools["davinci_resolve_open_project_file"](
        "/etc/passwd.drp")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_open_project_file_rejects_wrong_extension(monkeypatch, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("not a project", encoding="utf-8")
    monkeypatch.setattr(dr, "check_path_allowed", lambda p: Path(p).resolve())
    monkeypatch.setattr(dr, "detect_app", _fake_detect_installed)
    result = dr.davinci_resolve_open_project_file(str(target))
    assert result["ok"] is False
    assert ".drp" in result["error"]


def test_open_project_file_success_with_fakes(monkeypatch, tmp_path):
    target = tmp_path / "My Project.drp"
    target.write_bytes(b"fake drp payload")
    monkeypatch.setattr(dr, "check_path_allowed", lambda p: Path(p).resolve())
    monkeypatch.setattr(dr, "detect_app", _fake_detect_installed)
    spawned = {}

    class _FakeProc:
        pid = 9999

    def _fake_spawn(exe, file_path):
        spawned["exe"] = exe
        spawned["file"] = file_path
        return _FakeProc()

    monkeypatch.setattr(dr, "_spawn_project", _fake_spawn)
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    result = _register().tools["davinci_resolve_open_project_file"](
        str(target))
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["path"] == str(target.resolve())
    assert result["pid"] == 9999
    assert spawned["exe"].endswith("Resolve.exe")
    assert spawned["file"] == str(target.resolve())


def test_open_project_file_not_installed(monkeypatch, tmp_path):
    target = tmp_path / "My Project.drp"
    target.write_bytes(b"fake drp payload")
    monkeypatch.setattr(dr, "check_path_allowed", lambda p: Path(p).resolve())

    def _fake_detect_missing(app_id):
        result = _fake_detect_installed(app_id)
        result["installed"] = False
        result["path"] = None
        return result

    monkeypatch.setattr(dr, "detect_app", _fake_detect_missing)
    result = dr.davinci_resolve_open_project_file(str(target))
    assert result["ok"] is False
    assert "not installed" in result["error"]
