"""Tests for the Microsoft Outlook per-app adapter.

Hermetic: the COM app is faked through ``ComApp``'s injectable factory,
``apps_catalog.detect_app`` is monkeypatched where detection matters,
and ``outlook._launch_with_file`` is monkeypatched so no real process
is ever spawned. No Outlook installation, pywin32 or Windows machine
is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import outlook
from desktop_mcp.adapters.apps._common import ComApp

ALLOWED_FILE = str(Path.home() / "message.msg")
CHECKED_FILE = str(Path(ALLOWED_FILE).expanduser().resolve())
FAKE_EXE = "C:/Program Files/Microsoft Office/OUTLOOK.EXE"


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def make_tools() -> dict:
    mcp = FakeMCP()
    outlook.register(mcp)
    return mcp.tools


# ---------------------------------------------------------------------------
# Fake COM objects
# ---------------------------------------------------------------------------


class FakeOpenedDoc:
    def __init__(self, name: str = "message.msg"):
        self.Name = name


class FakeDocumentsOk:
    """Documents collection whose Open succeeds (generic COM open path)."""

    def __init__(self):
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeOpenedDoc:
        self.opened.append(path)
        return FakeOpenedDoc()


class FakeDocumentsBroken:
    """Documents collection whose Open raises — real Outlook has no
    Documents collection at all, so this drives the exe-launch fallback."""

    def Open(self, path: str):
        raise RuntimeError("Outlook has no Documents collection")


class FakeOutlookApp:
    def __init__(self, documents=None, session=None):
        if documents is not None:
            self.Documents = documents
        if session is not None:
            self.Session = session


class FakeFolder:
    def __init__(self, name: str, folder_path: str):
        self.Name = name
        self.FolderPath = folder_path


class FakeFolders:
    def __init__(self, folders: list[FakeFolder]):
        self._folders = list(folders)

    @property
    def Count(self) -> int:
        return len(self._folders)

    def Item(self, index: int) -> FakeFolder:  # COM collections are 1-based
        return self._folders[index - 1]


class FakeNamespace:
    def __init__(self, folders: list[FakeFolder]):
        self.Folders = FakeFolders(folders)


def fake_detect_installed(app_id: str) -> dict:
    assert app_id == "outlook"
    return {"ok": True, "id": "outlook", "name": "Microsoft Outlook",
            "installed": True, "path": FAKE_EXE, "via": "path"}


# ---------------------------------------------------------------------------
# Registration + status
# ---------------------------------------------------------------------------


def test_register_binds_tools():
    tools = make_tools()
    assert {"outlook_status", "outlook_launch", "outlook_open_file"} <= set(tools)
    assert "outlook_list_folders" in tools


def test_status_returns_catalog_shape(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)
    result = make_tools()["outlook_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "outlook"
    assert result["installed"] is True
    assert result["path"] == FAKE_EXE
    assert result["via"] == "path"
    # Keys the catalog helper populates (its strategy/prog_id/name fields
    # are read off the get_app wrapper — see _common.catalog_status).
    assert {"strategy", "prog_id", "name"} <= set(result)


# ---------------------------------------------------------------------------
# outlook_open_file
# ---------------------------------------------------------------------------


def test_open_file_via_com_when_documents_open_works(monkeypatch):
    docs = FakeDocumentsOk()
    app = FakeOutlookApp(documents=docs)
    monkeypatch.setattr(outlook, "_com_app",
                        ComApp("Outlook.Application", factory=lambda: app))

    launch_calls = []
    monkeypatch.setattr(outlook, "_launch_with_file",
                        lambda exe, f: launch_calls.append((exe, f)))

    result = make_tools()["outlook_open_file"](ALLOWED_FILE)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["via"] == "com"
    assert docs.opened == [CHECKED_FILE]
    assert launch_calls == []  # COM succeeded: no fallback needed


def test_open_file_falls_back_to_exe_launch(monkeypatch):
    factory_calls = []

    def factory():
        factory_calls.append(True)
        return FakeOutlookApp(documents=FakeDocumentsBroken())

    monkeypatch.setattr(outlook, "_com_app",
                        ComApp("Outlook.Application", factory=factory))
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect_installed)

    launch_calls = []

    class FakeProc:
        pid = 4321

    def fake_launch(exe_path, file_path):
        launch_calls.append((exe_path, file_path))
        return FakeProc()

    monkeypatch.setattr(outlook, "_launch_with_file", fake_launch)

    result = make_tools()["outlook_open_file"](ALLOWED_FILE)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["via"] == "exe_launch"
    assert result["exe"] == FAKE_EXE
    assert result["pid"] == 4321
    # The fallback received the detected exe and the allowlist-checked path.
    assert launch_calls == [(FAKE_EXE, CHECKED_FILE)]
    assert factory_calls == [True]


def test_open_file_fallback_fails_cleanly_when_not_installed(monkeypatch):
    monkeypatch.setattr(outlook, "_com_app",
                        ComApp("Outlook.Application",
                               factory=lambda: FakeOutlookApp(
                                   documents=FakeDocumentsBroken())))
    monkeypatch.setattr(apps_catalog, "detect_app",
                        lambda app_id: {"ok": True, "id": "outlook",
                                        "installed": False, "path": None,
                                        "via": None})
    launch_calls = []
    monkeypatch.setattr(outlook, "_launch_with_file",
                        lambda exe, f: launch_calls.append((exe, f)))

    result = make_tools()["outlook_open_file"](ALLOWED_FILE)
    assert result["ok"] is False
    assert "no Outlook executable" in result["error"]
    assert launch_calls == []


def test_open_file_rejects_disallowed_path_before_any_attempt(monkeypatch):
    factory_calls = []

    def factory():
        factory_calls.append(True)
        return FakeOutlookApp(documents=FakeDocumentsOk())

    monkeypatch.setattr(outlook, "_com_app",
                        ComApp("Outlook.Application", factory=factory))
    launch_calls = []
    monkeypatch.setattr(outlook, "_launch_with_file",
                        lambda exe, f: launch_calls.append((exe, f)))

    result = make_tools()["outlook_open_file"]("/etc/passwd")
    assert result["ok"] is False
    assert "not allowed" in result["error"]
    # Neither the COM factory nor the exe-launch fallback was touched.
    assert factory_calls == []
    assert launch_calls == []


# ---------------------------------------------------------------------------
# outlook_list_folders
# ---------------------------------------------------------------------------


def test_list_folders_via_session(monkeypatch):
    folders = [FakeFolder("Inbox", "\\\\acct\\Inbox"),
               FakeFolder("Sent Items", "\\\\acct\\Sent Items")]
    app = FakeOutlookApp(session=FakeNamespace(folders))
    monkeypatch.setattr(outlook, "_com_app",
                        ComApp("Outlook.Application", factory=lambda: app))

    result = make_tools()["outlook_list_folders"]()
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["folders"] == [
        {"name": "Inbox", "folder_path": "\\\\acct\\Inbox"},
        {"name": "Sent Items", "folder_path": "\\\\acct\\Sent Items"},
    ]


def test_list_folders_com_failure_returns_err_not_raise(monkeypatch):
    def bad_factory():
        raise RuntimeError("COM unavailable")

    monkeypatch.setattr(outlook, "_com_app",
                        ComApp("Outlook.Application", factory=bad_factory))
    result = make_tools()["outlook_list_folders"]()
    assert result["ok"] is False
    assert "COM unavailable" in result["error"]
