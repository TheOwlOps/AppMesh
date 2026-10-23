"""Rhino adapter — plugin strategy (catalog id ``rhino``).

Rhino (McNeel Rhinoceros 3D) has no COM ProgID in the catalog
(``prog_id=None``); it is automated through its scripting/plugin surface:

- Opening a model is just ``Rhino.exe <file.3dm>`` — Rhino registers the
  ``.3dm`` (and Grasshopper ``.gh``) file associations and accepts a file
  path as its first command-line argument.
- Scripts run headlessly with the ``/runscript`` switch, e.g.
  ``Rhino.exe /runscript="_-RunPythonScript C:\\scripts\\make.py"``,
  which starts Rhino, runs the RhinoPython script and (when the script
  quits Rhino / in practice the invocation) lets an agent drive Rhino
  without clicking through the GUI. RhinoScript ``.rvb`` files use
  ``_-RunScript`` instead.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "rhino"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for testability
    (mirrors ``apps_catalog._spawn``)."""
    return subprocess.Popen(argv, close_fds=True)


def _detected_exe() -> tuple[str | None, dict | None]:
    """Return ``(exe_path, None)`` when Rhino is installed, else
    ``(None, err_dict)`` explaining why not."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return None, det
    if not det.get("installed") or not det.get("path"):
        return None, err(
            "Rhino is not installed (none of its executables were found "
            "on this machine: Rhino.exe)."
        )
    return str(det["path"]), None


def build_runscript_argv(exe: str, script_path: str | Path) -> list[str]:
    """Build the headless Rhino argv for running *script_path*.

    ``Rhino.exe /runscript="<command>"`` where the command is
    ``_-RunPythonScript <script.py>`` for Python scripts and
    ``_-RunScript <script.rvb>`` for RhinoScript files. The leading
    ``-``/``_`` run the command silently / in English, as documented
    for Rhino's command-line scripting.
    """
    script = str(script_path)
    if Path(script).suffix.lower() == ".rvb":
        command = f"_-RunScript {script}"
    else:
        command = f"_-RunPythonScript {script}"
    return [str(exe), f'/runscript="{command}"']


def open_file(path: str) -> dict:
    """Open *path* (e.g. a ``.3dm`` model) in Rhino via exe-with-file-arg.

    The path must live under an allowed root (``core.check_path_allowed``).
    Returns ok with ``route="exe"`` stating how the file was opened.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    exe, failure = _detected_exe()
    if failure is not None:
        return failure
    argv = [exe, str(checked)]
    try:
        proc = _spawn(argv)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    pid = getattr(proc, "pid", None)
    audit("rhino_open_file", {"app_id": APP_ID, "path": str(checked),
                              "exe": exe, "route": "exe", "pid": pid})
    return ok(opened=True, path=str(checked), exe=exe, route="exe",
              argv=argv, pid=pid)


def run_script(script_path: str, runner=None) -> dict:
    """Run a RhinoPython (``.py``) / RhinoScript (``.rvb``) script
    headlessly via ``Rhino.exe /runscript="..."``.

    ``runner`` is injectable for tests and is forwarded to
    ``_common.run_cli``: a callable ``(argv, timeout)`` returning
    ``(returncode, stdout, stderr)``. Returns a graceful err dict when
    Rhino is not installed — the runner is not called in that case.
    """
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    exe, failure = _detected_exe()
    if failure is not None:
        return failure
    argv = build_runscript_argv(exe, checked)
    result = run_cli(argv, runner=runner)
    if not result.get("ok"):
        return result
    audit("rhino_run_script", {"app_id": APP_ID, "script": str(checked),
                               "exe": exe, "route": "runscript", "argv": argv})
    return ok(script=str(checked), exe=exe, route="runscript",
              argv=argv, returncode=result.get("returncode"),
              stdout=result.get("stdout"), stderr=result.get("stderr"))


# Backwards-friendly private aliases (other adapters use _-prefixed helpers).
_open_file = open_file
_run_script = run_script


def register(mcp):
    """Bind the rhino_* tools on a FastMCP server."""

    @mcp.tool()
    def rhino_status() -> dict:
        """Report whether Rhino is installed on this machine, where its
        Rhino.exe lives, and how AppMesh automates it (strategy "plugin":
        RhinoPython / RhinoScript via the /runscript switch; no COM)."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def rhino_launch() -> dict:
        """Launch Rhino if it is installed. Fails with a clear error
        when Rhino.exe cannot be found on this machine."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def rhino_open_file(path: str) -> dict:
        """Open a Rhino file (e.g. a .3dm model or .gh Grasshopper
        definition) in Rhino by passing it as an argument to Rhino.exe.

        The path must live under an allowed root (the user's home by
        default; extend via DESKTOP_MCP_ALLOWED_PATHS). The result
        states the route used ("exe")."""
        return open_file(path)

    @mcp.tool()
    def rhino_run_script(script_path: str) -> dict:
        """Run a RhinoPython script (.py) or RhinoScript file (.rvb)
        in Rhino headlessly via Rhino.exe /runscript="..." — e.g.
        /runscript="_-RunPythonScript C:\\scripts\\make.py".

        The script path must live under an allowed root. Returns the
        process stdout/stderr/returncode, or a clear error when Rhino
        is not installed."""
        return run_script(script_path)

    return APP_ID
