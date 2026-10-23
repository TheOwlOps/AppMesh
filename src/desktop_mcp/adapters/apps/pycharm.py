"""PyCharm adapter — strategy: ``ui``.

PyCharm (JetBrains) has no COM or scripting automation surface that this
project can drive portably, so this adapter stays at the UI tier:

- ``pycharm_status``      — is PyCharm installed, and where (catalog data)?
- ``pycharm_launch``      — start PyCharm with no project.
- ``pycharm_open_project``— start the detected PyCharm executable with a
  project folder/file as its command-line argument (the JetBrains IDEs
  open the path given as the first argument).
- ``pycharm_focus``       — bring an already-open PyCharm window to the
  front via Windows UI Automation.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "pycharm"

# Fallback display name if the catalog record cannot be read; the real
# name ("PyCharm") is taken from the catalog entry whenever possible.
_FALLBACK_NAME = "PyCharm"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen(argv, close_fds=True)


def _display_name() -> str:
    """PyCharm's display name from its catalog entry."""
    try:
        res = apps_catalog.get_app(APP_ID)
        if isinstance(res, dict):
            record = res.get("app") if isinstance(res.get("app"), dict) else res
            name = record.get("name")
            if name:
                return str(name)
    except Exception:  # noqa: BLE001 — fall back quietly
        pass
    return _FALLBACK_NAME


def _open_project(path: str) -> dict:
    """Launch the detected PyCharm exe with *path* as its argument."""
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return det
        if not det.get("installed") or not det.get("path"):
            return err(
                f"{det.get('name') or _FALLBACK_NAME} is not installed "
                "(none of its executables were found on this machine).")
        exe = str(det["path"])
        argv = [exe, str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("pycharm_open_project", {"app_id": APP_ID, "exe": exe,
                                       "path": str(checked), "pid": pid})
        return ok(app_id=APP_ID, launched=True, exe=exe, argv=argv,
                  path=str(checked), pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Bind the pycharm_* tools on a FastMCP server."""

    @mcp.tool()
    def pycharm_status() -> dict:
        """Report whether PyCharm is installed, its executable path and
        the catalog automation strategy for it."""
        try:
            return _common.catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def pycharm_launch() -> dict:
        """Launch PyCharm (no project) if it is installed."""
        try:
            return _common.catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def pycharm_open_project(path: str) -> dict:
        """Open a project folder (or file) in PyCharm.

        *path* must live under an allowed root (see
        ``core.check_path_allowed``); PyCharm is then started as
        ``<pycharm exe> <path>``. Returns an error dict when the path is
        not allowed or PyCharm is not installed.
        """
        return _open_project(path)

    @mcp.tool()
    def pycharm_focus() -> dict:
        """Bring the open PyCharm window to the front (Windows UI
        Automation; returns an error dict on other platforms)."""
        try:
            return _common.focus_window(_display_name())
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return mcp
