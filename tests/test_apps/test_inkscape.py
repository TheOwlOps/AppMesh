"""Hermetic tests for the Inkscape app adapter — fake runners only.

No real Inkscape binary is ever invoked: every execution path injects
a runner (callable ``(argv, timeout) -> (returncode, stdout, stderr)``,
the ``_common.run_cli`` contract) that records its argv and returns a
canned result. Builders are asserted argv-for-argv, and the core
allowlist is restricted to the test's tmp dir via ``core.ALLOWED_ROOTS``
(the same pattern as tests/test_cli_tools.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import inkscape
from desktop_mcp.adapters.apps.inkscape import (
    build_convert_command,
    build_version_command,
    inkscape_convert,
    inkscape_version,
    register,
)


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
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

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_at_least_four_tools():
    mcp = FakeMCP()
    names = register(mcp)
    assert len(mcp.tools) >= 4
    assert {"inkscape_status", "inkscape_launch",
            "inkscape_convert", "inkscape_version"} <= set(mcp.tools)
    assert set(names) == set(mcp.tools)


def test_status_tool_reports_catalog_info():
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["inkscape_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "inkscape"
    assert isinstance(result["installed"], bool)


# ---------------------------------------------------------------------------
# Builders — exact argv
# ---------------------------------------------------------------------------

def test_build_convert_command_argv():
    assert build_convert_command("in.svg", "out.png") == [
        "inkscape", "in.svg", "-o", "out.png",
    ]
    assert build_convert_command("a.svg", "b.pdf", exe="inkscape.exe") == [
        "inkscape.exe", "a.svg", "-o", "b.pdf",
    ]


def test_build_version_command_argv():
    assert build_version_command() == ["inkscape", "--version"]
    assert build_version_command(exe="inkscape.exe") == [
        "inkscape.exe", "--version",
    ]


# ---------------------------------------------------------------------------
# Convert — fake runner
# ---------------------------------------------------------------------------

def test_convert_with_fake_runner(allowed):
    inp = allowed / "drawing.svg"
    inp.write_text("<svg/>", encoding="utf-8")
    out = allowed / "drawing.png"
    calls: list = []
    result = inkscape_convert(
        str(inp), str(out),
        runner=fake_runner((0, "", ""), calls))
    assert result["ok"] is True
    assert calls == [["inkscape", str(inp.resolve()), "-o", str(out.resolve())]]
    assert result["argv"] == calls[0]
    assert result["returncode"] == 0
    assert result["input"] == str(inp.resolve())
    assert result["output"] == str(out.resolve())


def test_convert_via_registered_tool(allowed):
    inp = allowed / "icon.svg"
    inp.write_text("<svg/>", encoding="utf-8")
    out = allowed / "icon.pdf"
    calls: list = []
    mcp = FakeMCP()
    register(mcp, runner=fake_runner((0, "", ""), calls))
    result = mcp.tools["inkscape_convert"](str(inp), str(out))
    assert result["ok"] is True
    assert calls == [["inkscape", str(inp.resolve()), "-o", str(out.resolve())]]


def test_convert_nonzero_exit_is_err(allowed):
    inp = allowed / "bad.svg"
    inp.write_text("not svg", encoding="utf-8")
    result = inkscape_convert(
        str(inp), str(allowed / "bad.png"),
        runner=fake_runner((1, "", "parse error")))
    assert result["ok"] is False
    assert "parse error" in result["error"]


def test_convert_disallowed_input_path(allowed):
    calls: list = []
    result = inkscape_convert(
        "/etc/passwd.svg", str(allowed / "out.png"),
        runner=fake_runner((0, "", ""), calls))
    assert result["ok"] is False
    assert result["error"]
    assert calls == []  # runner NOT called


def test_convert_disallowed_output_path(allowed):
    inp = allowed / "drawing.svg"
    inp.write_text("<svg/>", encoding="utf-8")
    calls: list = []
    result = inkscape_convert(
        str(inp), "/etc/out.png",
        runner=fake_runner((0, "", ""), calls))
    assert result["ok"] is False
    assert calls == []  # runner NOT called


def test_convert_missing_input(allowed):
    calls: list = []
    result = inkscape_convert(
        str(allowed / "missing.svg"), str(allowed / "out.png"),
        runner=fake_runner((0, "", ""), calls))
    assert result["ok"] is False
    assert "does not exist" in result["error"]
    assert calls == []  # runner NOT called


# ---------------------------------------------------------------------------
# Version — fake runner
# ---------------------------------------------------------------------------

def test_version_with_fake_runner():
    calls: list = []
    result = inkscape_version(
        runner=fake_runner((0, "Inkscape 1.3 (deadbeef, 2023-07-01)\n", ""),
                           calls))
    assert result["ok"] is True
    assert calls == [["inkscape", "--version"]]
    assert "Inkscape 1.3" in result["version"]
    assert "Inkscape 1.3" in result["stdout"]


def test_version_via_registered_tool():
    mcp = FakeMCP()
    register(mcp, runner=fake_runner((0, "Inkscape 1.3.2\n", "")))
    result = mcp.tools["inkscape_version"]()
    assert result["ok"] is True
    assert result["version"] == "Inkscape 1.3.2"
