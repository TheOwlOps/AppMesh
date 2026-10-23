"""Hermetic tests for the CATIA per-app adapter.

No real CATIA, no real COM and no real process spawns:

- the COM success route is exercised through a *fake ComApp factory*
  (a zero-arg callable returning a fake CATIA application object whose
  ``Documents.Open`` records its calls) injected into
  ``catia.open_file`` — the real ``_common.ComApp`` wrapper runs;
- the exe fallback is exercised with COM failing, detection patched
  via ``apps_catalog.detect_app`` and ``subprocess.Popen``
  monkeypatched to a recording fake;
- status is exercised by monkeypatching ``apps_catalog.detect_app``,
  which the real ``catalog_status`` helper consults.
"""
from __future__ import annotations

import subprocess

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import catia


class _FakeMCP:
    """Captures the tools register() binds, keyed by function name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeDoc:
    def __init__(self, path):
        self.Name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        self.FullName = path


class _FakeDocuments:
    """Fake CATIA Documents collection: records Open() calls."""

    def __init__(self):
        self.open_calls: list = []

    def Open(self, path):  # noqa: N802 — mirrors the COM method name
        self.open_calls.append(path)
        return _FakeDoc(path)


class _FakeCatiaApp:
    """Fake CATIA.Application COM object."""

    def __init__(self):
        self.Documents = _FakeDocuments()


class _FakeProc:
    pid = 4242


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Allowlist = tmp_path only; audit log lands in tmp_path too."""
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    yield


def _tools():
    mcp = _FakeMCP()
    catia.register(mcp)
    return mcp.tools


def _no_spawn(monkeypatch):
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *a, **k: pytest.fail("must not spawn a process here"))


# ---------------------------------------------------------------- register

def test_register_binds_at_least_three_tools():
    tools = _tools()
    assert len(tools) >= 3
    assert {"catia_status", "catia_launch", "catia_open_file"} <= set(tools)


def test_app_id_and_prog_id():
    assert catia.APP_ID == "catia"
    assert catia.PROG_ID == "CATIA.Application"
    assert catia._catalog_prog_id() == "CATIA.Application"


# ------------------------------------------------------------------ status

def test_status_via_monkeypatched_detect_app(monkeypatch):
    def fake_detect(app_id, which_fn=None):
        assert app_id == "catia"
        return core.ok(id="catia", name="CATIA", category="CAD & 3D",
                       strategy="com", prog_id="CATIA.Application",
                       installed=True,
                       path="C:/Program Files/Dassault Systemes/B30/win_b64/code/bin/CNEXT.exe",
                       via="path")

    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect)
    result = _tools()["catia_status"]()

    assert result["ok"] is True
    assert result["app_id"] == "catia"
    assert result["installed"] is True
    assert result["path"].endswith("CNEXT.exe")
    assert result["via"] == "path"
    assert result["strategy"] == "com"
    assert result["prog_id"] == "CATIA.Application"
    assert result["name"] == "CATIA"


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, which_fn=None: core.ok(
            id="catia", name="CATIA", category="CAD & 3D",
            strategy="com", prog_id="CATIA.Application",
            installed=False, path=None, via=None))
    result = _tools()["catia_status"]()
    assert result["ok"] is True and result["installed"] is False
    assert result["path"] is None


# ------------------------------------------------------ open via COM route

def test_open_file_com_success_via_fake_factory(monkeypatch, tmp_path):
    _no_spawn(monkeypatch)
    fake_app = _FakeCatiaApp()
    factory_calls: list = []

    def fake_factory():
        factory_calls.append(True)
        return fake_app

    target = tmp_path / "bracket.CATPart"
    result = catia.open_file(str(target), com_factory=fake_factory)

    assert result["ok"] is True and result["route"] == "com"
    assert result["opened"] is True and result["app_id"] == "catia"
    assert result["path"] == str(target.resolve())
    assert result["document"] == "bracket.CATPart"
    # The real ComApp wrapper used the factory once and opened the file
    # through the Documents collection with the resolved, allow-listed path.
    assert factory_calls == [True]
    assert fake_app.Documents.open_calls == [str(target.resolve())]


