"""Adobe InDesign adapter — COM automation (InDesign.Application).

InDesign exposes a real COM/ActiveX interface on Windows, so the primary
route for opening a document is ``InDesign.Application`` →
``Documents.Open(path)`` (via :class:`desktop_mcp.adapters.apps._common.ComApp`).
If COM is unavailable (InDesign not running / not registered, pywin32
missing, non-Windows machine), :func:`open_file` falls back to launching
the detected ``InDesign.exe`` with the file as its argument — the result
dict says which route was used in its ``route`` field (``"com"`` or
``"exe"``).

Every tool returns a plain ``core.ok`` / ``core.err`` dict and never
raises across the MCP boundary. Paths must pass
``core.check_path_allowed`` *before* any open is attempted.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import ComApp, catalog_launch, catalog_status

APP_ID = "indesign"
PROG_ID = "InDesign.Application"


# ------------------------------------------------------------------- seams
# Factored out so tests can monkeypatch them (no real COM / process spawn
# in the test-suite).

def _detect_exe() -> dict:
    """Detect the installed InDesign executable via the app catalog."""
    return apps_catalog.detect_app(APP_ID)


def _spawn_with_file(exe_path: str, file_path: str):
    """Launch *exe_path* with *file_path* as its argument (detached)."""
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


# ------------------------------------------------------------------- logic

def open_file(path: str) -> dict:
    """Open an InDesign document, COM first, exe-launch as fallback."""
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — disallowed: attempt nothing
        return err(exc)

    com_result = ComApp(PROG_ID).open_via("Documents", str(checked))
    if com_result.get("ok"):
        return {**com_result, "route": "com", "app_id": APP_ID}

    com_error = com_result.get("error") or "COM open failed"
    try:
        det = _detect_exe()
        if not det.get("ok"):
            return err(f"InDesign COM open failed ({com_error}); "
                       f"exe fallback unavailable: {det.get('error')}")
        if not det.get("installed") or not det.get("path"):
            return err(f"InDesign COM open failed ({com_error}); exe fallback "
                       "unavailable: InDesign is not installed (no executable "
                       "was found on this machine).")
        proc = _spawn_with_file(det["path"], str(checked))
        pid = getattr(proc, "pid", None)
        audit("indesign_open_file", {"app_id": APP_ID, "route": "exe",
                                     "exe": det["path"], "path": str(checked),
                                     "pid": pid})
        return ok(opened=True, route="exe", app_id=APP_ID, path=str(checked),
                  exe=det["path"], pid=pid, com_error=com_error)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(f"InDesign COM open failed ({com_error}); "
                   f"exe fallback failed: {exc}")


# ---------------------------------------------------------------- register

def register(mcp):
    """Register the ``indesign_*`` tools on a FastMCP server."""

    @mcp.tool()
    def indesign_status() -> dict:
        """Report whether Adobe InDesign is installed on this machine,
        where its executable lives, and how it is automated (COM via
        InDesign.Application). Read-only; safe to call first."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def indesign_launch() -> dict:
        """Launch Adobe InDesign (no document). Fails with a clear error
        when InDesign is not installed. The launch is audit-logged."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def indesign_open_file(path: str) -> dict:
        """Open an InDesign document (.indd) at an allow-listed *path*.

        Tries COM first (InDesign.Application → Documents.Open) and
        reports ``route: "com"``; if COM is unavailable it falls back to
        launching the detected InDesign.exe with the file and reports
        ``route: "exe"``. Paths outside the allowed roots are rejected
        before anything is attempted.
        """
        return open_file(path)

    return {"app_id": APP_ID, "prog_id": PROG_ID}
