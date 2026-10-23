"""Filmora adapter — UI-level integration only (catalog strategy ``ui``).

Wondershare Filmora has NO public scripting / COM / CLI API and its
project files (``.wfp``) are proprietary, so AppMesh cannot automate
edits *inside* Filmora the way it can with COM or file-based apps.
What this adapter honestly offers is process/window-level help:

- ``filmora_status``    is Filmora installed, and where?
- ``filmora_launch``    start Filmora (without opening a file).
- ``filmora_open_file`` start Filmora with a file as its argument —
  a ``.wfp`` project or a media clip (``.mp4``, ``.mov``, …). Filmora
  itself does the opening; this adapter never reads or writes the
  file's contents.
- ``filmora_focus``     bring an already-open Filmora window to the
  front (Windows UI Automation via pywinauto; graceful error dict
  on other platforms or when no window is open).

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...apps_catalog import detect_app, get_app
from ...core import audit, check_path_allowed, err, ok
from . import _common  # noqa: F401  (kept in namespace: shared helpers home)
from ._common import catalog_launch, catalog_status, focus_window

APP_ID = "filmora"
DISPLAY_NAME = "Filmora"  # catalog entry name; also the window-title needle

# Remember the original binding so _status/_open_file can tell when
# detect_app in *this* module's namespace has been replaced (tests
# monkeypatch ``filmora.detect_app``) versus the catalog's own binding.
_ORIGINAL_DETECT_APP = detect_app


def build_open_command(exe, path) -> list[str]:
    """Pure helper: the argv that opens *path* in Filmora — ``[exe, path]``."""
    return [str(exe), str(path)]


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen(argv, close_fds=True)


def _effective_detect():
    """Return the ``detect_app`` callable to use for this call.

    This module's own binding wins when it has been replaced (the
    monkeypatch point for tests); otherwise the catalog module's
    current binding is used, so patching
    ``apps_catalog.detect_app`` is honoured too.
    """
    if detect_app is not _ORIGINAL_DETECT_APP:
        return detect_app
    return apps_catalog.detect_app


def _status() -> dict:
    try:
        if detect_app is not _ORIGINAL_DETECT_APP:
            # detect_app was patched in this module's namespace: build
            # the same result shape as _common.catalog_status from it,
            # reading strategy/prog_id/name out of the catalog record.
            det = detect_app(APP_ID)
            if not det.get("ok"):
                return det
            record: dict = {}
            got = get_app(APP_ID)
            if got.get("ok"):
                record = got.get("app") or {}
            return ok(app_id=APP_ID, installed=det.get("installed", False),
                      path=det.get("path"), via=det.get("via"),
                      strategy=record.get("strategy"),
                      prog_id=record.get("prog_id"),
                      name=record.get("name") or APP_ID)
        return catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def _launch() -> dict:
    try:
        return catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def _open_file(path: str) -> dict:
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        det = _effective_detect()(APP_ID)
        if not det.get("ok"):
            return det
        exe = det.get("path")
        if not det.get("installed") or not exe:
            return err(
                "Filmora is not installed (none of its executables — "
                "Filmora.exe — were found on this machine), so the file "
                "cannot be opened. Install Wondershare Filmora first.")
        argv = build_open_command(exe, str(checked))
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("filmora_open_file", {"path": str(checked), "exe": str(exe),
                                    "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def _focus() -> dict:
    try:
        return focus_window(DISPLAY_NAME)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the filmora_* tools on a FastMCP server. Returns APP_ID."""

    @mcp.tool()
    def filmora_status() -> dict:
        """Report whether Wondershare Filmora is installed on this machine.

        Returns the catalog detection result: installed flag, the
        Filmora.exe path when found, how it was found, and the catalog
        strategy ("ui" — Filmora has no scripting API, so AppMesh can
        only launch/focus it and hand it files to open).
        """
        return _status()

    @mcp.tool()
    def filmora_launch() -> dict:
        """Launch Wondershare Filmora (no file opened).

        Fails with a clear error dict when Filmora is not installed.
        The launch is written to the audit log. Note this is a plain
        process launch: Filmora exposes no API, so everything after
        launch happens in its own UI.
        """
        return _launch()

    @mcp.tool()
    def filmora_open_file(path: str) -> dict:
        """Open a file in Filmora, e.g. a .wfp project or a media clip
        (.mp4, .mov, ...).

        `path` must live under an allowed root (see the server's path
        allowlist). Filmora is started with the file as its command-line
        argument and Filmora itself loads it — the file's proprietary
        contents are never read or modified by this tool. Fails with a
        clear error dict when the path is not allowed or Filmora is
        not installed.
        """
        return _open_file(path)

    @mcp.tool()
    def filmora_focus() -> dict:
        """Bring an already-open Filmora window to the foreground.

        Uses Windows UI Automation: finds the first window whose title
        contains "Filmora" and focuses it. Returns an error dict when
        no such window is open, on non-Windows machines, or when
        pywinauto is not installed.
        """
        return _focus()

    return APP_ID
