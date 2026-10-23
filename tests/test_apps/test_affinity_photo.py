"""Hermetic tests for the Affinity Photo adapter (affinity_photo.py).

No real Affinity Photo and no real windows: detection goes through a
monkeypatched ``apps_catalog.detect_app``, process starts through a
monkeypatched module-level ``_spawn``, and window focus / catalog
delegation through monkeypatched ``_common`` helpers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import _common, affinity_photo


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _register():
    mcp = FakeMCP()
    affinity_photo.register(mcp)
    return mcp.tools


class _FakeProc:
    pid = 4242


# ---------------------------------------------------------------- register

def test_register_binds_at_least_four_prefixed_tools():
    tools = _register()
    assert len(tools) >= 4
    assert all(name.startswith("affinity_photo_") for name in tools)
    assert {
        "affinity_photo_status",
        "affinity_photo_launch",
        "affinity_photo_open_file",
        "affinity_photo_focus",
    } <= set(tools)


# ------------------------------------------------------- status / launch

def test_status_delegates_to_catalog_status_with_hyphen_id(monkeypatch):
    calls = []

    def fake_status(app_id):
        calls.append(app_id)
        return {"ok": True, "app_id": app_id, "via_fake": True}

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    # Also patch via the module's reference to be explicit.
    monkeypatch.setattr(affinity_photo._common, "catalog_status", fake_status)
    tools = _register()
    result = tools["affinity_photo_status"]()
    assert calls == ["affinity-photo"]
    assert result == {"ok": True, "app_id": "affinity-photo", "via_fake": True}


def test_launch_delegates_to_catalog_launch_with_hyphen_id(monkeypatch):
    calls = []

    def fake_launch(app_id):
        calls.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True, "via_fake": True}

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    monkeypatch.setattr(affinity_photo._common, "catalog_launch", fake_launch)
    tools = _register()
    result = tools["affinity_photo_launch"]()
    assert calls == ["affinity-photo"]
    assert result["ok"] is True and result["launched"] is True


# ------------------------------------------------------------------- focus

def test_focus_passthrough(monkeypatch):
    calls = []

    def fake_focus(title_contains):
        calls.append(title_contains)
        return {"ok": True, "focused": True, "window": "Affinity Photo — doc.afphoto"}

    monkeypatch.setattr(_common, "focus_window", fake_focus)
    monkeypatch.setattr(affinity_photo._common, "focus_window", fake_focus)
    tools = _register()
    result = tools["affinity_photo_focus"]()
    assert calls == ["Affinity Photo"]
    assert result == {"ok": True, "focused": True,
                      "window": "Affinity Photo — doc.afphoto"}


# --------------------------------------------------------------- open_file

def _fake_detect_installed(exe):
    def fake_detect(app_id, *args, **kwargs):
        assert app_id == "affinity-photo"
        return {"ok": True, "installed": True, "path": exe, "via": "path",
                "id": "affinity-photo", "name": "Affinity Photo"}
    return fake_detect


def _fake_detect_not_installed(app_id, *args, **kwargs):
    assert app_id == "affinity-photo"
    return {"ok": True, "installed": False, "path": None, "via": None,
            "id": "affinity-photo", "name": "Affinity Photo"}


def test_open_file_launches_exe_with_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    exe = "C:/Program Files/Affinity/Photo/Photo.exe"
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect_installed(exe))
    spawned = []

    def fake_spawn(argv):
        spawned.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(affinity_photo, "_spawn", fake_spawn)
    tools = _register()
    # A path under the user's home is inside the real allowlist.
    target = Path.home() / "photo-work.afphoto"
    expected_path = str(target.expanduser().resolve())
    result = tools["affinity_photo_open_file"](str(target))
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["pid"] == 4242
    assert spawned == [[exe, expected_path]]


def test_open_file_disallowed_path_does_not_spawn(monkeypatch):
    exe = "C:/Program Files/Affinity/Photo/Photo.exe"
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect_installed(exe))
    monkeypatch.setattr(
        affinity_photo, "_spawn",
        lambda argv: pytest.fail("must not spawn for a disallowed path"))
    tools = _register()
    result = tools["affinity_photo_open_file"]("/etc/passwd.afphoto")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_open_file_not_installed_does_not_spawn(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect_not_installed)
    monkeypatch.setattr(
        affinity_photo, "_spawn",
        lambda argv: pytest.fail("must not spawn when not installed"))
    tools = _register()
    target = Path.home() / "photo-work.afphoto"
    result = tools["affinity_photo_open_file"](str(target))
    assert result["ok"] is False
    assert "not installed" in result["error"].lower()
