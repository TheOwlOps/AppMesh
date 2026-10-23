"""REAPER adapter — plugin strategy via executable file-open.

REAPER (catalog id ``reaper``, strategy ``plugin``) is scriptable in-app
through ReaScript (Lua / Python / EEL) and can render from the CLI
(``reaper.exe -renderproject <file.rpp>``), but the reliable way to hand
it work from outside the app is the plugin strategy used here: launch
the detected ``reaper.exe`` with a file as its single command-line
argument, e.g.::

    reaper.exe C:\\Users\\me\\song.rpp

REAPER opens its own ``.rpp`` project files that way, and also imports
plain media files (``.wav`` / ``.mp3`` / ``.flac`` / …) onto a new track.
Deeper ReaScript automation can be layered on later; this adapter covers
detection, launching, opening a file and opening a project.

Safety: the target path must pass ``core.check_path_allowed`` (it has to
live under an allowed root) before anything is launched, project paths
must end in ``.rpp``, and every tool returns a plain ``core.ok`` /
``core.err`` dict — tools never raise across the MCP boundary.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "reaper"


# --------------------------------------------------------------------- argv

def build_open_argv(exe_path: str | Path, file_path: str | Path) -> list[str]:
    """Build the argv used to open *file_path* in REAPER. Pure helper.

    REAPER takes the file to open as a plain positional argument, so the
    argv is exactly ``[exe, file]`` — no flags, no shell quoting (the
    list is passed straight to ``subprocess.Popen``). Shared by the
    file-open and project-open tools.
    """
    return [str(exe_path), str(file_path)]


def build_project_argv(exe_path: str | Path, project_path: str | Path) -> list[str]:
    """Build the argv used to open a ``.rpp`` project in REAPER. Pure.

    A project is opened exactly like any other file — REAPER decides
    what to do from the ``.rpp`` extension — so this delegates to the
    shared :func:`build_open_argv`.
    """
    return build_open_argv(exe_path, project_path)


# Backwards-friendly alias for the shared builder.
build_argv = build_open_argv


# ------------------------------------------------------------------ process

def _popen(argv: list[str]):
    """Start *argv* via ``subprocess.Popen``. Factored out for tests."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _spawn(argv: list[str]):
    """Launch *argv* as a detached process.

    Module-level and monkeypatchable: tests replace this (or
    :func:`_popen`, or ``subprocess.Popen``) so no real app is launched.
    """
    return _popen(argv)


# ---------------------------------------------------------------- detection

def _detect() -> dict:
    """Detection record for REAPER from the catalog.

    Factored out so tests can monkeypatch detection (here, or on
    ``apps_catalog.detect_app``) instead of probing the real machine.
    """
    return apps_catalog.detect_app(APP_ID)


def _open(path: str, *, project: bool) -> dict:
    """Shared body of the open tools: validate → detect → spawn → audit."""
    try:
        if not path or not str(path).strip():
            return err("`path` must be a non-empty file path.")
        if project and Path(str(path)).suffix.lower() != ".rpp":
            return err(
                f"REAPER project files must end with .rpp: {path!r}. "
                "Use reaper_open_file for media or other files.")
        checked = check_path_allowed(path)
        det = _detect()
        if not det.get("ok"):
            return det
        exe = det.get("path")
        if not det.get("installed") or not exe:
            return err(
                "REAPER is not installed (none of its executables — "
                "reaper.exe — were found on this machine).")
        argv = build_open_argv(exe, checked)
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        tool = "reaper_open_project" if project else "reaper_open_file"
        audit(tool, {"app_id": APP_ID, "path": str(checked),
                     "exe": str(exe), "argv": argv, "pid": pid})
        return ok(app_id=APP_ID, name=det.get("name") or "REAPER",
                  opened=True, launched=True, project=project,
                  path=str(checked), exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def open_file(path: str) -> dict:
    """Open a media / project file in REAPER by launching its exe.

    Returns an ``ok`` dict with the resolved ``path``, the detected
    ``exe``, the exact ``argv`` used and the child ``pid``; an ``err``
    dict when the path is not allowed or REAPER is not installed.
    """
    return _open(path, project=False)


def open_project(path: str) -> dict:
    """Open a ``.rpp`` project in REAPER by launching its exe.

    Like :func:`open_file`, but *path* must end with ``.rpp``
    (case-insensitive) — anything else is rejected with an ``err``
    dict before any path check, detection or launch happens.
    """
    return _open(path, project=True)


# ----------------------------------------------------------------- register

def register(mcp):
    """Register the reaper_* tools on a FastMCP server."""

    @mcp.tool()
    def reaper_status() -> dict:
        """Check whether REAPER is installed on this machine.

        Returns the catalog detection result for REAPER: installed
        flag, the reaper.exe path when found, how it was found, and
        its automation strategy ("plugin" — ReaScript).
        """
        try:
            return _common.catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def reaper_launch() -> dict:
        """Launch REAPER (with no file) if it is installed.

        Fails with a clear error dict when REAPER is not installed.
        The launch is written to the audit log.
        """
        try:
            return _common.catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def reaper_open_file(path: str) -> dict:
        """Open a file in REAPER (a media file, or any file REAPER imports).

        `path` is the file to open — for example a .wav, .mp3 or .flac
        recording, which REAPER imports onto a track, or a .rpp project.
        The file is opened by launching the detected reaper.exe with
        the file as its argument. The path must live under an allowed
        root (see DESKTOP_MCP_ALLOWED_PATHS); disallowed paths are
        rejected before anything is launched. Fails with a clear error
        dict when REAPER is not installed. For projects, prefer
        reaper_open_project, which validates the .rpp extension.
        """
        return open_file(path)

    @mcp.tool()
    def reaper_open_project(path: str) -> dict:
        """Open a REAPER project (.rpp) in REAPER.

        `path` must end with .rpp and live under an allowed root (see
        DESKTOP_MCP_ALLOWED_PATHS); non-.rpp paths and disallowed
        paths are rejected before anything is launched. The project is
        opened by launching the detected reaper.exe with the project
        path as its argument. Fails with a clear error dict when
        REAPER is not installed.
        """
        return open_project(path)

    return mcp
