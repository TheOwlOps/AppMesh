"""GIMP adapter — plugin strategy via Script-Fu / Python-Fu batch mode.

GIMP has no COM interface. Its official automation surface is batch
mode: ``gimp -i -b '<Script-Fu expression>' -b '(gimp-quit 0)'`` runs
headless (``-i`` = no interface), evaluates the Script-Fu / Python-Fu
expression, then quits. This adapter exposes that, plus the usual
catalog status/launch tools and a file-open helper.

Pure builder + injectable runner pattern (see ``cli_tools``):
``build_batch_command`` is pure; ``gimp_run_batch`` runs the argv it
builds through :func:`_common.run_cli`, whose ``runner`` parameter is
injectable for tests. ``gimp_open_file`` spawns the detected executable
through the module-level :func:`_spawn` helper so tests can monkeypatch
it. All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "gimp"

#: Default timeout (seconds) for a batch run — image exports can be slow.
BATCH_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Pure builder
# ---------------------------------------------------------------------------

def build_batch_command(script_expr: str, exe: str = "gimp") -> list[str]:
    """Build the GIMP batch-mode argv for one Script-Fu expression.

    Pure: no I/O, no detection — *exe* is taken as given (callers pass
    the detected executable path when one is available).

    >>> build_batch_command('(gimp-message "hi")')
    ['gimp', '-i', '-b', '(gimp-message "hi")', '-b', '(gimp-quit 0)']
    """
    return [exe, "-i", "-b", script_expr, "-b", "(gimp-quit 0)"]


# ---------------------------------------------------------------------------
# Injectable helpers
# ---------------------------------------------------------------------------

def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for monkeypatching."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _detected_exe() -> str | None:
    """Return the detected GIMP executable path, or None if not installed."""
    try:
        det = apps_catalog.detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — detection must never crash a tool
        return None
    if det.get("ok") and det.get("installed"):
        return det.get("path") or None
    return None


# ---------------------------------------------------------------------------
# Tool implementations (module-level, directly testable)
# ---------------------------------------------------------------------------

def gimp_status() -> dict:
    """Return GIMP detection + strategy info from the catalog."""
    return _common.catalog_status(APP_ID)


def gimp_launch() -> dict:
    """Launch the installed GIMP application (no file)."""
    return _common.catalog_launch(APP_ID)


def gimp_open_file(path: str, spawn=None) -> dict:
    """Open *path* (e.g. an .xcf / image file) in GIMP.

    The path must pass ``core.check_path_allowed`` first; a disallowed
    path returns an err dict and nothing is launched. When GIMP is not
    installed (per catalog detection) an err dict is returned and
    nothing is spawned either.
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
                f"GIMP is not installed (none of its executables were found "
                f"on this machine); cannot open {checked}.")
        exe = det.get("path") or "gimp"
        argv = [exe, str(checked)]
        spawner = spawn if spawn is not None else _spawn
        proc = spawner(argv)
        pid = getattr(proc, "pid", None)
        audit("gimp_open_file", {"path": str(checked), "exe": exe, "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  exe=exe, argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def gimp_run_batch(script_expr: str, runner=None, timeout: int = BATCH_TIMEOUT) -> dict:
    """Run one Script-Fu batch expression in GIMP headless batch mode.

    *script_expr* is a Script-Fu expression evaluated by
    ``gimp -i -b <expr> -b '(gimp-quit 0)'`` — for example
    ``(gimp-message "hello")`` or a full export pipeline such as::

        (let* ((image (car (gimp-file-load RUN-NONINTERACTIVE
                        "in.xcf" "in")))
               (drawable (car (gimp-image-get-active-layer image))))
          (file-png-export RUN-NONINTERACTIVE image drawable
                           "out.png" "out.png" 0 9 1 1 1 1 1))

    Python-Fu expressions work the same way when the python-fu plug-in
    is installed. The detected GIMP executable is used when available,
    otherwise plain ``gimp`` from PATH. The run is audit-logged and the
    :func:`_common.run_cli` result dict is returned as-is (``ok`` with
    stdout/stderr/returncode, or ``err`` on a non-zero exit / missing
    executable) — this function never raises.
    """
    try:
        exe = _detected_exe() or "gimp"
        argv = build_batch_command(script_expr, exe=exe)
        audit("gimp_run_batch", {"argv": argv, "script_expr": script_expr})
        return _common.run_cli(argv, timeout=timeout, runner=runner)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the gimp_* tools on a FastMCP server. Returns tool names."""
    # Capture module-level impls first: the wrappers below reuse the
    # same names, so bare references inside them would recurse.
    _g = globals()
    _status = _g["gimp_status"]
    _launch = _g["gimp_launch"]
    _open_file = _g["gimp_open_file"]
    _run_batch = _g["gimp_run_batch"]

    @mcp.tool()
    def gimp_status() -> dict:
        """Check whether GIMP is installed on this machine, where its
        executable lives, and which automation strategy applies
        (plugin — Script-Fu / Python-Fu batch mode)."""
        return _status()

    @mcp.tool()
    def gimp_launch() -> dict:
        """Launch GIMP (no file). Fails with a clear error when GIMP
        is not installed. The launch is written to the audit log."""
        return _launch()

    @mcp.tool()
    def gimp_open_file(path: str) -> dict:
        """Open an image/project file (e.g. .xcf, .png, .jpg) in GIMP.
        The path must be under an allowed root; GIMP must be installed."""
        return _open_file(path)

    @mcp.tool()
    def gimp_run_batch(script_expr: str) -> dict:
        """Run a Script-Fu batch expression in GIMP headless batch mode.

        The expression is passed to ``gimp -i -b <expr> -b '(gimp-quit 0)'``.
        Examples: ``(gimp-message "hello")`` to smoke-test, or a
        ``gimp-file-load`` + ``file-png-export`` let* form to convert /
        export an image without opening the GUI. Python-Fu expressions
        also work when the python-fu plug-in is installed. Returns
        GIMP's stdout/stderr and return code.
        """
        return _run_batch(script_expr)

    return ["gimp_status", "gimp_launch", "gimp_open_file", "gimp_run_batch"]
