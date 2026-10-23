"""Audacity adapter — plugin strategy via executable file-open.

Audacity (catalog id ``audacity``, strategy ``plugin``) can be scripted
in-app through Macros and the mod-script-pipe named pipe, but the
reliable cross-version way to hand it work from outside is the plugin
strategy used here: launch the detected ``audacity.exe`` with an
audio or project file as its single command-line argument, e.g.::

    audacity.exe C:\\Users\\me\\take.wav

Audacity opens ``.wav`` / ``.mp3`` / ``.flac`` / ``.ogg`` audio files
and its own ``.aup3`` project files that way. Deeper scripted editing
(macros / mod-script-pipe) can be layered on later; this adapter covers
detection, launching and opening a file.

Safety: the file path must pass ``core.check_path_allowed`` (it has to
live under an allowed root) before anything is launched, and every
tool returns a plain ``core.ok`` / ``core.err`` dict — tools never
raise across the MCP boundary.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status

APP_ID = "audacity"


def build_open_argv(exe_path: str | Path, file_path: str | Path) -> list[str]:
    """Build the argv used to open *file_path* in Audacity. Pure helper.

    Audacity takes the file to open as a plain positional argument, so
    the argv is exactly ``[exe, file]`` — no flags, no shell quoting
    (the list is passed straight to ``subprocess.Popen``).
    """
    return [str(exe_path), str(file_path)]


def _spawn(argv: list[str]):
    """Launch *argv* as a detached process via ``subprocess.Popen``.

    Factored out as a module-level function so tests can monkeypatch it
    (or ``subprocess.Popen``) and never launch a real application.
    """
    return subprocess.Popen(argv, close_fds=True)


def _detect() -> dict:
    """Detection record for Audacity from the catalog.

    Factored out so tests can monkeypatch detection instead of probing
    the real machine.
    """
    return apps_catalog.detect_app(APP_ID)


def open_file(path: str) -> dict:
    """Open an audio/project file in Audacity by launching its exe.

    Returns an ``ok`` dict with the resolved ``path``, the detected
    ``exe``, the exact ``argv`` used and the child ``pid``; an ``err``
    dict when the path is not allowed or Audacity is not installed.
    """
    try:
        if not path or not str(path).strip():
            return err("`path` must be a non-empty file path.")
        checked = check_path_allowed(path)
        det = _detect()
        if not det.get("ok"):
            return det
        exe = det.get("path")
        if not det.get("installed") or not exe:
            return err(
                "Audacity is not installed (none of its executables — "
                "audacity.exe — were found on this machine).")
        argv = build_open_argv(exe, checked)
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("audacity_open_file", {"app_id": APP_ID, "path": str(checked),
                                     "exe": str(exe), "argv": argv, "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the audacity_* tools on a FastMCP server."""

    @mcp.tool()
    def audacity_status() -> dict:
        """Check whether Audacity is installed on this machine.

        Returns the catalog detection result for Audacity: installed
        flag, the executable path when found, how it was found, and
        its automation strategy ("plugin").
        """
        try:
            return catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def audacity_launch() -> dict:
        """Launch Audacity (with no file) if it is installed.

        Fails with a clear error dict when Audacity is not installed.
        The launch is written to the audit log.
        """
        try:
            return catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def audacity_open_file(path: str) -> dict:
        """Open an audio or project file in Audacity.

        `path` is the file to open — for example a .wav, .mp3, .flac
        or .ogg recording, or an Audacity .aup3 project file. The file
        is opened by launching the detected Audacity executable with
        the file as its argument. The path must live under an allowed
        root (see DESKTOP_MCP_ALLOWED_PATHS); disallowed paths are
        rejected before anything is launched. Fails with a clear error
        dict when Audacity is not installed.
        """
        return open_file(path)

    return {"app_id": APP_ID, "tools": ["audacity_status", "audacity_launch",
                                        "audacity_open_file"]}
