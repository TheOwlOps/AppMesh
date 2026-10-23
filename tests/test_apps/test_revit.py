"""Hermetic tests for the Revit adapter (plugin strategy, no COM).

No real Revit is probed, launched or spawned: detection goes through a
monkeypatched ``apps_catalog.detect_app``, process starts through a fake
``popen``, and journal playback through an injectable fake ``runner``
for ``_common.run_cli``.
"""
from __future__ import annotations

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import revit


# ------------------------------------------------------------------ fakes

class FakeMCP:
    """Minimal FastMCP stand-in capturing @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeProc:
    pid = 4321


def _fake_detect(installed: bool, path: str | None = "C:/Program Files/Autodesk/Revit.exe"):
    """Build a detect_app replacement returning a catalog-shaped dict."""
    def _detect(app_id, which_fn=None):
        assert app_id == "revit"
        return core.ok(id="revit", name="Revit", strategy="plugin",
                       prog_id=None, installed=installed,
                       path=path if installed else None,
                       via="path" if installed else None)
    return _detect


def _allowed(name: str) -> str:
    """A path under the first allowed root (passes check_path_allowed)."""
    return str(core.ALLOWED_ROOTS[0] / "revit" / name)


# ------------------------------------------------------------- registration

def test_register_binds_at_least_three_tools():
    mcp = FakeMCP()
    revit.register(mcp)
    assert len(mcp.tools) >= 3
    assert {"revit_status", "revit_launch", "revit_open_file"} <= set(mcp.tools)
    assert "revit_run_script" in mcp.tools


# ------------------------------------------------------------------- status

def test_status_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    mcp = FakeMCP()
    revit.register(mcp)
    result = mcp.tools["revit_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "revit"
    assert result["installed"] is True
    assert result["path"].endswith("Revit.exe")
    assert result["via"] == "path"
    # catalog_status passes strategy/prog_id through from the catalog;
    # Revit's prog_id is genuinely None (no COM interface).
    assert result["prog_id"] is None


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(False))
    mcp = FakeMCP()
    revit.register(mcp)
    result = mcp.tools["revit_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


# --------------------------------------------------------------- open_file

def test_open_file_uses_detected_exe_with_file_argument(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append(argv)
        return FakeProc()

    target = _allowed("house.rvt")
    result = revit.open_file(target, popen=fake_popen)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["route"] == "detected_exe_with_file_argument"
    assert result["pid"] == 4321
    assert spawned == [["C:/Program Files/Autodesk/Revit.exe", target]]


def test_open_file_via_tool_monkeypatched_subprocess(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    spawned = []
    monkeypatch.setattr(revit.subprocess, "Popen",
                        lambda argv, **kw: spawned.append(argv) or FakeProc())
    mcp = FakeMCP()
    revit.register(mcp)
    target = _allowed("model.rvt")
    result = mcp.tools["revit_open_file"](target)
    assert result["ok"] is True and result["route"] == "detected_exe_with_file_argument"
    assert spawned == [["C:/Program Files/Autodesk/Revit.exe", target]]


def test_open_file_not_installed_returns_err(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(False))

    def boom(argv, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("must not spawn when Revit is missing")

    result = revit.open_file(_allowed("house.rvt"), popen=boom)
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_open_file_rejects_disallowed_path(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    spawned = []
    result = revit.open_file("/etc/passwd.rvt",
                             popen=lambda argv, **kw: spawned.append(argv))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert spawned == []  # spawn never reached


# -------------------------------------------------------------- run_script

def test_run_script_builds_journal_argv_with_fake_runner(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    seen = []

    def fake_runner(argv, timeout):
        seen.append((argv, timeout))
        return 0, "journal replayed", ""

    journal = _allowed("playback.txt")
    result = revit.run_script(journal, runner=fake_runner)
    assert result["ok"] is True
    assert result["route"] == "journal_playback"
    assert result["returncode"] == 0
    assert seen == [(["C:/Program Files/Autodesk/Revit.exe", journal],
                     revit.JOURNAL_TIMEOUT)]


def test_run_script_missing_exe_graceful_err(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(False))
    result = revit.run_script(_allowed("playback.txt"),
                              runner=lambda argv, timeout: (0, "", ""))
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_run_script_rejects_dynamo_and_disallowed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    runner = lambda argv, timeout: (0, "", "")  # noqa: E731
    dyn = revit.run_script(_allowed("graph.dyn"), runner=runner)
    assert dyn["ok"] is False and "Dynamo" in dyn["error"]
    bad = revit.run_script("/etc/passwd.txt", runner=runner)
    assert bad["ok"] is False and "not allowed" in bad["error"].lower()


def test_run_script_nonzero_exit_is_err(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", _fake_detect(True))
    result = revit.run_script(_allowed("playback.txt"),
                              runner=lambda argv, timeout: (2, "", "boom"))
    assert result["ok"] is False
    assert "exited 2" in result["error"]
