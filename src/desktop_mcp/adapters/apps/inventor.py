"""Autodesk Inventor adapter — COM automation with an exe fallback.

Inventor exposes a real COM Automation interface on Windows
(``Inventor.Application``): documents are opened through
``app.Documents.Open(path)`` — Inventor's Application.Documents
collection. That is the primary route here, via the shared
:class:`._common.ComApp` wrapper.

When COM is unavailable (Inventor not running/registering, pywin32
missing, non-Windows host, …) the adapter falls back to launching the
detected ``Inventor.exe`` with the file path as its argument, which
Inventor honours by opening the file on start. Every result from the
open tool carries ``"route": "com"`` or ``"route": "exe"`` (also
mirrored as ``"via"``) so the caller knows which path was taken.

Safety: the target path must pass ``core.check_path_allowed`` *before*
any COM call or process launch is attempted; a disallowed path returns
an err dict and nothing is opened or spawned.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
The COM wrapper is built through :func:`make_com_app` (monkeypatchable,
and :func:`open_file` also accepts an injected ``com_app`` /
``com_factory``), and the exe fallback spawns through
``subprocess.Popen``, so tests can inject fakes without a real
Inventor, COM, or Windows.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import ComApp

APP_ID = "inventor"
PROG_ID = "Inventor.Application"
COLLECTION = "Documents"


def _catalog_prog_id() -> str:
    """Return the ProgID recorded in the catalog for Inventor."""
    try:
        app = apps_catalog.get_app(APP_ID)
        if app.get("ok"):
            prog_id = app.get("app", {}).get("prog_id")
            if prog_id:
                return str(prog_id)
    except Exception:  # noqa: BLE001 — fall back to the known ProgID
        pass
    return PROG_ID


def make_com_app(factory=None) -> ComApp:
    """Build the COM wrapper for Inventor (factory seam for tests).

    *factory*, when given, is forwarded to :class:`ComApp` as its
    zero-arg application factory, bypassing the real Windows/pywin32
    dispatch entirely.
    """
    if factory is not None:
        return ComApp(_catalog_prog_id(), factory=factory)
    return ComApp(_catalog_prog_id())


def _open_via_exe(checked_path) -> dict:
    """Fallback route: launch the detected Inventor exe with the file."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    if not det.get("installed") or not det.get("path"):
        return err("Autodesk Inventor is not installed (Inventor.exe was "
                   "not found on this machine), so the exe fallback cannot "
                   "open the file either.")
    try:
        proc = subprocess.Popen([det["path"], str(checked_path)],
                                close_fds=True)
        pid = getattr(proc, "pid", None)
        audit("inventor_open_exe", {"app_id": APP_ID, "exe": det["path"],
                                    "path": str(checked_path), "pid": pid})
        return ok(opened=True, route="exe", via="exe",
                  path=str(checked_path), exe=det["path"], pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def open_file(path: str, com_app=None, com_factory=None) -> dict:
    """Open *path* in Inventor: COM first, exe launch as fallback.

    The allowlist check runs first; a rejected path is an err dict and
    neither COM nor a process launch is attempted.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        if com_app is not None:
            app = com_app
        elif com_factory is not None:
            app = make_com_app(factory=com_factory)
        else:
            app = make_com_app()
        result = app.open_via(COLLECTION, str(checked))
    except Exception as exc:  # noqa: BLE001 — a broken factory is a COM failure
        result = err(exc)
    if result.get("ok"):
        return {**result, "route": "com", "via": "com"}
    fallback = _open_via_exe(checked)
    if fallback.get("ok"):
        fallback["com_error"] = result.get("error")
        return fallback
    return err(f"Could not open {checked} in Autodesk Inventor. "
               f"COM route failed: {result.get('error')}. "
               f"Exe route failed: {fallback.get('error')}.")


def register(mcp):
    """Register the inventor_* tools on a FastMCP server."""

    @mcp.tool()
    def inventor_status() -> dict:
        """Check whether Autodesk Inventor is installed on this machine.

        Returns the catalog detection result for Inventor: installed
        flag, the detected Inventor.exe path (and how it was found),
        its automation strategy ("com") and its COM ProgID
        ("Inventor.Application"). Use this before trying to open a
        file so you know which route (COM / exe) is likely to work.
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def inventor_launch() -> dict:
        """Launch Autodesk Inventor (without opening any file).

        Starts the installed Inventor.exe found by the catalog and
        returns its path and pid. Fails with a clear error when
        Inventor is not installed on this machine.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def inventor_open_file(path: str) -> dict:
        """Open an Inventor file (.ipt part, .iam assembly, .idw/.dwg drawing, .ipn presentation) in Autodesk Inventor.

        The path must live under an allowed root (see the project path
        allowlist) — a disallowed path is rejected and nothing is
        opened. The file is opened through Inventor's COM interface
        (Application.Documents.Open) when available; if COM fails,
        Inventor.exe is launched with the file as its argument
        instead. The result's "route" field says which happened:
        "com" or "exe".
        """
        return open_file(path)

    return ["inventor_status", "inventor_launch", "inventor_open_file"]
