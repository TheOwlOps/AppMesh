"""Adobe Lightroom Classic adapter — plugin strategy, executable launch.

Lightroom Classic's official automation surface is its Lua plugin SDK,
which runs *inside* the app — there is no external scripting host, and
critically Lightroom has **no public COM API** (unlike Photoshop or
Illustrator). So from the outside the practical control points are:

- ``lightroom_status`` — catalog detection/strategy info for
  ``lightroom`` (via :mod:`._common`).
- ``lightroom_launch`` — launch Lightroom via the catalog.
- ``lightroom_open_file`` — open a photo or an ``.lrcat`` catalog by
  launching the detected ``Lightroom.exe`` with the file path as its
  argument.

The file path must pass ``core.check_path_allowed`` first; a disallowed
path returns an ``err`` dict and nothing is launched. Process start is
factored into the module-level :func:`_spawn` helper so tests can
monkeypatch it, and every successful launch is written to the audit
log. All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "lightroom"


# ---------------------------------------------------------------------------
# Injectable helpers
# ---------------------------------------------------------------------------

def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for monkeypatching."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


# ---------------------------------------------------------------------------
# Tool implementations (module-level, directly testable)
# ---------------------------------------------------------------------------

def lightroom_status() -> dict:
    """Return Lightroom Classic detection + strategy info from the catalog."""
    return _common.catalog_status(APP_ID)


def lightroom_launch() -> dict:
    """Launch the installed Lightroom Classic application (no file)."""
    return _common.catalog_launch(APP_ID)


def lightroom_open_file(path: str, spawn=None) -> dict:
    """Open a photo or Lightroom catalog file in Lightroom Classic.

    *path* may be a photo Lightroom can import/open (e.g. ``.dng``,
    ``.cr2``, ``.jpg``, ``.tif``) or a Lightroom catalog (``.lrcat``).

    Note: Lightroom has no public COM API, so this goes through the
    executable — the detected ``Lightroom.exe`` is launched with the
    file path as its argument, exactly as if the file had been opened
    from Explorer / ``Lightroom.exe <file>`` on the command line.

    The path must pass ``core.check_path_allowed`` first; a disallowed
    path returns an err dict and nothing is launched. When Lightroom
    is not installed (per catalog detection) an err dict is returned
    and nothing is spawned either. On success the result includes the
    launched file path and the exact command (``argv``) that was run,
    and the launch is written to the audit log.
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
                f"Adobe Lightroom Classic is not installed (none of its "
                f"executables were found on this machine); cannot open "
                f"{checked}.")
        exe = det.get("path")
        if not exe:
            return err(
                f"Adobe Lightroom Classic was detected but its executable "
                f"path is unknown; cannot open {checked}.")
        argv = [exe, str(checked)]
        spawner = spawn if spawn is not None else _spawn
        proc = spawner(argv)
        pid = getattr(proc, "pid", None)
        audit("lightroom_open_file", {"app_id": APP_ID, "path": str(checked),
                                      "exe": exe, "argv": argv, "pid": pid})
        return ok(app_id=APP_ID, opened=True, launched=True,
                  path=str(checked), file=str(checked),
                  exe=exe, argv=argv, command=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the lightroom_* tools on a FastMCP server. Returns tool names."""
    # Capture module-level impls first: the wrappers below reuse the
    # same names, so bare references inside them would recurse.
    _g = globals()
    _status = _g["lightroom_status"]
    _launch = _g["lightroom_launch"]
    _open_file = _g["lightroom_open_file"]

    @mcp.tool()
    def lightroom_status() -> dict:
        """Check whether Adobe Lightroom Classic is installed on this
        machine, where its executable (Lightroom.exe) was found, and
        which automation strategy applies (plugin — Lua plugin SDK
        inside the app; there is no public COM API)."""
        return _status()

    @mcp.tool()
    def lightroom_launch() -> dict:
        """Launch Adobe Lightroom Classic (no file). Fails with a clear
        error when Lightroom is not installed. The launch is written
        to the audit log."""
        return _launch()

    @mcp.tool()
    def lightroom_open_file(path: str) -> dict:
        """Open a photo or a Lightroom catalog (.lrcat) in Lightroom Classic.

        Lightroom has no public COM API, so this goes through the
        executable: the detected Lightroom.exe is launched with `path`
        as its argument. `path` must be under an allowed root;
        disallowed paths are rejected with an error and nothing is
        launched. Lightroom must be installed.
        """
        return _open_file(path)

    return ["lightroom_status", "lightroom_launch", "lightroom_open_file"]
