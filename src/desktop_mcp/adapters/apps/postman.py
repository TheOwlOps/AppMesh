"""Postman adapter — UI strategy.

Postman's desktop GUI exposes no local automation API (the catalog
lists it as strategy ``ui``; collections can be run headless via the
separate ``newman`` CLI, which is not part of this adapter). What this
adapter can honestly do locally:

- ``postman_status`` / ``postman_launch`` — catalog detection and launch.
- ``postman_open_collection`` — launch the detected Postman executable
  with a collection ``.json`` file as its argument, so Postman opens
  with that collection file.
- ``postman_focus`` — bring an already-open Postman window to the front
  via Windows UI Automation (``_common.focus_window``).

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "postman"


# ---------------------------------------------------------------------------
# Injectable helpers
# ---------------------------------------------------------------------------

def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for monkeypatching."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _display_name() -> str:
    """Return Postman's display name from its catalog entry.

    ``apps_catalog.get_app`` returns ``ok(app={...})``; fall back to the
    plain name ``"Postman"`` if the catalog lookup ever fails.
    """
    try:
        rec = apps_catalog.get_app(APP_ID)
        entry = rec.get("app") if isinstance(rec, dict) else None
        if isinstance(entry, dict) and entry.get("name"):
            return str(entry["name"])
        if isinstance(rec, dict) and rec.get("name"):
            return str(rec["name"])
    except Exception:  # noqa: BLE001 — a name lookup must never crash a tool
        pass
    return "Postman"


# ---------------------------------------------------------------------------
# Tool implementations (module-level, directly testable)
# ---------------------------------------------------------------------------

def postman_status() -> dict:
    """Return Postman detection + strategy info from the catalog."""
    return _common.catalog_status(APP_ID)


def postman_launch() -> dict:
    """Launch the installed Postman application (no collection file)."""
    return _common.catalog_launch(APP_ID)


def postman_focus() -> dict:
    """Bring an already-open Postman window to the front.

    Uses ``_common.focus_window`` with Postman's display name taken
    from the catalog entry. Fails with an err dict when no Postman
    window is open or on non-Windows machines.
    """
    try:
        return _common.focus_window(_display_name())
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def postman_open_collection(path: str, spawn=None) -> dict:
    """Open a Postman collection ``.json`` file in Postman.

    The path must pass ``core.check_path_allowed`` first and must name
    a ``.json`` file — a disallowed path or a non-``.json`` path returns
    an err dict and nothing is launched. When Postman is not installed
    (per catalog detection) an err dict is returned and nothing is
    spawned either. Otherwise the detected Postman executable is
    launched with the collection file as its sole argument:
    ``[exe, path]``.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        if Path(checked).suffix.lower() != ".json":
            return err(
                f"Postman collections must be .json files: {checked}")
        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return det
        if not det.get("installed"):
            return err(
                "Postman is not installed (none of its executables were "
                f"found on this machine); cannot open {checked}.")
        exe = det.get("path")
        if not exe:
            return err(
                "Postman is not installed (no executable path was "
                f"detected); cannot open {checked}.")
        argv = [exe, str(checked)]
        spawner = spawn if spawn is not None else _spawn
        proc = spawner(argv)
        pid = getattr(proc, "pid", None)
        audit("postman_open_collection",
              {"path": str(checked), "exe": exe, "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  exe=exe, argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the postman_* tools on a FastMCP server. Returns tool names."""
    # Capture module-level impls first: the wrappers below reuse the
    # same names, so bare references inside them would recurse.
    _g = globals()
    _status = _g["postman_status"]
    _launch = _g["postman_launch"]
    _focus = _g["postman_focus"]
    _open_collection = _g["postman_open_collection"]

    @mcp.tool()
    def postman_status() -> dict:
        """Check whether Postman is installed on this machine, where
        its executable lives, and which automation strategy applies
        (UI — the desktop GUI has no local API)."""
        return _status()

    @mcp.tool()
    def postman_launch() -> dict:
        """Launch Postman (no collection file). Fails with a clear
        error when Postman is not installed. The launch is written to
        the audit log."""
        return _launch()

    @mcp.tool()
    def postman_open_collection(path: str) -> dict:
        """Open a Postman collection .json file in Postman.

        The path must be under an allowed root and end in .json;
        Postman must be installed. Postman is launched with the
        collection file as its argument."""
        return _open_collection(path)

    @mcp.tool()
    def postman_focus() -> dict:
        """Bring an already-open Postman window to the front (Windows
        UI Automation). Fails when no Postman window is open or on
        non-Windows machines."""
        return _focus()

    return ["postman_status", "postman_launch",
            "postman_open_collection", "postman_focus"]
