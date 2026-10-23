"""Hermetic tests for the Cinema 4D adapter (plugin strategy).

No real Cinema 4D install is required: detection goes through a
monkeypatched ``detect_app`` (patched both in the ``cinema_4d``
module namespace and in ``apps_catalog``, which is where
``_common.catalog_status`` looks it up), and execution through the
injectable ``runner`` forwarded to ``_common.run_cli``.
"""
from __future__ import annotations

from pathlib import Path

from desktop_mcp import apps_catalog
from desktop_mcp.adapters.apps import cinema_4d
from desktop_mcp.adapters.apps.cinema_4d import (
    build_c4dpy_command,
    build_render_command,
    register,
    render_scene,
    run_script,
)

ALLOWED_SCENE = str(Path.home() / "scene.c4d")
ALLOWED_OUTPUT = str(Path.home() / "render.png")
ALLOWED_SCRIPT = str(Path.home() / "script.py")
FAKE_GUI = "C:/Program Files/Maxon Cinema 4D 2024/Cinema 4D.exe"
FAKE_RENDER = "C:/Program Files/Maxon Cinema 4D 2024/Commandline.exe"
FAKE_C4DPY = "C:/Program Files/Maxon Cinema 4D 2024/c4dpy.exe"


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _fake_detect(installed=True, path=FAKE_GUI):
    """Build a detect_app replacement with the real return shape."""
    def _detect(app_id, which_fn=None):
        assert app_id == "cinema-4d"
        return {
            "ok": True,
            "id": "cinema-4d",
            "name": "Cinema 4D",
            "category": "CAD & 3D",
            "strategy": "plugin",
            "prog_id": None,
            "installed": installed,
            "path": path if installed else None,
            "via": "path" if installed else None,
        }

    return _detect


def _patch_detect(monkeypatch, installed=True, path=FAKE_GUI):
    """Patch detect_app in the cinema_4d module namespace (and in
    apps_catalog, the module _common.catalog_status reads it from)."""
    fake = _fake_detect(installed=installed, path=path)
    monkeypatch.setattr(cinema_4d, "detect_app", fake)
    monkeypatch.setattr(apps_catalog, "detect_app", fake)
    return fake


# ------------------------------------------------------------------ register

def test_register_binds_at_least_three_tools():
    mcp = FakeMCP()
    register(mcp)
    assert len(mcp.tools) >= 3
    assert "cinema_4d_status" in mcp.tools
    assert "cinema_4d_launch" in mcp.tools
    assert "cinema_4d_render" in mcp.tools
    assert "cinema_4d_run_script" in mcp.tools


def test_app_id_matches_catalog():
    assert cinema_4d.APP_ID == "cinema-4d"


# -------------------------------------------------------------------- status

