"""iTunes adapter — playback control via the legacy iTunes COM API.

iTunes for Windows exposes a real COM Automation interface
(ProgID ``iTunes.Application``): library, playlists and playback
control (``PlayPause()``, ``NextTrack()``, ``PreviousTrack()``, …).

COM is Windows-only: on any other platform, or when iTunes / pywin32
is not installed, every action tool degrades to a plain
``{"ok": False, "error": "..."}`` dict — tools never raise across
the MCP boundary.

Test seam: action tools obtain the COM application object through the
module-level :func:`_get_app` factory. Tests monkeypatch ``_get_app``
to return a fake app object (or to raise), so no Windows machine,
iTunes installation or pywin32 is needed to exercise the tools.
"""
from __future__ import annotations

from ._common import ComApp, audit, catalog_launch, catalog_status, err, ok

APP_ID = "itunes"
PROG_ID = "iTunes.Application"

# Lazily-created shared COM connection (constructed on first use, so
# merely importing/registering this module never touches COM).
_com_app: ComApp | None = None


def _get_app():
    """Return the underlying iTunes COM application object.

    Goes through :class:`._common.ComApp`, which raises a clear
    RuntimeError off Windows or when pywin32 / iTunes is missing;
    callers convert that to ``core.err``. Tests replace this factory
    with one returning a fake app object.
    """
    global _com_app
    if _com_app is None:
        _com_app = ComApp(PROG_ID)
    return _com_app.app()


def _call_com(method_name: str, action: str) -> dict:
    """Call ``app.<method_name>()`` on the iTunes COM app, never raising."""
    try:
        app = _get_app()
        method = getattr(app, method_name, None)
        if not callable(method):
            return err(f"iTunes COM object does not expose a callable {method_name}().")
        method()
        audit(f"itunes_{action}", {"prog_id": PROG_ID, "method": method_name})
        return ok(app_id=APP_ID, action=action, prog_id=PROG_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def play_pause() -> dict:
    """Toggle iTunes playback (play/pause) via COM ``PlayPause()``."""
    return _call_com("PlayPause", "play_pause")


def next_track() -> dict:
    """Skip to the next track in iTunes via COM ``NextTrack()``."""
    return _call_com("NextTrack", "next_track")


def register(mcp):
    """Register the ``itunes_*`` tools on a FastMCP server.

    Binds ``itunes_status``, ``itunes_launch``, ``itunes_play_pause``
    and ``itunes_next_track``. Playback control uses COM and is
    therefore Windows-only (with iTunes installed); status/launch
    come straight from the app catalog and work anywhere.
    """

    @mcp.tool()
    def itunes_status() -> dict:
        """Report whether iTunes is installed on this machine, plus its
        catalog automation strategy (com) and COM ProgID
        (iTunes.Application). Works on any platform."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def itunes_launch() -> dict:
        """Launch iTunes if it is installed. Fails with a clear error
        when iTunes was not found on this machine. The launch is
        written to the audit log."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def itunes_play_pause() -> dict:
        """Toggle iTunes playback between play and pause.

        Uses the iTunes COM API (Windows-only, iTunes must be
        installed): calls PlayPause() on the iTunes.Application COM
        object. On non-Windows machines or without iTunes, returns
        an error dict instead of raising."""
        return play_pause()

    @mcp.tool()
    def itunes_next_track() -> dict:
        """Skip iTunes to the next track.

        Uses the iTunes COM API (Windows-only, iTunes must be
        installed): calls NextTrack() on the iTunes.Application COM
        object. On non-Windows machines or without iTunes, returns
        an error dict instead of raising."""
        return next_track()

    return {"app_id": APP_ID, "prog_id": PROG_ID}
