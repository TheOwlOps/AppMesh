"""Hermetic tests for the LibreOffice Calc app adapter — fake runners only.

No real ``soffice`` binary is ever invoked: conversions inject a
runner (callable ``(argv, timeout) -> (returncode, stdout, stderr)``,
the ``_common.run_cli`` contract) that records its argv and returns a
canned result, and file-open goes through a monkeypatched ``_spawn``.
The core allowlist is restricted to the test's tmp dir via
``core.ALLOWED_ROOTS`` (the same pattern as the other app tests).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import libreoffice_calc
from desktop_mcp.adapters.apps.libreoffice_calc import (
    build_convert_argv,
    libreoffice_calc_convert,
    libreoffice_calc_open_file,
    register,
)


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    return tmp_path


def fake_runner(result, calls=None):
    """A _common.run_cli-style runner returning a canned tuple."""
    def run(argv, timeout=120):
        if calls is not None:
            calls.append(list(argv))
        return result
    return run


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


class FakeProc:
    pid = 4242


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_the_four_tools():
    mcp = FakeMCP()
    names = register(mcp)
    assert {"libreoffice_calc_status", "libreoffice_calc_launch",
            "libreoffice_calc_convert",
            "libreoffice_calc_open_file"} <= set(mcp.tools)
    for name in ("libreoffice_calc_status", "libreoffice_calc_launch",
                 "libreoffice_calc_convert", "libreoffice_calc_open_file"):
        assert name in mcp.tools
        assert callable(mcp.tools[name])
    assert set(names) == set(mcp.tools)


def test_status_tool_reports_catalog_info():
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["libreoffice_calc_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "libreoffice-calc"
    assert result["strategy"] == "cli"


# ---------------------------------------------------------------------------
# Builder — exact argv
# ---------------------------------------------------------------------------

def test_build_convert_argv_exact_with_default_format():
    assert build_convert_argv("in.ods", "outdir") == [
        "soffice", "--headless", "--convert-to", "xlsx",
        "--outdir", "outdir", "in.ods",
    ]


def test_build_convert_argv_explicit_format():
    assert build_convert_argv("sheet.xlsx", "/tmp/out", "csv") == [
        "soffice", "--headless", "--convert-to", "csv",
        "--outdir", "/tmp/out", "sheet.xlsx",
    ]


# ---------------------------------------------------------------------------
# Convert — injected fake runner
# ---------------------------------------------------------------------------

def test_convert_with_injected_fake_runner(allowed):
    inp = allowed / "sheet.ods"
    inp.write_bytes(b"fake-ods")
    outdir = allowed / "out"
    calls: list = []
    result = libreoffice_calc_convert(
        str(inp), str(outdir),
        runner=fake_runner((0, "out", ""), calls))
    expected_argv = [
        "soffice", "--headless", "--convert-to", "xlsx",
        "--outdir", str(outdir.resolve()), str(inp.resolve()),
    ]
    assert result["ok"] is True
    assert calls == [expected_argv]
    assert result["argv"] == expected_argv
    assert result["returncode"] == 0
    assert result["stdout"] == "out"
    assert result["input"] == str(inp.resolve())
    assert result["output_dir"] == str(outdir.resolve())
    assert result["target_format"] == "xlsx"


def test_convert_via_registered_tool_with_register_runner(allowed):
    inp = allowed / "book.xls"
    inp.write_bytes(b"fake-xls")
    outdir = allowed / "converted"
    calls: list = []
    mcp = FakeMCP()
    register(mcp, runner=fake_runner((0, "out", ""), calls))
    result = mcp.tools["libreoffice_calc_convert"](str(inp), str(outdir))
    assert result["ok"] is True
    assert calls == [[
        "soffice", "--headless", "--convert-to", "xlsx",
        "--outdir", str(outdir.resolve()), str(inp.resolve()),
    ]]


def test_convert_via_module_level_runner_variable(allowed, monkeypatch):
    inp = allowed / "data.csv"
    inp.write_text("a,b\n1,2\n", encoding="utf-8")
    outdir = allowed / "out"
    calls: list = []
    monkeypatch.setattr(
        libreoffice_calc, "RUNNER", fake_runner((0, "out", ""), calls))
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["libreoffice_calc_convert"](
        str(inp), str(outdir), "pdf")
    assert result["ok"] is True
    assert result["target_format"] == "pdf"
    assert calls == [[
        "soffice", "--headless", "--convert-to", "pdf",
        "--outdir", str(outdir.resolve()), str(inp.resolve()),
    ]]


def test_convert_disallowed_input_rejected_runner_never_called(allowed):
    calls: list = []
    result = libreoffice_calc_convert(
        "/etc/secret.ods", str(allowed / "out"),
        runner=fake_runner((0, "out", ""), calls))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []  # runner never called


def test_convert_disallowed_output_dir_rejected_runner_never_called(allowed):
    inp = allowed / "sheet.ods"
    inp.write_bytes(b"fake-ods")
    calls: list = []
    result = libreoffice_calc_convert(
        str(inp), "/etc/out",
        runner=fake_runner((0, "out", ""), calls))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert calls == []  # runner never called


def test_convert_missing_input_rejected_runner_never_called(allowed):
    calls: list = []
    result = libreoffice_calc_convert(
        str(allowed / "missing.ods"), str(allowed / "out"),
        runner=fake_runner((0, "out", ""), calls))
    assert result["ok"] is False
    assert "does not exist" in result["error"]
    assert calls == []  # runner never called


# ---------------------------------------------------------------------------
# Open file — monkeypatched _spawn
# ---------------------------------------------------------------------------

def test_open_file_spawns_soffice_with_checked_path(allowed, monkeypatch):
    target = allowed / "sheet.ods"
    target.write_bytes(b"fake-ods")
    checked = str(target.resolve())

    monkeypatch.setattr(
        shutil, "which",
        lambda name: "soffice" if name == "soffice" else None)
    spawns: list = []

    def fake_spawn(argv):
        spawns.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(libreoffice_calc, "_spawn", fake_spawn)

    result = libreoffice_calc_open_file(str(target))

    assert result["ok"] is True
    assert result["opened"] is True
    assert result["pid"] == 4242
    assert spawns == [["soffice", checked]]
    assert result["argv"] == ["soffice", checked]


def test_open_file_via_registered_tool(allowed, monkeypatch):
    target = allowed / "book.xlsx"
    target.write_bytes(b"fake-xlsx")
    checked = str(target.resolve())

    monkeypatch.setattr(
        shutil, "which",
        lambda name: "soffice" if name == "soffice" else None)
    spawns: list = []
    monkeypatch.setattr(
        libreoffice_calc, "_spawn",
        lambda argv: spawns.append(list(argv)) or FakeProc())

    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["libreoffice_calc_open_file"](str(target))

    assert result["ok"] is True
    assert spawns == [["soffice", checked]]


def test_open_file_not_found_returns_err_and_never_spawns(allowed, monkeypatch):
    target = allowed / "sheet.ods"
    target.write_bytes(b"fake-ods")

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(
        libreoffice_calc.apps_catalog, "detect_app",
        lambda app_id, **kw: {"ok": True, "id": app_id,
                              "name": "LibreOffice Calc",
                              "installed": False, "path": None,
                              "via": None})
    spawns: list = []
    monkeypatch.setattr(
        libreoffice_calc, "_spawn",
        lambda argv: spawns.append(list(argv)))

    result = libreoffice_calc_open_file(str(target))

    assert result["ok"] is False
    assert "soffice" in result["error"]
    assert spawns == []


def test_open_file_disallowed_path_never_spawns(allowed, monkeypatch):
    spawns: list = []
    monkeypatch.setattr(
        libreoffice_calc, "_spawn",
        lambda argv: spawns.append(list(argv)))

    result = libreoffice_calc_open_file("/etc/secret.ods")

    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert spawns == []
