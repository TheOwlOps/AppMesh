"""Tests for the Adobe Illustrator per-app adapter.

Hermetic: no real Illustrator, no real COM, no Windows. The COM wrapper
is injected through the adapter's ``make_com_app`` factory seam, the exe
fallback through monkeypatched catalog detection + ``_spawn_with_file``,
and status/launch through monkeypatched ``_common`` catalog helpers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import _common, illustrator

ALLOWED_FILE = str(Path.home() / "art.ai")
DISALLOWED_FILE = "/etc/passwd.ai"
FAKE_EXE = r"C:\Program Files\Adobe\Adobe Illustrator\Illustrator.exe"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeMCP:
    """Minimal stand-in for FastMCP: records tools registered via @mcp.tool()."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeComApp:
    """Stand-in for _common.ComApp returning a canned open_via result."""

    def __init__(self, result: dict):
        self._result = result
        self.open_calls: list = []

    def open_via(self, collection: str, path: str) -> dict:
        self.open_calls.append((collection, path))
        return dict(self._result)


class FakeProc:
    pid = 4242


@pytest.fixture()
def registered():
    mcp = FakeMCP()
    illustrator.register(mcp)
    return mcp.tools


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_binds_at_least_three_tools(registered):
    assert len(registered) >= 3
    for name in ("illustrator_status", "illustrator_launch", "illustrator_open_file"):
        assert name in registered
        assert callable(registered[name])


# --------------------------------------------------------------------------
# illustrator_open_file — COM route
# --------------------------------------------------------------------------


def test_open_file_com_success_route_com(registered, monkeypatch):
    fake = FakeComApp({"ok": True, "opened": True, "document": "art.ai",
                       "path": ALLOWED_FILE})
    monkeypatch.setattr(illustrator, "make_com_app", lambda: fake)
    spawns: list = []
    monkeypatch.setattr(illustrator, "_spawn_with_file",
                        lambda exe, f: spawns.append((exe, f)))

    result = registered["illustrator_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["opened"] is True
    assert fake.open_calls == [("Documents", ALLOWED_FILE)]
    assert spawns == []  # exe fallback must not run when COM succeeds


# --------------------------------------------------------------------------
# illustrator_open_file — exe fallback
# --------------------------------------------------------------------------


def test_open_file_com_error_falls_back_to_exe(registered, monkeypatch):
    fake = FakeComApp({"ok": False, "error": "COM unavailable"})
    monkeypatch.setattr(illustrator, "make_com_app", lambda: fake)
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Adobe Illustrator",
                              "installed": True, "path": FAKE_EXE,
                              "via": "path"})
    spawns: list = []

    def fake_spawn(exe, file_path):
        spawns.append((exe, file_path))
        return FakeProc()

    monkeypatch.setattr(illustrator, "_spawn_with_file", fake_spawn)

    result = registered["illustrator_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "exe"
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4242
    assert result["com_error"] == "COM unavailable"
    assert spawns == [(FAKE_EXE, ALLOWED_FILE)]


def test_open_file_both_routes_fail_returns_err(registered, monkeypatch):
    fake = FakeComApp({"ok": False, "error": "COM unavailable"})
    monkeypatch.setattr(illustrator, "make_com_app", lambda: fake)
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Adobe Illustrator",
                              "installed": False, "path": None, "via": None})

    result = registered["illustrator_open_file"](ALLOWED_FILE)

    assert result["ok"] is False
    assert "COM unavailable" in result["error"]


# --------------------------------------------------------------------------
# illustrator_open_file — allowlist gate
# --------------------------------------------------------------------------


def test_open_file_disallowed_path_touches_nothing(registered, monkeypatch):
    calls = {"factory": 0, "spawn": 0}

    def factory():
        calls["factory"] += 1
        raise AssertionError("COM factory must not be called")

    def spawn(exe, file_path):
        calls["spawn"] += 1
        raise AssertionError("spawn must not be called")

    monkeypatch.setattr(illustrator, "make_com_app", factory)
    monkeypatch.setattr(illustrator, "_spawn_with_file", spawn)

    result = registered["illustrator_open_file"](DISALLOWED_FILE)

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == {"factory": 0, "spawn": 0}


# --------------------------------------------------------------------------
# status / launch delegate to the catalog helpers
# --------------------------------------------------------------------------


def test_status_delegates_to_catalog_helper(registered, monkeypatch):
    seen: list = []

    def fake_status(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "installed": True,
                "path": FAKE_EXE, "strategy": "com",
                "prog_id": "Illustrator.Application"}

    monkeypatch.setattr(_common, "catalog_status", fake_status)

    result = registered["illustrator_status"]()

    assert seen == ["illustrator"]
    assert result["ok"] is True
    assert result["installed"] is True
    assert result["prog_id"] == "Illustrator.Application"


def test_launch_delegates_to_catalog_helper(registered, monkeypatch):
    seen: list = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True,
                "path": FAKE_EXE, "pid": 4242}

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)

    result = registered["illustrator_launch"]()

    assert seen == ["illustrator"]
    assert result["ok"] is True
    assert result["launched"] is True


def test_status_with_detect_nothing_installed(registered, monkeypatch):
    """End-to-end through the real _common helper with detection faked."""
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Adobe Illustrator",
                              "installed": False, "path": None, "via": None})

    result = registered["illustrator_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "illustrator"
    assert result["installed"] is False
    assert result["path"] is None
