"""Microsoft OneNote adapter — COM attempt with an exe fallback.

OneNote is in the catalog as a ``com`` app (ProgID
``OneNote.Application``), but its COM object model is limited: it
exposes the notebooks / sections / pages hierarchy as XML and has **no**
``Documents.Open`` for opening files the way Word or Excel do. This
adapter therefore tries the COM route first —

``ComApp("OneNote.Application").open_via("Documents", path)``

— and, when that errors (the normal case on a real machine), falls back
to launching the detected ``ONENOTE.EXE`` with the file path as its
argument, which OneNote honours by opening the file on start. **The exe
route is the expected real-world path for OneNote**; the COM attempt is
kept first only so a future / fuller OneNote COM surface would be used
automatically if it ever appears. Every result from the open tool
carries ``"route": "com"`` or ``"route": "exe"`` so the caller knows
which path was taken.

Safety: the target path must pass ``core.check_path_allowed`` *before*
any COM call or process launch is attempted; a disallowed path returns
an err dict and nothing is opened or spawned.

All tools return ``core.ok`` / ``core.err`` dicts and never raise. The
COM wrapper lives at module level (:data:`com_app`, built through
:func:`make_com_app` with an injectable factory) and the exe spawn goes
through :func:`_launch_with_file`, so tests can inject fakes without a
real OneNote, COM, or Windows.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import ComApp

APP_ID = "onenote"
PROG_ID = "OneNote.Application"
COLLECTION = "Documents"

# Module-level COM wrapper. It is lazy: constructing it touches no COM —
# the real ``OneNote.Application`` dispatch only happens on the first
# ``open_via`` call, and a factory can be injected for tests.
com_app = ComApp(PROG_ID)


def make_com_app(factory=None) -> ComApp:
    """Return the COM wrapper for OneNote (factory seam for tests).

    With no *factory*, returns the module-level :data:`com_app`. With a
    factory (a zero-arg callable returning a fake COM application),
    returns a fresh :class:`ComApp` using it — that is how tests inject
    a fake ``OneNote.Application`` object.
    """
    if factory is not None:
        return ComApp(PROG_ID, factory=factory)
    return com_app


def _launch_with_file(exe_path: str, file_path: str):
    """Start *exe_path* with *file_path* as its argument (detached).

    Factored out so tests can monkeypatch the spawn without launching
    a real process.
    """
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


def _open_via_exe(checked_path) -> dict:
    """Fallback route: launch the detected ONENOTE.EXE with the file."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    if not det.get("installed") or not det.get("path"):
        return err("Microsoft OneNote is not installed (ONENOTE.EXE was "
                   "not found on this machine), so the exe fallback cannot "
                   "open the file either.")
    try:
        proc = _launch_with_file(det["path"], str(checked_path))
        pid = getattr(proc, "pid", None)
        audit("onenote_open_exe", {"app_id": APP_ID, "exe": det["path"],
                                   "path": str(checked_path), "pid": pid})
        return ok(opened=True, route="exe", path=str(checked_path),
                  exe=det["path"], pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def open_file(path: str, com_app=None) -> dict:
    """Open *path* in OneNote: COM first, exe launch as fallback.

    The allowlist check runs first; a rejected path is an err dict and
    neither COM nor a process launch is attempted. Note that OneNote's
    COM API has no ``Documents.Open`` for files, so on a real machine
    the COM attempt is expected to fail and the file is opened by
    launching ONENOTE.EXE with it — the exe route is the expected
    real-world path for this app.
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
    return err(f"Could not open {checked} in OneNote. "
               f"COM route failed: {result.get('error')}. "
               f"Exe route failed: {fallback.get('error')}.")


def register(mcp):
    """Register the onenote_* tools on a FastMCP server."""

    @mcp.tool()
    def onenote_status() -> dict:
        """Check whether Microsoft OneNote is installed on this machine.

        Returns the catalog detection result for OneNote: installed
        flag, the detected ONENOTE.EXE path (and how it was found),
        its automation strategy ("com") and its COM ProgID
        ("OneNote.Application"). Use this before trying to open a
        file so you know whether OneNote is available at all.
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def onenote_launch() -> dict:
        """Launch Microsoft OneNote (without opening any file).

        Starts the installed ONENOTE.EXE found by the catalog and
        returns its path and pid. Fails with a clear error when
        OneNote is not installed on this machine.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def onenote_open_file(path: str) -> dict:
        """Open a file (e.g. a .one notebook section) in Microsoft OneNote.

        The path must live under an allowed root (see the project path
        allowlist) — a disallowed path is rejected and nothing is
        opened. OneNote's COM API has no Documents.Open for files, so
        although the COM route is attempted first, the expected
        real-world path is the fallback: ONENOTE.EXE is launched with
        the file as its argument and opens it on start. The result's
        "route" field says which happened: "com" or "exe".
        """
        return open_file(path)

    return ["onenote_status", "onenote_launch", "onenote_open_file"]
