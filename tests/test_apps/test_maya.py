"""Hermetic tests for the Maya adapter (adapters/apps/maya.py).

No real Maya is probed, launched or executed: detection goes through
monkeypatched ``apps_catalog.detect_app``, process spawning through a
fake Popen / injected ``popen``, and headless script runs through a
fake ``run_cli`` runner plus a fake ``which_fn``.
"""
from __future__ import annotations

import pytest

from desktop_mcp import apps_catalog, core
from desktop_mcp.adapters.apps import maya


class FakeMCP:
    """Minimal FastMCP stand-in capturing registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeProc:
    pid = 9876


@pytest.fixture()
def allowed(tmp_path, monkeypatch):
    """Restrict the path allowlist to tmp_path for hermetic checks."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


def fake_detect(installed=True, path="C:/Program Files/Autodesk/Maya2025/bin/maya.exe"):
    def _detect(app_id, which_fn=None):
        assert app_id == "maya"
        return {
            "ok": True, "id": "maya", "name": "Maya",
            "category": "CAD & 3D", "strategy": "plugin", "prog_id": None,
            "installed": installed, "path": path if installed else None,
            "via": "path" if installed else None,
        }
    return _detect


def fake_which(found):
    def _which(name):
        return found.get(name)
    return _which


# ---------------------------------------------------------------- register

def test_register_binds_at_least_three_tools():
    mcp = FakeMCP()
    maya.register(mcp)
    assert len(mcp.tools) >= 3
    assert {"maya_status", "maya_launch", "maya_open_file",
            "maya_run_script"} <= set(mcp.tools)


# ------------------------------------------------------------------ status

