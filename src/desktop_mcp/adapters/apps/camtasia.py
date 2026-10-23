"""Camtasia adapter — UI-tier automation for TechSmith Camtasia.

The catalog lists Camtasia with strategy ``ui``: it has **no scripting
API, no COM ProgID and no plugin interface**. A ``.cmproj`` project is a
folder containing XML and media assets, but it is owned by the running
app — editing it behind Camtasia's back is not supported, so this
adapter does not try. What it can honestly do locally:

- ``camtasia_status``   — is Camtasia installed, and where?
- ``camtasia_launch``   — start Camtasia (no project).
- ``camtasia_open_file`` — start the detected Camtasia executable with
  one file as its argument, e.g. a ``.cmproj`` project folder or a
  ``.trec`` recording to open in the editor. The path must live under
  an allowed root (``core.check_path_allowed``).
- ``camtasia_focus``    — bring an already-open Camtasia window to the
  front via Windows UI Automation (graceful error elsewhere).

Anything richer (recording, timeline editing, effects, export) happens
inside Camtasia's own UI and is out of scope for local tools — an agent
would drive it with UI automation after opening the project here.

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...apps_catalog import detect_app
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import catalog_launch, catalog_status, focus_window

APP_ID = "camtasia"
# Display name from the catalog entry for "camtasia" — also the window
# title substring used when focusing the app.
APP_NAME = "Camtasia"

# Original bindings, remembered so the resolvers below can tell a
# monkeypatched module global apart from the real implementation.
_ORIGINAL_DETECT_APP = detect_app
_ORIGINAL_CATALOG_STATUS = catalog_status
_ORIGINAL_CATALOG_LAUNCH = catalog_launch
_ORIGINAL_FOCUS_WINDOW = focus_window


# --------------------------------------------------------------- helpers
# Factored out (and resolved through module globals at call time) so
# tests can monkeypatch detection, the _common helpers and spawning
# without a real Camtasia install.

def build_open_command(exe, path) -> list:
    """Build the argv for opening *path* in Camtasia: ``[exe, path]``.

    Pure helper — no validation, no side effects. ``camtasia_open_file``
    passes the detected executable and the already-allowlist-checked
    path (as a string), so the result is exactly ``[exe, path]``.
    """
    return [exe, path]


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Monkeypatched in tests.

    A GUI editor must be spawned detached (``Popen``), not run to
    completion through ``run_cli`` — Camtasia stays open.
    """
    return subprocess.Popen(argv, close_fds=True)


def _detect() -> dict:
    """Catalog detection for Camtasia, honouring monkeypatches.

    Prefers a ``detect_app`` patched directly into this module's
    namespace; otherwise calls the catalog's own ``detect_app`` (which
    itself may have been patched via ``apps_catalog.detect_app``).
    """
    current = globals().get("detect_app")
    if current is not None and current is not _ORIGINAL_DETECT_APP:
        return current(APP_ID)
    return apps_catalog.detect_app(APP_ID)


def _call_catalog_status() -> dict:
    """``catalog_status(APP_ID)``, honouring monkeypatches of either
    this module's ``catalog_status`` global or ``_common``'s, and of
    this module's ``detect_app`` global (temporarily swapped into the
    catalog module, where the real ``catalog_status`` looks it up)."""
    fn = globals().get("catalog_status")
    if fn is not None and fn is not _ORIGINAL_CATALOG_STATUS:
        return fn(APP_ID)
    det = globals().get("detect_app")
    if (det is not None and det is not _ORIGINAL_DETECT_APP
            and det is not apps_catalog.detect_app):
        original = apps_catalog.detect_app
        apps_catalog.detect_app = det
        try:
            return _common.catalog_status(APP_ID)
        finally:
            apps_catalog.detect_app = original
    return _common.catalog_status(APP_ID)


def _call_catalog_launch() -> dict:
    fn = globals().get("catalog_launch")
    if fn is not None and fn is not _ORIGINAL_CATALOG_LAUNCH:
        return fn(APP_ID)
    return _common.catalog_launch(APP_ID)


def _call_focus_window() -> dict:
    fn = globals().get("focus_window")
    if fn is not None and fn is not _ORIGINAL_FOCUS_WINDOW:
        return fn(APP_NAME)
    return _common.focus_window(APP_NAME)


def open_file(path: str) -> dict:
    """Open *path* (e.g. a .cmproj project or .trec recording) in Camtasia."""
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
        argv = build_open_command(exe, str(checked))
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("camtasia_open_file", {"app_id": APP_ID, "name": APP_NAME,
                                     "exe": exe, "path": str(checked),
                                     "pid": pid})
        return ok(app_id=APP_ID, name=APP_NAME, opened=True, launched=True,
                  exe=exe, path=str(checked), pid=pid, argv=argv)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# -------------------------------------------------------------- register

def register(mcp):
    """Register the camtasia_* tools on a FastMCP server."""

    @mcp.tool()
    def camtasia_status() -> dict:
        """Check whether Camtasia is installed on this machine.

        Returns the catalog detection result: installed flag,
        executable path (when found), how it was found and the
        automation strategy ("ui" — Camtasia has no scripting API,
        so control beyond opening files is UI automation only).
        """
        return _call_catalog_status()

    @mcp.tool()
    def camtasia_launch() -> dict:
        """Launch the Camtasia desktop app (no project).

        Fails with a clear error when Camtasia is not installed.
        The launch is written to the audit log.
        """
        return _call_catalog_launch()

    @mcp.tool()
    def camtasia_open_file(path: str) -> dict:
        """Open a file in Camtasia, e.g. a .cmproj project or .trec recording.

        Launches the detected Camtasia executable with *path* as its
        argument. The path must live under an allowed root (see
        core.check_path_allowed); a disallowed path is an error and
        nothing is launched. Also an error when Camtasia is not
        installed. The launch is written to the audit log.

        This is UI-level integration only: Camtasia has no scripting
        API, so this hands the file to the app — recording, editing
        and exporting continue in Camtasia's own UI.
        """
        return open_file(path)

    @mcp.tool()
    def camtasia_focus() -> dict:
        """Bring an already-open Camtasia window to the front.

        Uses Windows UI Automation to find a window whose title
        contains "Camtasia" and focus it. On non-Windows machines
        (or when no Camtasia window is open) this returns a graceful
        error dict rather than raising.
        """
        return _call_focus_window()

    return [camtasia_status, camtasia_launch, camtasia_open_file, camtasia_focus]
