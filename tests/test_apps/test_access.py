"""Tests for the Microsoft Access per-app adapter.

Hermetic: no real Access, no real COM, no Windows. The COM connection
is injected by swapping the adapter's module-level ``_com`` for a
``ComApp("Access.Application", factory=lambda: fake_app)``, the exe
fallback through monkeypatched catalog detection + ``_launch_with_file``,
and status/launch through the real ``_common`` catalog helpers with
detection faked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import access
from desktop_mcp.adapters.apps._common import ComApp

ALLOWED_FILE = str(Path.home() / "inventory.accdb")
CHECKED_FILE = str(Path(ALLOWED_FILE).expanduser().resolve())
DISALLOWED_FILE = "/etc/passwd.accdb"
FAKE_EXE = r"C:\Program Files\Microsoft Office\root\Office16\MSACCESS.EXE"


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


class FakeProc:
    pid = 7777


class FakeDatabase:
    """What a usable Documents.Open would return."""

    Name = "inventory.accdb"
    FullName = CHECKED_FILE


class FakeDocuments:
    """A working Documents collection (route 1 surface)."""

    def __init__(self):
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeDatabase:
        self.opened.append(path)
        return FakeDatabase()


class BrokenDocuments:
    """A Documents collection whose Open always fails (route 1 dead)."""

    def Open(self, path: str):
        raise RuntimeError("Documents.Open is not usable for Access databases")


class FakeAccessAppFull:
    """COM app where route 1 works; OpenCurrentDatabase must stay unused."""

    def __init__(self):
        self.Documents = FakeDocuments()
        self.current_database_calls: list[str] = []

    def OpenCurrentDatabase(self, path: str):
        self.current_database_calls.append(path)


class FakeAccessAppNativeOnly:
    """COM app with NO usable Documents collection, but a working
    OpenCurrentDatabase — Access's real pattern (route 2)."""

    Documents = None  # getattr(app, "Documents").Open(...) raises AttributeError

    def __init__(self):
        self.current_database_calls: list[str] = []

    def OpenCurrentDatabase(self, path: str):
        self.current_database_calls.append(path)


class FakeAccessAppBroken:
    """COM app where both COM routes fail (route 3 must take over)."""

    def __init__(self):
        self.Documents = BrokenDocuments()
        self.current_database_calls: list[str] = []

    def OpenCurrentDatabase(self, path: str):
        self.current_database_calls.append(path)
        raise RuntimeError("database is locked or corrupt")


@pytest.fixture()
def registered():
    mcp = FakeMCP()
    access.register(mcp)
    return mcp.tools


def _install_fake_com(monkeypatch, fake_app, factory_calls: list | None = None):
    """Swap the module-level ComApp for one built with an injected factory."""

    def factory():
        if factory_calls is not None:
            factory_calls.append(True)
        return fake_app

    monkeypatch.setattr(
        access, "_com", ComApp("Access.Application", factory=factory))


def _stub_detect(monkeypatch, installed: bool = True):
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Microsoft Access",
                              "installed": installed,
                              "path": FAKE_EXE if installed else None,
                              "via": "path" if installed else None})


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_binds_access_tools(registered):
    for name in ("access_status", "access_launch", "access_open_file"):
        assert name in registered
        assert callable(registered[name])


# --------------------------------------------------------------------------
# access_open_file — route 1: Documents.Open
# --------------------------------------------------------------------------


def test_open_file_route1_documents_open(registered, monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    app = FakeAccessAppFull()
    _install_fake_com(monkeypatch, app)
    launches: list = []
    monkeypatch.setattr(access, "_launch_with_file",
                        lambda exe, f: launches.append((exe, f)))

    result = registered["access_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["path"] == CHECKED_FILE
    assert app.Documents.opened == [CHECKED_FILE]
    # Route 1 succeeded, so neither route 2 nor the exe fallback ran.
    assert app.current_database_calls == []
    assert launches == []


# --------------------------------------------------------------------------
# access_open_file — route 2: OpenCurrentDatabase
# --------------------------------------------------------------------------


def test_open_file_route2_open_current_database(registered, monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    app = FakeAccessAppNativeOnly()
    _install_fake_com(monkeypatch, app)
    launches: list = []
    monkeypatch.setattr(access, "_launch_with_file",
                        lambda exe, f: launches.append((exe, f)))

    result = registered["access_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["via"] == "OpenCurrentDatabase"
    assert result["path"] == CHECKED_FILE
    # Route 2 received exactly the allowlist-checked path …
    assert app.current_database_calls == [CHECKED_FILE]
    # … and the exe fallback was not needed.
    assert launches == []

    # The successful open was audited.
    log = tmp_path / "audit.jsonl"
    assert log.exists()
    records = [json.loads(line)
               for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("tool") == "access_open_file"
               and r.get("via") == "OpenCurrentDatabase"
               and r.get("path") == CHECKED_FILE for r in records)


# --------------------------------------------------------------------------
# access_open_file — route 3: MSACCESS.EXE fallback
# --------------------------------------------------------------------------


def test_open_file_route3_exe_fallback(registered, monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    app = FakeAccessAppBroken()
    _install_fake_com(monkeypatch, app)
    _stub_detect(monkeypatch, installed=True)
    launches: list = []

    def fake_launch(exe_path, file_path):
        launches.append((exe_path, file_path))
        return FakeProc()

    monkeypatch.setattr(access, "_launch_with_file", fake_launch)

    result = registered["access_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "exe"
    assert result["path"] == CHECKED_FILE
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 7777
    # Both COM routes were genuinely attempted before the fallback.
    assert app.current_database_calls == [CHECKED_FILE]
    assert launches == [(FAKE_EXE, CHECKED_FILE)]


def test_open_file_all_routes_fail_returns_err(registered, monkeypatch):
    app = FakeAccessAppBroken()
    _install_fake_com(monkeypatch, app)
    _stub_detect(monkeypatch, installed=False)

    result = registered["access_open_file"](ALLOWED_FILE)

    assert result["ok"] is False
    assert "OpenCurrentDatabase" in result["error"]
    assert "MSACCESS.EXE" in result["error"]


# --------------------------------------------------------------------------
# access_open_file — allowlist gate
# --------------------------------------------------------------------------


def test_open_file_disallowed_path_touches_nothing(registered, monkeypatch):
    factory_calls: list = []
    _install_fake_com(monkeypatch, FakeAccessAppNativeOnly(),
                      factory_calls=factory_calls)
    launches: list = []
    monkeypatch.setattr(access, "_launch_with_file",
                        lambda exe, f: launches.append((exe, f)))

    result = registered["access_open_file"](DISALLOWED_FILE)

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    # The allowlist check fired before any COM or spawn attempt.
    assert factory_calls == []
    assert launches == []


# --------------------------------------------------------------------------
# status / launch delegate to the catalog helpers
# --------------------------------------------------------------------------


def test_status_reports_catalog_info(registered, monkeypatch):
    _stub_detect(monkeypatch, installed=True)

    result = registered["access_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "access"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["via"] == "path"


def test_launch_not_installed_returns_err(registered, monkeypatch):
    _stub_detect(monkeypatch, installed=False)

    result = registered["access_launch"]()

    assert result["ok"] is False
    assert "not installed" in result["error"]