def test_status_via_monkeypatched_detect(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    mcp = FakeMCP()
    maya.register(mcp)
    result = mcp.tools["maya_status"]()
    assert result["ok"] is True
    assert result["app_id"] == "maya"
    assert result["installed"] is True
    assert result["path"].endswith("maya.exe")
    assert result["via"] == "path"
    # NB: catalog_status reads strategy/name off the get_app() wrapper,
    # whose payload nests them under "app" — so the live helper surfaces
    # strategy=None; the authoritative strategy is in the catalog entry.
    assert apps_catalog.get_app("maya")["app"]["strategy"] == "plugin"


def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    result = maya._common.catalog_status(maya.APP_ID)
    assert result["ok"] is True and result["installed"] is False


# --------------------------------------------------------------- open_file

def test_open_file_via_detected_exe_with_fake_popen(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    scene = allowed / "shot010.ma"
    scene.write_text("// Maya ASCII scene\n", encoding="utf-8")
    spawned = []

    def fake_popen(argv, close_fds=True):
        spawned.append(argv)
        return FakeProc()

    result = maya.open_file(str(scene), popen=fake_popen)
    assert result["ok"] is True
    assert result["opened"] is True and result["route"] == "exe"
    assert result["pid"] == 9876
    assert spawned == [["C:/Program Files/Autodesk/Maya2025/bin/maya.exe",
                        str(scene.resolve())]]


def test_open_file_tool_with_monkeypatched_subprocess(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    scene = allowed / "scene.mb"
    scene.write_bytes(b"binary")
    spawned = []
    monkeypatch.setattr(
        maya.subprocess, "Popen",
        lambda argv, close_fds=True: spawned.append(argv) or FakeProc())
    mcp = FakeMCP()
    maya.register(mcp)
    result = mcp.tools["maya_open_file"](str(scene))
    assert result["ok"] is True and result["route"] == "exe"
    assert spawned and spawned[0][1] == str(scene.resolve())


def test_open_file_not_installed(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    scene = allowed / "shot.ma"
    scene.write_text("x", encoding="utf-8")
    result = maya.open_file(str(scene), popen=lambda *a, **k: pytest.fail("no spawn"))
    assert result["ok"] is False and "not installed" in result["error"]


def test_open_file_rejects_disallowed_path(monkeypatch):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    result = maya.open_file("/etc/passwd.ma",
                            popen=lambda *a, **k: pytest.fail("no spawn"))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_open_file_rejects_non_scene_extension(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    bad = allowed / "notes.txt"
    bad.write_text("x", encoding="utf-8")
    result = maya.open_file(str(bad), popen=lambda *a, **k: pytest.fail("no spawn"))
    assert result["ok"] is False and "scene" in result["error"]


# -------------------------------------------------------------- run_script

def _make_script(allowed, name, text="# script\n"):
    script = allowed / name
    script.write_text(text, encoding="utf-8")
    return script


def test_run_script_python_via_mayapy_argv(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    script = _make_script(allowed, "build.py", "import maya.cmds as cmds\n")
    captured = {}

    def fake_runner(argv, timeout):
        captured["argv"] = argv
        captured["timeout"] = timeout
        return 0, "built ok", ""

    which = fake_which({"mayapy.exe": "C:/Maya/bin/mayapy.exe"})
    result = maya.run_script(str(script), runner=fake_runner, which_fn=which)
    assert result["ok"] is True
    assert result["route"] == "mayapy"
    assert captured["argv"] == ["C:/Maya/bin/mayapy.exe", str(script.resolve())]
    assert result["argv"] == captured["argv"]
    assert result["stdout"] == "built ok"


def test_run_script_mel_via_mayabatch_argv(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    script = _make_script(allowed, "setup.mel", "sphere;\n")
    captured = {}

    def fake_runner(argv, timeout):
        captured["argv"] = argv
        return 0, "", ""

    which = fake_which({"mayabatch.exe": "C:/Maya/bin/mayabatch.exe"})
    result = maya.run_script(str(script), runner=fake_runner, which_fn=which)
    assert result["ok"] is True and result["route"] == "mayabatch"
    posix = str(script.resolve()).replace("\\", "/")
    assert captured["argv"] == ["C:/Maya/bin/mayabatch.exe", "-command",
                                f'source "{posix}"']


def test_run_script_python_falls_back_to_mayabatch(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    script = _make_script(allowed, "job.py")
    captured = {}

    def fake_runner(argv, timeout):
        captured["argv"] = argv
        return 0, "", ""

    which = fake_which({"mayabatch.exe": "C:/Maya/bin/mayabatch.exe"})
    result = maya.run_script(str(script), runner=fake_runner, which_fn=which)
    assert result["ok"] is True and result["route"] == "mayabatch"
    posix = str(script.resolve()).replace("\\", "/")
    assert captured["argv"] == [
        "C:/Maya/bin/mayabatch.exe", "-command",
        f'python("exec(open(\'{posix}\').read())")',
    ]


def test_run_script_uses_sibling_of_detected_maya(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=True))
    script = _make_script(allowed, "sibling.py")
    captured = {}

    def fake_runner(argv, timeout):
        captured["argv"] = argv
        return 0, "", ""

    result = maya.run_script(str(script), runner=fake_runner,
                             which_fn=fake_which({}))
    assert result["ok"] is True and result["route"] == "mayapy"
    assert captured["argv"][0].endswith("mayapy.exe")
    assert "Maya2025/bin" in captured["argv"][0].replace("\\", "/")


def test_run_script_missing_exe_is_graceful_err(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    script = _make_script(allowed, "orphan.py")
    result = maya.run_script(
        str(script),
        runner=lambda *a, **k: pytest.fail("runner must not be called"),
        which_fn=fake_which({}))
    assert result["ok"] is False
    assert "mayapy" in result["error"] and "mayabatch" in result["error"]


def test_run_script_rejects_disallowed_path(monkeypatch):
    result = maya.run_script("/etc/passwd.py", runner=lambda *a, **k: (0, "", ""),
                             which_fn=fake_which({"mayapy.exe": "/x/mayapy.exe"}))
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_run_script_nonzero_exit_becomes_err(monkeypatch, allowed):
    monkeypatch.setattr(apps_catalog, "detect_app", fake_detect(installed=False))
    script = _make_script(allowed, "boom.py")
    result = maya.run_script(
        str(script),
        runner=lambda argv, timeout: (3, "", "NameError: cmds"),
        which_fn=fake_which({"mayapy.exe": "C:/Maya/bin/mayapy.exe"}))
    assert result["ok"] is False and "exited 3" in result["error"]
