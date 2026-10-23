"""Tests for the Microsoft OneNote per-app adapter.

Hermetic: no real OneNote, no real COM, no Windows. The COM route is
exercised through a real ``_common.ComApp`` with an injected fake
factory (an app object with — or without — a ``Documents`` collection),
the exe fallback through monkeypatched catalog detection +
``_launch_with_file``, and status/launch through monkeypatched
``_common`` catalog helpers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import _common, onenote
from desktop_mcp.adapters.apps._common import ComApp

ALLOWED_FILE = str(Path.home() / "note.one")
CHECKED_FILE = str(Path(ALLOWED_FILE).resolve())
DISALLOWED_FILE = "/etc/passwd.one"
FAKE_EXE = r"C:\Program Files\Microsoft Office\root\Office16\ONENOTE.EXE"


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


class FakeOpenedDoc:
    """The document object a working Documents.Open returns."""

    Name = "note.one"
    FullName = ALLOWED_FILE


class FakeDocuments:
    """A working Documents collection: Open(path) records and returns a doc."""

    def __init__(self):
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeOpenedDoc:
        self.opened.append(path)
        return FakeOpenedDoc()


class FakeAppWithDocuments:
    """Fake OneNote.Application whose Documents.Open works."""

    def __init__(self):
        self.Documents = FakeDocuments()


class FakeAppWithoutDocuments:
    """Fake OneNote.Application with no Documents attribute at all.

    Mirrors real OneNote, whose COM model has no Documents.Open:
    ``getattr(app, "Documents")`` raises AttributeError inside
    ``ComApp.open_via``, which converts it to an err dict and triggers
    the adapter's exe fallback.
    """


class FakeProc:
    pid = 4242


@pytest.fixture()
def registered():
    mcp = FakeMCP()
    onenote.register(mcp)
    return mcp.tools


@pytest.fixture()
def installed(monkeypatch):
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Microsoft OneNote",
                              "installed": True, "path": FAKE_EXE,
                              "via": "path"})


@pytest.fixture()
def launches(monkeypatch):
    """Patch _launch_with_file to record (exe, file) instead of spawning."""
    calls: list = []

    def fake_launch(exe_path, file_path):
        calls.append((exe_path, file_path))
        return FakeProc()

    monkeypatch.setattr(onenote, "_launch_with_file", fake_launch)
    return calls


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_binds_required_tools(registered):
    assert {"onenote_status", "onenote_launch", "onenote_open_file"} <= set(registered)
    for name in ("onenote_status", "onenote_launch", "onenote_open_file"):
        assert callable(registered[name])


# --------------------------------------------------------------------------
# onenote_open_file — COM route
# --------------------------------------------------------------------------


def test_open_file_com_success_route_com(registered, monkeypatch, launches):
    app = FakeAppWithDocuments()
    fake_com = ComApp("OneNote.Application", factory=lambda: app)
    monkeypatch.setattr(onenote, "make_com_app", lambda factory=None: fake_com)

    result = registered["onenote_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["opened"] is True
    # Documents.Open was called with the checked (resolved) path.
    assert app.Documents.opened == [CHECKED_FILE]
    assert result["path"] == CHECKED_FILE
    assert launches == []  # exe fallback must not run when COM succeeds


# --------------------------------------------------------------------------
# onenote_open_file — exe fallback (the expected real-world path)
# --------------------------------------------------------------------------


def test_open_file_no_documents_falls_back_to_exe(registered, monkeypatch,
                                                  installed, launches):
    factory_calls: list = []

    def factory():
        factory_calls.append(True)
        return FakeAppWithoutDocuments()

    fake_com = ComApp("OneNote.Application", factory=factory)
    # Inject via the module-level ComApp seam.
    monkeypatch.setattr(onenote, "com_app", fake_com)

    result = registered["onenote_open_file"](ALLOWED_FILE)

    assert factory_calls == [True]  # the COM route really was attempted
    assert result["ok"] is True
    assert result["route"] == "exe"
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4242
    assert result["path"] == CHECKED_FILE
    assert result["com_error"]  # the COM failure is reported, not hidden
    # The fallback launched the detected exe with the checked path.
    assert launches == [(FAKE_EXE, CHECKED_FILE)]


def test_open_file_both_routes_fail_returns_err(registered, monkeypatch,
                                                launches):
    fake_com = ComApp("OneNote.Application",
                      factory=lambda: FakeAppWithoutDocuments())
    monkeypatch.setattr(onenote, "com_app", fake_com)
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Microsoft OneNote",
                              "installed": False, "path": None, "via": None})

    result = registered["onenote_open_file"](ALLOWED_FILE)

    assert result["ok"] is False
    assert "Documents" in result["error"]  # COM AttributeError is surfaced
    assert launches == []


# --------------------------------------------------------------------------
# onenote_open_file — allowlist gate
# --------------------------------------------------------------------------


def test_open_file_disallowed_path_touches_nothing(registered, monkeypatch,
                                                   launches):
    calls = {"factory": 0}

    def factory():
        calls["factory"] += 1
        raise AssertionError("COM factory must not be called")

    fake_com = ComApp("OneNote.Application", factory=factory)
    monkeypatch.setattr(onenote, "com_app", fake_com)
    monkeypatch.setattr(onenote, "make_com_app", lambda factory=None: fake_com)

    result = registered["onenote_open_file"](DISALLOWED_FILE)

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    # The allowlist check happens before any COM or launch attempt.
    assert calls == {"factory": 0}
    assert launches == []


# --------------------------------------------------------------------------
# status / launch delegate to the catalog helpers
# --------------------------------------------------------------------------


def test_status_delegates_to_catalog_helper(registered, monkeypatch):
    seen: list = []

    def fake_status(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "installed": True,
                "path": FAKE_EXE, "strategy": "com",
                "prog_id": "OneNote.Application"}

    monkeypatch.setattr(_common, "catalog_status", fake_status)

    result = registered["onenote_status"]()

    assert seen == ["onenote"]
    assert result["ok"] is True
    assert result["installed"] is True
    assert result["prog_id"] == "OneNote.Application"


def test_launch_delegates_to_catalog_helper(registered, monkeypatch):
    seen: list = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True,
                "path": FAKE_EXE, "pid": 4242}

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)

    result = registered["onenote_launch"]()

    assert seen == ["onenote"]
    assert result["ok"] is True
    assert result["launched"] is True


def test_status_with_detect_nothing_installed(registered, monkeypatch):
    """End-to-end through the real _common helper with detection faked."""
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Microsoft OneNote",
                              "installed": False, "path": None, "via": None})

    result = registered["onenote_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "onenote"
    assert result["installed"] is False
    assert result["path"] is None
