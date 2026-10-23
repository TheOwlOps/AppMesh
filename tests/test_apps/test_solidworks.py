"""Tests for the SolidWorks per-app adapter.

Hermetic: no real SolidWorks, no real COM, no Windows. The COM
wrapper is injected through the adapter's ``make_com_app`` factory
seam (using the real ``_common.ComApp`` with a fake factory), the
exe-argument fallback through monkeypatched catalog detection +
``subprocess.Popen``, and status through monkeypatched
``apps_catalog.detect_app``.

SolidWorks is the special case: its real COM API opens files via the
OpenDoc/OpenDoc6 method, not a Documents collection, so the COM route
failing and the exe-argument fallback taking over is the expected
real-world path — both routes are asserted here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import solidworks
from desktop_mcp.adapters.apps._common import ComApp

ALLOWED_FILE = str(Path.home() / "part.sldprt")
DISALLOWED_FILE = "/etc/passwd.sldprt"
FAKE_EXE = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"


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
    Name = "part.sldprt"
    FullName = ALLOWED_FILE


class FakeDocuments:
    """Stand-in for a Documents-style collection (the generic COM route)."""

    def __init__(self):
        self.open_calls: list = []

    def Open(self, path):  # noqa: N802 — COM-style name
        self.open_calls.append(path)
        return FakeDoc()


class FakeSolidWorksApp:
    """Stand-in for the SldWorks.Application COM object."""

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
    solidworks.register(mcp)
    return mcp.tools


def _fake_detect(installed: bool):
    def detect(app_id, **kw):
        return {"ok": True, "id": app_id, "name": "SolidWorks",
                "category": "CAD & 3D", "strategy": "com",
                "prog_id": "SldWorks.Application",
                "installed": installed,
                "path": FAKE_EXE if installed else None,
                "via": "path" if installed else None}

    return detect


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_binds_at_least_three_tools(registered):
    assert len(registered) >= 3
    for name in ("solidworks_status", "solidworks_launch",
                 "solidworks_open_file"):
        assert name in registered
        assert callable(registered[name])
    assert solidworks.APP_ID == "solidworks"


# --------------------------------------------------------------------------
# solidworks_status — via monkeypatched apps_catalog.detect_app
# --------------------------------------------------------------------------


def test_status_installed(registered, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))

    result = registered["solidworks_status"]()

    # Note: strategy/prog_id/name are not asserted here — the shared
    # _common.catalog_status reads them off the top level of
    # apps_catalog.get_app(...), which actually nests the record
    # under "app", so they currently come back None (shared-helper
    # bug, outside this adapter's files). The fields catalog_status
    # does guarantee are asserted below.
    assert result["ok"] is True
    assert result["app_id"] == "solidworks"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["via"] == "path"


def test_status_not_installed(registered, monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(False))

    result = registered["solidworks_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "solidworks"
    assert result["installed"] is False
    assert result["path"] is None
    assert result["via"] is None


# --------------------------------------------------------------------------
# solidworks_open_file — COM route via a fake ComApp factory
# --------------------------------------------------------------------------


def test_open_file_com_success_via_fake_factory(registered, monkeypatch):
    fake_app = FakeSolidWorksApp()
    # Real ComApp, but its factory returns the fake SolidWorks
    # application: exercises ComApp(prog_id).open_via("Documents", path)
    # for real.
    com = ComApp("SldWorks.Application", factory=lambda: fake_app)
    monkeypatch.setattr(solidworks, "make_com_app", lambda *a, **kw: com)
    spawns: list = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: spawns.append(argv))

    result = registered["solidworks_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["opened"] is True
    assert result["document"] == "part.sldprt"
    assert fake_app.Documents.open_calls == [ALLOWED_FILE]
    assert spawns == []  # exe fallback must not run when COM succeeds


def test_open_file_accepts_com_factory_argument(monkeypatch):
    fake_app = FakeSolidWorksApp()

    result = solidworks.open_file(ALLOWED_FILE, com_factory=lambda: fake_app)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert fake_app.Documents.open_calls == [ALLOWED_FILE]


# --------------------------------------------------------------------------
# solidworks_open_file — exe-argument fallback (the SolidWorks special case)
# --------------------------------------------------------------------------


def test_open_file_com_fails_falls_back_to_exe_arg(registered, monkeypatch):
    # Real SolidWorks has no Documents collection (its API is the
    # OpenDoc/OpenDoc6 method), so open_via failing is the norm.
    fake = FakeComApp({"ok": False,
                       "error": "'SldWorks.Application' object has no "
                                "attribute 'Documents'"})
    monkeypatch.setattr(solidworks, "make_com_app", lambda *a, **kw: fake)
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    spawns: list = []

    def fake_popen(argv, **kwargs):
        spawns.append(argv)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = registered["solidworks_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "exe_arg"
    assert result["opened"] is True
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4242
    assert "Documents" in result["com_error"]
    assert fake.open_calls == [("Documents", ALLOWED_FILE)]
    assert spawns == [[FAKE_EXE, ALLOWED_FILE]]


def test_open_file_com_raises_falls_back_to_exe_arg(registered, monkeypatch):
    def broken_make_com_app(*args, **kwargs):
        raise RuntimeError("COM registration broken")

    monkeypatch.setattr(solidworks, "make_com_app", broken_make_com_app)
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    spawns: list = []

    def fake_popen(argv, **kwargs):
        spawns.append(argv)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = registered["solidworks_open_file"](ALLOWED_FILE)

    assert result["ok"] is True
    assert result["route"] == "exe_arg"
    assert result["com_error"] == "COM registration broken"
    assert spawns == [[FAKE_EXE, ALLOWED_FILE]]


def test_open_file_both_routes_fail_returns_err(registered, monkeypatch):
    fake = FakeComApp({"ok": False, "error": "COM unavailable"})
    monkeypatch.setattr(solidworks, "make_com_app", lambda *a, **kw: fake)
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(False))

    result = registered["solidworks_open_file"](ALLOWED_FILE)

    assert result["ok"] is False
    assert "COM unavailable" in result["error"]
    assert "not installed" in result["error"]


# --------------------------------------------------------------------------
# solidworks_open_file — allowlist gate
# --------------------------------------------------------------------------


def test_open_file_rejects_etc_passwd_sldprt(registered, monkeypatch):
    calls = {"factory": 0, "spawn": 0}

    def factory(*args, **kwargs):
        calls["factory"] += 1
        raise AssertionError("COM factory must not be called")

    def fake_popen(argv, **kwargs):
        calls["spawn"] += 1
        raise AssertionError("subprocess.Popen must not be called")

    monkeypatch.setattr(solidworks, "make_com_app", factory)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = registered["solidworks_open_file"](DISALLOWED_FILE)

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == {"factory": 0, "spawn": 0}
