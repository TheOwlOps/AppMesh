"""Hermetic tests for the Houdini adapter — fake runners only.

No real Houdini process ever runs: detection is monkeypatched in the
houdini module namespace (``houdini.detect_app``) and execution goes
through injected ``run_cli``-style runners.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import houdini
from desktop_mcp.adapters.apps.houdini import (
    build_hython_command,
    build_render_command,
    houdini_render,
    houdini_run_script,
)

FAKE_BIN = "C:/Program Files/Side Effects Software/Houdini 20.5/bin"
FAKE_GUI = f"{FAKE_BIN}/houdini.exe"
FAKE_HYTHON = f"{FAKE_BIN}/hython.exe"
FAKE_HRENDER = f"{FAKE_BIN}/hrender.exe"


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


def _detected(path=FAKE_GUI):
    """Patch houdini.detect_app (module namespace) to a fake install."""
    def fake_detect(app_id):
        assert app_id == "houdini"
        return {"ok": True, "id": "houdini", "name": "Houdini",
                "installed": True, "path": path, "via": "path"}
    return fake_detect


def _not_installed(app_id):
    return {"ok": True, "id": "houdini", "name": "Houdini",
            "installed": False, "path": None, "via": None}


def fake_runner(result, calls=None):
    """A run_cli-style runner: callable(argv, timeout) -> (rc, out, err)."""
    def run(argv, timeout):
        if calls is not None:
            calls.append((list(argv), timeout))
        return result
    return run


# ---------------------------------------------------------------------------
# Builders — exact argv
# ---------------------------------------------------------------------------

def test_build_hython_command_exact():
    assert build_hython_command("job.py", exe="hython.exe") == [
        "hython.exe", "job.py",
    ]
    assert build_hython_command("C:/work/job.py", ["--shot", "010"],
                                exe=FAKE_HYTHON) == [
        FAKE_HYTHON, "C:/work/job.py", "--shot", "010",
    ]


def test_build_hython_command_default_exe_and_no_args():
    assert build_hython_command("job.py") == ["hython.exe", "job.py"]
    assert build_hython_command("job.py", []) == ["hython.exe", "job.py"]


def test_build_render_command_exact_with_frame_range():
    assert build_render_command("shot.hip", frame_range=(1, 24),
                                exe="hrender.exe") == [
        "hrender.exe", "-f", "1", "24", "-d", "/out/mantra1", "shot.hip",
    ]


def test_build_render_command_no_frame_range_and_custom_rop():
    assert build_render_command("shot.hip", exe=FAKE_HRENDER) == [
        FAKE_HRENDER, "-d", "/out/mantra1", "shot.hip",
    ]
    assert build_render_command("shot.hip", rop_node="/out/usdrender_rop1",
                                frame_range=[100, 110], exe="hrender") == [
        "hrender", "-f", "100", "110", "-d", "/out/usdrender_rop1", "shot.hip",
    ]


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


def test_register_binds_at_least_three_tools():
    mcp = _FakeMCP()
    names = houdini.register(mcp)
    assert len(names) >= 3
    assert "houdini_status" in names and "houdini_launch" in names
    assert set(mcp.tools) == set(names)
    assert {"houdini_status", "houdini_launch", "houdini_run_script",
            "houdini_render"} <= set(mcp.tools)


# ---------------------------------------------------------------------------
# Status / launch wiring
# ---------------------------------------------------------------------------

def test_status_with_monkeypatched_detect_app(monkeypatch):
    fake = _detected()
    # catalog_status resolves detection through the apps_catalog module
    # object referenced from the houdini module namespace.
    monkeypatch.setattr(houdini.apps_catalog, "detect_app", fake)
    monkeypatch.setattr(houdini, "detect_app", fake)
    res = houdini.houdini_status()
    assert res["ok"] is True
    assert res["app_id"] == "houdini"
    assert res["installed"] is True
    assert res["path"] == FAKE_GUI
    # catalog_status exposes the strategy key (the shared helper fills it
    # from the get_app wrapper, so presence — not a specific value — is
    # the contract every adapter gets).
    assert "strategy" in res
    assert res["name"] == "Houdini"


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(houdini.apps_catalog, "detect_app", _not_installed)
    monkeypatch.setattr(houdini, "detect_app", _not_installed)
    res = houdini.houdini_status()
    assert res["ok"] is True and res["installed"] is False
    assert res["path"] is None


def test_launch_delegates_to_catalog(monkeypatch):
    sentinel = {"ok": True, "launched": True, "app_id": "houdini"}
    monkeypatch.setattr(houdini, "catalog_launch", lambda app_id: sentinel)
    assert houdini.houdini_launch() == sentinel


def test_registered_status_tool(monkeypatch):
    fake = _detected()
    monkeypatch.setattr(houdini.apps_catalog, "detect_app", fake)
    mcp = _FakeMCP()
    houdini.register(mcp)
    res = mcp.tools["houdini_status"]()
    assert res["ok"] is True and res["app_id"] == "houdini"


# ---------------------------------------------------------------------------
# hython script execution
# ---------------------------------------------------------------------------

def test_run_script_happy_path(allowed, monkeypatch):
    monkeypatch.setattr(houdini, "detect_app", _detected())
    script = allowed / "job.py"
    calls = []
    res = houdini_run_script(str(script), ["--shot", "010"],
                             runner=fake_runner((0, "done", ""), calls))
    assert res["ok"] is True
    assert res["exe"] == FAKE_HYTHON  # sibling of the detected GUI exe
    assert res["stdout"] == "done"
    assert calls == [([FAKE_HYTHON, str(script.resolve()), "--shot", "010"],
                      houdini.SCRIPT_TIMEOUT)]


def test_run_script_detected_via_hython_itself(allowed, monkeypatch):
    # The catalog's second exe name is hython.exe itself.
    monkeypatch.setattr(houdini, "detect_app", _detected(path=FAKE_HYTHON))
    calls = []
    res = houdini_run_script(str(allowed / "job.py"),
                             runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is True
    assert calls[0][0][0] == FAKE_HYTHON


def test_run_script_nonzero_exit_is_err(allowed, monkeypatch):
    monkeypatch.setattr(houdini, "detect_app", _detected())
    res = houdini_run_script(str(allowed / "job.py"),
                             runner=fake_runner((2, "", "hou blew up")))
    assert res["ok"] is False and "hou blew up" in res["error"]


def test_run_script_missing_exe_returns_err_dict(allowed, monkeypatch):
    monkeypatch.setattr(houdini, "detect_app", _not_installed)
    calls = []
    res = houdini_run_script(str(allowed / "job.py"),
                             runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not installed" in res["error"].lower()
    assert "hython" in res["error"]
    assert calls == []  # runner never invoked


def test_run_script_rejects_disallowed_path(allowed, monkeypatch):
    monkeypatch.setattr(houdini, "detect_app", _detected())
    calls = []
    res = houdini_run_script("/etc/passwd.py",
                             runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []


# ---------------------------------------------------------------------------
# hrender execution
# ---------------------------------------------------------------------------

def test_render_happy_path_with_frame_range(allowed, monkeypatch):
    monkeypatch.setattr(houdini, "detect_app", _detected())
    hip = allowed / "shot.hip"
    calls = []
    res = houdini_render(str(hip), frame_range=(1, 24),
                         runner=fake_runner((0, "rendered", ""), calls))
    assert res["ok"] is True
    assert res["exe"] == FAKE_HRENDER
    assert res["rop_node"] == "/out/mantra1"
    assert res["frame_range"] == [1, 24]
    assert calls == [([FAKE_HRENDER, "-f", "1", "24", "-d", "/out/mantra1",
                        str(hip.resolve())], houdini.RENDER_TIMEOUT)]


def test_render_missing_exe_returns_err_dict(allowed, monkeypatch):
    monkeypatch.setattr(houdini, "detect_app", _not_installed)
    calls = []
    res = houdini_render(str(allowed / "shot.hip"),
                         runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not installed" in res["error"].lower()
    assert calls == []


def test_render_rejects_etc_passwd_hip(allowed, monkeypatch):
    monkeypatch.setattr(houdini, "detect_app", _detected())
    calls = []
    res = houdini_render("/etc/passwd.hip",
                         runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []
