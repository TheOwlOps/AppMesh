"""Microsoft Access adapter — COM automation with an exe fallback.

Strategy (catalog id ``access``): ``com`` via the ``Access.Application``
ProgID. Access is a database front-end, so the file it opens is a
database (``.accdb`` / ``.mdb``). Its object model does not really open
databases through a ``Documents`` collection the way Word/Excel do — the
idiomatic call is ``app.OpenCurrentDatabase(path)`` — but the shared
route order below tries the generic collection pattern first, then the
Access-native one, then simply launching ``MSACCESS.EXE`` with the file.

Safety: the target path must pass ``core.check_path_allowed`` *before*
any COM call or process launch is attempted; a disallowed path returns
an err dict and nothing else is called.

All tools return ``core.ok`` / ``core.err`` dicts and never raise. The
COM wrapper is a module-level :class:`._common.ComApp` singleton whose
``factory`` is injectable (tests swap ``_com`` for
``ComApp("Access.Application", factory=lambda: fake_app)``), and the exe
spawn goes through :func:`_launch_with_file`, so tests can monkeypatch
both seams without a real Access, COM, or Windows.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import ComApp

APP_ID = "access"
PROG_ID = "Access.Application"

# Module-level lazy singleton: the Access.Application COM object is only
# created on first use, so importing/registering this module never
# touches COM (and works fine on Linux without pywin32). Tests replace
# this with ``ComApp(PROG_ID, factory=<fake factory>)``.
_com = ComApp(PROG_ID)


def _launch_with_file(exe_path: str, file_path: str):
    """Start *exe_path* with *file_path* as its argument (detached).

    Factored out (wrapping ``subprocess.Popen([exe, file])``) so tests
    can monkeypatch the spawn without launching a real process.
    """
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


def open_file(path: str) -> dict:
    """Open a database file *path* in Microsoft Access.

    Route order:
    1. ``ComApp.open_via("Documents", path)`` — the generic Office
       collection pattern (``app.Documents.Open(path)``); Access builds
       that expose a usable Documents collection succeed here.
    2. ``app.OpenCurrentDatabase(path)`` — Access's real, native
       pattern, tried on the same COM application object (getattr
       guarded, so a missing/non-callable attribute just falls through).
    3. Exe fallback — launch the detected ``MSACCESS.EXE`` (via
       ``apps_catalog.detect_app("access")``) with the file as its
       argument, through :func:`_launch_with_file`.

    Successful opens are audited (route 1 audits inside ``open_via``;
    routes 2 and 3 audit here). The path must pass
    ``core.check_path_allowed`` before any route is attempted.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — rejection happens before anything else
        return err(exc)
    target = str(checked)

    # -- Route 1: generic Documents collection --------------------------
    try:
        result = _com.open_via("Documents", target)
    except Exception as exc:  # noqa: BLE001 — a broken factory is a COM failure
        result = err(exc)
    if result.get("ok"):
        return {**result, "route": "com"}
    route1_error = result.get("error")

    # -- Route 2: Access-native OpenCurrentDatabase ----------------------
    route2_error: str | None = None
    try:
        app = _com.app()
        opener = getattr(app, "OpenCurrentDatabase", None)
        if callable(opener):
            opener(target)
            audit("access_open_file", {"app_id": APP_ID, "prog_id": PROG_ID,
                                       "path": target,
                                       "via": "OpenCurrentDatabase"})
            return ok(opened=True, route="com", via="OpenCurrentDatabase",
                      path=target)
        route2_error = "Access.Application has no callable OpenCurrentDatabase."
    except Exception as exc:  # noqa: BLE001 — fall through to the exe route
        route2_error = str(exc)

    # -- Route 3: launch MSACCESS.EXE with the file -----------------------
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return det
    exe = det.get("path") if det.get("installed") else None
    if not exe:
        return err(
            f"Could not open {target} in Microsoft Access. "
            f"Documents route failed: {route1_error}. "
            f"OpenCurrentDatabase route failed: {route2_error}. "
            "Exe fallback unavailable: MSACCESS.EXE was not found on "
            "this machine (Access does not appear to be installed).")
    try:
        proc = _launch_with_file(exe, target)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    pid = getattr(proc, "pid", None)
    if isinstance(proc, int):  # a monkeypatched spawn may return a bare pid
        pid = proc
    audit("access_open_file", {"app_id": APP_ID, "path": target,
                               "via": "exe", "exe": exe, "pid": pid})
    return ok(opened=True, route="exe", via="exe", launched=True,
              path=target, exe=exe, pid=pid)


def register(mcp):
    """Register the access_* tools on a FastMCP server."""

    @mcp.tool()
    def access_status() -> dict:
        """Check whether Microsoft Access is installed on this machine.

        Returns the catalog detection result for Access: installed flag,
        the detected MSACCESS.EXE path (and how it was found), its
        automation strategy ("com") and its COM ProgID
        ("Access.Application"). Use this before opening a database so
        you know whether Access is available at all.
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def access_launch() -> dict:
        """Launch Microsoft Access (without opening any database).

        Starts the installed MSACCESS.EXE found by the catalog and
        returns its path and pid. Fails with a clear error when Access
        is not installed on this machine.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def access_open_file(path: str) -> dict:
        """Open an Access database file (.accdb / .mdb) in Microsoft Access.

        The path must live under an allowed root (see the project path
        allowlist) — a disallowed path is rejected immediately and
        nothing else is called. Routes, in order: (1) COM via
        ``app.Documents.Open(path)``; (2) COM via Access's native
        ``app.OpenCurrentDatabase(path)``; (3) launch the detected
        MSACCESS.EXE with the file as its argument. The result's
        "route" field says which happened ("com" or "exe"), and "via"
        names the exact COM call when route 2 was used. Successful
        opens are written to the audit log.
        """
        return open_file(path)

    return ["access_status", "access_launch", "access_open_file"]