def test_status_via_monkeypatched_detect(monkeypatch):
    _patch_detect(monkeypatch)
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["cinema_4d_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "cinema-4d"
    assert result["installed"] is True
    assert result["path"] == FAKE_GUI
    assert result["strategy"] == "plugin"
    assert result["prog_id"] is None


def test_status_not_installed(monkeypatch):
    _patch_detect(monkeypatch, installed=False)
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["cinema_4d_status"]()
    assert result["ok"] is True
    assert result["installed"] is False
    assert result["path"] is None


# ----------------------------------------------------------------- builders

def test_build_render_command_basic_exact_argv():
    assert build_render_command(FAKE_RENDER, "scene.c4d", "out.png") == [
        FAKE_RENDER, "-render", "scene.c4d", "-oimage", "out.png",
    ]


def test_build_render_command_frame_range_exact_argv():
    argv = build_render_command(FAKE_RENDER, "scene.c4d", "out.png",
                                frame_range=(0, 10))
    assert argv == [
        FAKE_RENDER, "-render", "scene.c4d",
        "-frame", "0", "10",
        "-oimage", "out.png",
    ]


def test_build_render_command_resolution_exact_argv():
    argv = build_render_command(FAKE_RENDER, "scene.c4d", "out.png",
                                resolution=(1920, 1080))
    assert argv == [
        FAKE_RENDER, "-render", "scene.c4d",
        "-oimage", "out.png",
        "-oresolution", "1920", "1080",
    ]


def test_build_render_command_frame_range_and_resolution_exact_argv():
    argv = build_render_command(FAKE_RENDER, "scene.c4d", "out.png",
                                frame_range=(5, 25), resolution=(1280, 720))
    assert argv == [
        FAKE_RENDER, "-render", "scene.c4d",
        "-frame", "5", "25",
        "-oimage", "out.png",
        "-oresolution", "1280", "720",
    ]


def test_build_c4dpy_command_exact_argv():
    assert build_c4dpy_command(FAKE_C4DPY, "script.py") == [FAKE_C4DPY, "script.py"]
    assert build_c4dpy_command(FAKE_C4DPY, "script.py", script_args=["--flag"]) == [
        FAKE_C4DPY, "script.py", "--flag",
    ]


def test_sibling_exe_derivation():
    assert cinema_4d._render_exe_from_gui(FAKE_GUI) == FAKE_RENDER
    assert cinema_4d._c4dpy_exe_from_gui(FAKE_GUI) == FAKE_C4DPY


# ------------------------------------------------------------------- render

def test_render_via_fake_runner_exact_argv(monkeypatch):
    _patch_detect(monkeypatch)
    captured = []

    def fake_runner(argv, timeout=120):
        captured.append(list(argv))
        return (0, "render done", "")

    result = render_scene(ALLOWED_SCENE, ALLOWED_OUTPUT,
                          frame_range=(0, 10), resolution=(1920, 1080),
                          runner=fake_runner)
    assert result["ok"] is True
    assert result["route"] == "commandline"
    assert result["render_exe"] == FAKE_RENDER
    assert result["returncode"] == 0
    assert result["stdout"] == "render done"
    expected = [
        FAKE_RENDER, "-render", str(Path(ALLOWED_SCENE).resolve()),
        "-frame", "0", "10",
        "-oimage", str(Path(ALLOWED_OUTPUT).resolve()),
        "-oresolution", "1920", "1080",
    ]
    assert captured == [expected]
    assert result["argv"] == expected


def test_render_tool_wrapper_builds_frame_range(monkeypatch):
    _patch_detect(monkeypatch)
    captured = []

    def fake_run_cli(argv, timeout=120, runner=None):
        captured.append(list(argv))
        return {"ok": True, "argv": argv, "returncode": 0,
                "stdout": "", "stderr": ""}

    monkeypatch.setattr(cinema_4d, "run_cli", fake_run_cli)
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["cinema_4d_render"](
        ALLOWED_SCENE, ALLOWED_OUTPUT, frame_start=2, frame_end=4)
    assert result["ok"] is True
    assert captured == [[
        FAKE_RENDER, "-render", str(Path(ALLOWED_SCENE).resolve()),
        "-frame", "2", "4",
        "-oimage", str(Path(ALLOWED_OUTPUT).resolve()),
    ]]
    # Half-specified optional groups are a clear error, not a render.
    bad = mcp.tools["cinema_4d_render"](ALLOWED_SCENE, ALLOWED_OUTPUT,
                                        frame_start=1)
    assert bad["ok"] is False and "frame_start and frame_end" in bad["error"]


def test_render_missing_exe_is_graceful_err(monkeypatch):
    _patch_detect(monkeypatch, installed=False)
    monkeypatch.setattr(cinema_4d.shutil, "which", lambda name: None)

    def runner_must_not_run(argv, timeout=120):  # pragma: no cover
        raise AssertionError("runner must not be called without a renderer exe")

    result = render_scene(ALLOWED_SCENE, ALLOWED_OUTPUT,
                          runner=runner_must_not_run)
    assert result["ok"] is False
    assert "Commandline.exe" in result["error"]


def test_render_rejects_disallowed_scene_path(monkeypatch):
    _patch_detect(monkeypatch)
    result = render_scene("/etc/passwd.c4d", ALLOWED_OUTPUT,
                          runner=lambda argv, timeout=120: (0, "", ""))
    assert result["ok"] is False
    assert "not allowed" in result["error"]
    mcp = FakeMCP()
    register(mcp)
    result = mcp.tools["cinema_4d_render"]("/etc/passwd.c4d", ALLOWED_OUTPUT)
    assert result["ok"] is False
    assert "not allowed" in result["error"]


def test_render_rejects_disallowed_output_path(monkeypatch):
    _patch_detect(monkeypatch)
    result = render_scene(ALLOWED_SCENE, "/etc/passwd.png",
                          runner=lambda argv, timeout=120: (0, "", ""))
    assert result["ok"] is False
    assert "not allowed" in result["error"]


def test_render_nonzero_exit_is_err(monkeypatch):
    _patch_detect(monkeypatch)
    result = render_scene(ALLOWED_SCENE, ALLOWED_OUTPUT,
                          runner=lambda argv, timeout=120: (1, "", "boom"))
    assert result["ok"] is False
    assert "boom" in result["error"] or "exited 1" in result["error"]


# --------------------------------------------------------------- run_script

def test_run_script_via_fake_runner_exact_argv(monkeypatch):
    _patch_detect(monkeypatch)
    captured = []

    def fake_runner(argv, timeout=120):
        captured.append(list(argv))
        return (0, "script done", "")

    result = run_script(ALLOWED_SCRIPT, runner=fake_runner)
    assert result["ok"] is True
    assert result["route"] == "c4dpy"
    assert result["c4dpy_exe"] == FAKE_C4DPY
    assert result["returncode"] == 0
    assert result["stdout"] == "script done"
    assert captured == [[FAKE_C4DPY, str(Path(ALLOWED_SCRIPT).resolve())]]
    assert result["argv"] == captured[0]


def test_run_script_falls_back_to_c4dpy_on_path(monkeypatch):
    _patch_detect(monkeypatch, installed=False)
    monkeypatch.setattr(
        cinema_4d.shutil, "which",
        lambda name: "/opt/maxon/c4dpy" if name == "c4dpy" else None,
    )
    captured = []

    def fake_runner(argv, timeout=120):
        captured.append(list(argv))
        return (0, "", "")

    result = run_script(ALLOWED_SCRIPT, runner=fake_runner)
    assert result["ok"] is True
    assert captured[0][0] == "/opt/maxon/c4dpy"
    assert captured[0][1] == str(Path(ALLOWED_SCRIPT).resolve())


def test_run_script_missing_exe_is_graceful_err(monkeypatch):
    _patch_detect(monkeypatch, installed=False)
    monkeypatch.setattr(cinema_4d.shutil, "which", lambda name: None)

    def runner_must_not_run(argv, timeout=120):  # pragma: no cover
        raise AssertionError("runner must not be called without c4dpy")

    result = run_script(ALLOWED_SCRIPT, runner=runner_must_not_run)
    assert result["ok"] is False
    assert "c4dpy" in result["error"]


def test_run_script_rejects_disallowed_path(monkeypatch):
    _patch_detect(monkeypatch)
    result = run_script("/etc/passwd.py",
                        runner=lambda argv, timeout=120: (0, "", ""))
    assert result["ok"] is False
    assert "not allowed" in result["error"]
