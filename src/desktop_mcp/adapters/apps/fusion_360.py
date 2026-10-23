"""Autodesk Fusion 360 adapter — plugin strategy.

Fusion 360 has no COM ProgID ( ``prog_id`` is ``None`` in the catalog).
Its automation surface is the built-in Python add-in API (the ``adsk``
modules) whose scripts live in Fusion's API Scripts / Add-Ins folder, plus
the plain executable itself:

- ``Fusion360.exe <file>`` opens a design file (``.f3d`` / ``.f3z`` and
  the CAD formats Fusion imports, e.g. STEP/IGES/STL/OBJ) in the GUI.
- ``Fusion360.exe <script.py>`` is used here as the script entry point:
  Fusion launches and loads the Python script through its ``adsk`` API
  environment, where the script can use ``adsk.core`` / ``adsk.fusion``.

Both routes first require the executable from the catalog detection and
the path allowlist in :mod:`desktop_mcp.core`. Every tool returns a
``core.ok`` / ``core.err`` dict and never raises.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "fusion-360"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for testability
    (mirrors ``apps_catalog._spawn``)."""
    return subprocess.Popen(argv, close_fds=True)


def _exe_path() -> str | None:
    """Return the detected Fusion360.exe path, or None when not installed."""
    try:
        det = apps_catalog.detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — detection must never break a tool
        return None
    if not det.get("ok") or not det.get("installed"):
        return None
    return det.get("path")


def open_file(path: str, popen_fn=None) -> dict:
    """Open *path* in Fusion 360 via ``Fusion360.exe <file>``.

    The allowlist check runs first, before detection or spawning, so a
    disallowed path never reaches the executable.
    """
    try:
        checked = check_path_allowed(path)
        exe = _exe_path()
        if not exe:
            return err(
                "Fusion 360 is not installed (Fusion360.exe was not found "
                "on this machine)."
            )
        spawn = popen_fn or _spawn
        proc = spawn([exe, str(checked)])
        pid = getattr(proc, "pid", None)
        audit("fusion_360_open_file", {"path": str(checked), "exe": exe, "pid": pid})
        return ok(opened=True, path=str(checked), exe=exe,
                  route="exe-file-arg", pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def run_script(script_path: str, runner=None) -> dict:
    """Run a Fusion 360 Python (``adsk`` API) script via the executable.

    Builds ``[Fusion360.exe, <script.py>]`` and executes it through
    :func:`_common.run_cli`. ``runner`` is injectable for tests: a
    callable ``(argv, timeout) -> (returncode, stdout, stderr)``.
    Returns a graceful error dict when Fusion 360 is not installed.
    """
    try:
        checked = check_path_allowed(script_path)
        if checked.suffix.lower() != ".py":
            return err(
                f"Fusion 360 scripts must be Python (.py) files: {checked}"
            )
        exe = _exe_path()
        if not exe:
            return err(
                "Fusion 360 is not installed (Fusion360.exe was not found "
                "on this machine); cannot run script."
            )
        argv = [exe, str(checked)]
        result = run_cli(argv, runner=runner)
        if not result.get("ok"):
            return result
        audit("fusion_360_run_script", {"script": str(checked), "exe": exe})
        return {**result, "script": str(checked), "exe": exe,
                "route": "run_cli"}
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the ``fusion_360_*`` tools on a FastMCP server."""

    @mcp.tool()
    def fusion_360_status() -> dict:
        """Check whether Autodesk Fusion 360 is installed on this machine.

        Returns the catalog detection result: installed flag, the
        Fusion360.exe path when found, detection source, and the
        automation strategy ("plugin" — the built-in Python ``adsk``
        add-in API; Fusion 360 has no COM interface).
        """
        return catalog_status(APP_ID)

    @mcp.tool()
    def fusion_360_launch() -> dict:
        """Launch Autodesk Fusion 360 if it is installed.

        Fails with a clear error when Fusion360.exe was not detected on
        this machine. The launch is written to the audit log.
        """
        return catalog_launch(APP_ID)

    @mcp.tool()
    def fusion_360_open_file(path: str) -> dict:
        """Open a design file in Fusion 360 (e.g. .f3d, .f3z, .step, .stl).

        The file must live under an allowed root (see the path allowlist).
        Fusion 360 is started with the file as its argument
        (``Fusion360.exe <file>``). The result states the route used
        ("exe-file-arg") and the Fusion360.exe path.
        """
        return open_file(path)

    @mcp.tool()
    def fusion_360_run_script(script_path: str) -> dict:
        """Run a Python script inside Fusion 360's ``adsk`` API environment.

        `script_path` must be an allow-listed ``.py`` file written against
        the Fusion 360 API (``import adsk.core, adsk.fusion`` — the same
        scripts Fusion's Scripts and Add-Ins dialog runs). Fusion 360 is
        invoked with the script (``Fusion360.exe <script.py>``) via a
        subprocess call. Fails gracefully when Fusion 360 is not
        installed.
        """
        return run_script(script_path)

    return APP_ID
