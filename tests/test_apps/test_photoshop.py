"""Tests for the Photoshop adapter (adapters/apps/photoshop.py).

Hermetic: no real Photoshop, no real COM, no real processes.
- COM access is faked by monkeypatching ``photoshop._make_com_app``
  to return a fake ``ComApp`` wrapper.
- The exe fallback is faked by monkeypatching ``photoshop._detect_exe``
  (the wrapper over ``apps_catalog.detect_app``) and
  ``photoshop._spawn_exe_with_file`` (the wrapper over
  ``subprocess.Popen``).
- Status/launch delegation is verified by monkeypatching the
  ``_common`` catalog helpers the tools call.
"""
from __future__ import annotations

from pathlib import Path

from desktop_mcp.adapters.apps import photoshop

ALLOWED_PSD = str(Path.home() / "photo.psd")
ALLOWED_PNG = str(Path.home() / "export.png")
DISALLOWED = "/etc/passwd.psd"
FAKE_EXE = "C:/Program Files/Adobe/Adobe Photoshop/Photoshop.exe"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeComApp:
    """Fake _common.ComApp: open_via returns a canned result dict."""

    def __init__(self, result: dict):
        self._result = result
        self.open_calls: list[tuple[str, str]] = []

    def open_via(self, collection: str, path: str) -> dict:
        self.open_calls.append((collection, path))
        return dict(self._result)


class FakeProc:
    def __init__(self, pid: int = 4321):
        self.pid = pid


class FakeDoc:
    def __init__(self):
        self.Name = "active.psd"
        self.saved_as: list[str] = []

    def SaveAs(self, path: str):
        self.saved_as.append(path)


class FakePhotoshopApp:
    def __init__(self, doc: FakeDoc):
        self.ActiveDocument = doc


class FakeExportComApp:
    """Fake ComApp wrapper for the export path: .app() -> fake PS app."""

    def __init__(self, doc: FakeDoc):
        self._app = FakePhotoshopApp(doc)

    def app(self):
        return self._app


def _register() -> FakeMCP:
    mcp = FakeMCP()
    photoshop.register(mcp)
    return mcp


def _forbid_spawn(monkeypatch) -> list:
    spawned: list = []
    monkeypatch.setattr(
        photoshop, "_spawn_exe_with_file",
        lambda exe, file: spawned.append((exe, file)) or FakeProc())
    monkeypatch.setattr(photoshop, "_detect_exe", lambda: FAKE_EXE)
    return spawned


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_binds_at_least_three_tools():
    mcp = _register()
    assert len(mcp.tools) >= 3
    assert "photoshop_status" in mcp.tools
    assert "photoshop_launch" in mcp.tools
    assert "photoshop_open_file" in mcp.tools


# ---------------------------------------------------------------------------
# status / launch delegate to the catalog helpers
# ---------------------------------------------------------------------------


def test_status_delegates_to_catalog_helper(monkeypatch):
    seen = []

    def fake_status(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "installed": True,
                "strategy": "com", "prog_id": "Photoshop.Application"}

    monkeypatch.setattr(photoshop._common, "catalog_status", fake_status)
    mcp = _register()
    result = mcp.tools["photoshop_status"]()
    assert result["ok"] is True
    assert result["installed"] is True
    assert result["prog_id"] == "Photoshop.Application"
    assert seen == ["photoshop"]


def test_launch_delegates_to_catalog_helper(monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True, "pid": 111}

    monkeypatch.setattr(photoshop._common, "catalog_launch", fake_launch)
    mcp = _register()
    result = mcp.tools["photoshop_launch"]()
    assert result["ok"] is True
    assert result["launched"] is True
    assert seen == ["photoshop"]


# ---------------------------------------------------------------------------
# open_file: COM route
# ---------------------------------------------------------------------------


def test_open_file_com_success_route_com(monkeypatch):
    fake_com = FakeComApp({"ok": True, "opened": True,
                           "document": "photo.psd", "path": ALLOWED_PSD})
    monkeypatch.setattr(photoshop, "_make_com_app", lambda: fake_com)
    spawned = _forbid_spawn(monkeypatch)

    mcp = _register()
    result = mcp.tools["photoshop_open_file"](ALLOWED_PSD)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["document"] == "photo.psd"
    # COM was asked to open through the Documents collection...
    assert fake_com.open_calls == [("Documents", ALLOWED_PSD)]
    # ...and the exe fallback was never spawned.
    assert spawned == []


