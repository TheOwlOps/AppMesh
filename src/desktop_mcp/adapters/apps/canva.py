"""Canva adapter — UI-tier automation for the Canva desktop app.

Canva is primarily cloud/UI-driven: the desktop app is an Electron
wrapper of the web app (catalog strategy ``ui``, no COM ProgID, no
local scripting API, no editable local project-file format). Designs
live in the user's Canva cloud account, so there is no deep local
automation surface here — this adapter covers the basics an agent
needs before driving the UI:

- ``canva_status``  — is the desktop app installed, and where?
- ``canva_launch``  — start the desktop app.
- ``canva_open_file`` — start the desktop app with a local file path
  as its argument (e.g. an image/design asset to bring into Canva via
  the UI). The file must live under an allowed root.
- ``canva_focus``   — bring an already-open Canva window to the front
  (Windows UI Automation; graceful error elsewhere).

Anything richer (creating/editing designs, uploading assets, export)
happens inside Canva's cloud UI and is out of scope for local tools.
All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "canva"
# Display name from the catalog entry for "canva" — also the window
# title substring used when focusing the app.
APP_NAME = "Canva"


def _spawn(argv: list[str]):
    """Start *argv* as a detached process.

    Factored out at module level so tests can monkeypatch it and
    assert the exact argv without launching anything real.
    """
    return subprocess.Popen(argv, close_fds=True)


def register(mcp):
    """Bind the canva_* tools on a FastMCP server."""

    @mcp.tool()
    def canva_status() -> dict:
        """Check whether the Canva desktop app is installed.

        Returns the catalog detection result for Canva: installed
        flag, executable path (when found) and the automation
        strategy ("ui" — Canva is a cloud-first, Electron-wrapped web
        app with no local API, so control is UI-driven).
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def canva_launch() -> dict:
        """Launch the Canva desktop app.

        Fails with a clear error when Canva is not installed. The
        launch is written to the audit log. Note Canva itself is
        cloud/UI-driven: after launch, designs are worked on in the
        app's web-like UI.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def canva_open_file(path: str) -> dict:
        """Open a local file with the Canva desktop app.

        Launches the detected Canva executable with *path* as its
        argument, e.g. an image or design asset you want to bring
        into Canva. The path must live under an allowed root
        (see core.check_path_allowed); a disallowed path is an error
        and nothing is launched. Also an error when Canva is not
        installed. The launch is written to the audit log.

        Canva is primarily cloud/UI-driven, so this only hands the
        file to the desktop app — any import/upload steps continue
        in the Canva UI.
        """
        try:
            checked = check_path_allowed(path)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)
        try:
            det = apps_catalog.detect_app(APP_ID)
            if not det.get("ok"):
                return det
            if not det.get("installed"):
                return err(
                    f"{APP_NAME} is not installed (none of its executables "
                    "were found on this machine).")
            exe = det.get("path")
            if not exe:
                return err(f"{APP_NAME} was detected but no executable path was found.")
            argv = [exe, str(checked)]
            proc = _spawn(argv)
            pid = getattr(proc, "pid", None)
            audit("canva_open_file", {"app_id": APP_ID, "name": APP_NAME,
                                      "exe": exe, "path": str(checked),
                                      "pid": pid})
            return ok(app_id=APP_ID, name=APP_NAME, opened=True,
                      launched=True, exe=exe, path=str(checked), pid=pid)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def canva_focus() -> dict:
        """Bring an open Canva window to the front.

        Uses Windows UI Automation to find a window whose title
        contains "Canva" and focus it. On non-Windows machines (or
        when no Canva window is open) this returns a graceful error
        dict rather than raising.
        """
        return _common.focus_window(APP_NAME)

    return [canva_status, canva_launch, canva_open_file, canva_focus]
