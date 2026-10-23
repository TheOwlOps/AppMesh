"""Autodesk 3ds Max adapter — plugin strategy (MAXScript / pymxs).

Catalog entry ``3ds-max`` (strategy ``plugin``, exe ``3dsmax.exe``, no
COM ProgID): 3ds Max is automated through its own scripting surface —
MAXScript (and pymxs, its Python API) — not through COM or UI clicking.

Tools (registered under these exact names; they start with a digit, so
``register`` passes each name explicitly to ``mcp.tool(name=...)``
instead of relying on the Python function name):

- ``3ds_max_status``     catalog detection + strategy info.
- ``3ds_max_launch``     launch the installed ``3dsmax.exe``.
- ``3ds_max_open_file``  open a scene (.max) by launching the detected
  ``3dsmax.exe`` with the file as its argument — 3ds Max's own
  file-open route; the result states ``route="exe"``.
- ``3ds_max_run_script`` run a MAXScript file (.ms / .mcr) headlessly.
  Default route: ``3dsmax.exe -silent -mxs 'fileIn @"<script.ms>"'``
  (``-silent`` suppresses dialogs, ``-mxs`` evaluates the MAXScript
  expression that loads and runs the script file). Batch route
  (``use_batch=True``): ``3dsmaxbatch.exe <script.ms>`` —
  3dsmaxbatch.exe is Autodesk's dedicated headless MAXScript host and
  ships in the same folder as 3dsmax.exe in every standard install, so
  its location is derived from the detected 3dsmax.exe.

Every tool returns a ``core.ok`` / ``core.err`` dict and never raises.
The headless runner (``run_cli``'s ``runner``) and the process spawner
(``popen`` / module-level :func:`_spawn`) are injectable so the adapter
is fully testable off-Windows. All file paths must pass
``core.check_path_allowed`` before anything is spawned or run.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "3ds-max"

#: Default timeout (seconds) for a headless script run — batch renders
#: and scene-processing scripts can legitimately take minutes.
SCRIPT_TIMEOUT = 600

_SCENE_SUFFIXES = {".max"}
_SCRIPT_SUFFIXES = {".ms", ".mcr"}


# --------------------------------------------------------------- helpers

def _detect() -> dict:
    return apps_catalog.detect_app(APP_ID)


def _detected_exe() -> str | None:
    """Return the detected 3dsmax.exe path, or None if not installed."""
    try:
        det = _detect()
    except Exception:  # noqa: BLE001 — detection must never crash a tool
        return None
    if det.get("ok") and det.get("installed"):
        return det.get("path") or None
    return None


def _batch_exe() -> str:
    """Resolve 3dsmaxbatch.exe: sibling of the detected 3dsmax.exe
    first (they ship together), then PATH, then the bare name (so
    ``run_cli`` can produce its graceful "not found" error)."""
    detected = _detected_exe()
    if detected:
        return str(Path(detected).with_name("3dsmaxbatch.exe"))
    return shutil.which("3dsmaxbatch.exe") or shutil.which("3dsmaxbatch") \
        or "3dsmaxbatch.exe"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


# --------------------------------------------------------------- builders

def build_open_argv(path: str, exe: str = "3dsmax.exe") -> list[str]:
    """Build the argv to open *path* in 3ds Max: ``[exe, path]`` (pure)."""
    return [exe, str(path)]


def build_run_script_argv(script_path: str, exe: str = "3dsmax.exe") -> list[str]:
    """Build the headless MAXScript argv for *script_path* (pure).

    ``3dsmax.exe -silent -mxs 'fileIn @"<script.ms>"'`` — the ``-mxs``
    argument is a MAXScript expression; ``fileIn`` with a ``@"..."``
    verbatim string literal loads and executes the script file.
    """
    return [exe, "-silent", "-mxs", f'fileIn @"{script_path}"']


def build_batch_argv(script_path: str, exe: str = "3dsmaxbatch.exe") -> list[str]:
    """Build the 3dsmaxbatch.exe argv for *script_path* (pure):
    ``[3dsmaxbatch.exe, script.ms]``."""
    return [exe, str(script_path)]


# ----------------------------------------------------------------- tools

def open_file(path: str, popen=None) -> dict:
    """Open a 3ds Max scene file (.max) with the detected 3dsmax.exe.

    The path must pass ``core.check_path_allowed`` first; a disallowed
    path returns an err dict and nothing is launched. When 3ds Max is
    not installed (per catalog detection) an err dict is returned and
    nothing is spawned either. On success the result states
    ``route="exe"`` (and ``via="exe"``).
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — rejection before any spawn
        return err(exc)
    if checked.suffix.lower() not in _SCENE_SUFFIXES:
        return err(
            f"Not a 3ds Max scene file: {checked.name} "
            f"(expected one of: {', '.join(sorted(_SCENE_SUFFIXES))})."
        )
    try:
        det = _detect()
        if not det.get("ok"):
            return det
        if not det.get("installed") or not det.get("path"):
            return err(
                "3ds Max is not installed (3dsmax.exe was not found on "
                "this machine), so the scene cannot be opened."
            )
        exe = str(det["path"])
        argv = build_open_argv(str(checked), exe=exe)
        proc = popen(argv) if popen is not None else _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("3ds_max_open_file", {"path": str(checked), "exe": exe, "pid": pid})
        return ok(app_id=APP_ID, opened=True, via="exe", route="exe",
                  path=str(checked), exe=exe, argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def run_script(script_path: str, runner=None, use_batch: bool = False,
               timeout: int = SCRIPT_TIMEOUT) -> dict:
    """Run a MAXScript file (.ms / .mcr) in 3ds Max headlessly.

    Default route: ``3dsmax.exe -silent -mxs 'fileIn @"<script>"'``
    using the detected 3dsmax.exe (falling back to plain
    ``3dsmax.exe`` from PATH). With ``use_batch=True`` the script runs
    under ``3dsmaxbatch.exe`` instead, Autodesk's dedicated headless
    MAXScript host that ships beside 3dsmax.exe.

    The path must pass ``core.check_path_allowed`` first. Execution
    goes through ``_common.run_cli`` (whose ``runner`` parameter is
    injectable for tests); when the executable cannot be found and no
    runner is injected, a graceful err dict is returned — this
    function never raises. On success the result carries the route
    used (``"3dsmax-mxs"`` or ``"3dsmaxbatch"``), the exact argv, and
    the process stdout/stderr/returncode.
    """
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — rejection before any run
        return err(exc)
    if checked.suffix.lower() not in _SCRIPT_SUFFIXES:
        return err(
            f"Not a MAXScript file: {checked.name} "
            f"(expected one of: {', '.join(sorted(_SCRIPT_SUFFIXES))})."
        )
    try:
        if use_batch:
            exe = _batch_exe()
            argv = build_batch_argv(str(checked), exe=exe)
            route = "3dsmaxbatch"
        else:
            exe = _detected_exe() or "3dsmax.exe"
            argv = build_run_script_argv(str(checked), exe=exe)
            route = "3dsmax-mxs"
        audit("3ds_max_run_script", {"script": str(checked), "argv": argv,
                                     "route": route})
        result = run_cli(argv, timeout=timeout, runner=runner)
        if not result.get("ok"):
            return result
        return ok(app_id=APP_ID, script=str(checked), route=route, exe=exe,
                  argv=argv, returncode=result.get("returncode"),
                  stdout=result.get("stdout"), stderr=result.get("stderr"))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ------------------------------------------------------------- registration

def register(mcp):
    """Register the ``3ds_max_*`` tools on a FastMCP server.

    Tool names start with a digit, which is not a valid Python
    identifier, so each name is passed explicitly via
    ``mcp.tool(name=...)`` and the wrapper functions keep private
    names. Returns the list of registered tool names.
    """

    @mcp.tool(name="3ds_max_status")
    def _status_tool() -> dict:
        """Check whether Autodesk 3ds Max is installed on this machine.

        Returns the catalog detection result (installed, executable
        path, detection route) plus 3ds Max's automation strategy
        ("plugin": MAXScript / pymxs scripting, batch via
        3dsmaxbatch.exe).
        """
        return catalog_status(APP_ID)

    @mcp.tool(name="3ds_max_launch")
    def _launch_tool() -> dict:
        """Launch Autodesk 3ds Max (the detected 3dsmax.exe).

        Fails with a clear error when 3ds Max is not installed.
        The launch is written to the audit log.
        """
        return catalog_launch(APP_ID)

    @mcp.tool(name="3ds_max_open_file")
    def _open_file_tool(path: str) -> dict:
        """Open a 3ds Max scene file (.max) in 3ds Max.

        The path must live under an allowed root. 3ds Max is started
        via its detected 3dsmax.exe with the scene as argument, which
        is 3ds Max's own file-open route; the result states
        route="exe".
        """
        return open_file(path)

    @mcp.tool(name="3ds_max_run_script")
    def _run_script_tool(script_path: str) -> dict:
        """Run a MAXScript file (.ms or .mcr) in 3ds Max headlessly,
        without opening the GUI.

        Runs ``3dsmax.exe -silent -mxs 'fileIn @"<script>"'``: the
        script executes with full MAXScript / pymxs access (open and
        process scenes, export, render) and its output and exit code
        are returned. (The adapter's ``run_script`` also supports the
        3dsmaxbatch.exe host via ``use_batch=True``.) The path must
        live under an allowed root. Returns a clear error when no
        3ds Max executable is available on this machine.
        """
        return run_script(script_path)

    return ["3ds_max_status", "3ds_max_launch",
            "3ds_max_open_file", "3ds_max_run_script"]
