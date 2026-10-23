"""FreeCAD adapter — plugin strategy (Python console / macros).

FreeCAD has no COM ProgID (catalog ``prog_id`` is ``None``); its
automation surface is its built-in Python API:

- GUI: ``FreeCAD.exe`` opens a document when passed a file argument
  (``FreeCAD.exe model.FCStd``) — that is how :func:`open_file` works.
- Headless: the console sibling ``FreeCADCmd.exe`` (``freecadcmd`` on
  Linux / PATH) runs a Python script and exits:
  ``FreeCADCmd script.py`` — that is how :func:`run_script` works. The
  console exe is derived from the detected GUI install (same folder,
  ``FreeCAD`` -> ``FreeCADCmd``) and falls back to a PATH lookup.

Every tool returns a ``core.ok`` / ``core.err`` dict and never raises.
Paths must pass ``core.check_path_allowed`` before anything is spawned
or executed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "freecad"

# Console executable names to probe on PATH, most specific first.
_CONSOLE_NAMES = ("FreeCADCmd.exe", "FreeCADCmd", "freecadcmd.exe", "freecadcmd")


def _console_exe_from_gui(gui_path: str) -> str:
    """Derive the console exe that sits alongside the GUI exe.

    ``.../FreeCAD.exe`` -> ``.../FreeCADCmd.exe`` and
    ``.../freecad`` -> ``.../freecadcmd``. Pure string/Path logic: no
    disk access, so it works for Windows-style paths on any OS and is
    trivially testable with a fake detected path.
    """
    p = Path(gui_path)
    lower = p.name.lower()
    if lower == "freecad.exe":
        cmd_name = "FreeCADCmd.exe"
    elif lower == "freecad":
        cmd_name = "freecadcmd"
    elif lower.startswith("freecad"):
        cmd_name = f"{p.stem}Cmd{p.suffix}"
    else:
        cmd_name = "FreeCADCmd.exe" if p.suffix.lower() == ".exe" else "freecadcmd"
    parent = str(p.parent)
    if parent in (".", ""):
        return cmd_name
    return str(p.parent / cmd_name)


def _find_console_exe() -> str | None:
    """Locate FreeCADCmd: alongside the detected GUI exe, else on PATH."""
    try:
        det = apps_catalog.detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — detection must never break the tool
        det = {}
    if det.get("ok") and det.get("installed") and det.get("path"):
        return _console_exe_from_gui(str(det["path"]))
    for name in _CONSOLE_NAMES:
        found = shutil.which(name)
        if found:
            return str(found)
    return None


def build_run_script_argv(console_exe: str, script_path: str) -> list[str]:
    """Build the FreeCADCmd argv: ``[console_exe, script]``."""
    return [str(console_exe), str(script_path)]


def open_file(path: str, popen=None) -> dict:
    """Open a FreeCAD document (e.g. .FCStd) in the GUI via exe + file arg.

    The file must live under an allow-listed root. Returns the route
    used (``"exe"``) so callers can tell this was not COM/plugin IPC.
    ``popen`` is injectable for tests (defaults to subprocess.Popen).
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return det
        if not det.get("installed") or not det.get("path"):
            return err(
                "FreeCAD is not installed (FreeCAD.exe was not found "
                "on this machine)."
            )
        exe = str(det["path"])
        spawn = popen if popen is not None else subprocess.Popen
        proc = spawn([exe, str(checked)], close_fds=True)
        pid = getattr(proc, "pid", None)
        audit("freecad_open_file", {"path": str(checked), "exe": exe, "pid": pid})
        return ok(opened=True, path=str(checked), exe=exe, route="exe", pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def run_script(script_path: str, runner=None) -> dict:
    """Run a Python script headlessly with FreeCADCmd.

    The script runs with FreeCAD's Python interpreter (``import
    FreeCAD`` / ``import Part`` etc. available) and FreeCADCmd exits
    when the script finishes. The console exe is derived from the
    detected FreeCAD install, falling back to ``freecadcmd`` on PATH;
    a clear error is returned when neither is available.

    ``runner`` is injectable for tests and forwarded to
    ``_common.run_cli``: a callable ``(argv, timeout)`` returning
    ``(returncode, stdout, stderr)``.
    """
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        console_exe = _find_console_exe()
        if not console_exe:
            return err(
                "FreeCAD console executable not found: FreeCAD is not "
                "installed and no FreeCADCmd/freecadcmd was found on PATH."
            )
        argv = build_run_script_argv(console_exe, str(checked))
        result = run_cli(argv, runner=runner)
        if not result.get("ok"):
            return result
        audit("freecad_run_script", {"script": str(checked), "argv": argv})
        return ok(
            script=str(checked),
            console_exe=console_exe,
            argv=argv,
            route="freecadcmd",
            returncode=result.get("returncode"),
            stdout=result.get("stdout"),
            stderr=result.get("stderr"),
        )
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the freecad_* tools on a FastMCP server."""

    @mcp.tool()
    def freecad_status() -> dict:
        """Check whether FreeCAD is installed and how it can be automated.

        Returns the catalog detection result: installed flag, executable
        path and detection source, plus FreeCAD's automation strategy
        ("plugin" — Python console/macros, headless via FreeCADCmd).
        """
        return catalog_status(APP_ID)

    @mcp.tool()
    def freecad_launch() -> dict:
        """Launch the FreeCAD GUI. Fails clearly when not installed."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def freecad_open_file(path: str) -> dict:
        """Open a FreeCAD document (e.g. a .FCStd file) in the GUI.

        The file is passed as an argument to the detected FreeCAD.exe.
        The path must live under an allow-listed root.
        """
        return open_file(path)

    @mcp.tool()
    def freecad_run_script(script_path: str) -> dict:
        """Run a Python script headlessly via FreeCADCmd (no GUI).

        The script has access to FreeCAD's Python API (FreeCAD, Part,
        Sketcher, ...). The path must live under an allow-listed root.
        Returns the script's stdout/stderr and exit code on success.
        """
        return run_script(script_path)

    return [freecad_status, freecad_launch, freecad_open_file, freecad_run_script]
