"""VLC adapter — playback control for the VLC media player.

VLC has no COM interface; the catalog strategy is ``cli``. This adapter
exposes the basics an agent needs:

- ``vlc_status``  — is VLC installed, and where (catalog detection),
- ``vlc_launch``  — start VLC with no media (catalog launch),
- ``vlc_play_media(path)`` — open a media file in VLC,
- ``vlc_version`` — report the installed VLC version (``vlc --version``).

Safety: the media path must pass ``core.check_path_allowed`` (the user's
allowlisted roots) before anything is spawned. Every tool returns a
``core.ok`` / ``core.err`` dict and never raises across the MCP boundary.
Process spawning is factored into the module-level :func:`_spawn` and the
version probe goes through ``_common.run_cli`` with an injectable runner,
so tests can exercise everything without launching a real player.
"""
from __future__ import annotations

import shutil
import subprocess

from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "vlc"

#: Executable names to probe, in order. "vlc" first: on Windows
#: ``shutil.which`` resolves it to vlc.exe via PATHEXT, and on
#: Linux/macOS the binary is plain ``vlc``.
_EXE_CANDIDATES = ("vlc", "vlc.exe")

#: Fallback executable name when no installed VLC can be located.
_DEFAULT_EXE = "vlc"


# --------------------------------------------------------------------------
# Helpers (pure / factored out for tests)
# --------------------------------------------------------------------------

def build_play_argv(exe, path) -> list:
    """Build the argv that opens *path* in VLC: ``[exe, path]``.

    Pure function. A falsy *exe* falls back to plain ``"vlc"`` (PATH
    lookup at spawn time).
    """
    return [str(exe) if exe else _DEFAULT_EXE, str(path)]


def _resolve_exe() -> str:
    """Return the path of the installed VLC executable, or ``"vlc"``.

    Detection is a plain PATH probe over the candidate names; when VLC
    is not found the bare name is returned and the caller's spawn /
    run_cli call produces a clean "not found" error dict.
    """
    for name in _EXE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return str(found)
    return _DEFAULT_EXE


def _spawn(argv: list):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen(argv, close_fds=True)


# --------------------------------------------------------------------------
# Tool implementations (module-level so tests can inject fakes)
# --------------------------------------------------------------------------

def vlc_status() -> dict:
    """Report whether VLC is installed, where, and its strategy."""
    return catalog_status(APP_ID)


def vlc_launch() -> dict:
    """Launch the installed VLC application (no media loaded)."""
    return catalog_launch(APP_ID)


def vlc_play_media(path: str) -> dict:
    """Open a media file in VLC and start playing it.

    The path must live under one of the user's allowed roots
    (``DESKTOP_MCP_ALLOWED_PATHS`` / home directory). Returns the argv
    used and the player process pid.
    """
    try:
        checked = check_path_allowed(path)
        argv = build_play_argv(_resolve_exe(), str(checked))
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("vlc_play_media", {"path": str(checked), "argv": argv, "pid": pid})
        return ok(playing=True, path=str(checked), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def vlc_version(runner=None) -> dict:
    """Report the installed VLC version via ``vlc --version``.

    *runner* is injectable for tests: a callable(argv, timeout)
    returning (returncode, stdout, stderr). When VLC is not installed
    this returns an error dict, not an exception.
    """
    try:
        argv = [_resolve_exe(), "--version"]
        res = run_cli(argv, runner=runner)
        if not res.get("ok"):
            return res
        stdout = res.get("stdout") or ""
        version = next((line.strip() for line in stdout.splitlines()
                        if line.strip()), "")
        return ok(version=version, argv=argv,
                  returncode=res.get("returncode"),
                  stdout=stdout, stderr=res.get("stderr", ""))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# --------------------------------------------------------------------------
# MCP registration
# --------------------------------------------------------------------------

def register(mcp) -> list:
    """Register the vlc_* tools on a FastMCP server. Returns tool names.

    The wrappers expose only user-facing parameters (the test-only
    ``runner`` argument of :func:`vlc_version` stays out of the MCP
    schema); module-level implementations are captured first so the
    same-named wrappers do not recurse.
    """
    _g = globals()
    _status = _g["vlc_status"]
    _launch = _g["vlc_launch"]
    _play = _g["vlc_play_media"]
    _version = _g["vlc_version"]

    @mcp.tool()
    def vlc_status() -> dict:
        """Check whether VLC media player is installed on this machine,
        where its executable lives, and which automation strategy it
        uses (CLI)."""
        return _status()

    @mcp.tool()
    def vlc_launch() -> dict:
        """Launch the VLC media player (no media loaded). Fails with a
        clear error when VLC is not installed."""
        return _launch()

    @mcp.tool()
    def vlc_play_media(path: str) -> dict:
        """Open a media file (video or audio) in VLC and play it. The
        path must be inside one of the user's allowed folders."""
        return _play(path)

    @mcp.tool()
    def vlc_version() -> dict:
        """Report the installed VLC version (runs `vlc --version`).
        Returns an error when VLC is not installed."""
        return _version()

    return ["vlc_status", "vlc_launch", "vlc_play_media", "vlc_version"]
