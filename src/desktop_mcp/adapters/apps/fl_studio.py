"""FL Studio adapter — UI strategy (catalog id ``fl-studio``).

FL Studio (Image-Line) exposes no automation API to outside processes:
its scripting only runs *inside* the app (Piano roll / Edison scripts).
From the desktop, an agent can therefore:

- check whether FL Studio is installed (``fl_studio_status``),
- start it (``fl_studio_launch``),
- open a project (``.flp`` file) by launching the detected FL Studio
  executable with the project as its argument (``fl_studio_open_file``),
- bring FL Studio's window to the front (``fl_studio_focus``).

Everything else (transport, mixer, playlist editing) would need
Windows UI Automation against the running app.

The executable (``FL64.exe``) is located through the shared app
catalog's detection (``apps_catalog.detect_app``), never hard-coded
here. All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, focus_window

APP_ID = "fl-studio"
APP_NAME = "FL Studio"

# FL Studio's main window title looks like "<project> - FL Studio 21"
# (or just "FL Studio" before a project is loaded), so this fragment
# matches it.
WINDOW_TITLE_FRAGMENT = "FL Studio"


# ------------------------------------------------------------ pure helpers

def build_open_argv(exe_path: str, file_path: str) -> list[str]:
    """Build the argv used to open *file_path* in FL Studio.

    Pure helper: ``[exe, file]`` — FL Studio opens a project (``.flp``)
    passed as its first command-line argument.
    """
    return [str(exe_path), str(file_path)]


def _popen(argv: list[str]):
    """Start *argv* as a detached process via ``subprocess.Popen``.

    Factored out as a module-level function so tests can monkeypatch it
    and no real process is ever started.
    """
    return subprocess.Popen(argv, close_fds=True)


def _detect() -> dict:
    """Detect FL Studio via the catalog. Factored out for tests."""
    return apps_catalog.detect_app(APP_ID)


def open_file(path: str) -> dict:
    """Open an FL Studio project file (.flp). Never raises.

    The path must pass the project allowlist (``check_path_allowed``)
    before anything is spawned. Returns an err dict when the path is
    not allowed or FL Studio is not detected on this machine.
    """
    try:
        checked = check_path_allowed(path)
        det = _detect()
        if not det.get("ok"):
            return det
        if not det.get("installed"):
            return err(
                f"{APP_NAME} is not installed "
                "(none of its executables were found on this machine), "
                "so the file cannot be opened.")
        exe = det.get("path")
        if not exe:
            return err(f"{APP_NAME} was detected without an executable path.")
        argv = build_open_argv(exe, str(checked))
        proc = _popen(argv)
        pid = getattr(proc, "pid", None)
        audit("fl_studio_open_file",
              {"app_id": APP_ID, "exe": str(exe), "path": str(checked),
               "argv": argv, "pid": pid})
        return ok(app_id=APP_ID, name=det.get("name") or APP_NAME,
                  opened=True, launched=True, path=str(checked),
                  exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the fl_studio_* tools on a FastMCP server."""

    @mcp.tool()
    def fl_studio_status() -> dict:
        """Check whether FL Studio is installed on this machine.

        Returns the detected executable path (FL64.exe), how it was
        found and the catalog automation strategy ("ui": FL Studio has
        no external automation API, so outside control is UI-level)."""
        try:
            return catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def fl_studio_launch() -> dict:
        """Launch FL Studio (no project) via the app catalog.

        Fails with a clear error when FL Studio is not installed. The
        launch is written to the audit log."""
        try:
            return catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def fl_studio_open_file(path: str) -> dict:
        """Open an FL Studio project file (.flp) in FL Studio.

        Launches the detected FL Studio executable with the project
        file as its argument (or hands the file to the already-running
        instance). The path must live under an allowed root (the user's
        home folder, or DESKTOP_MCP_ALLOWED_PATHS); disallowed paths are
        rejected before anything is launched. Fails with a clear error
        when FL Studio is not installed.
        """
        return open_file(path)

    @mcp.tool()
    def fl_studio_focus() -> dict:
        """Bring the FL Studio window to the front.

        Uses Windows UI Automation to find the window whose title
        contains "FL Studio" and focus it. Fails with a clear error
        when no such window is open or this machine is not on Windows.
        """
        try:
            return focus_window(WINDOW_TITLE_FRAGMENT)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return APP_ID
