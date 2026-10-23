"""Hermetic tests for the LibreOffice Impress adapter.

No real ``soffice`` binary is required: conversions run through a fake
runner injected via the module-level ``runner`` variable, and file
opens go through a monkeypatched ``_spawn`` (with ``shutil.which``
faked so the machine looks like LibreOffice is installed).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import libreoffice_impress as impress
from desktop_mcp.adapters.apps.libreoffice_impress import (
    build_convert_argv,
    libreoffice_impress_convert,
    libreoffice_impress_open_file,
    libreoffice_impress_status,
)


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


def _mkfile(base: Path, name: str, data: bytes = b"x") -> Path:
    p = base / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def fake_runner(result, calls=None):
    """A _common.run_cli-compatible runner: callable(argv, timeout)."""
    def run(argv, timeout=120):
        if calls is not None:
            calls.append(list(argv))
        return result
    return run


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_binds_four_tools():
    mcp = _FakeMCP()
    names = impress.register(mcp)
    expected = {
        "libreoffice_impress_status",
        "libreoffice_impress_launch",
        "libreoffice_impress_convert",
        "libreoffice_impress_open_file",
    }
    assert set(mcp.tools) == expected
    assert set(names) == expected


def test_status_reports_catalog_info():
    result = libreoffice_impress_status()
    assert result["ok"] is True
    assert result["app_id"] == "libreoffice-impress"
    assert isinstance(result["installed"], bool)


# ---------------------------------------------------------------------------
# Pure builder — exact argv
# ---------------------------------------------------------------------------

def test_build_convert_argv_default_format_is_pptx():
    assert build_convert_argv("deck.odp", "outdir") == [
        "soffice", "--headless", "--convert-to", "pptx",
        "--outdir", "outdir", "deck.odp",
    ]


def test_build_convert_argv_explicit_format():
    assert build_convert_argv("deck.pptx", "/tmp/out", "pdf") == [
        "soffice", "--headless", "--convert-to", "pdf",
        "--outdir", "/tmp/out", "deck.pptx",
    ]


# ---------------------------------------------------------------------------
# Convert — injected fake runner
# ---------------------------------------------------------------------------

def test_convert_with_injected_runner_ok_and_argv(allowed, monkeypatch):
    inp = _mkfile(allowed, "deck.odp")
    outdir = allowed / "converted"
    outdir.mkdir()
    calls = []
    # Inject through the module-level runner variable (the documented seam).
    monkeypatch.setattr(impress, "runner",
                        fake_runner((0, "out", ""), calls))
    result = libreoffice_impress_convert(str(inp), str(outdir))
    assert result["ok"] is True
    assert result["stdout"] == "out"
    assert result["returncode"] == 0
    assert result["target_format"] == "pptx"
    expected = build_convert_argv(str(inp.resolve()), str(outdir.resolve()),
                                  "pptx")
    assert calls == [expected]
    assert result["argv"] == expected


def test_convert_with_runner_kwarg_and_explicit_format(allowed):
    inp = _mkfile(allowed, "deck.pptx")
    outdir = allowed / "pdfs"
    outdir.mkdir()
    calls = []
    result = libreoffice_impress_convert(
        str(inp), str(outdir), "pdf",
        runner=fake_runner((0, "done", ""), calls))
    assert result["ok"] is True
    assert result["target_format"] == "pdf"
    assert calls == [build_convert_argv(str(inp.resolve()),
                                         str(outdir.resolve()), "pdf")]


def test_convert_is_audited_on_success(allowed, monkeypatch, tmp_path):
    home = tmp_path / "mcp-home"
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(home))
    inp = _mkfile(allowed, "deck.odp")
    outdir = allowed / "out"
    outdir.mkdir()
    monkeypatch.setattr(impress, "runner", fake_runner((0, "out", "")))
    result = libreoffice_impress_convert(str(inp), str(outdir))
    assert result["ok"] is True
    log = (home / "audit.jsonl").read_text(encoding="utf-8")
    assert "libreoffice_impress_convert" in log
    assert "deck.odp" in log


def test_convert_nonzero_exit_is_err(allowed, monkeypatch):
    inp = _mkfile(allowed, "deck.odp")
    outdir = allowed / "out"
    outdir.mkdir()
    monkeypatch.setattr(impress, "runner",
                        fake_runner((1, "", "soffice blew up")))
    result = libreoffice_impress_convert(str(inp), str(outdir))
    assert result["ok"] is False
    assert "soffice blew up" in result["error"]


# ---------------------------------------------------------------------------
# Convert — validation happens before anything runs
# ---------------------------------------------------------------------------

def test_convert_disallowed_input_rejected_runner_never_called(allowed,
                                                                monkeypatch):
    calls = []
    monkeypatch.setattr(impress, "runner",
                        fake_runner((0, "out", ""), calls))
    result = libreoffice_impress_convert("/etc/secret-deck.odp",
                                         str(allowed / "out"))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []  # runner never invoked


def test_convert_disallowed_output_dir_rejected(allowed, monkeypatch):
    inp = _mkfile(allowed, "deck.odp")
    calls = []
    monkeypatch.setattr(impress, "runner",
                        fake_runner((0, "out", ""), calls))
    result = libreoffice_impress_convert(str(inp), "/etc/out")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []


def test_convert_missing_input_rejected(allowed, monkeypatch):
    calls = []
    monkeypatch.setattr(impress, "runner",
                        fake_runner((0, "out", ""), calls))
    result = libreoffice_impress_convert(str(allowed / "nope.odp"),
                                         str(allowed / "out"))
    assert result["ok"] is False
    assert "does not exist" in result["error"]
    assert calls == []


# ---------------------------------------------------------------------------
# Open file — monkeypatched _spawn
# ---------------------------------------------------------------------------

class _FakeProc:
    pid = 4321


def test_open_file_spawns_soffice_with_checked_path(allowed, monkeypatch):
    deck = _mkfile(allowed, "deck.pptx")
    spawned = []
    monkeypatch.setattr(impress, "_spawn",
                        lambda argv: spawned.append(list(argv)) or _FakeProc())
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/usr/bin/soffice" if name == "soffice"
                        else None)
    result = libreoffice_impress_open_file(str(deck))
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["pid"] == 4321
    assert spawned == [["soffice", str(deck.resolve())]]


def test_open_file_not_installed_returns_err(allowed, monkeypatch):
    deck = _mkfile(allowed, "deck.pptx")
    spawned = []
    monkeypatch.setattr(impress, "_spawn",
                        lambda argv: spawned.append(list(argv)) or _FakeProc())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(
        impress.apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "installed": False,
                              "path": None, "name": "LibreOffice Impress"})
    result = libreoffice_impress_open_file(str(deck))
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert spawned == []  # nothing launched


def test_open_file_disallowed_path_rejected(allowed, monkeypatch):
    spawned = []
    monkeypatch.setattr(impress, "_spawn",
                        lambda argv: spawned.append(list(argv)) or _FakeProc())
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/soffice")
    result = libreoffice_impress_open_file("/etc/deck.pptx")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert spawned == []
