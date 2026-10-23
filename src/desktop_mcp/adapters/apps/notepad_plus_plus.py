"""Notepad++ adapter — UI strategy.

Notepad++ has no COM ProgID and no automation API; the useful agent
surface is detection, launching, and opening a file by passing it as a
command-line argument (``notepad++.exe <file>``). Everything else
(macros, plugins) happens inside the app itself.

Tools (prefix ``notepad_plus_plus``):
- ``notepad_plus_plus_status`` — catalog detection + strategy info.
- ``notepad_plus_plus_launch`` — launch Notepad++ via the catalog.
- ``notepad_plus_plus_open_file(path)`` — open *path* in Notepad++.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "notepad-plus-plus"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen(argv, close_fds=True)


def open_file(path: str) -> dict:
    """Open *path* in Notepad++ (``[exe, path]``). Allowlist-checked."""
    try:
        checked = check_path_allowed(path)
        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return det
        if not det.get("installed"):
            return err(f"{det.get('name') or 'Notepad++'} is not installed "
                       "(notepad++.exe was not found on this machine).")
        exe = det.get("path")
        if not exe:
            return err("Notepad++ was detected but no executable path was reported.")
        argv = [exe, str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("notepad_plus_plus_open_file", {"path": str(checked), "exe": exe, "pid": pid})
        return ok(opened=True, path=str(checked), exe=exe, argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Bind the Notepad++ tools on a FastMCP server."""

    @mcp.tool()
    def notepad_plus_plus_status() -> dict:
        """Report whether Notepad++ is installed, where its executable
        was found, and its catalog automation strategy (ui)."""
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def notepad_plus_plus_launch() -> dict:
        """Launch Notepad++ if it is installed; returns an error dict
        when it is not."""
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def notepad_plus_plus_open_file(path: str) -> dict:
        """Open a text file in Notepad++.

        *path* must live under an allowed root (see
        DESKTOP_MCP_ALLOWED_PATHS). Notepad++ is started with the file
        as its sole argument; if Notepad++ is already running it opens
        the file in a new tab.
        """
        return open_file(path)
