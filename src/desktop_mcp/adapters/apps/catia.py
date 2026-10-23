"""CATIA adapter — COM automation (strategy: ``com``).

CATIA (Dassault Systèmes) exposes a real COM Automation interface on
Windows via the ProgID ``CATIA.Application`` (VBScript heritage):
documents are opened through ``Application.Documents.Open(path)`` —
CATIA's Documents collection. That is the primary route here, via the
shared :class:`._common.ComApp` wrapper.

Fallback: if the COM route fails (CATIA not running/registering,
pywin32 missing, non-Windows host, …) the file is opened by launching
the detected CATIA executable (``CNEXT.exe``) with the file as its
argument. The result dict always states which route was used via
``"route"``: ``"com"`` or ``"exe"`` (the fallback also carries the
``com_error`` that triggered it).

Safety: the target path must pass ``core.check_path_allowed`` *before*
any COM call or process launch is attempted — a disallowed path
returns an err dict and nothing is opened or spawned.

Every tool returns a ``core.ok`` / ``core.err`` dict and never raises.
Testability: COM access goes through :func:`make_com_app`, which
forwards an optional zero-arg *factory* to :class:`ComApp` (so tests
can inject a fake CATIA application object), :func:`open_file` also
accepts an injected ``com_app`` / ``com_factory``, ``ComApp`` itself is
imported into this module's namespace (monkeypatchable), and the exe
fallback spawns through ``subprocess.Popen`` after detecting the exe
via ``apps_catalog.detect_app``.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import ComApp, catalog_launch, catalog_status

APP_ID = "catia"
PROG_ID = "CATIA.Application"
COLLECTION = "Documents"


def _catalog_prog_id() -> str:
    """Return the ProgID recorded in the catalog for CATIA."""
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
    """Build the COM wrapper for CATIA (factory seam for tests).

    *factory*, when given, is forwarded to :class:`ComApp` as its
    zero-arg application factory, bypassing the real Windows/pywin32
    dispatch entirely.
    """
    return ComApp(_catalog_prog_id(), factory=factory)


# ------------------------------------------------------- exe fallback

def _open_via_exe(checked) -> dict:
    """Fallback route: launch the detected CATIA exe with the file."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    if not det.get("installed") or not det.get("path"):
        return err("CATIA executable not found (CNEXT.exe was not "
                   "detected on this machine), so the exe fallback "
                   "cannot open the file either.")
    try:
        proc = subprocess.Popen([det["path"], str(checked)],
                                close_fds=True)
        pid = getattr(proc, "pid", None)
        audit("catia_open_file", {"app_id": APP_ID, "route": "exe",
                                  "exe": det["path"],
                                  "path": str(checked), "pid": pid})
        return ok(opened=True, route="exe", app_id=APP_ID,
                  path=str(checked), exe=det["path"], pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ----------------------------------------------------------------- open

def open_file(path: str, com_app=None, com_factory=None) -> dict:
    """Open *path* in CATIA: COM first, exe launch as fallback.

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
        return {**result, "route": "com", "app_id": APP_ID}
    fallback = _open_via_exe(checked)
    if fallback.get("ok"):
        fallback["com_error"] = result.get("error")
        return fallback
    return err(f"CATIA could not open {checked}: "
               f"COM failed ({result.get('error')}); "
               f"exe fallback failed ({fallback.get('error')})")


# ------------------------------------------------------------- register

def register(mcp):
    """Register the ``catia_*`` tools on a FastMCP server."""

    @mcp.tool()
    def catia_status() -> dict:
        """Check whether CATIA is installed on this machine.

        Returns the catalog detection result for CATIA: installed
        flag, executable path (CNEXT.exe, when found) and how it was
        found, the automation strategy ("com") and its COM ProgID
        ("CATIA.Application"). Use this before catia_launch /
        catia_open_file.
        """
        return catalog_status(APP_ID)

    @mcp.tool()
    def catia_launch() -> dict:
        """Launch CATIA (no document) if it is installed.

        Fails with a clear error when CATIA is not installed on this
        machine. The launch is written to the audit log.
        """
        return catalog_launch(APP_ID)

    @mcp.tool()
    def catia_open_file(path: str) -> dict:
        """Open a CATIA file (.CATPart, .CATProduct, .CATDrawing, …) in CATIA.

        The path must live under an allowed root (the user's home by
        default) — a disallowed path is rejected and nothing is
        opened. The file is opened via COM automation
        (CATIA.Application → Documents.Open) when possible; if COM is
        unavailable it falls back to launching the detected CATIA
        executable (CNEXT.exe) with the file as an argument. The
        result's "route" field is "com" or "exe", saying which way
        the file was opened.
        """
        return open_file(path)

    return {"app_id": APP_ID, "prog_id": PROG_ID}
