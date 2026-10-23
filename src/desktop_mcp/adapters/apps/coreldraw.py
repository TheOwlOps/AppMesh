"""CorelDRAW adapter — COM automation (strategy: ``com``).

CorelDRAW exposes a full COM/VBA automation interface on Windows via
the ProgID ``CorelDRAW.Application``: documents, shapes and export can
all be driven through the application's own object model, so this
adapter opens files through ``app.Documents.Open(path)`` first.

Fallback: if the COM route fails (CorelDRAW not running/registering,
pywin32 missing, non-Windows host, …) the file is opened by launching
the detected CorelDRAW executable with the file as its argument. The
result dict always says which route was taken via ``"route"``:
``"com"`` or ``"exe"``.

Every tool returns a ``core.ok`` / ``core.err`` dict and never raises.
The target path must pass ``core.check_path_allowed`` *before* any
open attempt — a disallowed path attempts neither COM nor a spawn.

Testability: COM access goes through ``_common.ComApp`` (imported into
this module's namespace so tests can monkeypatch ``coreldraw.ComApp``),
and the exe fallback goes through the factored helpers
``_detect_exe`` / ``_spawn_with_file`` (also monkeypatchable).
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import ComApp, catalog_launch, catalog_status

APP_ID = "coreldraw"
PROG_ID = "CorelDRAW.Application"


# ------------------------------------------------------- exe fallback

def _detect_exe() -> str | None:
    """Return the detected CorelDRAW executable path, else None."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok") or not det.get("installed"):
        return None
    return det.get("path")


def _spawn_with_file(exe_path: str, file_path) -> object:
    """Launch *exe_path* with *file_path* as its argument. Factored
    out so tests can monkeypatch the spawn without touching a disk."""
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


def _open_with_exe(checked_path) -> dict:
    """Open *checked_path* by launching the CorelDRAW exe with it."""
    try:
        exe = _detect_exe()
        if not exe:
            return err("CorelDRAW executable not found (app not installed); "
                       "cannot use the exe fallback.")
        proc = _spawn_with_file(exe, checked_path)
        pid = getattr(proc, "pid", None)
        audit("coreldraw_open_file", {"app_id": APP_ID, "route": "exe",
                                      "exe": str(exe), "path": str(checked_path),
                                      "pid": pid})
        return ok(opened=True, route="exe", app_id=APP_ID,
                  path=str(checked_path), exe=str(exe), pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ----------------------------------------------------------------- open

def open_file(path: str) -> dict:
    """Open *path* in CorelDRAW: COM first, exe-launch as fallback."""
    # Allowlist gate first: a disallowed path must attempt nothing.
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — ValueError from the gate
        return err(exc)

    com_result: dict
    try:
        com_result = ComApp(PROG_ID).open_via("Documents", str(checked))
    except Exception as exc:  # noqa: BLE001 — defensive; open_via errs itself
        com_result = err(exc)
    if com_result.get("ok"):
        return {**com_result, "route": "com", "app_id": APP_ID}

    fallback = _open_with_exe(checked)
    if fallback.get("ok"):
        return {**fallback, "com_error": com_result.get("error")}
    return err(f"CorelDRAW could not open {checked}: "
               f"COM failed ({com_result.get('error')}); "
               f"exe fallback failed ({fallback.get('error')})")


# ------------------------------------------------------------- register

def register(mcp):
    """Register the ``coreldraw_*`` tools on a FastMCP server."""

    @mcp.tool()
    def coreldraw_status() -> dict:
        """Check whether CorelDRAW is installed on this machine.

        Returns the catalog detection result for CorelDRAW: installed
        flag, executable path (when found), the automation strategy
        ("com") and its COM ProgID ("CorelDRAW.Application"). Use this
        before coreldraw_launch / coreldraw_open_file.
        """
        return catalog_status(APP_ID)

    @mcp.tool()
    def coreldraw_launch() -> dict:
        """Launch CorelDRAW (no document) if it is installed.

        Fails with a clear error when CorelDRAW is not installed on
        this machine. The launch is written to the audit log.
        """
        return catalog_launch(APP_ID)

    @mcp.tool()
    def coreldraw_open_file(path: str) -> dict:
        """Open a CorelDRAW file (e.g. a .cdr drawing) in CorelDRAW.

        The path must live under an allowed root (the user's home by
        default). The file is opened via COM automation
        (CorelDRAW.Application → Documents.Open) when possible; if COM
        is unavailable it falls back to launching the CorelDRAW
        executable with the file as an argument. The result's "route"
        field is "com" or "exe", saying which way the file was opened.
        """
        return open_file(path)

    return {"app_id": APP_ID, "prog_id": PROG_ID}
