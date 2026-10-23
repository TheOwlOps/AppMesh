"""Zalo adapter — UI strategy.

Zalo (catalog id ``zalo``) has no official desktop API: the desktop app
can only be driven through Windows UI Automation. This adapter therefore
exposes just the generic surface — status, launch and window focus —
and no protocol/URL tool (there is no reliable public one for Zalo).
"""
from __future__ import annotations

from ._common import catalog_launch, catalog_status, focus_window

APP_ID = "zalo"
APP_NAME = "Zalo"  # catalog display name; also the window-title fragment


def register(mcp):
    """Register the zalo_* tools on a FastMCP server."""

    @mcp.tool()
    def zalo_status() -> dict:
        """Report whether Zalo is installed on this machine.

        Returns the catalog detection result for Zalo: installed flag,
        executable path (when found), how it was found, and its
        automation strategy ("ui" — Zalo has no desktop API).
        """
        return catalog_status(APP_ID)

    @mcp.tool()
    def zalo_launch() -> dict:
        """Launch the Zalo desktop app.

        Fails with a clear error when Zalo is not installed. The launch
        is written to the audit log.
        """
        return catalog_launch(APP_ID)

    @mcp.tool()
    def zalo_focus() -> dict:
        """Bring the Zalo window to the front.

        Searches open windows for one whose title contains "Zalo" and
        focuses it (Windows UI Automation). Fails with a clear error
        when no matching window is open or this machine is not running
        Windows.
        """
        return focus_window(APP_NAME)

    return [zalo_status, zalo_launch, zalo_focus]
