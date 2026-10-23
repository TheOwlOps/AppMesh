"""Spotify adapter — UI-tier control for the Spotify desktop app.

Catalog id ``spotify`` (strategy ``"ui"``): the desktop app exposes no
local COM / CLI / scripting API, so this adapter covers the basics an
agent needs:

- ``spotify_status`` — is Spotify installed, and where?
- ``spotify_launch`` — start the installed Spotify app.
- ``spotify_open_uri`` — open a Spotify link (track, album, playlist,
  artist, …) through the operating system's URI handler, which routes
  ``spotify:`` URIs and ``https://open.spotify.com/`` links to the app.
- ``spotify_focus`` — bring the Spotify window to the front.

Every tool returns a plain ``core.ok`` / ``core.err`` dict and never
raises across the MCP boundary.
"""
from __future__ import annotations

import os
import subprocess
import sys

from ...core import audit, err, ok
from ._common import catalog_launch, catalog_status, focus_window

APP_ID = "spotify"

#: Only these URI prefixes are accepted by ``spotify_open_uri``. Anything
#: else is rejected without touching the OS, so an agent (or a prompt
#: injection) cannot use this tool as a generic "open any URL / file"
#: primitive.
_VALID_URI_PREFIXES = ("spotify:", "https://open.spotify.com/")


def _open_uri(uri: str) -> None:
    """Open *uri* with the operating system's default URI handler.

    Factored out as a module-level function so tests can monkeypatch it
    and never touch the real OS. On Windows this uses ``os.startfile``
    (ShellExecute), which resolves ``spotify:`` URIs to the Spotify app;
    other platforms fall back to their standard opener.
    """
    if hasattr(os, "startfile"):  # Windows
        os.startfile(uri)  # type: ignore[attr-defined]
    elif sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "", uri], close_fds=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", uri], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", uri], close_fds=True)


def _is_valid_spotify_uri(uri: object) -> bool:
    """True only for ``spotify:…`` URIs and ``https://open.spotify.com/…`` links."""
    return isinstance(uri, str) and uri.strip().startswith(_VALID_URI_PREFIXES)


def register(mcp):
    """Register the ``spotify_*`` tools on a FastMCP server."""

    @mcp.tool()
    def spotify_status() -> dict:
        """Check whether Spotify is installed on this machine.

        Returns the catalog detection result: installed flag, the
        executable path when found, how it was found, and Spotify's
        automation strategy ("ui" — the desktop app has no local API).
        Does not launch anything.
        """
        try:
            return catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def spotify_launch() -> dict:
        """Launch the installed Spotify desktop app.

        Fails with a clear error when Spotify is not installed. The
        launch is written to the audit log.
        """
        try:
            return catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def spotify_open_uri(uri: str) -> dict:
        """Open a Spotify track, album, playlist, artist or other item.

        `uri` must be a Spotify URI (e.g.
        "spotify:track:4uLU6hMCjMI75M1A2tKUQC") or a web link starting
        with "https://open.spotify.com/" (e.g.
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC").
        The link is handed to the operating system's URI handler, which
        opens it in the Spotify desktop app when installed. Any other
        URI or URL is rejected and nothing is opened.
        """
        try:
            if not isinstance(uri, str) or not uri.strip():
                return err(
                    "spotify_open_uri requires a non-empty Spotify URI: "
                    "one starting with 'spotify:' or "
                    "'https://open.spotify.com/'.")
            cleaned = uri.strip()
            if not _is_valid_spotify_uri(cleaned):
                return err(
                    f"Invalid Spotify URI: {cleaned!r}. It must start with "
                    "'spotify:' or 'https://open.spotify.com/'.")
            _open_uri(cleaned)
            audit("spotify_open_uri", {"app_id": APP_ID, "uri": cleaned})
            return ok(app_id=APP_ID, opened=True, uri=cleaned)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def spotify_focus() -> dict:
        """Bring the Spotify window to the front.

        Uses Windows UI Automation; returns an error when no Spotify
        window is open or when this machine is not running Windows.
        """
        try:
            return focus_window("Spotify")
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)
