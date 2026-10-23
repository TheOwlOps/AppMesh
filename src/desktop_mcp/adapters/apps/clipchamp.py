"""Clipchamp adapter — UI strategy.

Clipchamp is a Microsoft Store video editor with no COM ProgID, no
scripting API and no editable project-file format reachable from
outside the app, so this adapter works at the launch / UI tier:

- ``clipchamp_status``   — catalog detection (is Clipchamp.exe present?).
- ``clipchamp_launch``   — start Clipchamp via the catalog launcher.
- ``clipchamp_open_file``— start the detected Clipchamp executable with
  a media file as its argument (``Clipchamp.exe <file>``), so the file
  opens in the editor for import / editing.
- ``clipchamp_focus``    — bring an open Clipchamp window to the front
  via Windows UI Automation.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "clipchamp"

#: Fragment of the Clipchamp main window's title, used for focusing.
WINDOW_TITLE_FRAGMENT = "Clipchamp"


def build_open_argv(exe_path, media_path) -> list[str]:
    """Build the argv used to open *media_path* in Clipchamp.

    Pure function: ``[exe, media]`` — Clipchamp is started with the
    media file as its sole argument. Both values are stringified so
    ``pathlib.Path`` inputs work too.
    """
    return [str(exe_path), str(media_path)]


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests
    (monkeypatch this instead of ever launching a real app)."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def open_file(path: str) -> dict:
    """Open *path* (a media file) in Clipchamp. Allowlist-checked."""
    try:
        checked = check_path_allowed(path)
        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return det
        if not det.get("installed"):
            return err(f"{det.get('name') or 'Clipchamp'} is not installed "
                       "(Clipchamp.exe was not found on this machine).")
        exe = det.get("path")
        if not exe:
            return err("Clipchamp was detected but no executable path "
                       "was reported.")
        argv = build_open_argv(exe, checked)
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("clipchamp_open_file",
              {"app_id": APP_ID, "exe": str(exe), "path": str(checked),
               "pid": pid})
        return ok(app_id=APP_ID, name=det.get("name") or "Clipchamp",
                  opened=True, launched=True, path=str(checked),
                  exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Bind the clipchamp_* tools on a FastMCP server."""

    @mcp.tool()
    def clipchamp_status() -> dict:
        """Report whether Clipchamp is installed on this machine.

        Detection comes from the AppMesh catalog: Clipchamp.exe is
        probed on PATH and (on Windows) in the usual install folders.
        Returns the detected executable path, how it was found and the
        app's automation strategy ("ui" — Clipchamp is a Store app
        with no API, so deeper control is UI automation only).
        """
        try:
            return _common.catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def clipchamp_launch() -> dict:
        """Launch Clipchamp (with no media file argument).

        Fails with an error dict when Clipchamp is not installed. To
        open a specific media file in the editor instead, use
        clipchamp_open_file.
        """
        try:
            return _common.catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def clipchamp_open_file(path: str) -> dict:
        """Open a media file (video/audio/image) in Clipchamp.

        *path* must live under an allowed root (the user's home folder,
        or any root added via DESKTOP_MCP_ALLOWED_PATHS). The detected
        Clipchamp.exe is started with the file as its sole argument,
        which opens it in the editor for import / editing. Returns an
        error dict when the path is not allowed or Clipchamp is not
        installed.
        """
        return open_file(path)

    @mcp.tool()
    def clipchamp_focus() -> dict:
        """Bring an open Clipchamp window to the front.

        Uses Windows UI Automation (pywinauto) to find a window whose
        title contains "Clipchamp" and focus it. Returns an error dict
        on non-Windows machines or when no Clipchamp window is open.
        """
        try:
            return _common.focus_window(WINDOW_TITLE_FRAGMENT)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return mcp
