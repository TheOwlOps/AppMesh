"""Hermetic tests for the Adobe Audition adapter (adapters/apps/audition.py).

No real Audition is probed or launched: detection is monkeypatched in
the audition module namespace (``adapter.detect_app``), process
spawning on the adapter's ``_spawn``, and the path allowlist is
redirected to a tmp dir.
"""
from __future__ import annotations

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import audition as adapter

EXE = r"C:\Program Files\Adobe\Adobe Audition 2025\Adobe Audition.exe"


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
    """Patch detect_app in the audition module namespace."""
    record = {
        "ok": True,
        "id": "audition",
        "name": "Adobe Audition",
        "category": "Video & Audio",
        "strategy": "plugin",
        "prog_id": None,
        "installed": True,
        "path": EXE,
        "via": "path",
    }
    record.update(overrides)
    monkeypatch.setattr(adapter, "detect_app", lambda app_id: dict(record))
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
    assert len(tools) >= 3
    assert {"audition_status", "audition_launch",
            "audition_open_file"} <= set(tools)


def test_catalog_entry_matches_adapter_assumptions():
    app = apps_catalog.get_app("audition")
    assert app["ok"] and app["app"]["name"] == "Adobe Audition"
    assert app["app"]["strategy"] == "plugin"
    assert app["app"]["prog_id"] is None
    assert app["app"]["exe_names"] == ["Adobe Audition.exe"]
    assert adapter.APP_ID == "audition"


# ------------------------------------------------------------------ status

def test_status_with_detect_app_patched_in_adapter_namespace(tools, monkeypatch):
    _patch_detect(monkeypatch)

    result = tools["audition_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "audition"
    assert result["installed"] is True
    assert result["path"] == EXE
    assert result["via"] == "path"
    # Strategy / name come from the real catalog record.
    assert result["strategy"] == "plugin"
    assert result["name"] == "Adobe Audition"


def test_status_not_installed(tools, monkeypatch):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)

    result = tools["audition_status"]()

    assert result["ok"] is True
    assert result["installed"] is False and result["path"] is None


# ------------------------------------------------------- build_open_command

def test_build_open_command_is_exact():
    assert adapter.build_open_command(EXE, "take1.wav") == [EXE, "take1.wav"]
    assert adapter.build_open_command("Adobe Audition.exe", r"C:\audio\session.sesx") == [
        "Adobe Audition.exe", r"C:\audio\session.sesx"]


# ----------------------------------------------------------------- open_file

def test_open_file_spawns_exe_with_file(tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)
    media = allowed_tmp / "take1.wav"
    media.write_bytes(b"RIFF")

    result = tools["audition_open_file"](str(media))

    assert result["ok"] is True and result["opened"] is True
    assert result["pid"] == FakeProc.pid
    assert result["exe"] == EXE
    assert result["path"] == str(media.resolve())
    assert result["argv"] == [EXE, str(media.resolve())]
    assert calls == [[EXE, str(media.resolve())]]


def test_open_file_accepts_session_and_other_audio_types(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    for name in ("session.sesx", "song.mp3", "master.aif", "master.aiff"):
        result = tools["audition_open_file"](str(allowed_tmp / name))
        assert result["ok"] is True, name
    assert len(calls) == 4


def test_open_file_missing_exe_returns_err_and_never_spawns(
        tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch, installed=False, path=None, via=None)
    calls = _patch_spawn(monkeypatch)

    result = tools["audition_open_file"](str(allowed_tmp / "take1.wav"))

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert calls == []


def test_open_file_rejects_path_outside_allowlist(tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["audition_open_file"]("/etc/passwd.wav")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_open_file_rejects_unsupported_extension(tools, monkeypatch, allowed_tmp):
    _patch_detect(monkeypatch)
    calls = _patch_spawn(monkeypatch)

    result = tools["audition_open_file"](str(allowed_tmp / "notes.txt"))

    assert result["ok"] is False
    assert "Unsupported file type" in result["error"]
    assert calls == []


def test_open_file_detect_failure_returns_err(tools, monkeypatch, allowed_tmp):
    monkeypatch.setattr(adapter, "detect_app",
                        lambda app_id: {"ok": False, "error": "boom"})
    calls = _patch_spawn(monkeypatch)

    result = tools["audition_open_file"](str(allowed_tmp / "take1.wav"))

    assert result == {"ok": False, "error": "boom"}
    assert calls == []


def test_tools_never_raise_on_empty_path(tools):
    result = tools["audition_open_file"]("")
    assert isinstance(result, dict) and result["ok"] is False
