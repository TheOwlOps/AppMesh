"""Affinity Photo adapter — UI-automation tier.

Affinity Photo (catalog id ``affinity-photo``, executable ``Photo.exe``)
has no scripting or COM API: macros run only inside the app. This adapter
therefore covers the basics an agent needs before / alongside Windows UI
Automation:

- ``affinity_photo_status`` — is Affinity Photo installed, and where?
- ``affinity_photo_launch`` — start Affinity Photo (no file).
- ``affinity_photo_open_file`` — start Affinity Photo with a document
  (``.afphoto``, raster image, …) as its command-line argument.
- ``affinity_photo_focus`` — bring the Affinity Photo window to the front.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "affinity-photo"
APP_NAME = "Affinity Photo"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process.

    Factored out as a module-level helper so tests can monkeypatch it
    (no real Affinity Photo process is started in tests).
    """
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _open_file(path: str) -> dict:
    """Launch the detected Affinity Photo exe with *path* as argument."""
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    if not det.get("installed"):
        return err(
            f"{APP_NAME} is not installed "
            "(none of its executables were found on this machine)."
        )
    exe = det.get("path")
    if not exe:
        return err(f"{APP_NAME} was detected as installed but no executable path was found.")
    argv = [str(exe), str(checked)]
    try:
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("affinity_photo_open_file", {
            "app_id": APP_ID,
            "name": APP_NAME,
            "exe": str(exe),
            "path": str(checked),
            "pid": pid,
        })
        return ok(app_id=APP_ID, name=APP_NAME, opened=True, launched=True,
                  exe=str(exe), path=str(checked), pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the affinity_photo_* tools on a FastMCP server."""

    @mcp.tool()
    def affinity_photo_status() -> dict:
        """Check whether Affinity Photo is installed on this machine.

        Returns the catalog detection result for Affinity Photo:
        installed flag, executable path (when found), how it was found
        (``via``) and its automation strategy (``ui`` — Affinity Photo
        has no scripting API, so deeper control is UI automation only).
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def affinity_photo_launch() -> dict:
        """Launch Affinity Photo (with no document open).

        Fails with a clear error when Affinity Photo is not installed.
        The launch is written to the audit log.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def affinity_photo_open_file(path: str) -> dict:
        """Open a file in Affinity Photo (e.g. a .afphoto document or an image).

        Launches the detected Affinity Photo executable with *path* as
        its command-line argument. *path* must live under an allowed
        root (the user's home folder, or a root added via
        DESKTOP_MCP_ALLOWED_PATHS); disallowed paths are rejected and
        nothing is launched. Fails with a clear error when Affinity
        Photo is not installed. The launch is written to the audit log.
        """
        return _open_file(path)

    @mcp.tool()
    def affinity_photo_focus() -> dict:
        """Bring the Affinity Photo window to the front.

        Uses Windows UI Automation to find a window whose title contains
        "Affinity Photo" and focus it. On non-Windows machines (or when
        no matching window is open) this returns a graceful error dict
        instead of raising.
        """
        return _common.focus_window(APP_NAME)

    return {"app_id": APP_ID, "name": APP_NAME}
