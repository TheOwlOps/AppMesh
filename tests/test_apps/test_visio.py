"""Tests for the Microsoft Visio per-app adapter.

Hermetic: no real Visio, no real COM, no Windows. The module-level
COM holder is replaced with a real ``_common.ComApp`` built on a fake
factory returning a fake Visio application object whose
``Documents.Open(path)`` mimics Visio's real COM open pattern. The exe
fallback is exercised through a monkeypatched ``_launch_with_file`` and
a monkeypatched catalog detection.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import visio
from desktop_mcp.adapters.apps._common import ComApp

ALLOWED_FILE = str(Path.home() / "diagram.vsdx")
CHECKED_FILE = str(Path(ALLOWED_FILE).expanduser().resolve())
DISALLOWED_FILE = "/etc/diagram.vsdx"
FAKE_EXE = r"C:\Program Files\Microsoft Office\root\Office16\VISIO.EXE"


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))


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


class FakeVisioDoc:
    def __init__(self, name: str = "diagram.vsdx"):
        self.Name = name


class FakeDocuments:
    """Mimics Visio's Documents collection: .Open(path) -> document."""

    def __init__(self, doc: FakeVisioDoc | None = None, raises: bool = False):
        self._doc = doc or FakeVisioDoc()
        self._raises = raises
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeVisioDoc:
        self.opened.append(path)
        if self._raises:
            raise RuntimeError("fake COM failure: Visio refused to open")
        return self._doc


class FakeVisioApp:
    def __init__(self, documents: FakeDocuments):
        self.Documents = documents


class FakeProc:
    pid = 4242


@pytest.fixture()
def registered():
    mcp = FakeMCP()
    visio.register(mcp)
    return mcp.tools


def _install_fake_com(monkeypatch, documents: FakeDocuments) -> FakeVisioApp:
    """Replace the module-level COM holder with one using a fake factory."""
    app = FakeVisioApp(documents)
    monkeypatch.setattr(
        visio, "_com", ComApp("Visio.Application", factory=lambda: app))
    return app


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_binds_visio_tools(registered):
    assert len(registered) >= 3
    for name in ("visio_status", "visio_launch", "visio_open_file"):
        assert name in registered
        assert callable(registered[name])


# --------------------------------------------------------------------------
# visio_open_file — COM route
# --------------------------------------------------------------------------


def test_open_file_via_com_documents_open(registered, monkeypatch):
    documents = FakeDocuments(FakeVisioDoc("diagram.vsdx"))
    _install_fake_com(monkeypatch, documents)
    spawns: list = []
    monkeypatch.setattr(visio, "_launch_with_file",
                        lambda exe, f: spawns.append((exe, f)))

    result = registered["visio_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["opened"] is True
    assert result["document"] == "diagram.vsdx"
    # The exact allowlist-checked (resolved) path was passed to Open.
    assert documents.opened == [CHECKED_FILE]
    assert result["path"] == CHECKED_FILE
    assert spawns == []  # exe fallback must not run when COM succeeds


# --------------------------------------------------------------------------
# visio_open_file — exe fallback
# --------------------------------------------------------------------------


def test_open_file_com_failure_falls_back_to_exe(registered, monkeypatch):
    documents = FakeDocuments(raises=True)
    _install_fake_com(monkeypatch, documents)
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Microsoft Visio",
                              "installed": True, "path": FAKE_EXE,
                              "via": "path"})
    spawns: list = []

    def fake_launch(exe_path, file_path):
        spawns.append((exe_path, file_path))
        return FakeProc()

    monkeypatch.setattr(visio, "_launch_with_file", fake_launch)

    result = registered["visio_open_file"](ALLOWED_FILE)

    # COM was attempted first, with the checked path.
    assert documents.opened == [CHECKED_FILE]
    # …then the detected VISIO.EXE was launched with the file.
    assert result["ok"] is True
    assert result["route"] == "exe"
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4242
    assert spawns == [(FAKE_EXE, CHECKED_FILE)]


def test_open_file_both_routes_fail_returns_err(registered, monkeypatch):
    documents = FakeDocuments(raises=True)
    _install_fake_com(monkeypatch, documents)
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "Microsoft Visio",
                              "installed": False, "path": None, "via": None})

    result = registered["visio_open_file"](ALLOWED_FILE)

    assert result["ok"] is False
    assert "not installed" in result["error"]


# --------------------------------------------------------------------------
# visio_open_file — allowlist gate
# --------------------------------------------------------------------------


def test_open_file_disallowed_path_rejected(registered, monkeypatch):
    documents = FakeDocuments()
    _install_fake_com(monkeypatch, documents)
    spawns: list = []
    monkeypatch.setattr(visio, "_launch_with_file",
                        lambda exe, f: spawns.append((exe, f)))

    result = registered["visio_open_file"](DISALLOWED_FILE)

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    # Documents.Open was never called and the fallback never spawned.
    assert documents.opened == []
    assert spawns == []
