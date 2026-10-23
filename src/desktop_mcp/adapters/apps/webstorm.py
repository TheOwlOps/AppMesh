"""WebStorm adapter (catalog id ``webstorm``, strategy ``ui``).

WebStorm has no COM / scripting API reachable from outside the IDE, so
this adapter works at the launch / UI tier:

- ``webstorm_status``       — catalog detection (is webstorm64.exe present?).
- ``webstorm_launch``       — start the IDE via the catalog launcher.
- ``webstorm_open_project`` — start the IDE with a project folder or
  file as its argument (``webstorm64.exe <path>`` opens that project).
- ``webstorm_focus``        — bring an open WebStorm window to the front via
  Windows UI Automation.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "webstorm"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _display_name() -> str:
    """WebStorm's display name from its catalog entry."""
    try:
        res = apps_catalog.get_app(APP_ID)
        if res.get("ok"):
            name = (res.get("app") or {}).get("name")
            if name:
                return str(name)
    except Exception:  # noqa: BLE001 — fall back to the known name
        pass
    return "WebStorm"


def _open_project(path: str) -> dict:
    """Open *path* (a project folder or file) in WebStorm."""
    try:
        checked = check_path_allowed(path)
        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return det
        if not det.get("installed"):
            return err(f"{det.get('name') or _display_name()} is not installed "
                       "(webstorm64.exe was not found on this machine).")
        exe = det.get("path")
        if not exe:
            return err("WebStorm was detected but its executable path "
                       "is unknown.")
        argv = [str(exe), str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("webstorm_open_project",
              {"app_id": APP_ID, "exe": str(exe), "path": str(checked),
               "pid": pid})
        return ok(app_id=APP_ID, name=det.get("name") or _display_name(),
                  opened=True, launched=True, path=str(checked),
                  exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Bind the webstorm_* tools on a FastMCP server."""

    @mcp.tool()
    def webstorm_status() -> dict:
        """Report whether WebStorm is installed on this machine.

        Detection comes from the AppMesh catalog: webstorm64.exe is probed
        on PATH and (on Windows) in the usual JetBrains install folders.
        Returns the detected executable path, how it was found and the
        app's automation strategy ("ui").
        """
        try:
            return _common.catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def webstorm_launch() -> dict:
        """Launch WebStorm (with no project argument).

        Fails with an error dict when the IDE is not installed. To open a
        specific project instead, use webstorm_open_project.
        """
        try:
            return _common.catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def webstorm_open_project(path: str) -> dict:
        """Open a project folder (or file) in WebStorm.

        *path* must live under an allowed root (the user's home folder, or
        any root added via DESKTOP_MCP_ALLOWED_PATHS). The detected
        webstorm64.exe is started with the path as its argument, which makes
        WebStorm open that project. Returns an error dict when the path is
        not allowed or WebStorm is not installed.
        """
        return _open_project(path)

    @mcp.tool()
    def webstorm_focus() -> dict:
        """Bring an open WebStorm window to the front.

        Uses Windows UI Automation (pywinauto) to find a window whose title
        contains "WebStorm" and focus it. Returns an error dict on
        non-Windows machines or when no WebStorm window is open.
        """
        try:
            return _common.focus_window(_display_name())
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return mcp
