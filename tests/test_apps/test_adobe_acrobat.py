"""Hermetic tests for the Adobe Acrobat adapter (adobe_acrobat_*).

No Acrobat installation, COM or Windows machine is required:

- registration is captured with a fake MCP,
- the COM route is driven by fake factories injected through the
  adapter module's ``_com_factory`` (one app *without* a ``Documents``
  attribute — like the real ``AcroExch.App`` — and one whose
  ``Documents.Open`` works),
- the exe-launch fallback goes through a monkeypatched
  ``_launch_with_file`` and a monkeypatched catalog ``detect_app``.
"""
from __future__ import annotations

from pathlib import Path

from desktop_mcp.adapters.apps import adobe_acrobat as mod
from desktop_mcp.adapters.apps._common import ComApp

ALLOWED_PDF = str(Path.home() / "report.pdf")
FAKE_EXE = "C:/Program Files/Adobe/Acrobat DC/Acrobat/Acrobat.exe"


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


class FakeAppNoDocuments:
    """Mimics the real AcroExch.App: no ``Documents`` attribute at all."""


class FakeDoc:
    def __init__(self, name: str = "report.pdf"):
        self.Name = name


class FakeDocuments:
    def __init__(self):
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeDoc:
        self.opened.append(path)
        return FakeDoc()


class FakeAppWithDocuments:
    def __init__(self):
        self.Documents = FakeDocuments()


class FakeProc:
    pid = 4321


def _fake_detect_installed(app_id: str) -> dict:
    assert app_id == "adobe-acrobat"
    return {"ok": True, "id": app_id, "name": "Adobe Acrobat",
            "installed": True, "path": FAKE_EXE, "via": "path"}


def _register() -> FakeMCP:
    mcp = FakeMCP()
    mod.register(mcp)
    return mcp


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_binds_three_adobe_acrobat_tools():
    mcp = _register()
    assert "adobe_acrobat_status" in mcp.tools
    assert "adobe_acrobat_launch" in mcp.tools
    assert "adobe_acrobat_open_file" in mcp.tools


# ---------------------------------------------------------------------------
# Open-file routes
# ---------------------------------------------------------------------------


def test_open_file_falls_back_to_exe_launch_when_no_documents(monkeypatch):
    factory_calls: list = []

    def factory():
        factory_calls.append(True)
        return FakeAppNoDocuments()

    launches: list = []

    def fake_launch(exe_path, file_path):
        launches.append((exe_path, file_path))
        return FakeProc()

    monkeypatch.setattr(mod, "_com_factory", factory)
    monkeypatch.setattr(mod, "_launch_with_file", fake_launch)
    monkeypatch.setattr(mod.apps_catalog, "detect_app", _fake_detect_installed)

    result = _register().tools["adobe_acrobat_open_file"](ALLOWED_PDF)

    assert result["ok"] is True
    assert result["via"] == "exe_launch"
    assert result["opened"] is True
    assert result["pid"] == 4321
    # The COM factory was tried first, then the fallback launched the
    # detected exe with the checked (resolved, allowlisted) path.
    assert factory_calls == [True]
    assert launches == [(FAKE_EXE, str(Path(ALLOWED_PDF).resolve()))]
    assert result["path"] == str(Path(ALLOWED_PDF).resolve())
    assert result["exe"] == FAKE_EXE


def test_open_file_com_route_when_documents_open_works(monkeypatch):
    app = FakeAppWithDocuments()
    monkeypatch.setattr(mod, "_com_factory", lambda: app)
    # Also exercise the module-level ComApp replacement route.
    monkeypatch.setattr(mod, "_com_app",
                        ComApp(mod.PROG_ID, factory=lambda: app))

    def fail_launch(exe_path, file_path):  # pragma: no cover - guard
        raise AssertionError("exe fallback must not run when COM works")

    monkeypatch.setattr(mod, "_launch_with_file", fail_launch)

    result = _register().tools["adobe_acrobat_open_file"](ALLOWED_PDF)

    assert result["ok"] is True
    assert result["via"] == "com"
    assert result["opened"] is True
    assert result["document"] == "report.pdf"
    assert app.Documents.opened == [str(Path(ALLOWED_PDF).resolve())]


def test_open_file_rejects_disallowed_path_before_com_or_launch(monkeypatch):
    factory_calls: list = []

    def factory():
        factory_calls.append(True)
        return FakeAppWithDocuments()

    launch_calls: list = []
    monkeypatch.setattr(mod, "_com_factory", factory)
    monkeypatch.setattr(mod, "_launch_with_file",
                        lambda exe, f: launch_calls.append((exe, f)))

    result = _register().tools["adobe_acrobat_open_file"]("/etc/passwd.pdf")

    assert result["ok"] is False
    assert "not allowed" in result["error"]
    # Allowlist rejection happens first: neither route was attempted.
    assert factory_calls == []
    assert launch_calls == []
