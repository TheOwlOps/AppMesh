"""SolidWorks adapter — COM attempt first, exe-with-file-argument fallback.

SolidWorks exposes a COM Automation interface on Windows
(``SldWorks.Application``), but it is the known special case among the
COM CAD apps: unlike Inventor/CATIA it has **no Documents collection**
to open files through — its real COM API opens files with a *method*
on the application object itself (``OpenDoc`` / ``OpenDoc6``, which
also want a document-type enum, open options and a configuration
name). The generic shared route,
``_common.ComApp(prog_id).open_via("Documents", path)``, therefore
normally fails against a real SolidWorks install.

So this adapter:

1. tries the shared COM route first (it succeeds against wrappers or
   proxies that do expose a Documents-style collection, and in tests),
2. and when that errors, falls back to the route that reliably works
   on a real machine: launching the detected ``SLDWORKS.exe`` with
   the file path as its command-line argument, which SolidWorks
   honours by opening the file on start.

Every result from the open tool carries ``"route": "com"`` or
``"route": "exe_arg"`` (also mirrored as ``"via"``) so the caller
knows which path was taken; the exe-fallback result also records the
COM error that triggered the fallback as ``"com_error"``.

Safety: the target path must pass ``core.check_path_allowed`` *before*
any COM call or process launch is attempted; a disallowed path
returns an err dict and nothing is opened or spawned.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
The COM wrapper is built through :func:`make_com_app`
(monkeypatchable, and :func:`open_file` also accepts an injected
``com_app`` / ``com_factory``), and the exe fallback spawns through
``subprocess.Popen`` after detecting the exe via
``apps_catalog.detect_app``, so tests can inject fakes without a real
SolidWorks, COM, or Windows.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import ComApp

APP_ID = "solidworks"
PROG_ID = "SldWorks.Application"
# Generic COM collection the shared open_via route tries. Real
# SolidWorks has no such collection (its API is the OpenDoc/OpenDoc6
# method on the app object), so this attempt is expected to error on a
# real install and hand over to the exe-argument fallback below.
COLLECTION = "Documents"


def _catalog_prog_id() -> str:
    """Return the ProgID recorded in the catalog for SolidWorks."""
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
    """Build the COM wrapper for SolidWorks (factory seam for tests).

    *factory*, when given, is forwarded to :class:`ComApp` as its
    zero-arg application factory, bypassing the real Windows/pywin32
    dispatch entirely.
    """
    if factory is not None:
        return ComApp(_catalog_prog_id(), factory=factory)
    return ComApp(_catalog_prog_id())


def _open_via_exe_arg(checked_path) -> dict:
    """Fallback route: launch the detected SLDWORKS.exe with the file
    as its argument (SolidWorks opens the file on start)."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    if not det.get("installed") or not det.get("path"):
        return err("SolidWorks is not installed (SLDWORKS.exe was not "
                   "found on this machine), so the exe-argument "
                   "fallback cannot open the file either.")
    try:
        proc = subprocess.Popen([det["path"], str(checked_path)],
                                close_fds=True)
        pid = getattr(proc, "pid", None)
        audit("solidworks_open_exe_arg", {"app_id": APP_ID,
                                          "exe": det["path"],
                                          "path": str(checked_path),
                                          "pid": pid})
        return ok(opened=True, route="exe_arg", via="exe_arg",
                  path=str(checked_path), exe=det["path"], pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def open_file(path: str, com_app=None, com_factory=None) -> dict:
    """Open *path* in SolidWorks: COM first, exe-argument fallback.

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
    fallback = _open_via_exe_arg(checked)
    if fallback.get("ok"):
        fallback["com_error"] = result.get("error")
        return fallback
    return err(f"Could not open {checked} in SolidWorks. "
               f"COM route failed: {result.get('error')}. "
               f"Exe-argument route failed: {fallback.get('error')}.")


def register(mcp):
    """Register the solidworks_* tools on a FastMCP server."""

    @mcp.tool()
    def solidworks_status() -> dict:
        """Check whether SolidWorks is installed on this machine.

        Returns the catalog detection result for SolidWorks: installed
        flag, the detected SLDWORKS.exe path (and how it was found),
        its automation strategy ("com") and its COM ProgID
        ("SldWorks.Application"). Use this before trying to open a
        file so you know whether SolidWorks is available at all.
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def solidworks_launch() -> dict:
        """Launch SolidWorks (without opening any file).

        Starts the installed SLDWORKS.exe found by the catalog and
        returns its path and pid. Fails with a clear error when
        SolidWorks is not installed on this machine.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def solidworks_open_file(path: str) -> dict:
        """Open a SolidWorks file (.sldprt part, .sldasm assembly, .slddrw drawing) in SolidWorks.

        The path must live under an allowed root (see the project
        path allowlist) — a disallowed path is rejected and nothing
        is opened. Two routes are tried, in order:

        1. COM: the generic shared route
           ``ComApp("SldWorks.Application").open_via("Documents", path)``.
           Note SolidWorks is a special case: its real COM API opens
           files via the OpenDoc/OpenDoc6 *method* on the application
           object, not a Documents collection, so this route normally
           errors against a real install.
        2. Exe-argument fallback: the detected SLDWORKS.exe is
           launched with the file path as its argument, and
           SolidWorks opens the file on start.

        The result's "route" field says which happened: "com" or
        "exe_arg" (the exe result also carries "exe", "pid" and the
        triggering "com_error").
        """
        return open_file(path)

    return ["solidworks_status", "solidworks_launch", "solidworks_open_file"]
