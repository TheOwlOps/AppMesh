"""Tests for the Autodesk Inventor per-app adapter.

Hermetic: no real Inventor, no real COM, no Windows. The COM wrapper
is injected through the adapter's ``make_com_app`` factory seam (using
the real ``_common.ComApp`` with a fake factory), the exe fallback
through monkeypatched catalog detection + ``subprocess.Popen``, and
status through monkeypatched ``apps_catalog.detect_app``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import inventor
from desktop_mcp.adapters.apps._common import ComApp

ALLOWED_FILE = str(Path.home() / "bracket.ipt")
DISALLOWED_FILE = "/etc/passwd.ipt"
FAKE_EXE = r"C:\Program Files\Autodesk\Inventor 2025\Bin\Inventor.exe"


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


class FakeDoc:
    Name = "bracket.ipt"
    FullName = ALLOWED_FILE


class FakeDocuments:
    """Stand-in for Inventor's Application.Documents collection."""

    def __init__(self):
        self.open_calls: list = []

    def Open(self, path):  # noqa: N802 — COM-style name
        self.open_calls.append(path)
        return FakeDoc()


class FakeInventorApp:
    """Stand-in for the Inventor.Application COM object."""

    def __init__(self):
        self.Documents = FakeDocuments()


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
    inventor.register(mcp)
    return mcp.tools


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_binds_at_least_three_tools(registered):
    assert len(registered) >= 3
    for name in ("inventor_status", "inventor_launch", "inventor_open_file"):
        assert name in registered
        assert callable(registered[name])
    assert inventor.APP_ID == "inventor"


# --------------------------------------------------------------------------
# inventor_status — via monkeypatched apps_catalog.detect_app
# --------------------------------------------------------------------------


def test_status_via_monkeypatched_detect_app(registered, monkeypatch):
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Autodesk Inventor",
                              "category": "CAD & 3D", "strategy": "com",
                              "prog_id": "Inventor.Application",
                              "installed": True, "path": FAKE_EXE,
                              "via": "path"})

    result = registered["inventor_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "inventor"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["via"] == "path"


def test_status_not_installed(registered, monkeypatch):
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Autodesk Inventor",
                              "installed": False, "path": None, "via": None})

    result = registered["inventor_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "inventor"
    assert result["installed"] is False
    assert result["path"] is None


# --------------------------------------------------------------------------
# inventor_open_file — COM route via a fake ComApp factory
# --------------------------------------------------------------------------


def test_open_file_com_success_via_fake_factory(registered, monkeypatch):
    fake_app = FakeInventorApp()
    # Real ComApp, but its factory returns the fake Inventor application:
    # exercises ComApp(prog_id).open_via("Documents", path) for real.
    com = ComApp("Inventor.Application", factory=lambda: fake_app)
    monkeypatch.setattr(inventor, "make_com_app", lambda *a, **kw: com)
    spawns: list = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: spawns.append(argv))

    result = registered["inventor_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["opened"] is True
    assert result["document"] == "bracket.ipt"
    assert fake_app.Documents.open_calls == [ALLOWED_FILE]
    assert spawns == []  # exe fallback must not run when COM succeeds


def test_open_file_accepts_com_factory_argument(monkeypatch):
    fake_app = FakeInventorApp()

    result = inventor.open_file(ALLOWED_FILE, com_factory=lambda: fake_app)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert fake_app.Documents.open_calls == [ALLOWED_FILE]


# --------------------------------------------------------------------------
# inventor_open_file — exe fallback via monkeypatched subprocess
# --------------------------------------------------------------------------


def test_open_file_com_fails_falls_back_to_subprocess(registered, monkeypatch):
    fake = FakeComApp({"ok": False, "error": "COM unavailable"})
    monkeypatch.setattr(inventor, "make_com_app", lambda *a, **kw: fake)
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Autodesk Inventor",
                              "installed": True, "path": FAKE_EXE,
                              "via": "path"})
    spawns: list = []

    def fake_popen(argv, **kwargs):
        spawns.append(argv)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = registered["inventor_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "exe"
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4242
    assert result["com_error"] == "COM unavailable"
    assert fake.open_calls == [("Documents", ALLOWED_FILE)]
    assert spawns == [[FAKE_EXE, ALLOWED_FILE]]


def test_open_file_both_routes_fail_returns_err(registered, monkeypatch):
    fake = FakeComApp({"ok": False, "error": "COM unavailable"})
    monkeypatch.setattr(inventor, "make_com_app", lambda *a, **kw: fake)
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Autodesk Inventor",
                              "installed": False, "path": None, "via": None})

    result = registered["inventor_open_file"](ALLOWED_FILE)

    assert result["ok"] is False
    assert "COM unavailable" in result["error"]


# --------------------------------------------------------------------------
# inventor_open_file — allowlist gate
# --------------------------------------------------------------------------


def test_open_file_rejects_etc_passwd_ipt(registered, monkeypatch):
    calls = {"factory": 0, "spawn": 0}

    def factory(*args, **kwargs):
        calls["factory"] += 1
        raise AssertionError("COM factory must not be called")

    def fake_popen(argv, **kwargs):
        calls["spawn"] += 1
        raise AssertionError("subprocess.Popen must not be called")

    monkeypatch.setattr(inventor, "make_com_app", factory)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = registered["inventor_open_file"](DISALLOWED_FILE)

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == {"factory": 0, "spawn": 0}
