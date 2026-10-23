"""Eclipse IDE adapter — UI strategy.

Eclipse has no COM ProgID or scripting API the catalog can drive, so this
adapter covers the UI-tier basics: detect/launch via the shared app
catalog, open a project folder by passing it to the Eclipse executable,
and bring the Eclipse window to the front with Windows UI Automation.

Tools (all return ``core.ok`` / ``core.err`` dicts and never raise):
- ``eclipse_status``       — installed? where? (catalog detection)
- ``eclipse_launch``       — launch Eclipse (catalog launch)
- ``eclipse_open_project`` — launch Eclipse with a project folder argument
- ``eclipse_focus``        — focus the Eclipse window (pywinauto, Windows)
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "eclipse"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for testability."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _display_name() -> str:
    """Eclipse's display name from the catalog entry (fallback: "Eclipse")."""
    try:
        rec = apps_catalog.get_app(APP_ID)
        if rec.get("ok"):
            name = (rec.get("app") or {}).get("name")
            if name:
                return str(name)
    except Exception:  # noqa: BLE001 — a name lookup must never break focus
        pass
    return "Eclipse"


def register(mcp):
    """Bind the eclipse_* tools on a FastMCP server."""

    @mcp.tool()
    def eclipse_status() -> dict:
        """Report whether Eclipse is installed on this machine, the path
        of its executable when found, and its catalog automation strategy."""
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def eclipse_launch() -> dict:
        """Launch the Eclipse IDE. Returns an error dict when Eclipse is
        not installed on this machine."""
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def eclipse_open_project(path: str) -> dict:
        """Open a project folder in Eclipse.

        The folder must live under an allowed root (see
        ``core.check_path_allowed``). Eclipse is launched as
        ``eclipse.exe <project folder>`` so the IDE starts on that
        project/workspace folder. Returns an error dict when the path is
        not allowed or Eclipse is not installed.
        """
        try:
            checked = check_path_allowed(path)
            det = apps_catalog.detect_app(APP_ID)
            if not det.get("ok"):
                return det
            if not det.get("installed"):
                return err(f"{det.get('name') or _display_name()} is not installed "
                           "(none of its executables were found on this machine).")
            exe = det.get("path")
            if not exe:
                return err("Eclipse was detected but its executable path is unknown.")
            argv = [str(exe), str(checked)]
            proc = _spawn(argv)
            pid = getattr(proc, "pid", None)
            audit("eclipse_open_project", {"app_id": APP_ID, "exe": str(exe),
                                           "path": str(checked), "pid": pid})
            return ok(app_id=APP_ID, opened=True, exe=str(exe),
                      path=str(checked), argv=argv, pid=pid)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def eclipse_focus() -> dict:
        """Bring the Eclipse window to the front (Windows UI Automation).
        Returns an error dict on non-Windows machines or when no Eclipse
        window is currently open."""
        return _common.focus_window(_display_name())

    return ["eclipse_status", "eclipse_launch", "eclipse_open_project", "eclipse_focus"]