def test_open_file_via_registered_tool_uses_com(monkeypatch, tmp_path):
    """The registered tool builds ComApp(PROG_ID) — patch the class."""
    _no_spawn(monkeypatch)
    fake_app = _FakeCatiaApp()
    seen: dict = {}

    class FakeComApp(catia.ComApp):
        def __init__(self, prog_id, factory=None):
            seen["prog_id"] = prog_id
            super().__init__(prog_id, factory=lambda: fake_app)

    monkeypatch.setattr(catia, "ComApp", FakeComApp)
    target = tmp_path / "assembly.CATProduct"
    result = _tools()["catia_open_file"](str(target))

    assert result["ok"] is True and result["route"] == "com"
    assert seen["prog_id"] == "CATIA.Application"
    assert fake_app.Documents.open_calls == [str(target.resolve())]


# ------------------------------------------------------------- exe fallback

def test_open_file_com_failure_falls_back_to_exe(monkeypatch, tmp_path):
    def failing_factory():
        raise RuntimeError("COM unavailable (fake)")

    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, which_fn=None: core.ok(
            id="catia", name="CATIA", category="CAD & 3D",
            strategy="com", prog_id="CATIA.Application", installed=True,
            path="C:/CATIA/CNEXT.exe", via="path"))

    popen_calls: list = []

    def fake_popen(argv, **kwargs):
        popen_calls.append((argv, kwargs))
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    target = tmp_path / "housing.CATPart"
    result = catia.open_file(str(target), com_factory=failing_factory)

    assert result["ok"] is True and result["route"] == "exe"
    assert result["opened"] is True and result["app_id"] == "catia"
    assert result["exe"] == "C:/CATIA/CNEXT.exe"
    assert result["pid"] == 4242
    assert result["path"] == str(target.resolve())
    assert "COM unavailable (fake)" in result["com_error"]
    assert popen_calls == [(["C:/CATIA/CNEXT.exe", str(target.resolve())],
                            {"close_fds": True})]


def test_open_file_both_routes_fail_returns_err(monkeypatch, tmp_path):
    def failing_factory():
        raise RuntimeError("COM unavailable (fake)")

    monkeypatch.setattr(
        apps_catalog, "detect_app",
        lambda app_id, which_fn=None: core.ok(
            id="catia", name="CATIA", category="CAD & 3D",
            strategy="com", prog_id="CATIA.Application",
            installed=False, path=None, via=None))
    _no_spawn(monkeypatch)

    result = catia.open_file(str(tmp_path / "x.CATPart"),
                             com_factory=failing_factory)

    assert result["ok"] is False
    assert "COM failed" in result["error"]
    assert "exe fallback failed" in result["error"]


# --------------------------------------------------------------- allowlist

def test_open_file_rejects_etc_passwd_catpart(monkeypatch):
    _no_spawn(monkeypatch)
    factory_calls: list = []

    def fake_factory():
        factory_calls.append(True)
        return _FakeCatiaApp()

    result = catia.open_file("/etc/passwd.CATPart",
                             com_factory=fake_factory)
    assert result["ok"] is False
    assert "not allowed" in result["error"]
    assert factory_calls == [], "COM must not be attempted for a rejected path"

    # And through the registered tool as well.
    result = _tools()["catia_open_file"]("/etc/passwd.CATPart")
    assert result["ok"] is False
    assert "not allowed" in result["error"]


# ------------------------------------------------------------------ launch

def test_launch_delegates_to_catalog_launch(monkeypatch):
    seen = []

    def fake_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "sentinel": "launch", "app_id": app_id,
                "launched": True}

    monkeypatch.setattr(catia, "catalog_launch", fake_launch)
    result = _tools()["catia_launch"]()
    assert result["sentinel"] == "launch" and result["launched"] is True
    assert seen == ["catia"]
