"""Hermetic tests for the Postman adapter — no real Postman required.

Detection goes through monkeypatched ``apps_catalog.detect_app``,
spawning through monkeypatched ``postman._spawn``, and focus through
monkeypatched ``_common.focus_window``, so no real Postman install,
window, or process is ever touched.
"""
from __future__ import annotations

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common, postman


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to tmp_path and isolate the audit log."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


FAKE_EXE = "/fake/Postman.exe"


def _detect_installed(app_id, **kwargs):
    assert app_id == "postman"
    return {"ok": True, "installed": True, "path": FAKE_EXE,
            "via": "path", "name": "Postman"}


def _detect_not_installed(app_id, **kwargs):
    assert app_id == "postman"
    return {"ok": True, "installed": False, "path": None,
            "via": None, "name": "Postman"}


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


@pytest.fixture
def tools():
    mcp = _FakeMCP()
    postman.register(mcp)
    return mcp.tools


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_all_tools(tools):
    assert set(tools) >= {
        "postman_status", "postman_launch",
        "postman_open_collection", "postman_focus",
    }
    assert len(tools) >= 4


# ---------------------------------------------------------------------------
# postman_open_collection — argv
# ---------------------------------------------------------------------------

def test_open_collection_argv_is_exe_and_path(allowed, monkeypatch):
    collection = allowed / "collection.json"
    collection.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(postman, "_spawn", _fake_spawn(calls))

    res = postman.postman_open_collection(str(collection))

    assert res["ok"] is True
    assert res["opened"] is True
    assert calls == [[FAKE_EXE, str(collection.resolve())]]
    assert res["argv"] == [FAKE_EXE, str(collection.resolve())]
    assert res["pid"] == 4321


def test_open_collection_via_registered_tool(allowed, monkeypatch, tools):
    collection = allowed / "api.postman_collection.json"
    collection.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(postman, "_spawn", _fake_spawn(calls))

    res = tools["postman_open_collection"](str(collection))

    assert res["ok"] is True
    assert calls == [[FAKE_EXE, str(collection.resolve())]]


def test_open_collection_spawn_param_is_used(allowed, monkeypatch):
    collection = allowed / "c.json"
    collection.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []

    res = postman.postman_open_collection(
        str(collection), spawn=_fake_spawn(calls))

    assert res["ok"] is True
    assert calls == [[FAKE_EXE, str(collection.resolve())]]


# ---------------------------------------------------------------------------
# postman_open_collection — rejection cases (nothing spawned)
# ---------------------------------------------------------------------------

def test_open_collection_non_json_rejected(allowed, monkeypatch):
    bad = allowed / "collection.txt"
    bad.write_text("nope", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(postman, "_spawn", _fake_spawn(calls))

    res = postman.postman_open_collection(str(bad))

    assert res["ok"] is False
    assert "error" in res
    assert ".json" in res["error"]
    assert calls == []


def test_open_collection_not_installed_returns_err(allowed, monkeypatch):
    collection = allowed / "collection.json"
    collection.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_not_installed)
    calls = []
    monkeypatch.setattr(postman, "_spawn", _fake_spawn(calls))

    res = postman.postman_open_collection(str(collection))

    assert res["ok"] is False
    assert "not installed" in res["error"]
    assert calls == []


def test_open_collection_disallowed_path_rejected_before_spawn(
        allowed, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _detect_installed)
    calls = []
    monkeypatch.setattr(postman, "_spawn", _fake_spawn(calls))

    res = postman.postman_open_collection("/etc/passwd.json")

    assert res["ok"] is False
    assert "error" in res
    assert "not allowed" in res["error"].lower()
    assert calls == []


# ---------------------------------------------------------------------------
# Delegation: status / launch / focus
# ---------------------------------------------------------------------------

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "postman", "installed": True}
    calls = []

    def fake_status(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    assert tools["postman_status"]() is sentinel
    assert calls == ["postman"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "postman", "launched": True}
    calls = []

    def fake_launch(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    assert tools["postman_launch"]() is sentinel
    assert calls == ["postman"]


def test_focus_delegates_to_focus_window_with_catalog_name(tools, monkeypatch):
    sentinel = {"ok": True, "focused": True, "window": "Postman"}
    calls = []

    def fake_focus(title_contains):
        calls.append(title_contains)
        return sentinel

    monkeypatch.setattr(_common, "focus_window", fake_focus)
    assert tools["postman_focus"]() is sentinel
    # Display name comes from the catalog entry for postman.
    assert calls == ["Postman"]
