"""Android Studio adapter — UI-tier automation for the Android IDE.

Android Studio (catalog id ``android-studio``, strategy ``ui``) has no
COM or scripting API an agent can drive directly; real project work
happens through Gradle / adb alongside it. This adapter therefore covers
the IDE lifecycle only:

- ``android_studio_status``      — is Android Studio installed, and where?
- ``android_studio_launch``      — start the IDE (no project).
- ``android_studio_open_project`` — start the IDE with a project folder.
- ``android_studio_focus``       — bring the IDE window to the front.

The executable (``studio64.exe``) is located through the shared app
catalog's detection (``apps_catalog.detect_app``), never hard-coded here.
All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "android-studio"


def _detect() -> dict:
    """Detect Android Studio via the catalog. Factored out for tests."""
    return apps_catalog.detect_app(APP_ID)


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _display_name() -> str:
    """Android Studio's display name, taken from its catalog entry."""
    try:
        rec = apps_catalog.get_app(APP_ID)
        if rec.get("ok"):
            name = (rec.get("app") or {}).get("name")
            if name:
                return str(name)
    except Exception:  # noqa: BLE001 — fall back to the known name
        pass
    return "Android Studio"


def _open_project(path: str) -> dict:
    """Open the Android project folder *path* in Android Studio.

    The path must pass the project allowlist (``check_path_allowed``)
    before anything is spawned. Returns an err dict when the path is
    not allowed or Android Studio is not detected on this machine.
    """
    try:
        checked = check_path_allowed(path)
        det = _detect()
        if not det.get("ok"):
            return det
        if not det.get("installed"):
            return err(
                f"{_display_name()} is not installed "
                "(none of its executables were found on this machine)."
            )
        exe = det.get("path")
        if not exe:
            return err(f"{_display_name()} was detected without an executable path.")
        argv = [str(exe), str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("android_studio_open_project",
              {"app_id": APP_ID, "exe": str(exe), "path": str(checked), "pid": pid})
        return ok(app_id=APP_ID, name=det.get("name") or _display_name(),
                  launched=True, opened=True, exe=str(exe),
                  path=str(checked), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def _focus() -> dict:
    """Bring the Android Studio window to the front (Windows UIA)."""
    try:
        return _common.focus_window(_display_name())
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the android_studio_* tools on a FastMCP server."""

    @mcp.tool()
    def android_studio_status() -> dict:
        """Report whether Android Studio is installed on this machine.

        Uses the shared app catalog detection for ``android-studio``
        (``studio64.exe``): returns the detected executable path, how it
        was found, and the catalog automation strategy (``ui``).
        """
        try:
            return _common.catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def android_studio_launch() -> dict:
        """Launch Android Studio (no project) via the app catalog.

        Returns an error dict if Android Studio is not installed.
        """
        try:
            return _common.catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def android_studio_open_project(path: str) -> dict:
        """Open an Android project folder in Android Studio.

        *path* is the project folder (the directory containing the
        project's Gradle files). It must live under an allowed root
        (see ``DESKTOP_MCP_ALLOWED_PATHS``); disallowed paths are
        rejected before anything is launched. The detected Android
        Studio executable is spawned as ``[exe, path]``. Returns an
        error dict if Android Studio is not installed.
        """
        return _open_project(path)

    @mcp.tool()
    def android_studio_focus() -> dict:
        """Bring the Android Studio window to the front.

        Uses Windows UI Automation to find a window whose title contains
        Android Studio's display name from the catalog and focus it.
        Returns an error dict on non-Windows machines or when no such
        window is open.
        """
        return _focus()

    return mcp