# ---------------------------------------------------------------------------
# open_file: exe fallback route
# ---------------------------------------------------------------------------


def test_open_file_com_failure_falls_back_to_exe(monkeypatch):
    fake_com = FakeComApp({"ok": False, "error": "COM unavailable"})
    monkeypatch.setattr(photoshop, "_make_com_app", lambda: fake_com)

    spawned: list[tuple[str, str]] = []

    def fake_spawn(exe, file):
        spawned.append((exe, file))
        return FakeProc(pid=4321)

    # Monkeypatch catalog detect (via the adapter's wrapper) + spawn.
    monkeypatch.setattr(photoshop, "_detect_exe", lambda: FAKE_EXE)
    monkeypatch.setattr(photoshop, "_spawn_exe_with_file", fake_spawn)

    mcp = _register()
    result = mcp.tools["photoshop_open_file"](ALLOWED_PSD)

    assert result["ok"] is True
    assert result["route"] == "exe"
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4321
    assert result["com_error"] == "COM unavailable"
    # The exe was launched with the file as its argument.
    assert spawned == [(FAKE_EXE, ALLOWED_PSD)]
    # COM was attempted first.
    assert fake_com.open_calls == [("Documents", ALLOWED_PSD)]


def test_open_file_both_routes_unavailable_returns_err(monkeypatch):
    fake_com = FakeComApp({"ok": False, "error": "COM unavailable"})
    monkeypatch.setattr(photoshop, "_make_com_app", lambda: fake_com)
    monkeypatch.setattr(photoshop, "_detect_exe", lambda: None)
    monkeypatch.setattr(
        photoshop, "_spawn_exe_with_file",
        lambda exe, file: (_ for _ in ()).throw(
            AssertionError("must not spawn when no exe was detected")))

    mcp = _register()
    result = mcp.tools["photoshop_open_file"](ALLOWED_PSD)
    assert result["ok"] is False
    assert "COM unavailable" in result["error"]


# ---------------------------------------------------------------------------
# open_file: allowlist gate
# ---------------------------------------------------------------------------


def test_open_file_disallowed_path_touches_nothing(monkeypatch):
    com_calls: list = []
    spawn_calls: list = []
    detect_calls: list = []

    def fake_make_com():
        com_calls.append(True)
        return FakeComApp({"ok": True, "opened": True})

    monkeypatch.setattr(photoshop, "_make_com_app", fake_make_com)
    monkeypatch.setattr(
        photoshop, "_detect_exe",
        lambda: detect_calls.append(True) or FAKE_EXE)
    monkeypatch.setattr(
        photoshop, "_spawn_exe_with_file",
        lambda exe, file: spawn_calls.append((exe, file)) or FakeProc())

    mcp = _register()
    result = mcp.tools["photoshop_open_file"](DISALLOWED)

    assert result["ok"] is False
    assert "not allowed" in result["error"]
    # Neither COM nor detect nor spawn was attempted.
    assert com_calls == []
    assert detect_calls == []
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# export (optional 4th tool)
# ---------------------------------------------------------------------------


def test_export_active_as_png_via_com(monkeypatch):
    doc = FakeDoc()
    monkeypatch.setattr(photoshop, "_make_com_app",
                        lambda: FakeExportComApp(doc))

    mcp = _register()
    assert "photoshop_export_active_as_png" in mcp.tools
    result = mcp.tools["photoshop_export_active_as_png"](ALLOWED_PNG)

    assert result["ok"] is True
    assert result["route"] == "com"
    assert result["path"] == ALLOWED_PNG
    assert doc.saved_as == [ALLOWED_PNG]


def test_export_disallowed_path_touches_nothing(monkeypatch):
    com_calls: list = []
    monkeypatch.setattr(
        photoshop, "_make_com_app",
        lambda: com_calls.append(True) or FakeExportComApp(FakeDoc()))

    mcp = _register()
    result = mcp.tools["photoshop_export_active_as_png"]("/etc/passwd.png")
    assert result["ok"] is False
    assert "not allowed" in result["error"]
    assert com_calls == []
