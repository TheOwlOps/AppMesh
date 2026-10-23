"""SketchUp adapter — plugin strategy via the Ruby API.

SketchUp (catalog id ``sketchup``, strategy ``plugin``) has no COM
interface: its official automation surface is the Ruby API, which runs
*inside* the app (Plugins folder, Ruby console, extensions). From
outside, the reliable hand-off points are the executable itself:

- ``SketchUp.exe <model.skp>`` opens a model file in the GUI;
- ``SketchUp.exe -RubyStartup <script.rb>`` starts SketchUp and runs
  the Ruby script ``<script.rb>`` at startup — a script can drive the
  whole Ruby API (open/build a model, export, ``Sketchup.quit``) and
  thereby work effectively headlessly.

This adapter exposes catalog status/launch plus those two routes,
following the project's pure-builder + injectable-runner pattern:
``build_open_argv`` / ``build_run_script_argv`` are pure,
``sketchup_open_file`` spawns through the module-level :func:`_spawn`
helper, and ``sketchup_run_script`` runs its argv through
:func:`_common.run_cli` whose ``runner`` parameter is injectable for
tests. Every path must pass ``core.check_path_allowed`` first and all
tools return ``core.ok`` / ``core.err`` dicts — they never raise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "sketchup"

#: Default timeout (seconds) for a -RubyStartup run. SketchUp is a GUI
#: process even in script mode, so give export-heavy scripts room.
SCRIPT_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Pure builders
# ---------------------------------------------------------------------------

def build_open_argv(exe: str, path: str) -> list[str]:
    """Build the argv that opens *path* in SketchUp. Pure helper.

    SketchUp takes the model to open as a plain positional argument:
    ``[exe, file.skp]``.
    """
    return [str(exe), str(path)]


def build_run_script_argv(exe: str, script_path: str) -> list[str]:
    """Build the argv that runs a Ruby script in SketchUp. Pure helper.

    SketchUp's ``-RubyStartup`` switch loads and executes the given
    Ruby file at startup: ``[exe, "-RubyStartup", script.rb]``.
    """
    return [str(exe), "-RubyStartup", str(script_path)]


# ---------------------------------------------------------------------------
# Injectable helpers
# ---------------------------------------------------------------------------

def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for monkeypatching."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _detect() -> dict:
    """Catalog detection record for SketchUp (monkeypatchable in tests)."""
    return apps_catalog.detect_app(APP_ID)


def _detected_exe(det: dict) -> str | None:
    """Executable path from a detection record, or None if not installed."""
    if det.get("ok") and det.get("installed"):
        return det.get("path") or None
    return None


# ---------------------------------------------------------------------------
# Tool implementations (module-level, directly testable)
# ---------------------------------------------------------------------------

def sketchup_status() -> dict:
    """Return SketchUp detection + strategy info from the catalog."""
    return _common.catalog_status(APP_ID)


def sketchup_launch() -> dict:
    """Launch the installed SketchUp application (no file)."""
    return _common.catalog_launch(APP_ID)


def sketchup_open_file(path: str) -> dict:
    """Open a SketchUp model file (.skp) in the SketchUp GUI.

    The detected ``SketchUp.exe`` is launched with the model path as
    its single argument (route ``exe_with_file_arg`` — SketchUp has no
    COM interface for this). The path must pass
    ``core.check_path_allowed`` first; a disallowed path returns an
    err dict and nothing is launched. When SketchUp is not installed
    an err dict is returned and nothing is spawned either. On success
    the result reports ``opened=True``, the ``route`` used, the exact
    ``argv`` and the child ``pid``.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        det = _detect()
        if not det.get("ok"):
            return det
        exe = _detected_exe(det)
        if not exe:
            return err(
                "SketchUp is not installed (none of its executables — "
                f"SketchUp.exe — were found on this machine); cannot open {checked}.")
        argv = build_open_argv(exe, str(checked))
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("sketchup_open_file", {"app_id": APP_ID, "path": str(checked),
                                     "exe": exe, "argv": argv, "pid": pid,
                                     "route": "exe_with_file_arg"})
        return ok(app_id=APP_ID, opened=True, route="exe_with_file_arg",
                  path=str(checked), exe=exe, argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def sketchup_run_script(script_path: str, runner=None,
                        timeout: int = SCRIPT_TIMEOUT) -> dict:
    """Run a Ruby script in SketchUp via ``-RubyStartup``.

    SketchUp is launched as ``SketchUp.exe -RubyStartup <script.rb>``;
    the script then executes inside SketchUp with full access to the
    Ruby API (``Sketchup.active_model``, entities, importers/exporters,
    ``Sketchup.quit`` to exit when done — a script that never quits
    keeps the GUI open and this call blocks until *timeout*).

    *script_path* must be a ``.rb`` file under an allowed root and
    must exist. When SketchUp is not installed an err dict is returned
    and nothing is run. The argv is executed through
    :func:`_common.run_cli` (``runner`` injectable for tests); its
    result — ok with argv/stdout/stderr/returncode, or err on a
    non-zero exit / missing executable — is returned, augmented with
    ``route="ruby_startup"`` and the script path. This function never
    raises.
    """
    try:
        checked = check_path_allowed(script_path)
        if checked.suffix.lower() != ".rb":
            return err(f"SketchUp startup scripts must be Ruby (.rb) files: {checked}")
        if not checked.exists():
            return err(f"Ruby script does not exist: {checked}")
        det = _detect()
        if not det.get("ok"):
            return det
        exe = _detected_exe(det)
        if not exe:
            return err(
                "SketchUp is not installed (none of its executables — "
                "SketchUp.exe — were found on this machine); "
                f"cannot run script {checked}.")
        argv = build_run_script_argv(exe, str(checked))
        audit("sketchup_run_script", {"app_id": APP_ID, "script": str(checked),
                                      "exe": exe, "argv": argv,
                                      "route": "ruby_startup"})
        result = _common.run_cli(argv, timeout=timeout, runner=runner)
        if result.get("ok"):
            result.update(app_id=APP_ID, route="ruby_startup",
                          script=str(checked), exe=exe)
        return result
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the sketchup_* tools on a FastMCP server. Returns tool names."""
    # Capture module-level impls first: the wrappers below reuse the
    # same names, so bare references inside them would recurse.
    _g = globals()
    _status = _g["sketchup_status"]
    _launch = _g["sketchup_launch"]
    _open_file = _g["sketchup_open_file"]
    _run_script = _g["sketchup_run_script"]

    @mcp.tool()
    def sketchup_status() -> dict:
        """Check whether SketchUp is installed on this machine, where
        its executable lives, and which automation strategy applies
        (plugin — the Ruby API, driven via -RubyStartup scripts)."""
        return _status()

    @mcp.tool()
    def sketchup_launch() -> dict:
        """Launch SketchUp (no file). Fails with a clear error when
        SketchUp is not installed. The launch is written to the audit log."""
        return _launch()

    @mcp.tool()
    def sketchup_open_file(path: str) -> dict:
        """Open a SketchUp model file (e.g. a .skp model) in SketchUp.

        The detected SketchUp.exe is launched with the file as its
        argument. The path must be under an allowed root; disallowed
        paths are rejected before anything is launched. Fails with a
        clear error when SketchUp is not installed.
        """
        return _open_file(path)

    @mcp.tool()
    def sketchup_run_script(script_path: str) -> dict:
        """Run a Ruby script inside SketchUp via -RubyStartup.

        SketchUp starts and executes the .rb script at startup with
        full Ruby API access — use it to build/modify a model, import
        or export files, then call Sketchup.quit in the script to exit
        (otherwise the GUI stays open). The script must be a .rb file
        under an allowed root and SketchUp must be installed. Returns
        SketchUp's stdout/stderr and return code.
        """
        return _run_script(script_path)

    return ["sketchup_status", "sketchup_launch",
            "sketchup_open_file", "sketchup_run_script"]
