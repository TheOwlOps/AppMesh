"""Hermetic tests for the HandBrake adapter — fake runners only.

No real transcode ever runs: the CLI runner is injected and the CLI
executable resolution is monkeypatched (or fed a fake ``which_fn``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import handbrake
from desktop_mcp.adapters.apps.handbrake import (
    build_convert_argv,
    handbrake_convert,
    resolve_cli_exe,
)

FAKE_CLI = "/fake/bin/HandBrakeCLI"


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


@pytest.fixture
def fake_cli(monkeypatch):
    """Make CLI resolution succeed with a fake path."""
    monkeypatch.setattr(handbrake, "resolve_cli_exe", lambda: FAKE_CLI)
    return FAKE_CLI


def _mkfile(base: Path, name: str, data: bytes = b"x") -> Path:
    p = base / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def fake_runner(result, calls=None):
    """A run_cli-style runner: callable(argv, timeout) -> (rc, out, err)."""
    def run(argv, timeout):
        if calls is not None:
            calls.append((list(argv), timeout))
        return result
    return run


# ---------------------------------------------------------------------------
# Builder — exact argv
# ---------------------------------------------------------------------------

def test_build_convert_argv_exact_with_default_preset():
    assert build_convert_argv("HandBrakeCLI", "in.mkv", "out.mp4") == [
        "HandBrakeCLI", "-i", "in.mkv", "-o", "out.mp4",
        "--preset", "Fast 1080p30",
    ]


def test_build_convert_argv_explicit_preset():
    assert build_convert_argv(FAKE_CLI, "a.mov", "b.mp4", "HQ 1080p30 Surround") == [
        FAKE_CLI, "-i", "a.mov", "-o", "b.mp4",
        "--preset", "HQ 1080p30 Surround",
    ]


# ---------------------------------------------------------------------------
# Happy path via injected runner
# ---------------------------------------------------------------------------

def test_convert_happy_default_preset(allowed, fake_cli):
    inp = _mkfile(allowed, "in.mkv")
    out = allowed / "sub" / "out.mp4"
    calls = []
    res = handbrake_convert(str(inp), str(out),
                            runner=fake_runner((0, "Encode done!", ""), calls))
    assert res["ok"] is True
    assert res["input"] == str(inp.resolve())
    assert res["output"] == str(out.resolve())
    assert res["preset"] == "Fast 1080p30"
    assert calls == [(
        [FAKE_CLI, "-i", str(inp.resolve()), "-o", str(out.resolve()),
         "--preset", "Fast 1080p30"],
        handbrake.CONVERT_TIMEOUT,
    )]


def test_convert_nonzero_exit_is_err(allowed, fake_cli):
    inp = _mkfile(allowed, "in.mkv")
    res = handbrake_convert(str(inp), str(allowed / "out.mp4"),
                            runner=fake_runner((1, "", "bad source")))
    assert res["ok"] is False
    assert "bad source" in res["error"]


# ---------------------------------------------------------------------------
# CLI resolution
# ---------------------------------------------------------------------------

def test_resolve_cli_exe_prefers_handbrakecli_on_path():
    def which(name):
        return "/hb/HandBrakeCLI.exe" if name == "HandBrakeCLI" else None
    assert resolve_cli_exe(which_fn=which) == "/hb/HandBrakeCLI.exe"


def test_resolve_cli_exe_gui_only_is_not_enough():
    # The GUI exe (HandBrake.exe) cannot transcode — detection landing
    # on it must NOT be accepted as the CLI.
    def which(name):
        return "/hb/HandBrake.exe" if name == "HandBrake.exe" else None
    assert resolve_cli_exe(which_fn=which) is None


def test_resolve_cli_exe_missing_everywhere():
    assert resolve_cli_exe(which_fn=lambda name: None) is None


def test_convert_missing_cli_returns_err(allowed, monkeypatch):
    monkeypatch.setattr(handbrake, "resolve_cli_exe", lambda: None)
    inp = _mkfile(allowed, "in.mkv")
    calls = []
    res = handbrake_convert(str(inp), str(allowed / "out.mp4"),
                            runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "HandBrakeCLI" in res["error"]
    assert calls == []  # runner never invoked


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------

def test_convert_disallowed_input_rejected(allowed, fake_cli):
    calls = []
    res = handbrake_convert("/etc/secret.mkv", str(allowed / "out.mp4"),
                            runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []


def test_convert_disallowed_output_rejected(allowed, fake_cli):
    inp = _mkfile(allowed, "in.mkv")
    calls = []
    res = handbrake_convert(str(inp), "/etc/out.mp4",
                            runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []


# ---------------------------------------------------------------------------
# Registration + status/launch wiring
# ---------------------------------------------------------------------------

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_binds_all_tools():
    mcp = _FakeMCP()
    names = handbrake.register(mcp)
    assert set(names) == {"handbrake_status", "handbrake_launch",
                          "handbrake_convert"}
    assert set(mcp.tools) == set(names)


def test_status_reports_catalog_info():
    res = handbrake.handbrake_status()
    assert res["ok"] is True
    assert res["app_id"] == "handbrake"
    assert isinstance(res["installed"], bool)
    assert "strategy" in res


def test_launch_delegates_to_catalog(monkeypatch):
    sentinel = {"ok": True, "launched": True, "app_id": "handbrake"}
    monkeypatch.setattr(handbrake, "catalog_launch", lambda app_id: sentinel)
    assert handbrake.handbrake_launch() == sentinel


def test_registered_convert_uses_default_preset(allowed, fake_cli, monkeypatch):
    inp = _mkfile(allowed, "in.mkv")
    out = allowed / "out.mp4"
    calls = []

    def fake_run_cli(argv, timeout=120, runner=None):
        calls.append(list(argv))
        return {"ok": True, "argv": argv, "returncode": 0,
                "stdout": "", "stderr": ""}

    monkeypatch.setattr(handbrake, "run_cli", fake_run_cli)
    mcp = _FakeMCP()
    handbrake.register(mcp)
    res = mcp.tools["handbrake_convert"](str(inp), str(out))
    assert res["ok"] is True
    assert calls == [[
        FAKE_CLI, "-i", str(inp.resolve()), "-o", str(out.resolve()),
        "--preset", "Fast 1080p30",
    ]]
