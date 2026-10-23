"""Autodesk Maya adapter — plugin strategy.

Catalog entry ``maya`` (strategy ``plugin``, exe ``maya.exe``, no COM
ProgID): Maya is automated through its own scripting APIs — MEL and
Python (``maya.cmds`` / OpenMaya) — not through COM or UI clicking.

Tools:
- ``maya_status``     catalog detection + strategy info.
- ``maya_launch``     launch the installed ``maya.exe``.
- ``maya_open_file``  open a scene (.ma / .mb) by launching the detected
  ``maya.exe`` with the file as its argument (Maya's own file-open route).
- ``maya_run_script`` run a .py or .mel script headlessly:
  .py scripts go to ``mayapy.exe`` (Maya's standalone Python
  interpreter, ``mayapy script.py``); when mayapy is unavailable they
  fall back to ``mayabatch.exe -command 'python("...")'``. .mel
  scripts go to ``mayabatch.exe -command 'source "..."'`` (mayabatch
  is Maya's headless batch host); when only mayapy is present, the
  MEL file is sourced through ``maya.mel`` instead.

Every tool returns a ``core.ok`` / ``core.err`` dict and never raises.
The headless runner (``run_cli``'s ``runner``), the executable lookup
(``which_fn``) and the process spawner (``popen``) are injectable so
the adapter is fully testable off-Windows.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import run_cli

APP_ID = "maya"

_SCENE_SUFFIXES = {".ma", ".mb"}
_SCRIPT_SUFFIXES = {".py", ".mel"}


# --------------------------------------------------------------- helpers

def _detect() -> dict:
    return apps_catalog.detect_app(APP_ID)


def _which_first(names, which_fn) -> str | None:
    """First exe *which_fn* finds among *names*, else None."""
    for name in names:
        try:
            found = which_fn(name)
        except Exception:  # noqa: BLE001 — lookup must never break a tool
            found = None
        if found:
            return str(found)
    return None


def _resolve_headless_exe(kind: str, which_fn, maya_exe: str | None) -> str | None:
    """Resolve ``mayapy.exe`` / ``mayabatch.exe`` for a headless run.

    Order: PATH lookup first, then the sibling of the detected
    ``maya.exe`` — mayapy and mayabatch ship in the same ``bin``
    folder as maya.exe in every standard Maya install, so a detected
    Maya implies that sibling location. Returns None when neither
    route yields an executable (caller returns a graceful error).
    """
    names = [f"{kind}.exe", kind]
    found = _which_first(names, which_fn)
    if found:
        return found
    if maya_exe:
        return str(Path(maya_exe).with_name(f"{kind}.exe"))
    return None


def build_run_script_argv(script: Path, which_fn=shutil.which) -> dict:
    """Build the headless argv for *script* (pure builder, no execution).

    Returns ``ok(argv=[...], route="mayapy"|"mayabatch", exe=...)`` or
    an ``err`` dict when no headless Maya executable can be found.
    """
    suffix = script.suffix.lower()
    det = _detect()
    maya_exe = det.get("path") if det.get("ok") and det.get("installed") else None

    mayapy = _resolve_headless_exe("mayapy", which_fn, maya_exe)
    mayabatch = _resolve_headless_exe("mayabatch", which_fn, maya_exe)

    posix = str(script).replace("\\", "/")
    if suffix == ".py":
        if mayapy:
            return ok(argv=[mayapy, str(script)], route="mayapy", exe=mayapy)
        if mayabatch:
            # mayabatch's -command takes MEL; MEL's python() runs the
            # file's Python source.
            mel = f'python("exec(open(\'{posix}\').read())")'
            return ok(argv=[mayabatch, "-command", mel],
                      route="mayabatch", exe=mayabatch)
    elif suffix == ".mel":
        if mayabatch:
            return ok(argv=[mayabatch, "-command", f'source "{posix}"'],
                      route="mayabatch", exe=mayabatch)
        if mayapy:
            code = f'import maya.mel as mel; mel.eval(\'source "{posix}"\')'
            return ok(argv=[mayapy, "-c", code], route="mayapy", exe=mayapy)
    return err(
        "No headless Maya executable found (looked for mayapy.exe and "
        "mayabatch.exe on PATH and beside the detected maya.exe). "
        "Is Maya installed on this machine?"
    )


# ----------------------------------------------------------------- tools

def open_file(path: str, popen=None) -> dict:
    """Open a Maya scene file (.ma / .mb) with the detected maya.exe."""
    try:
        checked = check_path_allowed(path)
        if checked.suffix.lower() not in _SCENE_SUFFIXES:
            return err(
                f"Not a Maya scene file: {checked.name} "
                f"(expected one of: {', '.join(sorted(_SCENE_SUFFIXES))})."
            )
        det = _detect()
        if not det.get("ok"):
            return det
        if not det.get("installed") or not det.get("path"):
            return err(
                "Maya is not installed (maya.exe was not found on this "
                "machine), so the scene cannot be opened."
            )
        exe = str(det["path"])
        spawn = popen if popen is not None else subprocess.Popen
        proc = spawn([exe, str(checked)], close_fds=True)
        pid = getattr(proc, "pid", None)
        audit("maya_open_file", {"path": str(checked), "exe": exe, "pid": pid})
        return ok(opened=True, path=str(checked), exe=exe,
                  route="exe", pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def run_script(script_path: str, runner=None, which_fn=shutil.which) -> dict:
    """Run a Maya .py / .mel script headlessly via mayapy / mayabatch."""
    try:
        checked = check_path_allowed(script_path)
        if checked.suffix.lower() not in _SCRIPT_SUFFIXES:
            return err(
                f"Not a Maya script: {checked.name} "
                f"(expected one of: {', '.join(sorted(_SCRIPT_SUFFIXES))})."
            )
        built = build_run_script_argv(checked, which_fn=which_fn)
        if not built.get("ok"):
            return built
        argv = built["argv"]
        audit("maya_run_script", {"script": str(checked), "argv": argv,
                                  "route": built["route"]})
        result = run_cli(argv, runner=runner)
        if not result.get("ok"):
            return result
        return ok(script=str(checked), route=built["route"], exe=built["exe"],
                  argv=argv, returncode=result.get("returncode"),
                  stdout=result.get("stdout"), stderr=result.get("stderr"))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the ``maya_*`` tools on a FastMCP server."""

    @mcp.tool()
    def maya_status() -> dict:
        """Check whether Autodesk Maya is installed on this machine.

        Returns the catalog detection result (installed, executable
        path, detection route) plus Maya's automation strategy
        ("plugin": MEL / Python scripting, headless via mayapy.exe).
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def maya_launch() -> dict:
        """Launch Autodesk Maya (the detected maya.exe).

        Fails with a clear error when Maya is not installed.
        The launch is written to the audit log.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def maya_open_file(path: str) -> dict:
        """Open a Maya scene file (.ma or .mb) in Maya.

        The path must live under an allowed root. Maya is started via
        its detected maya.exe with the scene as argument, which is
        Maya's own file-open route; the result states route="exe".
        """
        return open_file(path)

    @mcp.tool()
    def maya_run_script(script_path: str) -> dict:
        """Run a Maya script (.py or .mel) headlessly, without the GUI.

        .py scripts run under mayapy.exe (Maya's standalone Python
        interpreter with maya.cmds/OpenMaya available), falling back
        to mayabatch.exe -command python(...). .mel scripts run under
        mayabatch.exe -command 'source "..."'. The path must live
        under an allowed root. Returns the script's stdout/stderr and
        exit code, or a clear error when no headless Maya executable
        (mayapy.exe / mayabatch.exe) is available.
        """
        return run_script(script_path)

    return APP_ID
