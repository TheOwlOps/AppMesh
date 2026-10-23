"""Hermetic tests for the Lightroom adapter — no real Lightroom required.

Detection goes through monkeypatched ``apps_catalog.detect_app`` and
spawning through monkeypatched ``lightroom._spawn`` (a fake proc that
only records its argv). Status/launch delegation is checked with fake
``_common`` helpers.
"""
from __future__ import annotations

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import lightroom


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to tmp_path and isolate the audit log."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


FAKE_EXE = "/fake/Lightroom.exe"


def _detect_installed(exe=FAKE_EXE):
    def _detect(app_id, **kwargs):
        assert app_id == "lightroom"
        return {"ok": True, "installed": True, "path": exe,
                "via": "path", "name": "Adobe Lightroom Classic"}
    return _detect


def _detect_not_installed(app_id, **kwargs):
    assert app_id == "lightroom"
    return {"ok": True, "installed": False, "path": None,
            "via": None, "name": "Adobe Lightroom Classic"}


class _FakeProc:
    pid = 4321


def _fake_spawn(calls):
    def spawn(argv):
        calls.append(list(argv))
        return _FakeProc()
    return spawn


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_at_least_three_tools():
    mcp = _FakeMCP()
    names = lightroom.register(mcp)
    assert set(mcp.tools) >= {
        "lightroom_status", "lightroom_launch", "lightroom_open_file",
    }
    assert len(mcp.tools) >= 3
    for name in ("lightroom_status", "lightroom_launch", "lightroom_open_file"):
        assert name in names


# ---------------------------------------------------------------------------
# lightroom_open_file — detect + spawn monkeypatched
# ---------------------------------------------------------------------------

def test_open_file_happy_path_spawns_exe_with_file(allowed, monkeypatch):
    target = allowed / "photo.dng"
    target.write_bytes(b"dng")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed())
    calls = []
    monkeypatch.setattr(lightroom, "_spawn", _fake_spawn(calls))

    mcp = _FakeMCP()
    lightroom.register(mcp)
    res = mcp.tools["lightroom_open_file"](str(target))

    assert res["ok"] is True
    assert res["opened"] is True
    assert res["pid"] == 4321
    assert res["path"] == str(target.resolve())
    assert calls == [[FAKE_EXE, str(target.resolve())]]
    assert res["argv"] == [FAKE_EXE, str(target.resolve())]
    assert res["command"] == res["argv"]


def test_open_file_catalog_happy_path(allowed, monkeypatch):
    target = allowed / "catalog.lrcat"
    target.write_bytes(b"lrcat")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed())
    calls = []
    monkeypatch.setattr(lightroom, "_spawn", _fake_spawn(calls))
    res = lightroom.lightroom_open_file(str(target))
    assert res["ok"] is True
    assert calls == [[FAKE_EXE, str(target.resolve())]]


def test_open_file_disallowed_path_errs_and_never_spawns(allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed())
    calls = []
    monkeypatch.setattr(lightroom, "_spawn", _fake_spawn(calls))
    res = lightroom.lightroom_open_file("/etc/passwd.dng")
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []


def test_open_file_not_installed_errs_and_never_spawns(allowed, monkeypatch):
    target = allowed / "photo.dng"
    target.write_bytes(b"dng")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    calls = []
    monkeypatch.setattr(lightroom, "_spawn", _fake_spawn(calls))
    res = lightroom.lightroom_open_file(str(target))
    assert res["ok"] is False
    assert "not installed" in res["error"].lower()
    assert calls == []


# ---------------------------------------------------------------------------
# status / launch delegation
# ---------------------------------------------------------------------------

def test_status_and_launch_delegate_to_common(monkeypatch):
    seen = []

    def fake_status(app_id):
        seen.append(("status", app_id))
        return {"ok": True, "app_id": app_id, "installed": True,
                "strategy": "plugin"}

    def fake_launch(app_id):
        seen.append(("launch", app_id))
        return {"ok": True, "app_id": app_id, "launched": True}

    monkeypatch.setattr(lightroom._common, "catalog_status", fake_status)
    monkeypatch.setattr(lightroom._common, "catalog_launch", fake_launch)

    mcp = _FakeMCP()
    lightroom.register(mcp)

    status = mcp.tools["lightroom_status"]()
    assert status["ok"] is True and status["strategy"] == "plugin"
    launch = mcp.tools["lightroom_launch"]()
    assert launch["ok"] is True and launch["launched"] is True
    assert seen == [("status", "lightroom"), ("launch", "lightroom")]

    # Module-level impls delegate the same way.
    assert lightroom.lightroom_status()["ok"] is True
    assert lightroom.lightroom_launch()["ok"] is True
