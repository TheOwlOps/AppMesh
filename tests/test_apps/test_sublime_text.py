"""Hermetic tests for the Sublime Text adapter.

No real Sublime Text / ``subl`` binary is required: the spawn function is
monkeypatched with a recording fake, the catalog helpers are monkeypatched
with sentinels for the delegation tests, and the core allowlist is
restricted to the test's tmp dir.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import _common, sublime_text
from desktop_mcp.adapters.apps.sublime_text import build_open_argv


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------

class FakeMCP:
    """Minimal FastMCP stand-in: captures tools registered via @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir and silence audit."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(core, "audit", lambda *a, **k: None)
    return tmp_path


@pytest.fixture
def fake_spawn(monkeypatch):
    """Replace sublime_text.spawn with a recording fake (pid 4321)."""
    calls = []

    class _Proc:
        pid = 4321

    def _fake(argv):
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(sublime_text, "spawn", _fake)
    return calls


def _mkfile(base: Path, name: str = "notes.txt") -> Path:
    p = base / name
    p.write_text("hello", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def test_build_open_argv_exact():
    assert build_open_argv("/some/dir/file.txt") == ["subl", "/some/dir/file.txt"]


def test_build_open_argv_accepts_path_objects():
    assert build_open_argv(Path("/a/b.py")) == ["subl", "/a/b.py"]


# ---------------------------------------------------------------------------
# sublime_text_open
# ---------------------------------------------------------------------------

def test_open_spawns_exact_argv(allowed, fake_spawn):
    target = _mkfile(allowed)
    result = sublime_text.sublime_text_open(str(target))
    assert result["ok"] is True
    assert result["opened"] is True
    assert fake_spawn == [["subl", str(target.resolve())]]
    assert result["argv"] == ["subl", str(target.resolve())]
    assert result["pid"] == 4321


def test_open_disallowed_path_rejected_before_spawn(allowed, fake_spawn):
    result = sublime_text.sublime_text_open("/etc/passwd")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert fake_spawn == []  # nothing was spawned


def test_open_spawn_failure_returns_err_not_raise(allowed, monkeypatch):
    def _boom(argv):
        raise RuntimeError("subl exploded")

    monkeypatch.setattr(sublime_text, "spawn", _boom)
    target = _mkfile(allowed)
    result = sublime_text.sublime_text_open(str(target))
    assert result["ok"] is False
    assert "subl exploded" in result["error"]


# ---------------------------------------------------------------------------
# status / launch delegation to the catalog helpers
# ---------------------------------------------------------------------------

def test_status_delegates_to_catalog_status(monkeypatch):
    seen = {}

    def _fake_status(app_id):
        seen["app_id"] = app_id
        return {"ok": True, "app_id": app_id, "installed": True, "sentinel": "status"}

    monkeypatch.setattr(_common, "catalog_status", _fake_status)
    result = sublime_text.sublime_text_status()
    assert seen["app_id"] == "sublime-text"
    assert result == {"ok": True, "app_id": "sublime-text",
                      "installed": True, "sentinel": "status"}


def test_launch_delegates_to_catalog_launch(monkeypatch):
    seen = {}

    def _fake_launch(app_id):
        seen["app_id"] = app_id
        return {"ok": True, "app_id": app_id, "launched": True, "sentinel": "launch"}

    monkeypatch.setattr(_common, "catalog_launch", _fake_launch)
    result = sublime_text.sublime_text_launch()
    assert seen["app_id"] == "sublime-text"
    assert result == {"ok": True, "app_id": "sublime-text",
                      "launched": True, "sentinel": "launch"}


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------

def test_register_binds_at_least_three_tools(allowed, fake_spawn):
    mcp = FakeMCP()
    names = sublime_text.register(mcp)
    assert set(mcp.tools) >= {"sublime_text_status",
                              "sublime_text_launch",
                              "sublime_text_open"}
    assert set(names) >= {"sublime_text_status",
                          "sublime_text_launch",
                          "sublime_text_open"}


def test_registered_open_tool_uses_spawn(allowed, fake_spawn):
    mcp = FakeMCP()
    sublime_text.register(mcp)
    target = _mkfile(allowed)
    result = mcp.tools["sublime_text_open"](str(target))
    assert result["ok"] is True
    assert fake_spawn == [["subl", str(target.resolve())]]


def test_registered_open_tool_rejects_disallowed_path(allowed, fake_spawn):
    mcp = FakeMCP()
    sublime_text.register(mcp)
    result = mcp.tools["sublime_text_open"]("/etc/passwd")
    assert result["ok"] is False
    assert fake_spawn == []
