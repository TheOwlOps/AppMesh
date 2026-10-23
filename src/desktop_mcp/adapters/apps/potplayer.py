"""PotPlayer adapter — CLI playback control.

PotPlayer (catalog id ``potplayer``, strategy ``cli``) has no COM or
scripting API, but its executable accepts media files on the command
line: ``PotPlayerMini64.exe "<file>"`` opens and plays that file. That
is all this adapter needs:

- ``potplayer_status``     — is PotPlayer installed, and where?
- ``potplayer_launch``     — start PotPlayer (no media).
- ``potplayer_play_media`` — start PotPlayer playing one media file.

Playing passes through two safety gates: the media path must pass
``core.check_path_allowed`` (the project allowlist), and PotPlayer must
actually be detected on this machine — otherwise a clear ``err`` dict
is returned instead of raising or spawning anything.

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise
across the MCP boundary.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status

APP_ID = "potplayer"


# --------------------------------------------------------------------------
# Pure argv builder + factored process launch (both monkeypatchable)
# --------------------------------------------------------------------------

def build_play_argv(exe: str, path: str) -> list[str]:
    """Build the PotPlayer command line that opens *path* for playback.

    Pure function: ``[exe, path]`` — PotPlayer treats a bare file
    argument as "open and play this media".
    """
    return [str(exe), str(path)]


def _spawn(argv: list[str]):
    """Start *argv* as a detached process via ``subprocess.Popen``.

    Factored out at module level so tests can monkeypatch it and never
    launch a real application.
    """
    return subprocess.Popen(argv, close_fds=True)


# Alias with an explicit name, for callers/tests that prefer it.
_popen = _spawn


def _detect_exe() -> dict:
    """Detection record for PotPlayer (wraps ``apps_catalog.detect_app``).

    Factored out so tests can monkeypatch detection at either this
    module or the catalog module.
    """
    return apps_catalog.detect_app(APP_ID)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def potplayer_status() -> dict:
    """Report whether PotPlayer is installed, where its executable
    lives, how it was found and its automation strategy (cli)."""
    try:
        return catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def potplayer_launch() -> dict:
    """Launch PotPlayer (with no media). Fails with a clear error dict
    when PotPlayer is not installed on this machine."""
    try:
        return catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def potplayer_play_media(path: str) -> dict:
    """Open and play a media file in PotPlayer.

    The file path must be inside the allowed roots (see
    ``DESKTOP_MCP_ALLOWED_PATHS``); PotPlayer must be installed. The
    player is started detached with the media file as its argument.
    Returns the executable, resolved media path, argv and pid.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — not allowed / bad path
        return err(exc)
    try:
        det = _detect_exe()
        if not det.get("ok"):
            return det
        exe = det.get("path") if det.get("installed") else None
        if not exe:
            return err(
                "PotPlayer is not installed (none of its executables — "
                "PotPlayerMini64.exe, PotPlayer.exe — were found on this "
                "machine), so the media file cannot be played.")
        argv = build_play_argv(exe, str(checked))
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("potplayer_play_media",
              {"app_id": APP_ID, "exe": exe, "path": str(checked), "pid": pid})
        return ok(app_id=APP_ID, name=det.get("name") or "PotPlayer",
                  played=True, launched=True, exe=exe, path=str(checked),
                  media=str(checked), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# Alias matching the naming style of the other adapters' impl functions.
play_media = potplayer_play_media


# --------------------------------------------------------------------------
# MCP registration
# --------------------------------------------------------------------------

def register(mcp) -> list[str]:
    """Register the potplayer_* tools on a FastMCP server.

    Returns the tool names bound. The wrappers reuse the tool names,
    so the module-level implementations are captured through globals()
    first (same pattern as the cli_tools adapter).
    """
    _g = globals()
    _status = _g["potplayer_status"]
    _launch = _g["potplayer_launch"]
    _play = _g["potplayer_play_media"]

    @mcp.tool()
    def potplayer_status() -> dict:
        """Check whether PotPlayer is installed on this machine: its
        executable path, how it was detected and its automation
        strategy (cli)."""
        return _status()

    @mcp.tool()
    def potplayer_launch() -> dict:
        """Launch PotPlayer (no media). Fails with a clear error when
        PotPlayer is not installed."""
        return _launch()

    @mcp.tool()
    def potplayer_play_media(path: str) -> dict:
        """Open and play a media file (video/audio) in PotPlayer.
        The path must be inside the allowed roots; PotPlayer must be
        installed. The player starts detached playing that file."""
        return _play(path)

    return ["potplayer_status", "potplayer_launch", "potplayer_play_media"]
