"""Adobe Illustrator adapter — COM automation with an exe fallback.

Illustrator exposes a real COM Automation interface on Windows
(``Illustrator.Application``): documents are opened through
``app.Documents.Open(path)``. That is the primary route here, via the
shared :class:`._common.ComApp` wrapper.

When COM is unavailable (Illustrator not running/registering, pywin32
missing, non-Windows host, …) the adapter falls back to launching the
detected ``Illustrator.exe`` with the file path as its argument, which
Illustrator honours by opening the file on start. Every result from the
open tool carries ``"route": "com"`` or ``"route": "exe"`` so the caller
knows which path was taken.

Safety: the target path must pass ``core.check_path_allowed`` *before*
any COM call or process launch is attempted; a disallowed path returns
an err dict and nothing is opened or spawned.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
The COM wrapper is built through :func:`make_com_app` (monkeypatchable)
and the exe spawn through :func:`_spawn_with_file`, so tests can inject
fakes without a real Illustrator, COM, or Windows.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import ComApp

APP_ID = "illustrator"
PROG_ID = "Illustrator.Application"
COLLECTION = "Documents"


def make_com_app() -> ComApp:
    """Build the COM wrapper for Illustrator (factory seam for tests)."""
    return ComApp(PROG_ID)


def _spawn_with_file(exe_path: str, file_path: str):
    """Start *exe_path* with *file_path* as its argument (detached).

    Factored out so tests can monkeypatch the spawn without launching
    a real process.
    """
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


def _open_via_exe(checked_path) -> dict:
    """Fallback route: launch the detected Illustrator exe with the file."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    if not det.get("installed") or not det.get("path"):
        return err("Adobe Illustrator is not installed (Illustrator.exe was "
                   "not found on this machine), so the exe fallback cannot "
                   "open the file either.")
    try:
        proc = _spawn_with_file(det["path"], str(checked_path))
        pid = getattr(proc, "pid", None)
        audit("illustrator_open_exe", {"app_id": APP_ID, "exe": det["path"],
                                       "path": str(checked_path), "pid": pid})
        return ok(opened=True, route="exe", path=str(checked_path),
                  exe=det["path"], pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def open_file(path: str, com_app=None) -> dict:
    """Open *path* in Illustrator: COM first, exe launch as fallback.

    The allowlist check runs first; a rejected path is an err dict and
    neither COM nor a process launch is attempted.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        app = com_app if com_app is not None else make_com_app()
        result = app.open_via(COLLECTION, str(checked))
    except Exception as exc:  # noqa: BLE001 — a broken factory is a COM failure
        result = err(exc)
    if result.get("ok"):
        return {**result, "route": "com"}
    fallback = _open_via_exe(checked)
    if fallback.get("ok"):
        fallback["com_error"] = result.get("error")
        return fallback
    return err(f"Could not open {checked} in Illustrator. "
               f"COM route failed: {result.get('error')}. "
               f"Exe route failed: {fallback.get('error')}.")


def register(mcp):
    """Register the illustrator_* tools on a FastMCP server."""

    @mcp.tool()
    def illustrator_status() -> dict:
        """Check whether Adobe Illustrator is installed on this machine.

        Returns the catalog detection result for Illustrator: installed
        flag, the detected Illustrator.exe path (and how it was found),
        its automation strategy ("com") and its COM ProgID
        ("Illustrator.Application"). Use this before trying to open a
        file so you know which route (COM / exe) is likely to work.
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def illustrator_launch() -> dict:
        """Launch Adobe Illustrator (without opening any file).

        Starts the installed Illustrator.exe found by the catalog and
        returns its path and pid. Fails with a clear error when
        Illustrator is not installed on this machine.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def illustrator_open_file(path: str) -> dict:
        """Open an Illustrator file (.ai, .eps, .pdf, .svg, …) in Adobe Illustrator.

        The path must live under an allowed root (see the project path
        allowlist) — a disallowed path is rejected and nothing is
        opened. The file is opened through Illustrator's COM interface
        (Documents.Open) when available; if COM fails, Illustrator.exe
        is launched with the file as its argument instead. The result's
        "route" field says which happened: "com" or "exe".
        """
        return open_file(path)

    return ["illustrator_status", "illustrator_launch", "illustrator_open_file"]
