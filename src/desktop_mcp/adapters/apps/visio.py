"""Microsoft Visio adapter — COM automation with a VISIO.EXE fallback.

Catalog id ``visio``, strategy ``com``: Visio exposes a real COM
Automation interface on Windows (``Visio.Application``) and its real
open pattern is ``app.Documents.Open(path)`` — that is the primary route
here, via the shared :class:`._common.ComApp` wrapper held at module
level as ``_com`` (lazy: importing this module never touches COM, so it
works on any platform).

When COM fails (Visio not running/registering, pywin32 missing,
non-Windows host, the document open itself raising, …) the adapter
falls back to launching the detected ``VISIO.EXE`` with the file path as
its argument, which Visio honours by opening the file on start. The
fallback spawn goes through the module-level :func:`_launch_with_file`
so tests can monkeypatch it without launching a real process. Every
result from the open tool carries ``"route": "com"`` or ``"route":
"exe"`` so the caller knows which path was taken.

Safety: the target path must pass ``core.check_path_allowed``. That
check happens inside ``ComApp.open_via`` before any COM call, and the
fallback route re-checks it explicitly — a disallowed path returns an
err dict and is never opened via COM nor passed to a spawned process.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import ComApp

APP_ID = "visio"
PROG_ID = "Visio.Application"
COLLECTION = "Documents"

# Module-level lazy COM holder: the Visio application object is only
# created on first use. Tests inject a fake by replacing this with
# ``ComApp(PROG_ID, factory=lambda: fake_app)``.
_com = ComApp(PROG_ID)


def _launch_with_file(exe_path: str, file_path: str):
    """Start *exe_path* (VISIO.EXE) with *file_path* as its argument.

    Factored out so tests can monkeypatch the spawn without launching
    a real process.
    """
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


def _open_via_exe(checked_path, com_error: str | None) -> dict:
    """Fallback route: launch the detected VISIO.EXE with the file.

    *checked_path* must already have passed ``check_path_allowed`` —
    this function re-checks it defensively before spawning anything.
    """
    try:
        checked = check_path_allowed(str(checked_path))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    if not det.get("installed") or not det.get("path"):
        return err(
            "Microsoft Visio is not installed (VISIO.EXE was not found "
            "on this machine), so the exe fallback cannot open the "
            f"file either. COM route failed: {com_error}")
    try:
        proc = _launch_with_file(det["path"], str(checked))
        pid = getattr(proc, "pid", None)
        audit("visio_open_exe", {"app_id": APP_ID, "exe": det["path"],
                                 "path": str(checked), "pid": pid})
        return ok(opened=True, route="exe", launched=True,
                  path=str(checked), exe=det["path"], pid=pid,
                  com_error=com_error)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def open_file(path: str) -> dict:
    """Open *path* in Visio: COM first, VISIO.EXE launch as fallback."""
    try:
        result = _com.open_via(COLLECTION, path)
    except Exception as exc:  # noqa: BLE001 — a broken factory is a COM failure
        result = err(exc)
    if result.get("ok"):
        return {**result, "route": "com"}
    # COM route failed. The fallback only ever runs for an allowed path:
    # re-check explicitly (open_via already checked, but a disallowed
    # path must never reach a spawned process).
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    return _open_via_exe(checked, result.get("error"))


def register(mcp):
    """Register the visio_* tools on a FastMCP server."""

    @mcp.tool()
    def visio_status() -> dict:
        """Check whether Microsoft Visio is installed on this machine.

        Returns the catalog detection result for Visio: installed flag,
        the detected VISIO.EXE path (and how it was found), its
        automation strategy ("com") and its COM ProgID
        ("Visio.Application"). Use this before trying to open a file so
        you know which route (COM / exe) is likely to work.
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def visio_launch() -> dict:
        """Launch Microsoft Visio (without opening any file).

        Starts the installed VISIO.EXE found by the catalog and returns
        its path and pid. Fails with a clear error when Visio is not
        installed on this machine.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def visio_open_file(path: str) -> dict:
        """Open a Visio file (.vsdx, .vsd, .vssx, …) in Microsoft Visio.

        The path must live under an allowed root (see the project path
        allowlist) — a disallowed path is rejected and nothing is
        opened or launched. The file is opened through Visio's real COM
        interface (app.Documents.Open) when available; if COM fails,
        VISIO.EXE is launched with the file as its argument instead.
        The result's "route" field says which happened: "com" or "exe",
        and on the COM route "document" carries the opened document's
        name.
        """
        return open_file(path)

    return ["visio_status", "visio_launch", "visio_open_file"]
