"""Figma adapter — UI-tier automation for the Figma desktop app.

Figma is primarily cloud/UI-driven: the desktop app has no local API
(the Plugin API runs inside the app; the REST API is cloud-side), so
the catalog strategy for ``figma`` is ``ui`` — there is no COM ProgID,
no local scripting interface and no editable local project-file format
to drive. Designs live in the user's Figma cloud account. This adapter
therefore covers only the basics an agent needs before driving the UI:

- ``figma_status``    — is the desktop app installed, and where?
- ``figma_launch``    — start the desktop app.
- ``figma_open_file`` — start the desktop app with a local file path
  as its argument (the Figma desktop app accepts a local file path,
  e.g. a ``.fig`` file or an asset to bring into Figma via the UI).
  The file must live under an allowed root.
- ``figma_focus``     — bring an already-open Figma window to the
  front (Windows UI Automation; graceful error elsewhere).

Anything richer (creating/editing designs, comments, export) happens
inside Figma's cloud UI and is out of scope for local tools.
All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "figma"
# Display name from the catalog entry for "figma" — also the window
# title substring used when focusing the app.
APP_NAME = "Figma"


def _detect() -> dict:
    """Detect Figma via the catalog. Factored out for tests."""
    return apps_catalog.detect_app(APP_ID)


def _spawn(argv: list[str]):
    """Start *argv* as a detached process.

    Factored out at module level so tests can monkeypatch it and
    assert the exact argv without launching anything real.
    """
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _open_file(path: str) -> dict:
    """Open the local file *path* with the Figma desktop app.

    The path must pass the project allowlist (``check_path_allowed``)
    before anything is spawned. Returns an err dict when the path is
    not allowed or Figma is not detected on this machine.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        det = _detect()
        if not det.get("ok"):
            return det
        if not det.get("installed"):
            return err(
                f"{APP_NAME} is not installed (none of its executables "
                "were found on this machine).")
        exe = det.get("path")
        if not exe:
            return err(f"{APP_NAME} was detected but no executable path was found.")
        argv = [str(exe), str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("figma_open_file", {"app_id": APP_ID, "name": APP_NAME,
                                  "exe": str(exe), "path": str(checked),
                                  "pid": pid})
        return ok(app_id=APP_ID, name=APP_NAME, opened=True,
                  launched=True, exe=str(exe), path=str(checked),
                  argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Bind the figma_* tools on a FastMCP server."""

    @mcp.tool()
    def figma_status() -> dict:
        """Check whether the Figma desktop app is installed.

        Returns the catalog detection result for Figma: installed
        flag, executable path (when found) and the automation
        strategy ("ui" — Figma is primarily cloud/UI-driven, its
        desktop app has no local API, so control is UI-driven).
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def figma_launch() -> dict:
        """Launch the Figma desktop app.

        Fails with a clear error when Figma is not installed. The
        launch is written to the audit log. Note Figma itself is
        cloud/UI-driven: after launch, designs are worked on in the
        app's UI (or via the cloud REST API, not locally).
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def figma_open_file(path: str) -> dict:
        """Open a local file with the Figma desktop app.

        Launches the detected Figma executable with *path* as its
        argument, e.g. a ``.fig`` file or an asset you want to bring
        into Figma. The path must live under an allowed root
        (see core.check_path_allowed); a disallowed path is an error
        and nothing is launched. Also an error when Figma is not
        installed. The launch is written to the audit log.

        Figma is primarily cloud/UI-driven, so this only hands the
        file to the desktop app — any import steps continue in the
        Figma UI.
        """
        return _open_file(path)

    @mcp.tool()
    def figma_focus() -> dict:
        """Bring an open Figma window to the front.

        Uses Windows UI Automation to find a window whose title
        contains "Figma" and focus it. On non-Windows machines (or
        when no Figma window is open) this returns a graceful error
        dict rather than raising.
        """
        return _common.focus_window(APP_NAME)

    return [figma_status, figma_launch, figma_open_file, figma_focus]
