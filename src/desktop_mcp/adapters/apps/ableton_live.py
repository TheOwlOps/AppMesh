"""Ableton Live adapter — UI strategy (catalog id ``ableton-live``).

Ableton Live exposes no automation API to outside processes: its Live
Object Model is only reachable from Max for Live devices running *inside*
the app. From the desktop, an agent can therefore:

- check whether Live is installed (``ableton_live_status``),
- start it (``ableton_live_launch``),
- open a Live Set (``.als`` file) by launching the detected Live
  executable with the set as its argument (``ableton_live_open_file``),
- bring Live's window to the front (``ableton_live_focus``).

Everything else (transport, clips, mixing) would need Windows UI
Automation / MIDI from inside the running app.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, focus_window

APP_ID = "ableton-live"

# Live's main window title looks like "<Set name> - Ableton Live 12 Suite"
# (or "Untitled - Ableton Live …"), so this fragment matches it.
WINDOW_TITLE_FRAGMENT = "Ableton Live"


# ------------------------------------------------------------ pure helpers

def build_open_argv(exe_path: str, file_path: str) -> list[str]:
    """Build the argv used to open *file_path* in Ableton Live.

    Pure helper: ``[exe, file]`` — Live opens a Live Set (``.als``)
    passed as its first command-line argument.
    """
    return [str(exe_path), str(file_path)]


def _popen(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen(argv, close_fds=True)


def _detect_exe() -> str | None:
    """Return the detected Ableton Live executable path, or None."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok") or not det.get("installed"):
        return None
    return det.get("path")


def open_file(path: str) -> dict:
    """Open a Live Set file in Ableton Live. Never raises."""
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        exe = _detect_exe()
        if not exe:
            return err(
                "Ableton Live is not installed (its executable was not "
                "found on this machine), so the file cannot be opened.")
        argv = build_open_argv(exe, str(checked))
        proc = _popen(argv)
        pid = getattr(proc, "pid", None)
        audit("ableton_live_open_file",
              {"app_id": APP_ID, "path": str(checked), "exe": exe, "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  exe=exe, argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the ableton_live_* tools on a FastMCP server."""

    @mcp.tool()
    def ableton_live_status() -> dict:
        """Check whether Ableton Live is installed on this machine.

        Returns the detected executable path, how it was found and the
        catalog automation strategy ("ui": Live's Live Object Model is
        only reachable via Max for Live inside the app, so outside
        control is UI-level)."""
        try:
            return catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def ableton_live_launch() -> dict:
        """Launch Ableton Live if it is installed.

        Fails with a clear error when Live is not installed. The launch
        is written to the audit log."""
        try:
            return catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def ableton_live_open_file(path: str) -> dict:
        """Open a Live Set (.als file) in Ableton Live.

        Launches the detected Ableton Live executable with the set file
        as its argument (or hands the file to the already-running
        instance). The path must live under an allowed root (the user's
        home folder, or DESKTOP_MCP_ALLOWED_PATHS). Fails with a clear
        error when the path is not allowed or Live is not installed.
        """
        return open_file(path)

    @mcp.tool()
    def ableton_live_focus() -> dict:
        """Bring the Ableton Live window to the front.

        Uses Windows UI Automation to find the window whose title
        contains "Ableton Live" and focus it. Fails with a clear error
        when no such window is open or this machine is not on Windows.
        """
        try:
            return focus_window(WINDOW_TITLE_FRAGMENT)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return APP_ID
