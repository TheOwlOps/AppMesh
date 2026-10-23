"""Hermetic tests for the Eclipse adapter.

No real Eclipse, no real process spawns, no Windows APIs: detection and
spawning are monkeypatched, and the MCP server is a capture fake.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import _common, eclipse

FAKE_EXE = "/fake/eclipse/eclipse"


class FakeMCP:
    """Minimal FastMCP stand-in: records tools bound via @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    eclipse.register(mcp)
    return mcp.tools


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """Keep audit writes in tmp and make tmp_path an allowed root."""
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / ".desktop-mcp"))
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])


def _fake_detect_installed(app_id, **_kw):
    assert app_id == "eclipse"
    return core.ok(id="eclipse", name="Eclipse", installed=True,
                   path=FAKE_EXE, via="path")


def _fake_detect_missing(app_id, **_kw):
    return core.ok(id="eclipse", name="Eclipse", installed=False,
                   path=None, via=None)


def _capture_spawn(monkeypatch):
    calls = []

    def fake_spawn(argv):
        calls.append(list(argv))
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(eclipse, "_spawn", fake_spawn)
    return calls


# ---------------------------------------------------------------- registration

def test_register_binds_all_tools(tools):
    assert {"eclipse_status", "eclipse_launch",
            "eclipse_open_project", "eclipse_focus"} <= set(tools)


# ---------------------------------------------------------------- open project

def test_open_project_spawns_exe_with_project_path(tools, monkeypatch, tmp_path):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect_installed)
    calls = _capture_spawn(monkeypatch)
    project = tmp_path / "my-project"
    project.mkdir()

    res = tools["eclipse_open_project"](str(project))

    assert res["ok"] is True
    assert res["opened"] is True
    assert res["pid"] == 4321
    assert calls == [[FAKE_EXE, str(project.resolve())]]
    assert res["argv"] == [FAKE_EXE, str(project.resolve())]


def test_open_project_not_installed_returns_err(tools, monkeypatch, tmp_path):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect_missing)
    calls = _capture_spawn(monkeypatch)
    project = tmp_path / "my-project"
    project.mkdir()

    res = tools["eclipse_open_project"](str(project))

    assert res["ok"] is False
    assert "not installed" in res["error"]
    assert calls == []  # never spawned


def test_open_project_disallowed_path_rejected_before_spawn(tools, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect_installed)
    calls = _capture_spawn(monkeypatch)

    res = tools["eclipse_open_project"]("/etc/passwd")

    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []  # rejected before any spawn


# ---------------------------------------------------------------- status/launch

def test_status_delegates_to_catalog_status(tools, monkeypatch):
    seen = []

    def fake_status(app_id):
        seen.append(app_id)
        return core.ok(app_id=app_id, installed=False)

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    res = tools["eclipse_status"]()
    assert res["ok"] is True
    assert seen == ["eclipse"]


def test_launch_delegates_to_catalog_launch(tools, monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return core.ok(app_id=app_id, launched=True)

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    res = tools["eclipse_launch"]()
    assert res["ok"] is True
    assert res["launched"] is True
    assert seen == ["eclipse"]


# ---------------------------------------------------------------- focus

def test_focus_delegates_to_common_focus_window(tools, monkeypatch):
    seen = []

    def fake_focus(title_contains):
        seen.append(title_contains)
        return core.ok(focused=True, window=title_contains)

    monkeypatch.setattr(_common, "focus_window", fake_focus)
    res = tools["eclipse_focus"]()
    assert res["ok"] is True
    assert res["focused"] is True
    # Eclipse's display name comes from the catalog entry.
    assert seen == ["Eclipse"]
